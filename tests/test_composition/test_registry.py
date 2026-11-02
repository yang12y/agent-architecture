"""会话注册表的测试。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent_frame.composition import SessionRegistry
from agent_frame.contracts import CancelToken, SessionEntry, SessionEntryType, SessionStatus
from agent_frame.infra.storage import FileSessionStore
from agent_frame.modules.session import Session
from fakes import (
    START,
    USER,
    FakeClock,
    SequentialIds,
    assistant_message,
    make_app_config,
    make_entry,
    make_session_meta,
    trace_end_payload,
    trace_start_payload,
    user_message,
)


def _registry(scratch_dir: Path, store=None) -> SessionRegistry:
    """造一个用固定时钟、顺序 id、临时会话目录的注册表。

    举例：await _registry(scratch_dir).create(user_id=USER) -> LiveSession(session_id="sess_000001")
    """
    return SessionRegistry(
        store=store if store is not None else FileSessionStore(scratch_dir / "sessions"),
        ids=SequentialIds(),
        clock=FakeClock(),
        config=make_app_config(),
    )


def _pending_task() -> asyncio.Task[None]:
    """造一个一直挂着的 task，用完必须显式取消。

    举例：task = _pending_task(); ...; task.cancel()
    """
    return asyncio.create_task(asyncio.Event().wait())


async def _cancel(task: asyncio.Task[None]) -> None:
    """取消一个挂着的 task 并等它真的结束。

    取消后 await 它会抛 CancelledError，所以这里把它吃掉 —— 测试关心的是注册表，
    不是 asyncio 的取消语义。

    举例：await _cancel(task)
    """
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_create_registers_and_persists_the_meta(scratch_dir: Path) -> None:
    """新建会话：写进 store，同时登记成活状态。"""
    store = FileSessionStore(scratch_dir / "sessions")
    registry = _registry(scratch_dir, store)

    live = await registry.create(user_id=USER, title="重构 session 模块")

    assert live.session_id.startswith("sess_")
    assert live.session.meta.title == "重构 session 模块"
    assert live.session.meta.status is SessionStatus.ACTIVE
    assert [meta.session_id for meta in await store.list_sessions(USER)] == [live.session_id]


async def test_create_takes_defaults_from_the_config(scratch_dir: Path) -> None:
    """workspace / model / permission 取进程级配置的默认值。"""
    config = make_app_config(workspace="D:/proj", model="another-model")
    registry = SessionRegistry(
        store=FileSessionStore(scratch_dir / "sessions"),
        ids=SequentialIds(),
        clock=FakeClock(),
        config=config,
    )

    meta = (await registry.create(user_id=USER)).session.meta

    assert (meta.workspace, meta.model) == ("D:/proj", "another-model")


async def test_get_does_not_load_from_the_store(scratch_dir: Path) -> None:
    """get 只查活状态 —— 没登记过的会话返回 None，不偷偷去读盘。"""
    store = FileSessionStore(scratch_dir / "sessions")
    await store.save_meta(make_session_meta("sess_别的"))
    registry = _registry(scratch_dir, store)

    assert registry.get("sess_别的") is None


async def test_get_or_load_builds_from_the_store(scratch_dir: Path) -> None:
    """进程里没有的会话，从 store 装一份出来。"""
    store = FileSessionStore(scratch_dir / "sessions")
    await store.save_meta(make_session_meta("sess_ab12"))
    registry = _registry(scratch_dir, store)

    live = await registry.get_or_load("sess_ab12", USER)

    assert live is not None
    assert live.session_id == "sess_ab12"
    # 装过之后就在表里了，第二次拿的是同一个对象
    assert await registry.get_or_load("sess_ab12", USER) is live


async def test_get_or_load_returns_none_for_an_unknown_session(scratch_dir: Path) -> None:
    """store 里也没有的会话返回 None，上层据此走新建分支。"""
    assert await _registry(scratch_dir).get_or_load("sess_没见过", USER) is None


async def test_a_loaded_session_projects_the_entries_from_the_store(scratch_dir: Path) -> None:
    """从 store 装出来的会话，投影的就是 store 里的条目。

    注意内存 Session 的 append_entry **不写 store** —— 落库归 Checkpoint（S3），
    所以这里得先手工把条目放进去。
    """
    store = FileSessionStore(scratch_dir / "sessions")
    await store.save_meta(make_session_meta("sess_ab12"))
    await store.append("sess_ab12", [make_entry(1, user_message("历史消息"))])
    await store.flush("sess_ab12", up_to_seq=1)

    live = await _registry(scratch_dir, store).get_or_load("sess_ab12", USER)

    assert live is not None
    assert [message.content[0].text for message in live.session.messages()] == ["历史消息"]  # type: ignore[union-attr]


async def test_a_restart_drops_the_live_session_but_not_the_log(scratch_dir: Path) -> None:
    """重启：注册表空了，但会话还装得回来 —— 因为日志在文件里。"""
    first = _registry(scratch_dir)
    live = await first.create(user_id=USER, title="重启前")
    session_id = live.session_id

    restarted = _registry(scratch_dir)

    assert restarted.live_ids() == []
    reloaded = await restarted.get_or_load(session_id, USER)
    assert reloaded is not None
    assert reloaded.session.meta.title == "重启前"


async def test_get_or_load_rejects_another_user(scratch_dir: Path) -> None:
    """从盘上装：不是自己的会话，返回 None —— 和"不存在"回同一个话。"""
    store = FileSessionStore(scratch_dir / "sessions")
    await store.save_meta(make_session_meta("sess_ab12"))

    assert await _registry(scratch_dir, store).get_or_load("sess_ab12", "user_别的") is None


async def test_get_or_load_rejects_another_user_for_a_live_session(scratch_dir: Path) -> None:
    """已经在内存里的也要判归属：注册表是进程级的，多个用户共用同一张表。"""
    registry = _registry(scratch_dir)
    live = await registry.create(user_id=USER)

    assert registry.get(live.session_id) is live  # 裸查表不做归属判断
    assert await registry.get_or_load(live.session_id, "user_别的") is None
    assert await registry.get_or_load(live.session_id, USER) is live


async def test_stored_metas_are_scoped_to_one_user(scratch_dir: Path) -> None:
    """会话列表按用户分开 —— 别人的会话不出现。"""
    store = FileSessionStore(scratch_dir / "sessions")
    await store.save_meta(make_session_meta("sess_别人的", user_id="user_别的"))
    registry = _registry(scratch_dir, store)
    live = await registry.create(user_id=USER)

    assert [meta.session_id for meta in await registry.stored_metas(USER)] == [live.session_id]
    assert [meta.session_id for meta in await registry.stored_metas("user_别的")] == ["sess_别人的"]


async def test_attach_detach_and_is_running(scratch_dir: Path) -> None:
    """attach 之后算在跑，detach 之后不算。"""
    registry = _registry(scratch_dir)
    live = await registry.create(user_id=USER)
    task = _pending_task()

    registry.attach_task(live.session_id, task=task, signal=CancelToken())

    assert live.is_running is True
    assert registry.get(live.session_id) is live

    registry.detach_task(live.session_id)
    assert live.is_running is False
    assert live.signal is None
    assert live.session is not None  # session 要留着，多表面还要读

    await _cancel(task)


def test_detach_on_an_unknown_session_is_harmless(scratch_dir: Path) -> None:
    """对没登记过的会话 detach 不报错。"""
    _registry(scratch_dir).detach_task("sess_没见过")


async def test_interrupt_sets_the_signal_of_the_running_trace(scratch_dir: Path) -> None:
    """中断就是 set 取消信号；真正收尾由内核在安全点做。"""
    registry = _registry(scratch_dir)
    live = await registry.create(user_id=USER)
    token = CancelToken()
    task = _pending_task()
    registry.attach_task(live.session_id, task=task, signal=token)

    assert registry.interrupt(live.session_id) is True
    assert token.is_set() is True

    await _cancel(task)


async def test_interrupt_without_a_running_trace_returns_false(scratch_dir: Path) -> None:
    """没有正在跑的 trace 时是幂等的：返回 False，不报错。"""
    registry = _registry(scratch_dir)
    live = await registry.create(user_id=USER)

    assert registry.interrupt(live.session_id) is False
    assert registry.interrupt("sess_没见过") is False


async def test_live_ids_and_stored_metas(scratch_dir: Path) -> None:
    """活状态与已落库的元信息是两个视角。"""
    store = FileSessionStore(scratch_dir / "sessions")
    await store.save_meta(make_session_meta("sess_别的"))
    registry = _registry(scratch_dir, store)
    live = await registry.create(user_id=USER)

    assert registry.live_ids() == [live.session_id]
    assert {meta.session_id for meta in await registry.stored_metas(USER)} == {
        live.session_id,
        "sess_别的",
    }


async def test_register_uses_the_injected_session_class(scratch_dir: Path) -> None:
    """登记出来的是真正的 Session，不是桩。"""
    live = await _registry(scratch_dir).create(user_id=USER)

    assert isinstance(live.session, Session)


# ---- 分叉：复制一段前缀成新会话 ----


def _two_turns() -> list[SessionEntry]:
    """两轮完整对话：第一轮的用户消息在 seq=2，第二轮在 seq=6。

    举例：fork_prefix(_two_turns(), 2) -> 前四条
    """
    return [
        make_entry(1, trace_start_payload(), trace_id="trace_1"),
        make_entry(2, user_message("第一种问法"), trace_id="trace_1"),
        make_entry(3, assistant_message("第一种问法的回答"), trace_id="trace_1"),
        make_entry(4, trace_end_payload(), trace_id="trace_1"),
        make_entry(5, trace_start_payload(), trace_id="trace_2"),
        make_entry(6, user_message("第二种问法"), trace_id="trace_2"),
        make_entry(7, assistant_message("第二种问法的回答"), trace_id="trace_2"),
        make_entry(8, trace_end_payload(), trace_id="trace_2"),
    ]


async def _source_with_two_turns(registry: SessionRegistry) -> object:
    """造一个已经跑完两轮的会话。

    条目是直接塞进内存 Session 的：落库归 Checkpoint，注册表不写日志。

    举例：source = await _source_with_two_turns(_registry(scratch_dir))
    """
    live = await registry.create(user_id=USER, title="原会话")
    for entry in _two_turns():
        live.session.append(entry)
    return live


async def test_fork_copies_the_prefix_and_leaves_the_source_alone(scratch_dir: Path) -> None:
    """分叉 = 新会话 + 一段前缀。原会话一条不多、一条不少。"""
    registry = _registry(scratch_dir)
    source = await _source_with_two_turns(registry)

    forked = await registry.fork(source, turn_seq=2)  # type: ignore[arg-type]

    assert forked is not None
    assert forked.session_id != source.session_id  # type: ignore[attr-defined]
    assert [entry.seq for entry in forked.session.entries()] == [1, 2, 3, 4]
    assert forked.session.meta.parent_session_id == source.session_id  # type: ignore[attr-defined]
    assert forked.session.meta.title == "原会话"
    assert len(source.session.entries()) == 8  # type: ignore[attr-defined]
    assert set(registry.live_ids()) == {forked.session_id, source.session_id}  # type: ignore[attr-defined]


async def test_a_fork_is_on_disk_not_only_in_memory(scratch_dir: Path) -> None:
    """重开进程还能读到它 —— 分叉当场就落盘了，不等下一次屏障。"""
    registry = _registry(scratch_dir)
    source = await _source_with_two_turns(registry)
    forked = await registry.fork(source, turn_seq=2)  # type: ignore[arg-type]
    assert forked is not None

    restarted = _registry(scratch_dir)
    loaded = await restarted.get_or_load(forked.session_id, USER)

    assert loaded is not None
    assert [entry.seq for entry in loaded.session.entries()] == [1, 2, 3, 4]
    assert loaded.session.meta.parent_session_id == source.session_id  # type: ignore[attr-defined]


async def test_a_fork_continues_its_own_seq(scratch_dir: Path) -> None:
    """接着问话从自己的 seq 往下走，不是从 1 重来。"""
    registry = _registry(scratch_dir)
    source = await _source_with_two_turns(registry)
    forked = await registry.fork(source, turn_seq=2)  # type: ignore[arg-type]
    assert forked is not None

    entry = forked.session.append_entry(
        trace_id="trace_9",
        type=SessionEntryType.MESSAGE,
        payload=user_message("下一问"),
        now=START,
    )

    assert entry.seq == 5


async def test_a_fork_follows_the_source_settings(scratch_dir: Path) -> None:
    """配置逐字抄源会话，不抄进程级配置 —— 同一段日志在两边被截成同一个长度。"""
    registry = _registry(scratch_dir)
    source = await _source_with_two_turns(registry)
    source.session.meta.tool_result_max_chars = 1234  # type: ignore[attr-defined]

    forked = await registry.fork(source, turn_seq=2)  # type: ignore[arg-type]

    assert forked is not None
    assert forked.session.meta.tool_result_max_chars == 1234


async def test_a_fork_survives_deleting_its_source(scratch_dir: Path) -> None:
    """父会话删了，分叉照常 -- parent_session_id 只是一条血缘指针，不是外键。"""
    registry = _registry(scratch_dir)
    source = await _source_with_two_turns(registry)
    forked = await registry.fork(source, turn_seq=2)  # type: ignore[arg-type]
    assert forked is not None

    await registry.delete(source.session_id, USER)  # type: ignore[attr-defined]

    reloaded = await _registry(scratch_dir).get_or_load(forked.session_id, USER)
    assert reloaded is not None
    assert len(reloaded.session.entries()) == 4


async def test_fork_refuses_an_unforkable_turn(scratch_dir: Path) -> None:
    """不是用户消息、seq 不存在、这一轮还没收尾 —— 都拿不到前缀。"""
    registry = _registry(scratch_dir)
    source = await _source_with_two_turns(registry)

    assert await registry.fork(source, turn_seq=3) is None  # type: ignore[arg-type]
    assert await registry.fork(source, turn_seq=99) is None  # type: ignore[arg-type]


async def test_fork_of_an_open_turn_is_refused(scratch_dir: Path) -> None:
    """这一轮还在跑（没有 trace_end）时分不出来。"""
    registry = _registry(scratch_dir)
    source = await registry.create(user_id=USER)
    for entry in _two_turns()[:5]:  # 第二轮刚开个头
        source.session.append(entry)

    assert await registry.fork(source, turn_seq=6) is None

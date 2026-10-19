"""文件版存储的测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from fakes import USER, SequentialIds, make_entry, make_session_meta, user_message
from yzy_agent.contracts import FlushError, SessionEntryType
from yzy_agent.infra.storage import FileSessionStore
from yzy_agent.modules.session import Session


def _store(scratch_dir: Path) -> FileSessionStore:
    """造一个根在临时目录的存储。

    举例：_store(scratch_dir).root -> scratch_dir/"sessions"
    """
    return FileSessionStore(scratch_dir / "sessions")


def _entries(count: int):
    """造若干条带 seq 的条目。

    举例：_entries(2) -> [SessionEntry(seq=1, ...), SessionEntry(seq=2, ...)]
    """
    return [make_entry(seq, user_message(f"第 {seq} 条")) for seq in range(1, count + 1)]


async def _store_with(scratch_dir: Path, session_id: str = "sess_ab12") -> FileSessionStore:
    """造一个已经写了元信息的存储。

    举例：(await _store_with(scratch_dir)).pending_count("sess_ab12") -> 0
    """
    store = _store(scratch_dir)
    await store.save_meta(make_session_meta(session_id))
    return store


async def test_loading_an_unknown_session_returns_none(scratch_dir: Path) -> None:
    """没见过的会话就是 None，上层据此走新建分支。"""
    assert await _store(scratch_dir).load("sess_没见过", USER) is None


async def test_meta_is_available_without_any_entries(scratch_dir: Path) -> None:
    """刚建好的会话只有 meta 没有条目。"""
    store = await _store_with(scratch_dir)

    loaded = await store.load("sess_ab12", USER)

    assert loaded is not None
    assert loaded[1] == []


async def test_appended_entries_are_not_visible_until_flushed(scratch_dir: Path) -> None:
    """append 只进内存缓冲区，文件里还没有 —— 崩溃之后读到的就是这个状态。"""
    store = await _store_with(scratch_dir)
    await store.append("sess_ab12", _entries(2))

    assert (await store.load("sess_ab12", USER))[1] == []  # type: ignore[index]
    assert store.pending_count("sess_ab12") == 2


async def test_flush_makes_entries_durable(scratch_dir: Path) -> None:
    """flush 之后才读得到。"""
    store = await _store_with(scratch_dir)
    await store.append("sess_ab12", _entries(2))

    await store.flush("sess_ab12", up_to_seq=2)

    loaded = await store.load("sess_ab12", USER)
    assert [entry.seq for entry in loaded[1]] == [1, 2]  # type: ignore[index]
    assert store.pending_count("sess_ab12") == 0


async def test_flush_only_moves_entries_up_to_the_given_seq(scratch_dir: Path) -> None:
    """屏障推到第 N 号为止，后面的还留在缓冲区里。"""
    store = await _store_with(scratch_dir)
    await store.append("sess_ab12", _entries(3))

    await store.flush("sess_ab12", up_to_seq=2)

    loaded = await store.load("sess_ab12", USER)
    assert [entry.seq for entry in loaded[1]] == [1, 2]  # type: ignore[index]
    assert store.pending_count("sess_ab12") == 1


async def test_a_failed_flush_raises_and_moves_nothing(scratch_dir: Path) -> None:
    """落盘失败必须抛 FlushError，而且不能悄悄把缓冲区清掉。"""
    store = await _store_with(scratch_dir)
    await store.append("sess_ab12", _entries(2))
    store.fail_next_flush()

    with pytest.raises(FlushError):
        await store.flush("sess_ab12", up_to_seq=2)

    assert (await store.load("sess_ab12", USER))[1] == []  # type: ignore[index]
    assert store.pending_count("sess_ab12") == 2


async def test_the_failure_hook_fires_only_once(scratch_dir: Path) -> None:
    """注入的失败只作用一次，方便测「失败之后再试一次成功」。"""
    store = await _store_with(scratch_dir)
    await store.append("sess_ab12", _entries(1))
    store.fail_next_flush()

    with pytest.raises(FlushError):
        await store.flush("sess_ab12", up_to_seq=1)
    await store.flush("sess_ab12", up_to_seq=1)

    assert store.pending_count("sess_ab12") == 0


async def test_list_sessions_returns_every_meta(scratch_dir: Path) -> None:
    """会话列表读的是 meta，不需要碰条目。"""
    store = await _store_with(scratch_dir, "sess_a")
    await store.save_meta(make_session_meta("sess_b"))

    assert {meta.session_id for meta in await store.list_sessions(USER)} == {"sess_a", "sess_b"}


async def test_a_new_instance_reads_back_what_was_flushed(scratch_dir: Path) -> None:
    """重启语义：换一个实例 load，读到的就是上一次成功落盘的那一份。"""
    store = await _store_with(scratch_dir)
    await store.append("sess_ab12", _entries(2))
    await store.flush("sess_ab12", up_to_seq=2)

    restarted = FileSessionStore(scratch_dir / "sessions")
    loaded = await restarted.load("sess_ab12", USER)

    assert loaded is not None
    assert [entry.seq for entry in loaded[1]] == [1, 2]


async def test_the_unflushed_tail_is_gone_after_a_restart(scratch_dir: Path) -> None:
    """崩溃语义：没 flush 的那一截，换实例之后真的不在。"""
    store = await _store_with(scratch_dir)
    await store.append("sess_ab12", _entries(2))
    await store.flush("sess_ab12", up_to_seq=2)
    await store.append("sess_ab12", [make_entry(3, user_message("第 3 条"))])

    restarted = FileSessionStore(scratch_dir / "sessions")
    loaded = await restarted.load("sess_ab12", USER)

    assert loaded is not None
    assert [entry.seq for entry in loaded[1]] == [1, 2]


async def test_flushing_appends_to_what_is_already_on_disk(scratch_dir: Path) -> None:
    """第二次 flush 不能把第一次写下的覆盖掉。"""
    store = await _store_with(scratch_dir)
    await store.append("sess_ab12", _entries(2))
    await store.flush("sess_ab12", up_to_seq=2)
    await store.append("sess_ab12", [make_entry(3, user_message("第 3 条"))])
    await store.flush("sess_ab12", up_to_seq=3)

    loaded = await store.load("sess_ab12", USER)
    assert [entry.seq for entry in loaded[1]] == [1, 2, 3]  # type: ignore[index]


async def test_entries_land_one_per_line(scratch_dir: Path) -> None:
    """jsonl 一行一条 —— 写路径才不用解码。"""
    store = await _store_with(scratch_dir)
    await store.append("sess_ab12", _entries(3))
    await store.flush("sess_ab12", up_to_seq=3)

    text = (scratch_dir / "sessions" / "sess_ab12" / "entries.jsonl").read_text(encoding="utf-8")

    assert len(text.splitlines()) == 3


async def test_no_tmp_file_is_left_behind(scratch_dir: Path) -> None:
    """原子替换成功之后，临时文件不该留下。"""
    store = await _store_with(scratch_dir)
    await store.append("sess_ab12", _entries(1))
    await store.flush("sess_ab12", up_to_seq=1)

    leftovers = list((scratch_dir / "sessions" / "sess_ab12").glob("*.tmp"))

    assert leftovers == []


async def test_another_user_cannot_load_the_session(scratch_dir: Path) -> None:
    """会话在、但不属于这个用户 —— 读出来是 None，和"不存在"一个样子。"""
    store = await _store_with(scratch_dir)

    assert await store.load("sess_ab12", "user_别的") is None
    assert await store.load("sess_ab12", USER) is not None


async def test_delete_removes_the_session_for_good(scratch_dir: Path) -> None:
    """删掉之后连目录都没了 —— 不是打个标记，是真的没了。"""
    store = await _store_with(scratch_dir)

    assert await store.delete("sess_ab12", USER) is True
    assert await store.load("sess_ab12", USER) is None
    assert not (scratch_dir / "sess_ab12").exists()


async def test_delete_refuses_for_another_user(scratch_dir: Path) -> None:
    """归属对不上就删不动，而且会话原封不动 —— 和 load 同一条规则。"""
    store = await _store_with(scratch_dir)

    assert await store.delete("sess_ab12", "user_别的") is False
    assert await store.load("sess_ab12", USER) is not None


async def test_list_sessions_only_returns_one_users_sessions(scratch_dir: Path) -> None:
    """列表按 user_id 分：别人的会话不出现。"""
    store = await _store_with(scratch_dir, "sess_a")
    await store.save_meta(make_session_meta("sess_b", user_id="user_别的"))

    assert [meta.session_id for meta in await store.list_sessions(USER)] == ["sess_a"]
    assert [meta.session_id for meta in await store.list_sessions("user_别的")] == ["sess_b"]


async def test_a_session_survives_a_round_trip(scratch_dir: Path) -> None:
    """集成：内存 Session 写出来的条目，落盘再装回来，投影结果一致。"""
    store = await _store_with(scratch_dir)
    session = Session(make_session_meta("sess_ab12"), [], ids=SequentialIds())
    session.append_entry(
        trace_id="t",
        type=SessionEntryType.MESSAGE,
        payload=user_message("你好"),
        now=None,  # type: ignore[arg-type]
    )
    await store.append("sess_ab12", session.entries())
    await store.flush("sess_ab12", up_to_seq=1)

    meta, entries = await store.load("sess_ab12", USER)  # type: ignore[misc]
    reloaded = Session(meta, entries, ids=SequentialIds())

    assert reloaded.messages() == session.messages()

"""恢复七步的测试。"""

from __future__ import annotations

from pathlib import Path

from fakes import (
    START,
    USER,
    FakeToolRegistry,
    SequentialIds,
    make_session_meta,
    make_tool_declaration,
)
from test_checkpoint.traces import TRACE_ID, broken_log
from yzy_agent.contracts import MessageRole, SessionEntryType, SideEffect, TraceStatus
from yzy_agent.infra.storage import FileSessionStore
from yzy_agent.modules.checkpoint import Checkpoint, TailKind, classify_tail
from yzy_agent.modules.checkpoint import apply as apply_recovery
from yzy_agent.modules.session import Session

SESSION_ID = "sess_ab12"

# 一个只读工具与一个不可逆的写工具，用来验证配平走的确实是声明里那条重试规则。
TOOLS = FakeToolRegistry(
    [
        make_tool_declaration("read", side_effect=SideEffect.NONE),
        make_tool_declaration("write", side_effect=SideEffect.NON_IDEMPOTENT),
    ]
)


async def _restart(scratch_dir: Path, entries) -> tuple[FileSessionStore, Session]:
    """把一份日志落盘再装回来 —— 这就是"重启之后看到的会话"。

    举例：store, session = await _restart(scratch_dir, broken_log())
    """
    writer = FileSessionStore(scratch_dir / "sessions")
    await writer.save_meta(make_session_meta(SESSION_ID))
    await writer.append(SESSION_ID, entries)
    await writer.flush(SESSION_ID, entries[-1].seq)

    store = FileSessionStore(scratch_dir / "sessions")
    meta, loaded = await store.load(SESSION_ID, USER)  # type: ignore[misc]
    return store, Session(meta, loaded, ids=SequentialIds())


def _kinds(session: Session) -> list[SessionEntryType]:
    """日志里各条条目的类型，按顺序。

    举例：_kinds(session) -> [TRACE_START, MESSAGE, ...]
    """
    return [entry.type for entry in session.entries()]


async def test_a_clean_log_is_not_touched(scratch_dir: Path) -> None:
    """末尾正常收尾 —— 七步一条都不做。"""
    store, session = await _restart(scratch_dir, broken_log(end=TraceStatus.COMPLETED))
    before = session.entries()

    repaired = await apply_recovery(
        Checkpoint(store), session, classify_tail(before), now=START, tools=TOOLS
    )

    assert repaired == 0
    assert session.entries() == before


async def test_a_dangling_call_gets_a_result_and_a_trace_end(scratch_dir: Path) -> None:
    """③④：欠的 tool 结果 + 一条 trace_end(aborted)。"""
    store, session = await _restart(scratch_dir, broken_log())

    repaired = await apply_recovery(
        Checkpoint(store), session, classify_tail(session.entries()), now=START, tools=TOOLS
    )

    # call_1 与 call_2 都没结果 -> 两条 tool 消息，加一条 trace_end。
    assert repaired == 3
    assert _kinds(session)[-3:] == [
        SessionEntryType.MESSAGE,
        SessionEntryType.MESSAGE,
        SessionEntryType.TRACE_END,
    ]


async def test_the_aborted_tail_gets_results_but_no_second_trace_end(scratch_dir: Path) -> None:
    """位点 10 已经走过了，再补一条收尾就是多余的。"""
    store, session = await _restart(scratch_dir, broken_log(end=TraceStatus.ABORTED))
    before = len(session.entries())

    repaired = await apply_recovery(
        Checkpoint(store), session, classify_tail(session.entries()), now=START, tools=TOOLS
    )

    assert repaired == 2  # 两条 tool 消息，没有第二条 trace_end
    assert _kinds(session)[-2:] == [SessionEntryType.MESSAGE, SessionEntryType.MESSAGE]
    assert len(session.entries()) == before + 2


async def test_the_repair_lands_on_disk_immediately(scratch_dir: Path) -> None:
    """⑤：补完就在盘上，不用等下一次屏障。"""
    store, session = await _restart(scratch_dir, broken_log())

    await apply_recovery(
        Checkpoint(store), session, classify_tail(session.entries()), now=START, tools=TOOLS
    )

    loaded = await store.load(SESSION_ID, USER)
    assert len(loaded[1]) == len(session.entries())  # type: ignore[index]


async def test_the_repaired_entries_belong_to_the_broken_trace(scratch_dir: Path) -> None:
    """补出来的条目算在出事的那次 trace 名下，不是新开的那次。"""
    store, session = await _restart(scratch_dir, broken_log())

    await apply_recovery(
        Checkpoint(store), session, classify_tail(session.entries()), now=START, tools=TOOLS
    )

    assert {entry.trace_id for entry in session.entries()} == {TRACE_ID}


async def test_the_projection_shows_the_repaired_result(scratch_dir: Path) -> None:
    """⑥：补出来的结果消息要出现在上下文里，否则下一轮请求仍然缺配对。"""
    store, session = await _restart(scratch_dir, broken_log(did_answer=("call_1",)))

    await apply_recovery(
        Checkpoint(store), session, classify_tail(session.entries()), now=START, tools=TOOLS
    )

    tool_messages = [m for m in session.messages() if m.role is MessageRole.TOOL]
    assert [m.metadata.get("tool_call_id") for m in tool_messages] == ["call_1", "call_2"]


async def test_a_second_restart_sees_an_aborted_tail(scratch_dir: Path) -> None:
    """恢复是幂等的方向：补完之后再重启一次，尾部变成 aborted，不会再被当成 dangling。"""
    store, session = await _restart(scratch_dir, broken_log())
    await apply_recovery(
        Checkpoint(store), session, classify_tail(session.entries()), now=START, tools=TOOLS
    )

    again = FileSessionStore(scratch_dir / "sessions")
    meta, entries = await again.load(SESSION_ID, USER)  # type: ignore[misc]
    second = Session(meta, entries, ids=SequentialIds())

    assert classify_tail(second.entries()).kind is TailKind.ABORTED
    duplicate = await apply_recovery(
        Checkpoint(again), second, classify_tail(second.entries()), now=START, tools=TOOLS
    )
    # 悬空调用已经补过，这一次只剩"再补一条 trace_end"的可能 —— 而 aborted 尾部不补。
    assert duplicate == 0

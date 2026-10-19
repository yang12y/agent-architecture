"""会话统计的口径。

**全部从日志数**，所以每一条用例都是在拼一段日志，看数出来是什么。
"""

from __future__ import annotations

from agent_architecture.api.stats import session_stats
from agent_architecture.contracts import (
    BudgetSnapshot,
    CompactionSnapshot,
    Permission,
    TraceEndEntryPayload,
    TraceStartEntryPayload,
    TraceStatus,
    TraceTrigger,
)
from fakes.session import make_entry


def _started() -> TraceStartEntryPayload:
    """一次 trace 的开始条目。

    举例：_started() -> TraceStartEntryPayload(trigger=TraceTrigger.USER, ...)
    """
    return TraceStartEntryPayload(
        trigger=TraceTrigger.USER,
        parent_trace_id=None,
        model="fake-model",
        permission=Permission.EDITABLE,
        budget=BudgetSnapshot(max_turns=50, max_tokens=1000),
        compaction=CompactionSnapshot(
            compact_tokens=800,
            keep_recent=2,
            long_tool_result_max_tokens=100,
            long_tool_result_keep_recent=1,
        ),
    )


def _ended(turns: int, context_tokens: int) -> TraceEndEntryPayload:
    """一次 trace 的收尾条目。

    举例：_ended(turns=2, context_tokens=300)
    """
    return TraceEndEntryPayload(
        status=TraceStatus.COMPLETED,
        reason=None,
        turns=turns,
        context_tokens=context_tokens,
    )


def test_an_empty_log_counts_nothing() -> None:
    """新会话三个数都是 0，上限透传（圈要画得出来）。"""
    stats = session_stats([], max_tokens=1000)

    assert (stats.traces, stats.turns, stats.context_tokens, stats.max_tokens) == (0, 0, 0, 1000)


def test_traces_and_turns_accumulate() -> None:
    """跑过两次收尾的 trace：trace 数 2，轮数相加，上下文取最后一次。"""
    entries = [
        make_entry(1, _started()),
        make_entry(2, _ended(turns=2, context_tokens=300)),
        make_entry(3, _started()),
        make_entry(4, _ended(turns=3, context_tokens=700)),
    ]

    stats = session_stats(entries, max_tokens=1000)

    assert stats.traces == 2
    assert stats.turns == 5
    assert stats.context_tokens == 700


def test_a_trace_without_an_end_does_not_count_its_turns() -> None:
    """还没收尾的那次：trace 数算它，轮数不算 —— 轮次要等收尾才进日志。"""
    entries = [make_entry(1, _started())]

    stats = session_stats(entries, max_tokens=1000)

    assert stats.traces == 1
    assert stats.turns == 0


def test_a_synthesised_end_does_not_wipe_the_context() -> None:
    """崩溃之后补出来的那条 trace_end 没有记录（0），不能把上一次真实的占用盖掉。"""
    entries = [
        make_entry(1, _started()),
        make_entry(2, _ended(turns=1, context_tokens=640)),
        make_entry(3, _started()),
        make_entry(4, TraceEndEntryPayload(status=TraceStatus.ABORTED, reason="进程中断")),
    ]

    stats = session_stats(entries, max_tokens=1000)

    assert stats.turns == 1
    assert stats.context_tokens == 640

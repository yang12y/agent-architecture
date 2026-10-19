"""尾部判定的测试。"""

from __future__ import annotations

from test_checkpoint.traces import TRACE_ID, broken_log, trace_end, trace_start
from yzy_agent.contracts import TraceStatus
from yzy_agent.modules.checkpoint import TailKind, classify_tail, open_trace_id


def test_an_empty_log_is_closed() -> None:
    """空日志没什么可恢复的。"""
    tail = classify_tail([])

    assert tail.kind is TailKind.CLOSED
    assert tail.trace_id is None
    assert tail.needs_repair is False


def test_an_open_trace_is_dangling() -> None:
    """trace_start 之后没有 trace_end —— 位点 10 没走到。"""
    tail = classify_tail(broken_log())

    assert tail.kind is TailKind.DANGLING
    assert tail.trace_id == TRACE_ID
    assert tail.needs_repair is True


def test_an_aborted_tail_needs_repair_but_is_not_dangling() -> None:
    """位点 10 正常走了，只是按 aborted 收尾 —— 活还留着。"""
    tail = classify_tail(broken_log(end=TraceStatus.ABORTED))

    assert tail.kind is TailKind.ABORTED
    assert tail.trace_id == TRACE_ID
    assert tail.needs_repair is True


def test_a_completed_tail_is_closed() -> None:
    """正常收尾的 trace 什么都不用补。"""
    assert classify_tail(broken_log(end=TraceStatus.COMPLETED)).kind is TailKind.CLOSED


def test_failed_and_budget_exceeded_count_as_closed() -> None:
    """只看 aborted —— failed 与 budget_exceeded 都是位点 10 的正常收尾，留不下悬空调用。"""
    for status in (TraceStatus.FAILED, TraceStatus.BUDGET_EXCEEDED):
        assert classify_tail(broken_log(end=status)).kind is TailKind.CLOSED


def test_a_trace_started_after_an_aborted_one_is_dangling() -> None:
    """上次按 aborted 收尾之后又开了一次、又崩了 —— 判据看的是最后那次。"""
    entries = [*broken_log(end=TraceStatus.ABORTED), trace_start(9, "trace_b")]

    tail = classify_tail(entries)

    assert tail.kind is TailKind.DANGLING
    assert tail.trace_id == "trace_b"


def test_open_trace_id_ignores_an_already_closed_trace() -> None:
    """收过尾的 trace 不算开着的。"""
    assert open_trace_id(broken_log(end=TraceStatus.COMPLETED)) is None
    assert open_trace_id(broken_log()) == TRACE_ID


def test_a_log_with_only_a_trace_end_is_closed() -> None:
    """没有 trace_start 的裸条目不算"开着" —— 没有可配平的归属。"""
    assert classify_tail([trace_end(1, TraceStatus.COMPLETED, trace_id="trace_old")]).kind is (
        TailKind.CLOSED
    )

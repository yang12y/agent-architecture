"""本轮工具成功/失败聚合的测试。"""

from __future__ import annotations

from fakes import make_runtime_state
from yzy_agent.contracts import ToolStat
from yzy_agent.modules.runtime_state import record_failure, record_success


def test_first_record_creates_the_entry() -> None:
    """某个工具本轮第一次出现时先建零计数再累加，不会 KeyError。"""
    state = make_runtime_state()

    record_success(state, "grep")

    assert state.turn_tool_stats["grep"] == ToolStat(name="grep", succeeded=1, failed=0)


def test_counts_accumulate_per_tool() -> None:
    """按工具名分别累计，互不干扰 —— 统计是用来发现"哪个工具在反复失败"的。"""
    state = make_runtime_state()

    record_success(state, "grep")
    record_success(state, "grep")
    record_failure(state, "grep")
    record_failure(state, "read")

    assert state.turn_tool_stats["grep"] == ToolStat(name="grep", succeeded=2, failed=1)
    assert state.turn_tool_stats["read"] == ToolStat(name="read", succeeded=0, failed=1)


def test_stats_start_empty_and_are_not_shared_between_states() -> None:
    """两个执行态各有一份统计，不会互相串。"""
    first = make_runtime_state()
    second = make_runtime_state()

    record_success(first, "grep")

    assert second.turn_tool_stats == {}

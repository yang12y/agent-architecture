"""位点 2（turn:before）的操作测试。"""

from __future__ import annotations

from agent_architecture.contracts import Site, ToolStat, TurnBeforePayload
from agent_architecture.modules.runtime_state import s02_turn_before
from fakes import make_runtime_state, make_site_context, make_trace_context


def test_attempt_counter_is_reset() -> None:
    """新的一轮，模型重新从第一次尝试开始。"""
    ctx = make_site_context(
        Site.TURN_BEFORE,
        TurnBeforePayload(turn_index=2),
        trace=make_trace_context(state=make_runtime_state(llm_attempt_number=2)),
    )

    s02_turn_before.apply(ctx)

    assert ctx.state.llm_attempt_number == 0


def test_previous_turn_stats_are_cleared() -> None:
    """统计是按"本轮"聚合的，跨轮会失真。"""
    state = make_runtime_state(turn_tool_stats={"grep": ToolStat("grep", 3, 1)})
    ctx = make_site_context(
        Site.TURN_BEFORE, TurnBeforePayload(1), trace=make_trace_context(state=state)
    )

    s02_turn_before.apply(ctx)

    assert ctx.state.turn_tool_stats == {}


def test_leftover_pending_calls_do_not_leak_into_the_new_turn() -> None:
    """兜底：正常路径下不执行的调用在位点 6 就摘掉了，异常路径的残留不能拖到新一轮。"""
    state = make_runtime_state()
    state.pending_tool_calls.append(None)  # type: ignore[arg-type]
    ctx = make_site_context(
        Site.TURN_BEFORE, TurnBeforePayload(1), trace=make_trace_context(state=state)
    )

    s02_turn_before.apply(ctx)

    assert ctx.state.pending_tool_calls == []


def test_turn_index_is_left_to_the_kernel() -> None:
    """turn_index 的推进是内核固有职责（不变量 I9），这里一点都不该碰。"""
    ctx = make_site_context(
        Site.TURN_BEFORE,
        TurnBeforePayload(turn_index=4),
        trace=make_trace_context(state=make_runtime_state(turn_index=4)),
    )

    s02_turn_before.apply(ctx)

    assert ctx.state.turn_index == 4

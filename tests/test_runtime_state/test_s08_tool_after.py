"""位点 8（tool:after）的操作测试。"""

from __future__ import annotations

from fakes import make_runtime_state, make_site_context, make_trace_context
from yzy_agent.contracts import (
    Message,
    MessageRole,
    Site,
    TextPart,
    ToolAfterPayload,
    ToolCall,
    ToolCallStatus,
    ToolStat,
)
from yzy_agent.modules.runtime_state import s08_tool_after


def _state_with_pending(*call_ids: str):
    """造一个待执行队列里有若干条调用的执行态。

    举例：state = _state_with_pending("call_1", "call_2")
    """
    state = make_runtime_state()
    for call_id in call_ids:
        state.pending_tool_calls.append(
            ToolCall(id=call_id, name="read", arguments={}, status=ToolCallStatus.RUNNING)
        )
    return state


def _ctx(payload: ToolAfterPayload, state=None):
    """把位点 8 的 payload 挂到给定执行态上。

    举例：ctx = _ctx(ToolAfterPayload(call=call))
    """
    trace = make_trace_context(state=state) if state is not None else None
    return make_site_context(Site.TOOL_AFTER, payload, trace=trace)


def test_success_is_counted() -> None:
    """没有 error 就是成功。"""
    call = ToolCall("call_1", "grep", {}, ToolCallStatus.RUNNING)
    ctx = _ctx(ToolAfterPayload(call=call))

    s08_tool_after.apply(ctx)

    assert ctx.state.turn_tool_stats["grep"] == ToolStat("grep", 1, 0)


def test_failure_is_counted() -> None:
    """有 error 就是失败。"""
    call = ToolCall("call_1", "grep", {}, ToolCallStatus.RUNNING)
    ctx = _ctx(ToolAfterPayload(call=call, error=RuntimeError("炸了")))  # type: ignore[arg-type]

    s08_tool_after.apply(ctx)

    assert ctx.state.turn_tool_stats["grep"] == ToolStat("grep", 0, 1)


def test_rejected_call_is_not_counted() -> None:
    """被拒绝的调用根本没执行，既不算成功也不算失败。

    当前内核不会让被拒的调用走到位点 8，所以这条是守着统计口径的守卫用例。
    """
    call = ToolCall("call_1", "rm", {}, ToolCallStatus.REJECTED)
    ctx = _ctx(ToolAfterPayload(call=call))

    s08_tool_after.apply(ctx)

    assert ctx.state.turn_tool_stats == {}


def test_the_call_is_removed_from_the_pending_queue() -> None:
    """摘掉的是这一条，别的还留着。"""
    state = _state_with_pending("call_1", "call_2")
    done = state.pending_tool_calls[0]

    s08_tool_after.apply(_ctx(ToolAfterPayload(call=done), state))

    assert [call.id for call in state.pending_tool_calls] == ["call_2"]


def test_removing_an_unknown_call_is_harmless() -> None:
    """幂等：同一条被处理两遍也不会把别人误删。"""
    state = _state_with_pending("call_1")
    stranger = ToolCall("call_999", "read", {}, ToolCallStatus.RUNNING)

    s08_tool_after.apply(_ctx(ToolAfterPayload(call=stranger), state))

    assert [call.id for call in state.pending_tool_calls] == ["call_1"]


def test_pending_messages_are_moved_into_the_payload() -> None:
    """工具结果回来时才是注入中途消息的时机 —— 不打断正在跑的工具。"""
    state = _state_with_pending("call_1")
    state.pending_queue.append(Message(role=MessageRole.USER, content=[TextPart(text="插一句")]))
    payload = ToolAfterPayload(call=state.pending_tool_calls[0])

    s08_tool_after.apply(_ctx(payload, state))

    assert len(payload.pending_messages) == 1
    assert list(state.pending_queue) == []


def test_missing_call_still_drains_the_queue() -> None:
    """payload.call 可空，但队列照样要出队，不能把用户的话吞了。"""
    state = make_runtime_state()
    state.pending_queue.append(Message(role=MessageRole.USER, content=[TextPart(text="插一句")]))
    payload = ToolAfterPayload()

    s08_tool_after.apply(_ctx(payload, state))

    assert len(payload.pending_messages) == 1

"""位点 10（trace:end）的操作测试。"""

from __future__ import annotations

from agent_architecture.contracts import (
    Message,
    MessageRole,
    Site,
    TextPart,
    ToolCall,
    ToolCallStatus,
    ToolStat,
    TraceEndPayload,
)
from agent_architecture.modules.runtime_state import s10_trace_end
from fakes import make_runtime_state, make_site_context, make_trace_context


def _dirty_state():
    """造一个"刚跑完一串东西"的执行态：各处都留着内容。

    举例：state = _dirty_state(); state.turn_tool_stats -> {"grep": ToolStat(...)}
    """
    state = make_runtime_state(
        user_message=Message(role=MessageRole.USER, content=[TextPart(text="帮我读 a.py")]),
        turn_tool_stats={"grep": ToolStat("grep", 1, 0)},
    )
    state.pending_tool_calls.append(ToolCall("call_1", "read", {}, ToolCallStatus.RUNNING))
    state.pending_queue.append(Message(role=MessageRole.USER, content=[]))
    return state


def test_containers_and_message_reference_are_released() -> None:
    """清掉容器与消息引用，别让一次 trace 的内容跟着长命对象一起留着。"""
    state = _dirty_state()
    ctx = make_site_context(
        Site.TRACE_END, TraceEndPayload(), trace=make_trace_context(state=state)
    )

    s10_trace_end.apply(ctx)

    assert state.user_message is None
    assert state.turn_tool_stats == {}
    assert state.pending_tool_calls == []
    assert list(state.pending_queue) == []


def test_is_running_is_left_to_the_kernel() -> None:
    """is_running 的置位是内核固有职责（不变量 I9），而且内核在这一位点之前就置好了。"""
    state = make_runtime_state(is_running=False)
    ctx = make_site_context(
        Site.TRACE_END, TraceEndPayload(), trace=make_trace_context(state=state)
    )

    s10_trace_end.apply(ctx)

    assert state.is_running is False


def test_counters_are_kept_for_the_trace_summary() -> None:
    """turn_index 与 context_tokens 是这次 trace 的结论，收尾时还要给 trace_end 用，不能清。"""
    state = make_runtime_state(turn_index=3, context_tokens=1234)
    ctx = make_site_context(
        Site.TRACE_END, TraceEndPayload(), trace=make_trace_context(state=state)
    )

    s10_trace_end.apply(ctx)

    assert state.turn_index == 3
    assert state.context_tokens == 1234

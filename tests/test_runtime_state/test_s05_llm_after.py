"""位点 5（llm:after）的操作测试。"""

from __future__ import annotations

from agent_architecture.contracts import (
    LlmAfterPayload,
    Message,
    MessageRole,
    Site,
    TextPart,
    ToolCall,
    ToolCallPart,
    ToolCallStatus,
)
from agent_architecture.modules.runtime_state import s05_llm_after
from fakes import make_runtime_state, make_site_context, make_trace_context


def _part(call_id: str, name: str, **arguments: object) -> ToolCallPart:
    """造一条模型请求的工具调用。

    举例：_part("call_1", "read", path="a.py")
    """
    return ToolCallPart(tool_call_id=call_id, name=name, arguments=dict(arguments))


def _payload(parts: list[ToolCallPart] | None = None) -> LlmAfterPayload:
    """造一份位点 5 的 payload。

    举例：_payload([_part("call_1", "read", path="a.py")])
    """
    return LlmAfterPayload(
        assistant_message=Message(role=MessageRole.ASSISTANT, content=[]),
        tool_calls=parts or [],
    )


def test_tool_calls_become_pending_calls() -> None:
    """模型请求的工具调用被解析成待执行队列，状态是 PENDING。"""
    ctx = make_site_context(Site.LLM_AFTER, _payload([_part("call_1", "read", path="a.py")]))

    s05_llm_after.apply(ctx)

    assert ctx.state.pending_tool_calls == [
        ToolCall(
            id="call_1",
            name="read",
            arguments={"path": "a.py"},
            status=ToolCallStatus.PENDING,
        )
    ]


def test_pending_calls_are_replaced_not_appended() -> None:
    """整体替换：上一轮的批次不该混进这一轮。"""
    state = make_runtime_state()
    state.pending_tool_calls.append(ToolCall("call_old", "grep", {}, ToolCallStatus.PENDING))
    ctx = make_site_context(
        Site.LLM_AFTER,
        _payload([_part("call_new", "read", path="b.py")]),
        trace=make_trace_context(state=state),
    )

    s05_llm_after.apply(ctx)

    assert [call.id for call in ctx.state.pending_tool_calls] == ["call_new"]


def test_model_without_tool_calls_empties_the_queue() -> None:
    """这一轮没请求工具时，待执行队列必须是空的（终止判定也依赖这一点）。"""
    state = make_runtime_state()
    state.pending_tool_calls.append(ToolCall("call_old", "grep", {}, ToolCallStatus.PENDING))
    ctx = make_site_context(Site.LLM_AFTER, _payload(), trace=make_trace_context(state=state))

    s05_llm_after.apply(ctx)

    assert ctx.state.pending_tool_calls == []


def test_pending_messages_are_moved_into_the_payload() -> None:
    """中途消息从队列搬进 payload，由 session 随后落成 user 消息。"""
    state = make_runtime_state()
    state.pending_queue.append(Message(role=MessageRole.USER, content=[TextPart(text="顺便看 b")]))
    ctx = make_site_context(Site.LLM_AFTER, _payload(), trace=make_trace_context(state=state))

    s05_llm_after.apply(ctx)

    payload: LlmAfterPayload = ctx.payload  # type: ignore[assignment]
    assert len(payload.pending_messages) == 1
    assert list(state.pending_queue) == []


def test_payload_starts_with_no_pending_messages() -> None:
    """没人入队时是空列表，session 拿到的就是"没有中途消息"。"""
    ctx = make_site_context(Site.LLM_AFTER, _payload())

    s05_llm_after.apply(ctx)

    assert ctx.payload.pending_messages == []  # type: ignore[attr-defined]

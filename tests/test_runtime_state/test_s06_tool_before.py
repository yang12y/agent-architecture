"""位点 6（tool:before）的操作测试。"""

from __future__ import annotations

from fakes import make_runtime_state, make_site_context, make_trace_context
from yzy_agent.contracts import (
    Site,
    ToolBeforePayload,
    ToolCall,
    ToolCallStatus,
    ToolDecision,
)
from yzy_agent.modules.runtime_state import s06_tool_before


def _call(call_id: str) -> ToolCall:
    """造一条待执行调用。

    举例：_call("call_1")
    """
    return ToolCall(id=call_id, name="read", arguments={}, status=ToolCallStatus.PENDING)


def _setup(decisions: dict[str, ToolDecision], *call_ids: str):
    """造一个"队列里有这几条 + 判定结果如下"的执行现场。

    队列与 payload.calls 是两份不同的列表，装着同一批对象 —— 和内核的构造方式一致。

    举例：ctx = _setup({"call_1": ToolDecision.REJECT}, "call_1", "call_2")
    """
    state = make_runtime_state()
    calls = [_call(call_id) for call_id in call_ids]
    state.pending_tool_calls.extend(calls)
    payload = ToolBeforePayload(calls=calls, decisions=decisions)
    return make_site_context(Site.TOOL_BEFORE, payload, trace=make_trace_context(state=state))


def test_rejected_calls_leave_the_queue() -> None:
    """被拒的调用内核不会让它走位点 8，所以必须在这里摘掉，否则它永远挂着。"""
    ctx = _setup(
        {"call_1": ToolDecision.REJECT, "call_2": ToolDecision.EXECUTE}, "call_1", "call_2"
    )

    s06_tool_before.apply(ctx)

    assert [call.id for call in ctx.state.pending_tool_calls] == ["call_2"]


def test_calls_without_a_decision_are_treated_as_rejected() -> None:
    """缺键按拒绝处理 —— 和内核同一条规则，两边判据不能各判各的。"""
    ctx = _setup({"call_2": ToolDecision.EXECUTE}, "call_1", "call_2")

    s06_tool_before.apply(ctx)

    assert [call.id for call in ctx.state.pending_tool_calls] == ["call_2"]


def test_awaiting_approval_also_leaves_the_queue() -> None:
    """内核的判据是"不是 execute 就不往下跑"，await_approval 同样不执行。"""
    ctx = _setup(
        {"call_1": ToolDecision.AWAIT_APPROVAL, "call_2": ToolDecision.EXECUTE}, "call_1", "call_2"
    )

    s06_tool_before.apply(ctx)

    assert [call.id for call in ctx.state.pending_tool_calls] == ["call_2"]


def test_an_all_execute_batch_keeps_the_queue_intact() -> None:
    """全批放行时队列一条都不动。"""
    ctx = _setup(
        {"call_1": ToolDecision.EXECUTE, "call_2": ToolDecision.EXECUTE}, "call_1", "call_2"
    )

    s06_tool_before.apply(ctx)

    assert [call.id for call in ctx.state.pending_tool_calls] == ["call_1", "call_2"]


def test_calls_outside_the_batch_are_untouched() -> None:
    """只删本批里被判定的那些 —— 队列里别的条目不是这里该管的。"""
    ctx = _setup({"call_1": ToolDecision.REJECT}, "call_1")
    ctx.state.pending_tool_calls.append(_call("call_stranger"))

    s06_tool_before.apply(ctx)

    assert [call.id for call in ctx.state.pending_tool_calls] == ["call_stranger"]


def test_payload_batch_is_left_alone() -> None:
    """改的是 state，payload.calls 原样保留 —— 内核后面遍历的是它自己那份。"""
    ctx = _setup({"call_1": ToolDecision.REJECT}, "call_1")

    s06_tool_before.apply(ctx)

    assert [call.id for call in ctx.payload.calls] == ["call_1"]  # type: ignore[attr-defined]
    assert ctx.state.pending_tool_calls == []


def test_decisions_are_not_modified() -> None:
    """这里只读判定、不写判定 —— 判定是 tool_calling 与 sandbox 的产物。"""
    decisions = {"call_1": ToolDecision.REJECT, "call_2": ToolDecision.EXECUTE}
    ctx = _setup(decisions, "call_1", "call_2")

    s06_tool_before.apply(ctx)

    assert decisions == {"call_1": ToolDecision.REJECT, "call_2": ToolDecision.EXECUTE}

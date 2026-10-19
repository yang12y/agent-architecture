"""位点 7（tool:during）的操作测试。"""

from __future__ import annotations

from fakes import make_site_context
from yzy_agent.contracts import Site, ToolCall, ToolCallStatus, ToolDuringPayload
from yzy_agent.modules.runtime_state import s07_tool_during


def _call(status: ToolCallStatus = ToolCallStatus.PENDING) -> ToolCall:
    """造一条工具调用。

    举例：_call() -> ToolCall(id="call_1", name="read", ..., status=PENDING)
    """
    return ToolCall(id="call_1", name="read", arguments={"path": "a.py"}, status=status)


def test_call_is_marked_running_before_execution() -> None:
    """RUNNING 的含义是"已派发、副作用可能已经发生"，所以必须在执行前置位。"""
    call = _call()
    ctx = make_site_context(Site.TOOL_DURING, ToolDuringPayload(call=call))

    s07_tool_during.apply(ctx)

    assert call.status is ToolCallStatus.RUNNING


def test_missing_call_is_tolerated() -> None:
    """payload.call 可空（默认就是 None），真跑起来时内核一定会填，但不能因此崩。"""
    ctx = make_site_context(Site.TOOL_DURING, ToolDuringPayload())

    s07_tool_during.apply(ctx)

    assert ctx.payload.call is None  # type: ignore[attr-defined]

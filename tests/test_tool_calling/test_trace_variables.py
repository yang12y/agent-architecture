"""trace 变量从执行现场一路进到工具实现的测试。"""

from __future__ import annotations

from agent_frame.contracts import (
    JsonValue,
    Permission,
    SideEffect,
    Site,
    ToolCall,
    ToolCallStatus,
    ToolContext,
    ToolDeclaration,
    ToolDuringPayload,
)
from agent_frame.modules.tool_calling import build_handlers
from agent_frame.modules.tool_calling.registry import ToolCatalog
from fakes.context import make_site_context, make_trace_context

ECHO = ToolDeclaration(
    name="echo_variables",
    description="把执行现场里看到的变量原样回给测试。",
    parameters_schema={"type": "object", "properties": {}},
    output_contract="一句话。",
    side_effect=SideEffect.NONE,
    accepts_idempotency_key=False,
    required_permission=Permission.READ_ONLY,
    scope=None,
)


async def test_tool_context_carries_trace_variables() -> None:
    """前端请求头带进来的变量，工具实现能原样读到 —— 中间不做过滤也不改写。"""
    seen: dict[str, str] = {}

    async def echo(ctx: ToolContext, arguments: JsonValue) -> JsonValue:
        seen.update(ctx.variables)
        return "ok"

    catalog = ToolCatalog()
    catalog.register(ECHO, echo)
    handler = next(h for h in build_handlers(catalog) if h.id == "tool_calling.tool_during")
    trace = make_trace_context(variables={"tenant": "t1", "authorization": "Bearer x"})
    payload = ToolDuringPayload(
        call=ToolCall(id="call_1", name=ECHO.name, arguments={}, status=ToolCallStatus.PENDING)
    )
    ctx = make_site_context(Site.TOOL_DURING, payload, trace=trace)

    await handler.run(ctx)

    assert seen == {"tenant": "t1", "authorization": "Bearer x"}
    assert payload.raw_result == "ok"

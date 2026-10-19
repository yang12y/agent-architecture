"""位点 6 的两段：检查链与收尾。

一个模块在这个位点上挂两个 handler，因为**中间隔着 sandbox**：它会把 `EXECUTE` 改成更严的
结论，而"给不执行的调用写结果消息"必须等所有判定都落定才能做。

    100  检查链：存在性 → 补默认 → 校验 → 写 EXECUTE + 编排
    435  sandbox 判（别的模块）
    445  收尾：给每条不是 EXECUTE 的调用写一条配对的结果消息

**收尾那一段是不变量 I10 的落地处**：模型发出的每一条 tool_call 都必须收到一条 tool 消息，
要么是结果，要么是原因。少了任何一条，下一轮请求就不合法。
"""

from __future__ import annotations

from typing import cast

from yzy_agent.contracts import (
    Message,
    MessageRole,
    SessionEntryType,
    SiteContext,
    TextPart,
    ToolBeforePayload,
    ToolCallStatus,
    ToolDecision,
)

from .checks import validate, with_defaults
from .registry import ToolCatalog
from .scheduling import plan_for

# 判定缺失时给模型看的那句话。走到这里说明没人认领这条调用 —— 宁可说一句含糊的，
# 也不能让日志里缺一条配对结果。
#
# 举例："这条调用没有通过检查。"
MISSING_REASON = "这条调用没有通过检查。"


def screen(ctx: SiteContext, catalog: ToolCatalog) -> None:
    """检查链：存在性 → 补默认 → 校验 → 写 EXECUTE，最后算出编排。

    判不过的不在这里收尾 —— 后面还有 sandbox 要判，收尾统一放在 finalize。
    判不过的也没资格执行，所以不写 EXECUTE（缺键就是拒绝）。

    举例：screen(ctx, catalog)
    """
    payload = cast(ToolBeforePayload, ctx.payload)

    for call in payload.calls:
        tool = catalog.tool(call.name)
        if tool is None:
            payload.reasons[call.id] = f"没有叫 {call.name} 的工具。"
            continue

        arguments = with_defaults(tool.declaration, call.arguments)
        problem = validate(tool.declaration, arguments)
        if problem is not None:
            payload.reasons[call.id] = f"{call.name} 的参数不合法：{problem}"
            continue

        # 最终参数写回调用本身：审批与执行都用补齐校验过的这一份，不是模型的原始输入。
        call.arguments = arguments
        payload.decisions[call.id] = ToolDecision.EXECUTE

    payload.plan = plan_for(payload.calls, catalog.get)


def finalize(ctx: SiteContext) -> None:
    """给每条不是 EXECUTE 的调用写一条配对的结果消息。**所有判定都落定之后才跑。**

    写的是**具体原因**（"read 的参数不合法：缺少必填参数：path"），不是一句"被拒绝" ——
    笼统的说明只会让模型重复同一个错误。

    举例：finalize(ctx)
    """
    payload = cast(ToolBeforePayload, ctx.payload)
    now = ctx.ports.clock.now()
    trace_id = ctx.state.trace_id

    for call in payload.calls:
        if payload.decisions.get(call.id) is ToolDecision.EXECUTE:
            continue
        reason = payload.reasons.get(call.id) or MISSING_REASON
        call.status = ToolCallStatus.REJECTED
        ctx.session.append_entry(
            trace_id=trace_id,
            type=SessionEntryType.MESSAGE,
            payload=Message(
                role=MessageRole.TOOL,
                content=[TextPart(text=reason)],
                metadata={"tool_call_id": call.id},
            ),
            now=now,
        )

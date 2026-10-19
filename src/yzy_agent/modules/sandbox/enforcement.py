"""把判定结果落到 decisions 上，给payload.decision赋值 —— 这就是"执行前强制"。

强制执行不是事后审计：内核在位点 6 之后只对 `decisions[id] is EXECUTE` 的调用往下跑
位点 7/8。所以**判定写进 decisions 的那一刻，边界就已经生效了**。

**不写 = 拒绝**：缺键在位点 6 按 REJECT 处理（内核与 runtime_state 同一条规则）。
所以这里只对**判得了的**调用落笔 —— 查不到声明的（工具没注册）留给 tool_calling 的
存在性检查，本模块不替它发言，也不因为判不了就把整批拒掉。

**要人审批的调用在这里等**：审批端口没接时 fail closed，落成 REJECT ——
没有应答方时静默放行是最坏的收场。
"""

from __future__ import annotations

from typing import cast

from yzy_agent.contracts import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalRequired,
    SiteContext,
    ToolBeforePayload,
    ToolCall,
    ToolDecision,
)

from .approval import Verdict, decide


async def enforce(ctx: SiteContext) -> None:
    """逐条判，把结论写进 payload.decisions。

    两个端口都可能没接（现在就没接：工具注册表归 S7 的 tool_calling，审批要 API 端点）。
    没接时的行为是**不判**，不是"全拒" —— 缺键本来就按拒绝处理，这里不需要再踩一脚。

    举例：await enforce(ctx)
    """
    tools = ctx.ports.tools
    if tools is None:
        return

    payload = cast(ToolBeforePayload, ctx.payload)
    workspace = ctx.session.meta.workspace
    for call in payload.calls:
        if payload.decisions.get(call.id) is not ToolDecision.EXECUTE:
            # 检查链没放行（显式拒绝，或者压根没给判定 —— 缺键就是拒绝）。
            # 沙箱只负责**把放行的改小**，不替检查链做它已经做过的事，
            # 更不能把它的理由覆盖掉。
            continue
        # 获取工具的声明
        declaration = tools.get(call.name)
        if declaration is None:
            continue
        # 对工具判决
        verdict = decide(
            permission=ctx.config.permission,
            declaration=declaration,
            arguments=call.arguments,
            workspace=workspace,
        )
        decision, reason = await _settle(ctx, call, verdict)
        payload.decisions[call.id] = decision
        # 理由留给 tool_calling 的收尾去拼结果消息 —— 被拒的调用也得让模型知道为什么。
        payload.reasons[call.id] = reason


async def _settle(ctx: SiteContext, call: ToolCall, verdict: Verdict) -> tuple[ToolDecision, str]:
    """把"等审批"落实成一个结论，外加**给模型看的那句话**。

    拒绝时要写清是谁拒的：人明确点了拒绝就得说出来 —— 只回一句"越界"，
    模型会把同一个调用原样再发一遍。

    举例：await _settle(ctx, call, Verdict(ToolDecision.AWAIT_APPROVAL, "..."))
    """
    if verdict.decision is not ToolDecision.AWAIT_APPROVAL:
        return verdict.decision, verdict.reason

    approvals = ctx.ports.approvals
    if approvals is None:
        ctx.ports.logger.warning(
            "sandbox.no_approver",
            tool=call.name,
            tool_call_id=call.id,
            trace_id=ctx.state.trace_id,
            reason=verdict.reason,
        )
        return ToolDecision.REJECT, f"{verdict.reason}；没有可用的审批方，按拒绝处理"

    request = ApprovalRequest(
        request_id=ctx.ports.ids.new_id("appr"),
        session_id=ctx.session.meta.session_id,
        trace_id=ctx.state.trace_id,
        tool_call_id=call.id,
        tool_name=call.name,
        arguments=call.arguments,
        reason=verdict.reason,
    )
    ctx.ports.events.publish(
        ApprovalRequired(
            session_id=ctx.session.meta.session_id,
            request_id=request.request_id,
            tool_name=call.name,
            arguments=call.arguments,
            reason=verdict.reason,
        )
    )
    result = await approvals.request(request)
    if result.decision is ApprovalDecision.APPROVE:
        return ToolDecision.EXECUTE, verdict.reason
    comment = f"（{result.comment}）" if result.comment else ""
    return ToolDecision.REJECT, f"{verdict.reason}；用户拒绝了这次调用{comment}"

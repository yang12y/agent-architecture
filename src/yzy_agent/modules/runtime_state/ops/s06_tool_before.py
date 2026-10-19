"""位点 6（tool:before）的 runtime_state 操作：把不执行的调用从待执行队列里摘掉。

**为什么需要它**：内核只对判定为 `execute` 的调用往下跑位点 7/8，所以被拒（`reject`）与
判定缺失的调用**永远走不到**位点 8 —— 那里是执行路径的摘除点。不在这里摘掉，它们会一直挂在
`pending_tool_calls` 上，既和这个字段"尚未落地"的定义不符，也让 `TurnEndPayload.has_pending_work` 虚高。

**为什么做得到**：内核自己用的是进入位点 6 时 `list(state.pending_tool_calls)` 的**副本**
（`calls`），往下只认 `payload.plan` 与 `payload.decisions`。所以这里改 state 不影响内核
接下来遍历哪几条。

**判定缺失按 REJECT 处理**，和内核同一条规则。两边判据必须一致，否则会出现
"内核执行了、这里却把它摘了"这种错位。
"""

from __future__ import annotations

from typing import cast

from yzy_agent.contracts import RuntimeState, SiteContext, ToolBeforePayload, ToolDecision


def apply(ctx: SiteContext) -> None:
    """把本批里判定不是 `execute` 的调用从待执行队列里移除。

    举例：apply(ctx) -> 判定为 reject 的三条从队列消失，判定为 execute 的留下
    """
    payload = cast(ToolBeforePayload, ctx.payload)
    state: RuntimeState = ctx.state

    dropped = {
        call.id
        for call in payload.calls
        if payload.decisions.get(call.id) is not ToolDecision.EXECUTE
    }
    if not dropped:
        return

    # 只按 id 删，不做整体替换 —— 队列里若有不属于这一批的条目，那是别处的问题，
    # 不该被这里顺手抹掉。
    state.pending_tool_calls = [call for call in state.pending_tool_calls if call.id not in dropped]

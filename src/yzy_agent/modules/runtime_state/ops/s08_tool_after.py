"""位点 8（tool:after）的 runtime_state 操作：记一次统计、摘下这条调用、消费中途消息。

**被拒绝（rejected）的调用不计入统计**：它根本没执行，既不算成功也不算失败。
它照样要有一条结果消息，那条消息由 tool_calling 在本位点写（不变量 I10）。
"""

from __future__ import annotations

from typing import cast

from yzy_agent.contracts import (
    RuntimeState,
    SiteContext,
    ToolAfterPayload,
    ToolCallStatus,
)

from ..pending_queue import drain_messages
from ..tool_stats import record_failure, record_success


def apply(ctx: SiteContext) -> None:
    """按结果累加统计、把这条调用从待执行队列里摘掉，并消费中途消息。

    举例：apply(ctx) -> state.turn_tool_stats["grep"].succeeded == 1
    """
    payload = cast(ToolAfterPayload, ctx.payload)
    state: RuntimeState = ctx.state

    call = payload.call
    if call is not None:
        # 走到位点 8 的调用基本都执行过 —— 被拒的那种内核不让它往下走（见 docs §5 D2）。
        # 这里仍然判一次是当守卫用：万一将来内核改成"被拒的也过位点 8"，统计口径不变。
        # （ToolCallStatus 里没有"成功"这个取值，成功与否看 payload.error。）
        if call.status is not ToolCallStatus.REJECTED:
            if payload.error is None:
                record_success(state, call.name)
            else:
                record_failure(state, call.name)

        state.pending_tool_calls = [
            pending for pending in state.pending_tool_calls if pending.id != call.id
        ]

    payload.pending_messages = drain_messages(state)

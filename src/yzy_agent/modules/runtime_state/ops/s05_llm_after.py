"""位点 5（llm:after）的 runtime_state 操作：收下模型这一轮的产出，并消费中途消息。

两件事：
1. 把模型请求的工具调用解析成待执行队列；
2. 把排队的中途消息交给 payload，由 session 随后追加成 user 消息。

队列在这里**出队但不落盘**——落盘是 session 的事。走 payload 而不是让 session
直接去读 `state.pending_queue`，是因为位点内的跨模块数据一律走数据通道（§2.1）。
"""

from __future__ import annotations

from typing import cast

from yzy_agent.contracts import (
    LlmAfterPayload,
    RuntimeState,
    SiteContext,
    ToolCall,
    ToolCallStatus,
)

from ..pending_queue import drain_messages


def apply(ctx: SiteContext) -> None:
    """重建待执行队列，并把中途消息搬进 payload。

    举例：apply(ctx) -> state.pending_tool_calls 与 payload.tool_calls 一一对应
    """
    payload = cast(LlmAfterPayload, ctx.payload)
    state: RuntimeState = ctx.state

    # **整体替换**而不是追加：上一轮的批次在位点 8 已经逐条摘干净了，
    # 残留只可能来自"被拒绝却没走到位点 8"的异常情况，那些不该混进新批次。
    state.pending_tool_calls = [
        ToolCall(
            id=part.tool_call_id,
            name=part.name,
            arguments=part.arguments,
            status=ToolCallStatus.PENDING,
        )
        for part in payload.tool_calls
    ]

    # 中途消息交给 session 落成 user 消息；取完队列即为空，不会被写第二遍。
    payload.pending_messages = drain_messages(state)

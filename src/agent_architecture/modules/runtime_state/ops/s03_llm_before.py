"""位点 3（llm:before）的 runtime_state 操作：算这次请求实际占用多少上下文。

这是本架构**唯一的 token 指标**：compression 在本位点紧随其后，超阈值就压缩。
所以这个操作必须排在所有依赖它的 handler 之前。

两种口径，优先用准的那个：

1. **锚点** —— 上一次请求发出去的 messages 是这一次的前缀时，提供方报过的用量直接当基数，
   只补算后面新加的那几条。每次成功调用都重新锚定，所以误差不跨轮累积。
2. **全量估算** —— 没有锚点、或者上一次发出去的东西被改写过（压缩、中断）时退回这里。

口径都是"整份请求"：系统提示词由 prompts 填进同一个 payload（它不在 messages 里），
工具 schema 由 tool_calling 填进同一个 payload —— 两者都在这里一起算，
漏掉任何一份，阈值就形同虚设。
"""

from __future__ import annotations

import json
from typing import cast

from agent_architecture.contracts import (
    JsonValue,
    LlmBeforePayload,
    Message,
    MessageRole,
    SiteContext,
)


def apply(ctx: SiteContext) -> None:
    """算出 `context_tokens`，同时写回 payload 与执行态。

    两边都写是因为：payload 是给本位点后续 handler（compression）读的，
    执行态是给后续位点与预算守卫读的。

    举例：apply(ctx) -> payload.context_tokens == state.context_tokens == 42815
    """
    payload = cast(LlmBeforePayload, ctx.payload)
    payload.schema_tokens = _count_schema(ctx, payload.tools_schema)

    tokens = _from_anchor(ctx, payload.messages)
    if tokens is None:
        tokens = (
            ctx.ports.tokens.count_messages(payload.messages)
            + payload.schema_tokens
            + payload.system_tokens
        )

    payload.context_tokens = tokens
    ctx.state.context_tokens = tokens


def _from_anchor(ctx: SiteContext, messages: list[Message]) -> int | None:
    """有可复用的真实用量就用它，否则返回 None。

    **只有这一次的 messages 是上一次的严格扩展时才算数** —— 中间被压缩、被中断的输出
    都会让前缀对不上，那时退回全量估算。

    `input_tokens` 已经含工具 schema 与系统提示词，所以这条路径**不另加** schema：
    它们在同一个会话里是常量（工具箱在装配时就定了），不构成失效条件。

    举例：_from_anchor(ctx, messages) -> 42815 + 312 + 18
    """
    anchor = ctx.session.token_anchor()
    if anchor is None:
        return None

    # 上次发出去的最前面是那次现拼的系统提示词：它不在 messages 里（内核发请求时才放到最前），
    # 所以比对前缀时把它跳过去。
    sent = (
        anchor.messages[1:]
        if anchor.messages and anchor.messages[0].role is MessageRole.SYSTEM
        else anchor.messages
    )
    if len(messages) < len(sent) or tuple(messages[: len(sent)]) != sent:
        return None

    extra = ctx.ports.tokens.count_messages(messages[len(sent) :])
    return anchor.input_tokens + anchor.output_tokens + extra


def _count_schema(ctx: SiteContext, schema: list[dict[str, JsonValue]]) -> int:
    """把工具 schema 序列化成文本再估算 token 数。

    为什么要序列化：`TokenCounter` 只认文本。schema 是嵌套字典，序列化后按 JSON 估算
    足够用——这里要的是量级，不是精确值。

    举例：_count_schema(ctx, [{"name": "read", "parameters": {...}}]) -> 128
    """
    if not schema:
        return 0
    return ctx.ports.tokens.count_text(json.dumps(schema, ensure_ascii=False))

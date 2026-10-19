"""两级压缩的调度：位点 3 一处入口。

两级是"先便宜后昂贵"：一级纯字符串替换、不落盘；一级不够才升二级、调一次模型。

**不重复数上下文。** 位点 3 已经把 `context_tokens` 算好了，一级只换掉其中几条长结果，
它把省下的量报回来，这里做减法就够 —— 900K 字符的上下文重数一遍是几十毫秒，
而且它卡在事件循环上，所有会话一起等。只有二级真的重建了投影时，才必须重数一次。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from yzy_agent.contracts import (
    Compaction,
    CompactionEntryPayload,
    LlmBeforePayload,
    Message,
    SiteContext,
)

from .level1_placeholder import shrink
from .level2_summary import compact
from .token_budget import count, guard
from .trigger import over_budget

# 事件里的摘要预览留多长。全文在 compaction 条目里。
#
# 举例：80
COMPACTION_PREVIEW_CHARS = 80


async def compact_at_llm_before(ctx: SiteContext) -> None:
    """主路径：算出来的 token 到阈值了，先便宜后昂贵。

    二级会换掉投影起点，所以压完要重新取一遍上下文 —— 草稿槽还在，半截输出不会漏。

    举例：await compact_at_llm_before(ctx)
    """
    payload = cast(LlmBeforePayload, ctx.payload)
    if not over_budget(payload.context_tokens, payload.compact_tokens):
        return

    before = payload.context_tokens
    payload.messages[:], saved = _shrink(ctx, payload.messages)
    _record(ctx, payload, before - saved)
    if not over_budget(payload.context_tokens, payload.compact_tokens):
        return

    await _summarize(ctx)
    # 二级换掉了投影起点，重新取一遍（草稿槽还在，半截输出不会漏），**再补一次一级**：
    # 不补的话，保留窗口里那几条长工具结果会原样带着全文进门 —— 一级就成了白做的。
    payload.messages[:], _ = _shrink(ctx, ctx.session.messages())
    # 这一份是重建出来的，没有"上一份"可以相减，只能数一遍；好在它只剩摘要 + 保留窗口。
    # schema 不在 messages 里，所以要把那一份补回去。
    _record(
        ctx,
        payload,
        count(payload.messages, tokens=ctx.ports.tokens) + payload.schema_tokens,
    )
    guard(payload.context_tokens, compact_tokens=payload.compact_tokens)


def _record(ctx: SiteContext, payload: LlmBeforePayload, tokens: int) -> None:
    """把压完之后的数写回两处：payload 给后面的 handler，执行态给 trace_end 与统计。

    举例：_record(ctx, payload, 42000)
    """
    payload.context_tokens = tokens
    ctx.state.context_tokens = tokens


def _shrink(ctx: SiteContext, messages: Sequence[Message]) -> tuple[list[Message], int]:
    """跑一级：把较早的长工具结果换成占位词。

    举例：_shrink(ctx, payload.messages) -> (messages, 1234)
    """
    return shrink(
        messages,
        tokens=ctx.ports.tokens,
        max_tokens=ctx.config.long_tool_result_max_tokens,
        keep_recent=ctx.config.long_tool_result_keep_recent,
    )


async def _summarize(ctx: SiteContext) -> None:
    """跑二级压缩；真的压掉了就广播一条 Compaction 事件。

    举例：await _summarize(ctx)
    """
    entry = await compact(
        ctx.session,
        llm=ctx.ports.llm,
        model=ctx.config.summary_model or ctx.config.model,
        template=ctx.config.summary_prompt_template,
        keep_recent=ctx.config.keep_recent,
        trace_id=ctx.state.trace_id,
        now=ctx.ports.clock.now(),
    )
    if entry is None:
        return

    payload = cast(CompactionEntryPayload, entry.payload)
    ctx.ports.events.publish(
        Compaction(
            session_id=ctx.session.meta.session_id,
            trace_id=entry.trace_id,
            summary_preview=payload.summary[:COMPACTION_PREVIEW_CHARS],
            kept_entry_ids=payload.entry_ids,
        )
    )

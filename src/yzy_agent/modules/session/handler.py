"""session 在各个位点上的 SiteHandler。

**所有 Session 日志的写入都发生在这里** —— 别的模块只把要写的东西放进 payload，
由本位点把它变成条目。这样 seq 怎么递增、entry_id 从哪来，就只有这一处知道。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

from yzy_agent.contracts import (
    AssistantMessage,
    BudgetSnapshot,
    CompactionSnapshot,
    Handler,
    LlmAfterPayload,
    Message,
    MessageRole,
    SessionEntryType,
    Site,
    SiteContext,
    TextPart,
    ToolAfterPayload,
    ToolBeforePayload,
    ToolDecision,
    ToolStartEntryPayload,
    TraceBeforePayload,
    TraceEnd,
    TraceEndEntryPayload,
    TraceEndPayload,
    TraceStart,
    TraceStartEntryPayload,
)

from .title import title_from

# 一个位点操作的统一签名：读入位点上下文，原地改它负责的那部分。
#
# 举例：_trace_before
SiteOp = Callable[[SiteContext], None]

# 位点 1：checkpoint 要在最前面查恢复判据（50），runtime_state 要在这之后绑 session_id（300），
# 所以 session 夹在中间。
#
# 举例：200
PRIORITY_TRACE_BEFORE = 200

# 位点 3：必须排在 compression **之后**。二级压缩会在本位点重建上下文，而重建走的是全量
# 投影 —— 那时草稿槽得还在，否则重建出来的 messages 会漏掉那条"被打断的输出"。
# compression 落地时，它的优先级必须小于这个值。
#
# 举例：300
PRIORITY_LLM_BEFORE = 300

# 位点 5/8：runtime_state 先跑（它负责把 pending_queue 搬进 payload），session 才写得到中途消息。
#
# 举例：200
PRIORITY_AFTER_RUNTIME_STATE = 200

# 位点 6：同上，runtime_state 要先把不执行的调用摘掉（450），session 只给要执行的写 tool_start。
#
# 举例：460
PRIORITY_AFTER_DECISIONS = 460

# 位点 8 比位点 5 还要晚一点：tool_calling 规范化输出在前，session 写结果条目在后。
#
# 举例：410
PRIORITY_TOOL_AFTER = 410

# 位点 10：session 写 trace_end 是所有收尾动作里的第一件。
#
# 举例：100
PRIORITY_TRACE_END = 100


def _trace_before(ctx: SiteContext) -> None:
    """写 trace_start 与本次的用户消息，并广播 TraceStart。

    只写"推不出来"的事实：耗时能由时间戳相减，turn 数与 token 量能重算，都不写。

    举例：_trace_before(ctx) -> 日志里多出两条条目，事件总线上多出一条 TraceStart
    """
    payload = cast(TraceBeforePayload, ctx.payload)
    session = ctx.session
    now = ctx.ports.clock.now()
    trace_id = ctx.state.trace_id

    session.append_entry(
        trace_id=trace_id,
        type=SessionEntryType.TRACE_START,
        payload=TraceStartEntryPayload(
            trigger=payload.trigger,
            parent_trace_id=payload.parent_trace_id,
            model=payload.model,
            permission=payload.permission,
            budget=BudgetSnapshot(max_turns=ctx.config.max_turns, max_tokens=ctx.config.max_tokens),
            compaction=CompactionSnapshot(
                compact_tokens=ctx.config.compact_tokens,
                keep_recent=ctx.config.keep_recent,
                long_tool_result_max_tokens=ctx.config.long_tool_result_max_tokens,
                long_tool_result_keep_recent=ctx.config.long_tool_result_keep_recent,
            ),
        ),
        now=now,
    )
    user_entry = session.append_entry(
        trace_id=trace_id,
        type=SessionEntryType.MESSAGE,
        payload=payload.user_message,
        now=now,
    )
    ctx.ports.events.publish(
        TraceStart(
            session_id=session.meta.session_id,
            trace_id=trace_id,
            model=payload.model,
            permission=payload.permission,
            max_turns=ctx.config.max_turns,
            user_message_entry_id=user_entry.entry_id,
            user_message_entry_seq=user_entry.seq,
            user_message=payload.user_message.content,
        )
    )

    # 直接写 meta 而不是走 Session 的方法：位点上只有 SessionHandle 这个口子，
    # meta 是它暴露出来的可变契约对象。
    session.meta.updated_at = now

    # 标题只在还没有的时候写一次，素材就是这次的用户消息。第一次 trace 走到这里时它还是
    # None，之后就一直有值了 —— 所以取的必然是会话的第一句话。用户显式给过标题的不覆盖。
    if session.meta.title is None:
        first_title = title_from(payload.user_message)
        if first_title is not None:
            session.meta.title = first_title


def _llm_before(ctx: SiteContext) -> None:
    """取材之后清空草稿槽。

    内核在构 `LlmBeforePayload` 时已经调过 `messages()`，残留的草稿被叠成了一条
    "上一轮输出被打断"的 assistant 消息。**不清空的话**：本轮的流会直接接在那半截后面
    （`DraftSlot.append` 只在槽空时才新建 Draft），拼出一条"两遍开头"的消息，
    而且它会在位点 5 被当成新消息再提交一遍 —— 旧残留这一轮已经被放进上下文了。
    这就是不变量 I11 在跨 trace 这一路上的那半边。

    槽本来就是空的时候它是空操作（绝大多数轮次都是这样）。

    举例：_llm_before(ctx) -> ctx.session.draft_text() == ""
    """
    ctx.session.take_draft()


def _llm_after(ctx: SiteContext) -> None:
    """定稿模型这一轮的输出，并把中途消息落成 user 消息。

    空内容的 assistant 消息**不写**：它进上下文会被当成一条空回复，还可能撞上
    "content 不能为空"的校验；什么都没产出就什么都不记。

    举例：_llm_after(ctx) -> 日志里多出 assistant 消息与若干 user 消息
    """
    payload = cast(LlmAfterPayload, ctx.payload)
    now = ctx.ports.clock.now()
    trace_id = ctx.state.trace_id

    if payload.assistant_message.content:
        entry = ctx.session.append_entry(
            trace_id=trace_id,
            type=SessionEntryType.MESSAGE,
            payload=payload.assistant_message,
            now=now,
        )
        # 事件带正文全量，前端拿它覆盖增量拼出来的那条。
        ctx.ports.events.publish(
            AssistantMessage(
                session_id=ctx.session.meta.session_id,
                trace_id=trace_id,
                turn_index=ctx.state.turn_index,
                entry_id=entry.entry_id,
                entry_seq=entry.seq,
                content=payload.assistant_message.content,
            )
        )

    for message in payload.pending_messages:
        ctx.session.append_entry(
            trace_id=trace_id, type=SessionEntryType.MESSAGE, payload=message, now=now
        )


def _tool_before(ctx: SiteContext) -> None:
    """为每条**将要执行**的调用写一条 tool_start。

    只给要执行的写：tool_start 的含义是"已派发，副作用可能已经发生"，
    被拒的调用从没派发过，给它写一条会让恢复时误判成"结果未知"。

    举例：_tool_before(ctx) -> 日志里多出若干 tool_start
    """
    payload = cast(ToolBeforePayload, ctx.payload)
    now = ctx.ports.clock.now()
    trace_id = ctx.state.trace_id

    for call in payload.calls:
        if payload.decisions.get(call.id) is not ToolDecision.EXECUTE:
            continue
        ctx.session.append_entry(
            trace_id=trace_id,
            type=SessionEntryType.TOOL_START,
            payload=ToolStartEntryPayload(
                tool_call_id=call.id,
                name=call.name,
                idempotency_key=payload.idempotency_keys.get(call.id, ""),
            ),
            now=now,
        )


def _tool_after(ctx: SiteContext) -> None:
    """写工具结果消息，并把中途消息落成 user 消息。

    举例：_tool_after(ctx) -> 日志里多出一条 tool 消息
    """
    payload = cast(ToolAfterPayload, ctx.payload)
    now = ctx.ports.clock.now()
    trace_id = ctx.state.trace_id

    message = payload.normalized or _fallback_result(ctx, payload)
    if message is not None:
        ctx.session.append_entry(
            trace_id=trace_id, type=SessionEntryType.MESSAGE, payload=message, now=now
        )

    for pending in payload.pending_messages:
        ctx.session.append_entry(
            trace_id=trace_id, type=SessionEntryType.MESSAGE, payload=pending, now=now
        )


def _fallback_result(ctx: SiteContext, payload: ToolAfterPayload) -> Message | None:
    """tool_calling 没给出规范化结果时，自己拼一条最小的工具结果消息。

    为什么要有这条兜底：assistant 消息里的每个 tool_call 都**必须**有配对的 tool 结果，
    少一个下一轮请求就不合法（不变量 I10）。宁可告诉模型"结果没拿到"，
    也不能让日志里缺一条。同时记一条 warning —— 走到这里说明 tool_calling 漏了活。

    举例：_fallback_result(ctx, payload) -> Message(role=MessageRole.TOOL, ...)
    """
    call = payload.call
    if call is None:
        return None

    ctx.ports.logger.warning(
        "session.tool_result_missing",
        trace_id=ctx.state.trace_id,
        tool=call.name,
        tool_call_id=call.id,
    )
    return Message(
        role=MessageRole.TOOL,
        content=[TextPart(text=payload.raw_result or f"工具 {call.name} 没有返回可用的结果。")],
        metadata={"tool_call_id": call.id},
    )


def _trace_end(ctx: SiteContext) -> None:
    """写 trace_end，并把 updated_at 刷成本刻。

    举例：_trace_end(ctx) -> 日志末尾多出一条 trace_end
    """
    payload = cast(TraceEndPayload, ctx.payload)
    now = ctx.ports.clock.now()

    ctx.session.append_entry(
        trace_id=ctx.state.trace_id,
        type=SessionEntryType.TRACE_END,
        payload=TraceEndEntryPayload(
            status=payload.status,
            reason=payload.reason,
            # 三个数字都在收尾这一刻记下来：轮次没有条目，上下文量读过就没了。
            turns=ctx.state.turn_index,
            context_tokens=ctx.state.context_tokens,
        ),
        now=now,
    )
    ctx.session.meta.updated_at = now

    # 必发：前端靠它决定这次 trace 是不是结束了。
    ctx.ports.events.publish(
        TraceEnd(
            session_id=ctx.session.meta.session_id,
            trace_id=ctx.state.trace_id,
            status=payload.status,
            reason=payload.reason,
            turns=ctx.state.turn_index,
            duration_ms=payload.duration_ms,
        )
    )


class _SiteOpHandler:
    """把一个位点操作包成 Handler。一个实例只挂一个位点。

    举例：_SiteOpHandler("session.llm_after", Site.LLM_AFTER, 200, _llm_after)
    """

    def __init__(self, handler_id: str, site: Site, priority: int, op: SiteOp) -> None:
        """绑定一个位点操作。

        举例：_SiteOpHandler("session.llm_after", Site.LLM_AFTER, 200, _llm_after)
        """
        # handler 唯一标识，形如"模块名.位点名"，日志与排错靠它定位。
        #
        # 举例："session.llm_after"
        self.id = handler_id

        # 这个 handler 挂在哪一个位点上。一个实例只挂一个，优先级才不会被别的位点带偏。
        #
        # 举例：frozenset({Site.LLM_AFTER})
        self.sites: frozenset[Site] = frozenset({site})

        # 执行优先级，数值越小越先跑。
        #
        # 举例：200
        self.priority = priority

        # 本位点真正要跑的那段逻辑。
        #
        # 举例：_llm_after
        self._op = op

    async def run(self, ctx: SiteContext) -> bool | None:
        """跑本位点的操作。

        session 没有否决语义：它只写日志，返回值一律 None（表示继续）。

        举例：await handler.run(ctx)
        """
        self._op(ctx)
        return None


def build_handlers() -> tuple[Handler, ...]:
    """造出 session 要注册的全部 handler，交给装配根。

    举例：registry = HandlerRegistry(build_handlers())
    """
    return (
        _SiteOpHandler(
            "session.trace_before", Site.TRACE_BEFORE, PRIORITY_TRACE_BEFORE, _trace_before
        ),
        _SiteOpHandler("session.llm_before", Site.LLM_BEFORE, PRIORITY_LLM_BEFORE, _llm_before),
        _SiteOpHandler(
            "session.llm_after", Site.LLM_AFTER, PRIORITY_AFTER_RUNTIME_STATE, _llm_after
        ),
        _SiteOpHandler(
            "session.tool_before", Site.TOOL_BEFORE, PRIORITY_AFTER_DECISIONS, _tool_before
        ),
        _SiteOpHandler("session.tool_after", Site.TOOL_AFTER, PRIORITY_TOOL_AFTER, _tool_after),
        _SiteOpHandler("session.trace_end", Site.TRACE_END, PRIORITY_TRACE_END, _trace_end),
    )

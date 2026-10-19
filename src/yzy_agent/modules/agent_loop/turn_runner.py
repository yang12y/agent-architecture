"""一轮 turn：位点 2 到 9。

内核在这里做三件**不交给 handler** 的事：

    1. **位点 4 直接调模型收流** —— 主循环要拿到收流结果才能继续，这不是可插拔的；
    2. **收流增量只写草稿槽这一处** —— 不另开第二份缓冲；
    3. **位点 6 的否决落到代码上** —— 被判定为不执行的调用，内核真的不执行它。

其余一切（写日志、改状态、压缩、审批、执行工具）都由注册在这几个位点上的 handler 决定。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from yzy_agent.contracts import (
    AgentError,
    AssistantDelta,
    AssistantThought,
    ContentPart,
    ExecutionPlan,
    FlushError,
    LlmAfterPayload,
    LlmBeforePayload,
    LlmCallError,
    LlmChunkKind,
    LlmDuringPayload,
    LlmRequest,
    Message,
    MessageRole,
    Site,
    SiteAbort,
    StopReason,
    TextPart,
    ThinkingPart,
    TokenAnchor,
    ToolAfterPayload,
    ToolBeforePayload,
    ToolCall,
    ToolCallPart,
    ToolDecision,
    ToolDuringPayload,
    TraceContext,
    TurnBeforePayload,
    TurnEndPayload,
    TurnStart,
    Usage,
)

from .pipeline import SitePipeline


@dataclass(frozen=True, slots=True)
class TurnOutcome:
    """一轮跑完之后的结论；内核靠它决定还有没有下一轮。"""

    # 这一轮模型有没有请求工具。
    #
    # 作用：有就说明还得再来一轮把工具结果喂回去，不能就此收尾。
    # 举例：True
    requested_tools: bool

    # 模型这一轮为什么停下来。
    #
    # 举例：StopReason.TOOL_CALLS
    stop_reason: StopReason


class TurnRunner:
    """跑一轮 turn。**无状态**，可以跨 trace、跨会话复用。

    举例：turn = await TurnRunner(pipeline).run(trace)
    """

    def __init__(self, pipeline: SitePipeline) -> None:
        # 位点管线：本轮的每个位点都交给它调度。
        #
        # 举例：SitePipeline(HandlerRegistry([...]))
        self.pipeline = pipeline

    async def run(self, trace: TraceContext) -> TurnOutcome:
        """按顺序跑完这一轮的位点 2 到 9，返回这一轮的结论。

        出错时异常直接向上冒：位点 3 抛 FlushError 就拦掉本次模型调用，
        这就是"失败即阻止"，由 TraceRunner 统一收尾。

        举例：await runner.run(trace)
        """
        state = trace.state

        # 位点 2 —— turn 开始前。
        # turn_index 由**内核自己**推进：它是内核的循环计数，max_turns 守卫就靠它，
        # 不该因为某个 handler 忘了注册而失效。其余运行时状态（重试计数、工具统计）
        # 由 runtime_state 的 handler 在自己的位点上处理。
        state.turn_index += 1
        trace.ports.events.publish(
            TurnStart(
                session_id=trace.session.meta.session_id,
                trace_id=state.trace_id,
                turn_index=state.turn_index,
            )
        )
        await self.pipeline.run(
            Site.TURN_BEFORE, TurnBeforePayload(turn_index=state.turn_index), trace
        )

        # 位点 3 —— 模型调用前：压缩改 messages、tool_calling 填 schema、checkpoint 落盘。
        request = LlmBeforePayload(
            model=trace.config.model,
            messages=trace.session.messages(),
            compact_tokens=trace.config.compact_tokens,
        )
        await self.pipeline.run(Site.LLM_BEFORE, request, trace)

        # 位点 4 + 收流 —— 每次尝试都算一次"模型调用中"，所以它连同重试一起在 _call_model 里。
        response = await self._call_model(trace, request)

        # 位点 5 —— 模型调用后：定稿 assistant 消息、解析待执行工具、消费中途消息。
        await self.pipeline.run(Site.LLM_AFTER, response, trace)

        # 位点 6/7/8 —— 工具阶段。
        await self._run_tool_batch(trace)

        # 位点 9 —— turn 结束。
        await self.pipeline.run(
            Site.TURN_END,
            TurnEndPayload(
                turn_index=state.turn_index,
                tool_stats=dict(state.turn_tool_stats),
                has_pending_work=bool(response.tool_calls)
                or bool(state.pending_tool_calls)
                or bool(state.pending_queue),
            ),
            trace,
        )

        return TurnOutcome(
            requested_tools=bool(response.tool_calls),
            stop_reason=response.stop_reason,
        )

    async def _call_model(self, trace: TraceContext, request: LlmBeforePayload) -> LlmAfterPayload:
        """调模型并收流。**失败按种类处置**：能重试的再给一次机会，其余原样上抛。

        不重试的三种情况，任何一种都让真实异常带着种类一路冒到 trace 收尾：
            1. 种类本身不可重试（鉴权、参数、超窗…）—— 再试一次结果一样；
            2. 尝试次数已经用光 —— 最后一次的失败原因就是收尾理由；
            3. 已经吐出了半截输出 —— 重试会把新输出接在旧残留后面，拼出一条"两遍开头"
               的消息。半截输出留在槽里，等下一次 trace 在位点 3 取材时作为"上一轮被
               打断的输出"并入上下文（I11）。

        举例：response = await runner._call_model(trace, request)
        """
        state = trace.state
        attempts = max(1, trace.config.llm_max_attempts)
        attempt = 0

        while True:
            state.llm_attempt_number = attempt
            await self.pipeline.run(
                Site.LLM_DURING,
                LlmDuringPayload(model=request.model, attempt=attempt),
                trace,
            )
            try:
                return await self._collect_stream(trace, request)
            except LlmCallError as exc:
                last_chance = attempt + 1 >= attempts
                # 已经吐出过东西（正文或思考）就不再重试 —— 重试会把两段输出接在一起。
                produced = trace.session.draft_text() or trace.session.draft_reasoning()
                if not exc.retryable or last_chance or produced:
                    raise
                attempt += 1
                trace.ports.logger.warning(
                    "llm.retry",
                    trace_id=state.trace_id,
                    attempt=attempt,
                    kind=exc.kind.value,
                    reason=exc.message,
                )

    async def _collect_stream(
        self, trace: TraceContext, request: LlmBeforePayload
    ) -> LlmAfterPayload:
        """收流，并把结果收成位点 5 的 payload。

        模型输出**只落在草稿槽这一处**：这里不另开缓冲，收完把槽取空、拼成消息。

        举例：payload = await runner._collect_stream(trace, request)
        """
        state = trace.state
        session_id = trace.session.meta.session_id
        tool_calls: list[ToolCallPart] = []
        usage: Usage | None = None
        stop_reason = StopReason.END_TURN

        llm_request = LlmRequest(
            model=request.model,
            messages=request.messages,
            tools_schema=request.tools_schema,
            thinking=trace.config.thinking,
        )
        async for chunk in trace.ports.llm.stream(llm_request):
            # 安全点：每个分片看一眼取消信号。这里抛出去时草稿槽里留着半截输出 ——
            # 那正是要的：它会在下一次 trace 的位点 3 作为"被打断的输出"并入上下文（I11）。
            if trace.signal.is_set():
                raise SiteAbort("用户中断")

            if chunk.kind is LlmChunkKind.TEXT_DELTA:
                trace.session.append_draft(
                    chunk.text,
                    trace_id=state.trace_id,
                    turn_index=state.turn_index,
                    now=trace.ports.clock.now(),
                )
                # 草稿槽不落盘，这一片只有从这里推出去，别处补不回来。
                trace.ports.events.publish(
                    AssistantDelta(
                        session_id=session_id,
                        trace_id=state.trace_id,
                        turn_index=state.turn_index,
                        text=chunk.text,
                    )
                )
            elif chunk.kind is LlmChunkKind.REASONING_DELTA:
                # 思考也走草稿槽，只是另一条道 —— 它不会被发回给模型。
                trace.session.append_reasoning(
                    chunk.text,
                    trace_id=state.trace_id,
                    turn_index=state.turn_index,
                    now=trace.ports.clock.now(),
                )
                trace.ports.events.publish(
                    AssistantThought(
                        session_id=session_id,
                        trace_id=state.trace_id,
                        turn_index=state.turn_index,
                        text=chunk.text,
                    )
                )
            elif chunk.kind is LlmChunkKind.TOOL_CALL:
                if chunk.tool_call is not None:
                    tool_calls.append(chunk.tool_call)
            elif chunk.kind is LlmChunkKind.USAGE:
                usage = chunk.usage
            elif chunk.kind is LlmChunkKind.DONE and chunk.stop_reason is not None:
                stop_reason = chunk.stop_reason

        # 记下"这次真的发了什么、提供方报了多少" —— 下一轮的估算从它往后加。
        # usage 缺失时保留旧锚点：前缀对不上的情况由位点 3 自己判，留着不吃亏。
        if usage is not None:
            trace.session.set_token_anchor(
                TokenAnchor(
                    messages=tuple(llm_request.messages),
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                )
            )

        draft = trace.session.take_draft()
        text = draft.content if draft is not None else ""
        reasoning = draft.reasoning if draft is not None else ""

        content: list[ContentPart] = []
        # 思考排在正文前面：它本来就是先发生的，界面上也照着这个顺序展开。
        if reasoning:
            content.append(ThinkingPart(text=reasoning))
        if text:
            content.append(TextPart(text=text))
        content.extend(tool_calls)

        return LlmAfterPayload(
            assistant_message=Message(role=MessageRole.ASSISTANT, content=content),
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            usage=usage,
        )

    async def _run_tool_batch(self, trace: TraceContext) -> None:
        """位点 6/7/8：本轮待执行的调用，先整批过位点 6，再按组过位点 7 与 8。

        位点 6 是**整批一次**（并行/串行编排天生是批级决策），位点 7 与 8 是**逐条一次**。
        `plan.groups` 里同一组的调用**同时跑**，组与组之间按顺序 —— 编排由 tool_calling
        在位点 6 算出来（相邻的只读调用并一组），内核只负责照做。

        没写进 decisions 的调用按拒绝处理——宁可少执行，也不要绕开检查链乱跑。

        **不执行的调用由 tool_calling 在位点 6 自己收尾**，内核只把判定为
        execute 的调用往下送。

        举例：await runner._run_tool_batch(trace)
        """
        state = trace.state
        calls = list(state.pending_tool_calls)
        if not calls:
            return

        batch = ToolBeforePayload(calls=calls)
        allowed = await self.pipeline.run(Site.TOOL_BEFORE, batch, trace)
        if not allowed:
            state.pending_tool_calls.clear()
            return

        plan = batch.plan or ExecutionPlan(groups=[[call.id] for call in calls])
        by_id = {call.id: call for call in calls}

        for group in plan.groups:
            runnable = [
                by_id[call_id]
                for call_id in group
                if by_id.get(call_id) is not None
                and batch.decisions.get(call_id) is ToolDecision.EXECUTE
            ]
            if not runnable:
                continue
            # 安全点：每一组开跑之前看一眼。已执行完的那些不会白费，
            # 它们的结果已经写进日志了。
            if trace.signal.is_set():
                raise SiteAbort("用户中断")
            await self._run_group(trace, runnable, batch)

    async def _run_group(
        self, trace: TraceContext, calls: list[ToolCall], batch: ToolBeforePayload
    ) -> None:
        """同一组里的调用一起跑。

        **工具失败不会走到这里** —— 执行器把它收成结果了，所以一条工具挂了不影响别的。
        能掀翻整组的只剩结构性错误（用户中断、落盘失败）：那一刻让其余几条继续跑没有意义，
        结果也落不下去，所以要掐掉。已经跑完的那些不受影响。

        举例：await runner._run_group(trace, [call_a, call_b], batch)
        """
        running = [asyncio.ensure_future(self._run_one(trace, call, batch)) for call in calls]
        try:
            await asyncio.gather(*running)
        except BaseException:
            for task in running:
                task.cancel()
            raise

    async def _run_one(self, trace: TraceContext, call: ToolCall, batch: ToolBeforePayload) -> None:
        """跑一条调用：位点 7 执行，位点 8 收结果。

        **结果由内核搬运** —— 位点 8 拿到的 payload 是这里新造的，位点 7 填的正文与异常
        得由中间这一层递过去（跟位点 4 的收流结果交给位点 5 同一个形状）。

        举例：await runner._run_one(trace, call, batch)
        """
        during = ToolDuringPayload(
            call=call,
            idempotency_key=batch.idempotency_keys.get(call.id, ""),
        )
        try:
            await self.pipeline.run(Site.TOOL_DURING, during, trace)
        except (SiteAbort, FlushError, asyncio.CancelledError):
            # 结构性错误：用户中断、盘坏了。整组一起停。
            raise
        except Exception as exc:
            # 能到这里只剩位点 handler 自己写挂了（工具侧的失败在执行器里就收成结果）。
            # **一条挂了不该影响同组的其他工具** —— 收成这条调用自己的失败结果。
            trace.ports.logger.error(
                "tool.run_failed",
                tool=call.name,
                tool_call_id=call.id,
                trace_id=trace.state.trace_id,
                error=f"{type(exc).__name__}: {exc}",
            )
            during.raw_result = f"{call.name} 执行失败：{type(exc).__name__}: {exc}"
            during.error = AgentError(during.raw_result)
        await self.pipeline.run(
            Site.TOOL_AFTER,
            ToolAfterPayload(call=call, raw_result=during.raw_result, error=during.error),
            trace,
        )

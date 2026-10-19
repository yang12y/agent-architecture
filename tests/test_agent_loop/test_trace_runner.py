"""主循环的行为测试。

用一堆"在位点 X 干 Y"的小 handler 拼出执行现场，只验证**内核自己的职责**：
位点顺序、turn 循环、工具批次、终止判定、异常收尾、重试。
各维度模块的行为不在这里测。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable

import pytest

from agent_architecture.contracts import (
    AgentError,
    CancelToken,
    ContextOverflowError,
    ExecutionPlan,
    FlushError,
    LlmCallError,
    LlmErrorKind,
    Message,
    MessageRole,
    Permission,
    Site,
    SiteAbort,
    SiteContext,
    TextPart,
    ToolCall,
    ToolCallStatus,
    ToolDecision,
    TraceBeforePayload,
    TraceStatus,
    TraceTrigger,
)
from agent_architecture.infra.config import load_app_config
from agent_architecture.modules.agent_loop import Agent, HandlerRegistry, SitePipeline, TraceRunner
from fakes import (
    BlockingLlm,
    FailingLlm,
    PartialThenFailLlm,
    ScriptedLlm,
    StopDuringStreamLlm,
    StubSession,
    make_frozen_config,
    make_ports,
    make_trace_context,
    text_turn,
    tool_turn,
)


class OnSite:
    """把"在位点 X 干 Y"包成一个 handler，测试里随手拼。

    举例：OnSite("tick", {Site.TURN_BEFORE}, action, priority=100)
    """

    def __init__(
        self,
        handler_id: str,
        sites: Iterable[Site],
        action: Callable[[SiteContext], Awaitable[bool | None]],
        *,
        priority: int = 100,
    ) -> None:
        # handler 标识。
        #
        # 举例："recorder"
        self.id = handler_id

        # 挂载的位点。
        #
        # 举例：frozenset({Site.TURN_BEFORE})
        self.sites = frozenset(sites)

        # 执行优先级，数值越小越先跑。
        #
        # 举例：100
        self.priority = priority

        # 被调用时执行的协程函数。
        #
        # 举例：async def action(ctx): return None
        self.action = action

        # 每次被调用时所在的位点，用来断言执行顺序。
        #
        # 举例：[Site.TURN_BEFORE, Site.TURN_END]
        self.calls: list[Site] = []

    async def run(self, ctx: SiteContext) -> bool | None:
        """记录位点，然后执行 action。"""
        self.calls.append(ctx.site)
        return await self.action(ctx)


def make_trigger(text: str = "你好") -> TraceBeforePayload:
    """造一个位点 1 的 payload。

    举例：make_trigger().trigger -> TraceTrigger.USER
    """
    return TraceBeforePayload(
        session_id="sess_test",
        trigger=TraceTrigger.USER,
        parent_trace_id=None,
        user_message=Message(role=MessageRole.USER, content=[TextPart(text=text)]),
        model="fake-model",
        permission=Permission.EDITABLE,
    )


def recorder(order: list[Site]) -> OnSite:
    """造一个把位点顺序记进列表的 handler，挂满十个位点、优先级最低（最后跑）。

    900 是 barrier 段，也就是允许的最小优先级 —— 这样它排在别的测试 handler（100）之后。

    举例：order = []; pipeline = SitePipeline(HandlerRegistry([recorder(order)]))
    """

    async def action(ctx: SiteContext) -> None:
        order.append(ctx.site)

    return OnSite("recorder", set(Site), action, priority=900)


def stateful() -> OnSite:
    """模拟 runtime_state：位点 5 把模型请求的工具写进待执行队列，位点 8 逐条摘掉。"""

    async def action(ctx: SiteContext) -> None:
        if ctx.site is Site.LLM_AFTER:
            ctx.state.pending_tool_calls.extend(
                ToolCall(
                    id=part.tool_call_id,
                    name=part.name,
                    arguments=part.arguments,
                    status=ToolCallStatus.PENDING,
                )
                for part in ctx.payload.tool_calls  # type: ignore[attr-defined]
            )
        elif ctx.site is Site.TOOL_AFTER:
            done = ctx.payload.call  # type: ignore[attr-defined]
            ctx.state.pending_tool_calls = [
                call for call in ctx.state.pending_tool_calls if call.id != done.id
            ]

    return OnSite("state", {Site.LLM_AFTER, Site.TOOL_AFTER}, action, priority=100)


def execute_all(order: list[str]) -> OnSite:
    """模拟 tool_calling：位点 6 放行全部调用并算出幂等键，位点 7 记录真的执行了哪几条。"""

    async def action(ctx: SiteContext) -> None:
        if ctx.site is Site.TOOL_BEFORE:
            for call in ctx.payload.calls:  # type: ignore[attr-defined]
                ctx.payload.decisions[call.id] = ToolDecision.EXECUTE  # type: ignore[attr-defined]
                ctx.payload.idempotency_keys[call.id] = f"key-{call.id}"  # type: ignore[attr-defined]
        elif ctx.site is Site.TOOL_DURING:
            order.append(ctx.payload.call.name)  # type: ignore[attr-defined]

    return OnSite("tools", {Site.TOOL_BEFORE, Site.TOOL_DURING}, action, priority=100)


def build(*handlers: OnSite) -> SitePipeline:
    """按给定 handler 建一条管线。

    举例：pipeline = build(recorder(order), stateful())
    """
    return SitePipeline(HandlerRegistry(handlers))


# ---------------------------------------------------------------------------
# 位点顺序与基本收尾
# ---------------------------------------------------------------------------


async def test_site_order_for_a_single_turn_trace() -> None:
    """一轮就结束的 trace，位点顺序固定为 1 → 2 → 3 → 4 → 5 → 9 → 10。"""
    order: list[Site] = []
    trace = make_trace_context(ports=make_ports(llm=ScriptedLlm([text_turn("你好")])))

    await TraceRunner(build(recorder(order))).run(trace, make_trigger())

    assert order == [
        Site.TRACE_BEFORE,
        Site.TURN_BEFORE,
        Site.LLM_BEFORE,
        Site.LLM_DURING,
        Site.LLM_AFTER,
        Site.TURN_END,
        Site.TRACE_END,
    ]


async def test_outcome_reports_completed_with_turn_count() -> None:
    """模型没请求工具时，一轮就正常收尾。"""
    trace = make_trace_context(ports=make_ports(llm=ScriptedLlm([text_turn("你好")])))

    outcome = await TraceRunner(build()).run(trace, make_trigger())

    assert outcome.status is TraceStatus.COMPLETED
    assert outcome.reason is None
    assert outcome.turns == 1
    assert outcome.duration_ms >= 0
    assert trace.state.is_running is False
    assert trace.state.turn_index == 1


async def test_assistant_message_is_built_from_the_draft_slot() -> None:
    """模型输出只落草稿槽一处；位点 5 拿到的 assistant 消息就是槽里取出来拼的。"""
    seen: list[Message] = []

    async def peek(ctx: SiteContext) -> None:
        if ctx.site is Site.LLM_AFTER:
            seen.append(ctx.payload.assistant_message)  # type: ignore[attr-defined]

    trace = make_trace_context(ports=make_ports(llm=ScriptedLlm([text_turn("我来帮你读")])))

    await TraceRunner(build(OnSite("peek", {Site.LLM_AFTER}, peek))).run(trace, make_trigger())

    assert len(seen) == 1
    assert seen[0].role is MessageRole.ASSISTANT
    assert seen[0].content[0] == TextPart(text="我来帮你读")
    assert trace.session.draft_text() == ""


async def test_the_usage_anchor_is_recorded_after_each_call() -> None:
    """内核把"这次真发了什么、提供方报了多少"记到会话上 —— 下一轮估算从这里往后加。"""
    trace = make_trace_context(ports=make_ports(llm=ScriptedLlm([text_turn("你好")])))

    await TraceRunner(build()).run(trace, make_trigger())

    anchor = trace.session.token_anchor()
    assert anchor is not None
    assert (anchor.input_tokens, anchor.output_tokens) == (10, 2)
    assert isinstance(anchor.messages, tuple)


# ---------------------------------------------------------------------------
# 工具阶段
# ---------------------------------------------------------------------------


async def test_tool_loop_runs_a_second_turn() -> None:
    """模型先请求工具、工具跑完再给答复，一共两轮，位点 6/7/8 各跑到。"""
    order: list[Site] = []
    executed: list[str] = []
    llm = ScriptedLlm(
        [
            tool_turn(("call_1", "read", {"path": "a.py"})),
            text_turn("读完了"),
        ]
    )
    trace = make_trace_context(ports=make_ports(llm=llm))

    outcome = await TraceRunner(build(recorder(order), stateful(), execute_all(executed))).run(
        trace, make_trigger()
    )

    assert outcome.status is TraceStatus.COMPLETED
    assert outcome.turns == 2
    assert executed == ["read"]
    assert Site.TOOL_BEFORE in order and Site.TOOL_DURING in order and Site.TOOL_AFTER in order
    assert len(llm.requests) == 2


async def test_tools_without_a_decision_are_not_executed() -> None:
    """没被判定为 EXECUTE 的调用一律不执行——宁可少跑，也不绕开检查链。"""
    executed: list[str] = []
    llm = ScriptedLlm([tool_turn(("call_1", "write", {"path": "a.py"})), text_turn("好吧")])
    trace = make_trace_context(ports=make_ports(llm=llm))

    async def stateful_only(ctx: SiteContext) -> None:
        await stateful().action(ctx)

    outcome = await TraceRunner(
        build(OnSite("state", {Site.LLM_AFTER, Site.TOOL_AFTER}, stateful_only))
    ).run(trace, make_trigger())

    assert executed == []
    assert outcome.status is TraceStatus.COMPLETED


async def test_rejected_batch_clears_the_queue_and_skips_execution() -> None:
    """位点 6 整批被否决：一条都不执行，待执行队列清空，位点 7 不会跑到。

    注意循环**会继续**下一轮 —— 被拒这件事必须让模型知道，否则它只会重问同一个工具。
    所以这里给了第二轮脚本，模拟"模型收到拒绝后改口"。
    """
    order: list[Site] = []

    async def reject(ctx: SiteContext) -> bool:
        return False

    llm = ScriptedLlm([tool_turn(("call_1", "rm", {})), text_turn("那我换个办法")])
    trace = make_trace_context(ports=make_ports(llm=llm))

    outcome = await TraceRunner(
        build(recorder(order), stateful(), OnSite("reject", {Site.TOOL_BEFORE}, reject))
    ).run(trace, make_trigger())

    assert outcome.status is TraceStatus.COMPLETED
    assert outcome.turns == 2
    assert Site.TOOL_DURING not in order
    assert trace.state.pending_tool_calls == []


# ---------------------------------------------------------------------------
# 位点 7 → 8：结果由内核搬运，同组并发
# ---------------------------------------------------------------------------


def _one_call(name: str = "read") -> ScriptedLlm:
    """一个只会请求一条工具的脚本。

    举例：_one_call("read")
    """
    return ScriptedLlm([tool_turn(("call_1", name, {})), text_turn("好了")])


def _allow_all(*, plan: ExecutionPlan | None = None) -> OnSite:
    """位点 6：放行全部调用，可选地写一个编排。

    举例：_allow_all(plan=ExecutionPlan(groups=[["call_1", "call_2"]]))
    """

    async def action(ctx: SiteContext) -> None:
        if ctx.site is not Site.TOOL_BEFORE:
            return
        for call in ctx.payload.calls:  # type: ignore[attr-defined]
            ctx.payload.decisions[call.id] = ToolDecision.EXECUTE  # type: ignore[attr-defined]
        if plan is not None:
            ctx.payload.plan = plan  # type: ignore[attr-defined]

    return OnSite("allow", {Site.TOOL_BEFORE}, action)


async def test_the_result_is_carried_from_site_seven_to_eight() -> None:
    """位点 7 填的正文由内核搬到代码 8 —— 位点 8 拿到的 payload 是内核新造的。"""
    seen: list[str] = []

    async def produce(ctx: SiteContext) -> None:
        await stateful().action(ctx)
        if ctx.site is Site.TOOL_DURING:
            ctx.payload.raw_result = "正文"  # type: ignore[attr-defined]

    async def consume(ctx: SiteContext) -> None:
        await stateful().action(ctx)
        if ctx.site is Site.TOOL_AFTER:
            seen.append(ctx.payload.raw_result)  # type: ignore[attr-defined]

    trace = make_trace_context(ports=make_ports(llm=_one_call()))
    await TraceRunner(
        build(
            _allow_all(),
            OnSite("state+produce", {Site.LLM_AFTER, Site.TOOL_AFTER, Site.TOOL_DURING}, produce),
            OnSite("consume", {Site.TOOL_AFTER}, consume),
        )
    ).run(trace, make_trigger())

    assert seen == ["正文"]


async def test_the_error_is_carried_too() -> None:
    """执行失败时那个异常也要搬过去 —— 位点 8 靠它决定写"失败说明"而不是正文。"""
    seen: list[str] = []

    async def fail(ctx: SiteContext) -> None:
        await stateful().action(ctx)
        if ctx.site is Site.TOOL_DURING:
            ctx.payload.error = AgentError("工具炸了")  # type: ignore[attr-defined]

    async def consume(ctx: SiteContext) -> None:
        await stateful().action(ctx)
        if ctx.site is Site.TOOL_AFTER:
            error = ctx.payload.error  # type: ignore[attr-defined]
            seen.append(error.message if error is not None else "(没有错误)")

    trace = make_trace_context(ports=make_ports(llm=_one_call()))
    await TraceRunner(
        build(
            _allow_all(),
            OnSite("state+fail", {Site.LLM_AFTER, Site.TOOL_AFTER, Site.TOOL_DURING}, fail),
            OnSite("consume", {Site.TOOL_AFTER}, consume),
        )
    ).run(trace, make_trigger())

    assert seen == ["工具炸了"]


async def test_calls_in_one_group_run_concurrently() -> None:
    """同一组的调用**同时**跑 —— 串行的话这两条会互相等到超时。

    两条各等对方先到：只有真的并发才走得下去。改回串行这条会红（超时），
    所以它是这条行为的守门测试。
    """
    arrived = {"a": asyncio.Event(), "b": asyncio.Event()}

    async def rendezvous(ctx: SiteContext) -> None:
        call = ctx.payload.call  # type: ignore[attr-defined]
        arrived[call.name].set()
        other = "b" if call.name == "a" else "a"
        await asyncio.wait_for(arrived[other].wait(), timeout=5)

    llm = ScriptedLlm([tool_turn(("call_1", "a", {}), ("call_2", "b", {})), text_turn("好了")])
    trace = make_trace_context(ports=make_ports(llm=llm))

    outcome = await asyncio.wait_for(
        TraceRunner(
            build(
                _allow_all(plan=ExecutionPlan(groups=[["call_1", "call_2"]])),
                OnSite("state", {Site.LLM_AFTER, Site.TOOL_AFTER}, stateful().action),
                OnSite("meet", {Site.TOOL_DURING}, rendezvous),
            )
        ).run(trace, make_trigger()),
        timeout=15,
    )

    assert outcome.status is TraceStatus.COMPLETED
    assert arrived["a"].is_set() and arrived["b"].is_set()


# ---------------------------------------------------------------------------
# 位点 1 的否决
# ---------------------------------------------------------------------------


async def test_rejected_trace_before_skips_every_site_but_the_last() -> None:
    """位点 1 被否决：位点 2-9 一次都不跑，但位点 10 照常执行。

    位点 10 必须跑 —— Runtime State 的销毁与 is_running 的复位都在那儿，
    少了它这次 trace 就成了永远"运行中"的僵尸。
    """
    order: list[Site] = []

    async def reject(ctx: SiteContext) -> bool:
        return False

    llm = ScriptedLlm([text_turn("不该出现")])
    trace = make_trace_context(ports=make_ports(llm=llm))

    outcome = await TraceRunner(
        build(recorder(order), OnSite("reject", {Site.TRACE_BEFORE}, reject))
    ).run(trace, make_trigger())

    assert outcome.status is TraceStatus.FAILED
    assert outcome.reason is not None
    assert outcome.turns == 0
    assert order == [Site.TRACE_END]
    assert trace.state.is_running is False


# ---------------------------------------------------------------------------
# 异常与终止
# ---------------------------------------------------------------------------


async def test_flush_error_at_llm_before_blocks_the_model_call() -> None:
    """位点 3 落盘失败必须拦掉这次模型调用——模型一次都不该被调到。"""
    llm = ScriptedLlm([text_turn("不该出现")])
    trace = make_trace_context(ports=make_ports(llm=llm))

    async def boom(ctx: SiteContext) -> None:
        raise FlushError("磁盘写满")

    outcome = await TraceRunner(build(OnSite("flush", {Site.LLM_BEFORE}, boom))).run(
        trace, make_trigger()
    )

    assert llm.requests == []
    assert outcome.status is TraceStatus.FAILED
    assert "磁盘写满" in (outcome.reason or "")


async def test_site_abort_ends_the_trace_as_aborted() -> None:
    """handler 抛 SiteAbort 时按 aborted 收尾，位点 10 照样跑到。"""
    order: list[Site] = []
    trace = make_trace_context(ports=make_ports(llm=ScriptedLlm([text_turn("你好")])))

    async def stop(ctx: SiteContext) -> None:
        raise SiteAbort("用户中断")

    outcome = await TraceRunner(
        build(recorder(order), OnSite("stop", {Site.TURN_BEFORE}, stop))
    ).run(trace, make_trigger())

    assert outcome.status is TraceStatus.ABORTED
    assert outcome.reason == "用户中断"
    assert order[-1] is Site.TRACE_END


async def test_trace_end_runs_even_when_the_body_explodes() -> None:
    """位点 10 是收尾，无论中间怎么炸都必须跑到。"""
    order: list[Site] = []
    trace = make_trace_context(ports=make_ports())

    async def boom(ctx: SiteContext) -> None:
        raise RuntimeError("意料之外")

    outcome = await TraceRunner(
        build(recorder(order), OnSite("boom", {Site.TURN_BEFORE}, boom))
    ).run(trace, make_trigger())

    assert outcome.status is TraceStatus.FAILED
    assert "RuntimeError" in (outcome.reason or "")
    assert order[-1] is Site.TRACE_END


async def test_max_turns_stops_the_loop_with_budget_exceeded() -> None:
    """模型一直请求工具、永远问不完时，撞到 max_turns 就该停。"""
    trace = make_trace_context(
        config=make_frozen_config(max_turns=2),
        ports=make_ports(llm=ScriptedLlm([tool_turn(("call_1", "read", {}))] * 5)),
    )

    outcome = await TraceRunner(build(stateful(), execute_all([]))).run(trace, make_trigger())

    assert outcome.status is TraceStatus.BUDGET_EXCEEDED
    assert outcome.turns == 2


async def test_a_context_overflow_ends_as_budget_exceeded() -> None:
    """模型挡回来说放不下时，不重试也不压，直接按预算失败收尾。

    位点 3 是唯一的防线：算得出放不下就在出门前拦下（守卫抛错）。
    算漏了才轮到模型挡回来 —— 两条路都收在 budget_exceeded。
    """
    llm = FailingLlm(ContextOverflowError("超出上下文窗口"))
    trace = make_trace_context(ports=make_ports(llm=llm))

    outcome = await TraceRunner(build()).run(trace, make_trigger())

    assert outcome.status is TraceStatus.BUDGET_EXCEEDED
    assert len(llm.requests) == 1


# ---------------------------------------------------------------------------
# 重试
# ---------------------------------------------------------------------------


async def test_retryable_failure_is_retried_when_nothing_was_produced() -> None:
    """还没产出就失败时，重试是安全的——草稿槽是空的。"""
    llm = FailingLlm(
        LlmCallError("被限流", kind=LlmErrorKind.RATE_LIMIT), fail_times=1, then=text_turn("好了")
    )
    trace = make_trace_context(ports=make_ports(llm=llm))

    outcome = await TraceRunner(build()).run(trace, make_trigger())

    assert outcome.status is TraceStatus.COMPLETED
    assert len(llm.requests) == 2
    assert trace.state.llm_attempt_number == 1


async def test_partial_output_is_not_retried() -> None:
    """已经吐出半截就不能重试：重试会把新输出接在旧残留后面，拼出"两遍开头"的消息。"""
    llm = PartialThenFailLlm("我来帮你", LlmCallError("断了", kind=LlmErrorKind.CONNECTION))
    trace = make_trace_context(ports=make_ports(llm=llm))

    outcome = await TraceRunner(build()).run(trace, make_trigger())

    assert outcome.status is TraceStatus.FAILED
    assert len(llm.requests) == 1
    assert trace.session.draft_text() == "我来帮你"


async def test_non_retryable_failure_is_not_retried() -> None:
    """鉴权失败这类错误重试只是浪费时间。"""
    llm = FailingLlm(LlmCallError("鉴权失败", kind=LlmErrorKind.AUTH), fail_times=1)
    trace = make_trace_context(ports=make_ports(llm=llm))

    outcome = await TraceRunner(build()).run(trace, make_trigger())

    assert outcome.status is TraceStatus.FAILED
    assert len(llm.requests) == 1
    assert outcome.reason is not None and "鉴权失败" in outcome.reason


async def test_retries_stop_at_the_configured_attempts() -> None:
    """一直失败就一直重试到次数用光，收尾理由带的是最后一次的真实失败原因。"""
    llm = FailingLlm(LlmCallError("被限流", kind=LlmErrorKind.RATE_LIMIT), fail_times=99)
    trace = make_trace_context(ports=make_ports(llm=llm))

    outcome = await TraceRunner(build()).run(trace, make_trigger())

    assert outcome.status is TraceStatus.FAILED
    assert len(llm.requests) == trace.config.llm_max_attempts
    assert outcome.reason is not None and "被限流" in outcome.reason


# ---------------------------------------------------------------------------
# 门面
# ---------------------------------------------------------------------------


async def test_agent_facade_builds_the_context_and_runs() -> None:
    """门面负责组装 trace_id、Runtime State 与快照，然后交给 TraceRunner。"""
    session = StubSession()
    llm = ScriptedLlm([text_turn("你好呀")])
    agent = Agent(
        pipeline=build(),
        ports=make_ports(llm=llm),
        app_config=load_app_config({}),
    )

    outcome = await agent.run(
        session=session,
        user_message=Message(role=MessageRole.USER, content=[TextPart(text="你好")]),
    )

    assert outcome.status is TraceStatus.COMPLETED
    assert outcome.turns == 1
    # 模型取的是会话自己的偏好（StubSession 的 meta 写的是 fake-model），
    # 而不是全局配置里的默认值 —— 这是文档定的优先级：显式 > SessionMeta > AppConfig
    assert llm.requests[0].model == "fake-model"
    assert llm.requests[0].messages == []


# ---------------------------------------------------------------------------
# 取消
# ---------------------------------------------------------------------------


async def test_a_signal_set_before_the_first_turn_aborts_the_trace() -> None:
    """信号一开始就是 set 的：一轮都不跑，但位点 10 照常收尾。"""
    order: list[Site] = []
    signal = CancelToken()
    signal.set()
    llm = ScriptedLlm([text_turn("不该被调到")])
    trace = make_trace_context(ports=make_ports(llm=llm), signal=signal)

    outcome = await TraceRunner(build(recorder(order))).run(trace, make_trigger())

    assert outcome.status is TraceStatus.ABORTED
    assert outcome.turns == 0
    assert llm.requests == []
    assert Site.TRACE_END in order


async def test_a_signal_set_mid_stream_stops_the_trace() -> None:
    """收流途中被中断：状态是 aborted，半截输出留在草稿槽里（I11）。"""
    order: list[Site] = []
    signal = CancelToken()
    llm = StopDuringStreamLlm("我来帮你", signal)
    trace = make_trace_context(ports=make_ports(llm=llm), signal=signal)

    outcome = await TraceRunner(build(recorder(order))).run(trace, make_trigger())

    assert outcome.status is TraceStatus.ABORTED
    assert trace.session.draft_text() == "我来帮你"
    assert "不该被收下" not in trace.session.draft_text()
    assert Site.TRACE_END in order


async def test_a_cancelled_task_still_reaches_the_closing_site() -> None:
    """进程级取消是 BaseException，抓不到就等于没给这次 trace 收尾 ——
    日志末尾会留一个没有 trace_end 的尾巴，下次启动被当成崩溃。"""
    order: list[Site] = []
    llm = BlockingLlm()
    trace = make_trace_context(ports=make_ports(llm=llm))
    runner = TraceRunner(build(recorder(order)))

    task = asyncio.create_task(runner.run(trace, make_trigger()))
    for _ in range(100):
        if llm.requests:
            break
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert Site.TRACE_END in order
    assert trace.state.is_running is False

"""位点管线的行为测试。

覆盖六件事：执行顺序、否决短路、异常传播、位点隔离、数据通道、索引自检。
"""

from __future__ import annotations

import pytest

from agent_frame.contracts import (
    FlushError,
    LlmBeforePayload,
    Site,
    SiteContext,
    SpanKind,
    ToolBeforePayload,
    ToolCall,
    ToolCallStatus,
    ToolDuringPayload,
    TurnBeforePayload,
)
from agent_frame.modules.agent_loop import HandlerRegistry, SitePipeline
from fakes import (
    FakeClock,
    HandlerSpy,
    RecordingHandler,
    make_ports,
    make_runtime_state,
    make_trace_context,
)


async def test_handlers_run_in_priority_order() -> None:
    """priority 小的先跑，与注册顺序无关。"""
    order: list[str] = []
    late = HandlerSpy("late", {Site.TURN_BEFORE}, order=order, priority=900)
    early = HandlerSpy("early", {Site.TURN_BEFORE}, order=order, priority=100)
    pipeline = SitePipeline(HandlerRegistry([late, early]))

    allowed = await pipeline.run(
        Site.TURN_BEFORE, TurnBeforePayload(turn_index=1), make_trace_context()
    )

    assert allowed is True
    assert order == ["early", "late"]


async def test_same_priority_keeps_registration_order() -> None:
    """priority 相同时按注册顺序跑（sorted 是稳定排序）。"""
    order: list[str] = []
    first = HandlerSpy("first", {Site.TURN_BEFORE}, order=order, priority=100)
    second = HandlerSpy("second", {Site.TURN_BEFORE}, order=order, priority=100)
    pipeline = SitePipeline(HandlerRegistry([first, second]))

    await pipeline.run(Site.TURN_BEFORE, TurnBeforePayload(turn_index=1), make_trace_context())

    assert order == ["first", "second"]


async def test_reject_short_circuits_remaining_handlers() -> None:
    """返回 False 立刻停下，后面的 handler 一次都不跑。"""
    order: list[str] = []
    first = HandlerSpy("first", {Site.TOOL_BEFORE}, order=order, priority=100)
    rejector = HandlerSpy("rejector", {Site.TOOL_BEFORE}, order=order, priority=200, result=False)
    last = HandlerSpy("last", {Site.TOOL_BEFORE}, order=order, priority=300)
    pipeline = SitePipeline(HandlerRegistry([first, rejector, last]))

    allowed = await pipeline.run(Site.TOOL_BEFORE, ToolBeforePayload(), make_trace_context())

    assert allowed is False
    assert order == ["first", "rejector"]


async def test_reject_at_a_non_rejection_site_is_logged_as_an_error() -> None:
    """位点 2 返回 False 越界了：运行语义照旧短路，但日志必须是 ERROR。

    契约只有位点 1 与位点 6 是否决位点（REJECTION_SITES）。别处的 False 是 handler
    写错了，联调时必须一眼看见，不能混在正常的 site.rejected INFO 里。
    """
    trace = make_trace_context()
    pipeline = SitePipeline(
        HandlerRegistry([HandlerSpy("buggy", {Site.TURN_BEFORE}, order=[], result=False)])
    )

    allowed = await pipeline.run(Site.TURN_BEFORE, TurnBeforePayload(0), trace)

    assert allowed is False
    events = [record[1] for record in trace.ports.logger.records]  # type: ignore[attr-defined]
    levels = [record[0] for record in trace.ports.logger.records]  # type: ignore[attr-defined]
    assert "site.reject_out_of_contract" in events
    assert levels[events.index("site.reject_out_of_contract")] == "ERROR"


async def test_true_return_keeps_going() -> None:
    """返回 True 和返回 None 一样，都表示继续。"""
    order: list[str] = []
    a = HandlerSpy("a", {Site.TURN_BEFORE}, order=order, priority=100, result=True)
    b = HandlerSpy("b", {Site.TURN_BEFORE}, order=order, priority=200)
    pipeline = SitePipeline(HandlerRegistry([a, b]))

    assert await pipeline.run(Site.TURN_BEFORE, TurnBeforePayload(0), make_trace_context())
    assert order == ["a", "b"]


async def test_handler_exception_propagates_and_stops() -> None:
    """handler 抛异常时管线不吞：异常向上传，后面的 handler 不跑。"""
    order: list[str] = []
    boom = HandlerSpy("boom", {Site.LLM_BEFORE}, order=order, priority=100)
    boom.error = FlushError("磁盘写满")
    never = HandlerSpy("never", {Site.LLM_BEFORE}, order=order, priority=200)
    pipeline = SitePipeline(HandlerRegistry([boom, never]))

    with pytest.raises(FlushError, match="磁盘写满"):
        await pipeline.run(Site.LLM_BEFORE, TurnBeforePayload(0), make_trace_context())

    assert order == ["boom"]


async def test_only_handlers_of_that_site_run() -> None:
    """管线只跑当前位点的 handler，绝不会顺带跑到别的位点。"""
    at_turn_before = RecordingHandler("at-tb", {Site.TURN_BEFORE})
    at_trace_end = RecordingHandler("at-te", {Site.TRACE_END})
    both = RecordingHandler("both", {Site.TURN_BEFORE, Site.TRACE_END})
    pipeline = SitePipeline(HandlerRegistry([at_turn_before, at_trace_end, both]))

    await pipeline.run(Site.TURN_BEFORE, TurnBeforePayload(1), make_trace_context())

    assert len(at_turn_before.calls) == 1
    assert len(both.calls) == 1
    assert at_trace_end.calls == []


async def test_empty_site_is_allowed() -> None:
    """没有任何 handler 的位点直接放行。"""
    pipeline = SitePipeline(HandlerRegistry([]))

    assert await pipeline.run(Site.TURN_END, TurnBeforePayload(0), make_trace_context()) is True


async def test_payload_mutation_is_visible_to_later_handler() -> None:
    """数据通道是原地改：前一个 handler 改的 payload，后一个看得到。"""
    payload = TurnBeforePayload(turn_index=1)
    writer = RecordingHandler("writer", {Site.TURN_BEFORE}, priority=100)
    reader = RecordingHandler("reader", {Site.TURN_BEFORE}, priority=200)

    async def write(ctx: SiteContext) -> None:
        assert isinstance(ctx.payload, TurnBeforePayload)
        ctx.payload.turn_index = 42

    writer.run = write  # type: ignore[method-assign]

    await SitePipeline(HandlerRegistry([writer, reader])).run(
        Site.TURN_BEFORE, payload, make_trace_context()
    )

    assert payload.turn_index == 42
    assert reader.calls[0].payload is payload


async def test_pipeline_logs_site_begin_and_end() -> None:
    """管线把位点的进出记进日志，事后能按 trace_id 还原执行轨迹。"""
    trace = make_trace_context()
    pipeline = SitePipeline(HandlerRegistry([RecordingHandler("h", {Site.TURN_BEFORE})]))

    await pipeline.run(Site.TURN_BEFORE, TurnBeforePayload(0), trace)

    events = [record[1] for record in trace.ports.logger.records]  # type: ignore[attr-defined]
    assert events == ["site.begin", "site.end"]


def test_every_documented_band_is_accepted_at_its_edges() -> None:
    """段位表列出的每个边界值都必须能装配成功，并且排序结果就是段位顺序。"""
    edges = [50, 100, 499, 500, 899, 900]
    registry = HandlerRegistry(
        HandlerSpy(f"h{n}", {Site.TURN_BEFORE}, order=[], priority=n) for n in edges
    )

    assert [h.priority for h in registry.for_site(Site.TURN_BEFORE)] == edges


@pytest.mark.parametrize("priority", [-1, 0, 49, 51, 99, 901, 1000, 12345])
def test_priority_outside_every_band_is_rejected(priority: int) -> None:
    """段位之外的 priority 一律在装配期拒绝。

    空隙有三段：50 以下、51-99、901 以上。把 900 写成 9010 这种一位数错误必须当场报错，
    否则"落盘屏障最后跑"就静默失效了 —— 排序照旧能跑，只是落盘排到了别的 handler 前面。
    """
    with pytest.raises(ValueError, match="priority"):
        HandlerRegistry([HandlerSpy("buggy", {Site.TURN_BEFORE}, order=[], priority=priority)])


def test_duplicate_handler_id_is_rejected() -> None:
    """handler id 重复要在装配期就报错，而不是运行时才发现日志对不上。"""
    a = RecordingHandler("dup", {Site.TURN_BEFORE})
    b = RecordingHandler("dup", {Site.TRACE_END})

    with pytest.raises(ValueError, match="dup"):
        HandlerRegistry([a, b])


def test_registry_reports_mount_points() -> None:
    """sites_of 报出一个 handler 挂在哪些位点，找不到时返回空集合。"""
    handler = RecordingHandler("multi", {Site.LLM_AFTER, Site.TOOL_AFTER})
    registry = HandlerRegistry([handler])

    assert registry.sites_of("multi") == frozenset({Site.LLM_AFTER, Site.TOOL_AFTER})
    assert registry.sites_of("不存在") == frozenset()


def test_registry_for_site_returns_sorted_tuple() -> None:
    """for_site 返回已排序的元组，位点没有 handler 时是空元组。"""
    order: list[str] = []
    registry = HandlerRegistry(
        [
            HandlerSpy("z", {Site.TURN_BEFORE}, order=order, priority=900),
            HandlerSpy("a", {Site.TURN_BEFORE}, order=order, priority=100),
        ]
    )

    assert [h.id for h in registry.for_site(Site.TURN_BEFORE)] == ["a", "z"]
    assert registry.for_site(Site.TOOL_DURING) == ()


# ---- 时间轴：位点跑一次记一格 ----


class _SlowHandler:
    """跑一段假时间的 handler：把共享的那个假时钟往前推。

    举例：_SlowHandler("slow", clock, 25)
    """

    def __init__(self, handler_id: str, clock: FakeClock, milliseconds: float) -> None:
        # handler 标识。
        #
        # 举例："slow"
        self.id = handler_id

        # 只挂 turn:before，够测了。
        #
        # 举例：frozenset({Site.TURN_BEFORE})
        self.sites = frozenset({Site.TURN_BEFORE})

        # 维度段位。
        #
        # 举例：100
        self.priority = 100

        # 要推进多少毫秒。
        #
        # 举例：25
        self._clock = clock
        self._milliseconds = milliseconds

    async def run(self, ctx: SiteContext) -> None:
        """把时钟往前推一段，别的什么都不做。

        举例：await handler.run(ctx)
        """
        self._clock.advance(self._milliseconds / 1000)


def _timed_trace(clock: FakeClock):
    """造一个计价基准已经就位、时钟可控的 trace。

    举例：trace = _timed_trace(FakeClock())
    """
    trace = make_trace_context(ports=make_ports(clock=clock), state=make_runtime_state(turn_index=2))
    trace.state.started_monotonic = clock.monotonic()
    return trace


async def test_each_site_run_leaves_one_span() -> None:
    """位点跑完记一格：名字是位点取值，第几轮取当前轮数。"""
    clock = FakeClock()
    trace = _timed_trace(clock)
    pipeline = SitePipeline(
        HandlerRegistry([_SlowHandler("slow", clock, 25)])
    )

    await pipeline.run(Site.TURN_BEFORE, TurnBeforePayload(turn_index=2), trace)

    assert [span.name for span in trace.state.timeline] == ["turn:before"]
    span = trace.state.timeline[0]
    assert (span.kind, span.turn) == (SpanKind.SITE, 2)
    assert (round(span.start_ms, 3), round(span.elapsed_ms, 3)) == (0.0, 25.0)


async def test_a_span_is_relative_to_the_trace_start() -> None:
    """起点与时长都相对 trace 起点：先过两秒再跑，起点就是 2000ms。"""
    clock = FakeClock()
    trace = _timed_trace(clock)
    pipeline = SitePipeline(HandlerRegistry([_SlowHandler("slow", clock, 25)]))

    clock.advance(2.0)
    await pipeline.run(Site.TURN_BEFORE, TurnBeforePayload(turn_index=1), trace)

    (span,) = trace.state.timeline
    # 毫秒是浮点数：0.025 秒乘 1000 会差最后一位，比到三位小数就够。
    assert (round(span.start_ms, 3), round(span.elapsed_ms, 3)) == (2000.0, 25.0)


async def test_a_rejected_site_still_leaves_a_span() -> None:
    """被否决的那一段也花了时间，照样记。"""
    clock = FakeClock()
    trace = _timed_trace(clock)
    pipeline = SitePipeline(
        HandlerRegistry(
            [HandlerSpy("no", {Site.TOOL_BEFORE}, order=[], priority=100, result=False)]
        )
    )

    allowed = await pipeline.run(Site.TOOL_BEFORE, ToolBeforePayload(), trace)

    assert allowed is False
    assert [span.name for span in trace.state.timeline] == ["tool:before"]


async def test_tool_sites_record_the_tool_name() -> None:
    """工具阶段那一格要带工具名 —— 瀑布上"工具执行"一行得看出是哪个工具。"""
    clock = FakeClock()
    trace = _timed_trace(clock)
    pipeline = SitePipeline(HandlerRegistry([]))
    call = ToolCall(id="call_7f3a91", name="read", arguments={}, status=ToolCallStatus.PENDING)

    await pipeline.run(Site.TOOL_DURING, ToolDuringPayload(call=call), trace)

    assert trace.state.timeline[0].detail == "read"


async def test_other_sites_carry_no_detail() -> None:
    """不是工具阶段就没有说明，别凭空编一个。"""
    clock = FakeClock()
    trace = _timed_trace(clock)
    pipeline = SitePipeline(HandlerRegistry([]))

    await pipeline.run(Site.LLM_BEFORE, LlmBeforePayload(model="fake-model", messages=[]), trace)

    assert trace.state.timeline[0].detail == ""

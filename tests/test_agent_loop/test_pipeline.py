"""位点管线的行为测试。

覆盖六件事：执行顺序、否决短路、异常传播、位点隔离、数据通道、索引自检。
"""

from __future__ import annotations

import pytest

from agent_architecture.contracts import (
    FlushError,
    Site,
    SiteContext,
    ToolBeforePayload,
    TurnBeforePayload,
)
from agent_architecture.modules.agent_loop import HandlerRegistry, SitePipeline
from fakes import HandlerSpy, RecordingHandler, make_trace_context


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

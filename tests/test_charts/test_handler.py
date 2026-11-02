"""位点 8：chart_* 画出来的图落一条 chart 条目、发一条 ChartRendered。"""

from __future__ import annotations

from agent_frame.contracts import (
    AgentError,
    ChartEntryPayload,
    ChartKind,
    ChartRendered,
    SessionEntryType,
    Site,
    ToolAfterPayload,
    ToolCall,
    ToolCallStatus,
)
from agent_frame.modules.charts import PRIORITY_CHARTS, build_handlers
from agent_frame.modules.session import PRIORITY_TOOL_AFTER, Session
from fakes import (
    SequentialIds,
    make_ports,
    make_session_meta,
    make_site_context,
    make_trace_context,
)

# 一条干净的折线参数。
#
# 举例：_ARGS["x"] == ["08:00", "09:00"]
_ARGS: dict = {
    "title": "出口压力",
    "x": ["08:00", "09:00"],
    "series": [{"name": "压力", "values": [0.62, 0.71]}],
}


def _session() -> Session:
    """一个空会话。"""
    return Session(make_session_meta(), [], ids=SequentialIds())


def _ctx(
    session: Session,
    *,
    name: str = "chart_line",
    arguments: dict | None = None,
    error: AgentError | None = None,
):
    """造一个位点 8 的上下文，工具调用名与参数可换。

    举例：_ctx(session, name="chart_pie", arguments={...})
    """
    call = ToolCall(
        id="call_1",
        name=name,
        arguments=arguments if arguments is not None else _ARGS,
        status=ToolCallStatus.RUNNING,
    )
    payload = ToolAfterPayload(call=call, raw_result="ok", error=error)
    ports = make_ports()
    return make_site_context(
        Site.TOOL_AFTER, payload, trace=make_trace_context(session=session, ports=ports)
    )


def _seen(ctx) -> list[object]:
    """订阅 ChartRendered，把收到的都收进列表。

    举例：seen = _seen(ctx)
    """
    events: list[object] = []
    ctx.ports.events.subscribe(ChartRendered, events.append)
    return events


def test_the_handler_sits_before_the_tool_result_message() -> None:
    """排在 session 的工具结果消息（410）之前 —— 图先落盘，再声明这次调用有结果。"""
    assert PRIORITY_CHARTS == 405
    assert PRIORITY_CHARTS < PRIORITY_TOOL_AFTER
    assert [handler.priority for handler in build_handlers()] == [405]


async def test_a_chart_call_appends_an_entry_and_publishes() -> None:
    """一条完整链路：日志多一条 chart 条目，事件带着它的 entry_id 与 seq。"""
    session = _session()
    ctx = _ctx(session)
    seen = _seen(ctx)

    await build_handlers()[0].run(ctx)

    entries = session.entries()
    assert [entry.type for entry in entries] == [SessionEntryType.CHART]
    entry = entries[0]
    assert isinstance(entry.payload, ChartEntryPayload)
    chart = entry.payload.chart
    assert chart.chart_id == "c_call_1"
    assert chart.kind is ChartKind.LINE
    assert chart.series[0].values == (0.62, 0.71)

    assert len(seen) == 1
    event = seen[0]
    assert isinstance(event, ChartRendered)
    assert (event.entry_id, event.entry_seq) == (entry.entry_id, entry.seq)
    assert event.chart == chart


async def test_every_chart_tool_uses_its_own_kind() -> None:
    """图种由工具名定，不看参数里写了什么。"""
    for name, kind in (("chart_bar", ChartKind.BAR), ("chart_pie", ChartKind.PIE)):
        session = _session()
        await build_handlers()[0].run(_ctx(session, name=name))

        payload = session.entries()[0].payload
        assert isinstance(payload, ChartEntryPayload)
        assert payload.chart.kind is kind


async def test_a_box_call_is_written_as_a_box() -> None:
    """箱线图也归它管：工具名认出图种，样本在位点 8 摊成 5 个数一组。"""
    session = _session()
    args = {"x": ["1#", "2#"], "series": [{"name": "修复时长", "samples": [[1, 2], [3, 4]]}]}

    await build_handlers()[0].run(_ctx(session, name="chart_box", arguments=args))

    payload = session.entries()[0].payload
    assert isinstance(payload, ChartEntryPayload)
    assert payload.chart.kind is ChartKind.BOX
    assert len(payload.chart.series[0].values) == 10


async def test_another_tool_is_left_alone() -> None:
    """别的工具的结果不归它管。"""
    session = _session()
    ctx = _ctx(session, name="read", arguments={"path": "a.py"})
    seen = _seen(ctx)

    await build_handlers()[0].run(ctx)

    assert session.entries() == []
    assert seen == []


async def test_a_failed_call_is_not_written() -> None:
    """校验没过时模型已经拿到失败说明，别把坏图写进日志。"""
    session = _session()
    ctx = _ctx(
        session, arguments={**_ARGS, "x": ["08:00"]}, error=AgentError("x 至少要有 2 个标签")
    )
    seen = _seen(ctx)

    await build_handlers()[0].run(ctx)

    assert session.entries() == []
    assert seen == []

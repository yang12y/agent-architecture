"""位点 8：把 chart_* 画出来的图写进日志，并广播出去。

工具不写日志（它只校验参数，见 tools/chat_tools）；写日志、发事件在这一侧 ——
和 todo 在位点 8 写待办清单是同一种分工：**谁拥有这个事实，谁在位点上写**。

事件在落盘之后发，所以它带着 entry_id / entry_seq：前端能像消息一样按 id 去重、按 seq 定位。
"""

from __future__ import annotations

from typing import cast

from agent_frame.contracts import (
    KIND_OF_TOOL,
    ChartEntryPayload,
    ChartRendered,
    Handler,
    SessionEntryType,
    Site,
    SiteContext,
    ToolAfterPayload,
    parse_chart,
)

# 排在 session 的工具结果消息（410）**之前**：图的数据源是调用参数，重放一次就对；
# 但"图先落盘、再声明这次调用有结果"能让崩溃恢复时两边要么都有、要么都没有 ——
# 顺序反过来，恢复会看到"调用已成功"却没有图，而那条调用不会再跑。
# 也早于 hook（500）与落盘屏障（900）—— 屏障会把这一条一起推上盘。
#
# 举例：405
PRIORITY_CHARTS = 405


class _ChartHandler:
    """认领自己那一条工具调用的结果。一个实例只挂位点 8。

    举例：_ChartHandler()
    """

    def __init__(self) -> None:
        # handler 唯一标识。
        #
        # 举例："charts.tool_after"
        self.id = "charts.tool_after"

        # 只挂位点 8：位点 7 才是执行，位点 6 还没跑。
        #
        # 举例：frozenset({Site.TOOL_AFTER})
        self.sites: frozenset[Site] = frozenset({Site.TOOL_AFTER})

        # 见上面的 PRIORITY_CHARTS。
        #
        # 举例：405
        self.priority = PRIORITY_CHARTS

    async def run(self, ctx: SiteContext) -> bool | None:
        """是 chart_* 且没失败，就写一条 chart 条目再发一条图表事件。

        举例：await handler.run(ctx) -> 日志多一条 chart 条目，事件总线多一条 ChartRendered
        """
        payload = cast(ToolAfterPayload, ctx.payload)
        call = payload.call
        if call is None:
            return None
        kind = KIND_OF_TOOL.get(call.name)
        if kind is None or payload.error is not None:
            # 不是画图工具，或者校验没过：模型已经拿到失败说明，别把坏图写进日志。
            return None

        chart = parse_chart(call.arguments, tool_call_id=call.id, kind=kind)
        entry = ctx.session.append_entry(
            trace_id=ctx.state.trace_id,
            type=SessionEntryType.CHART,
            payload=ChartEntryPayload(chart=chart),
            now=ctx.ports.clock.now(),
        )
        ctx.ports.events.publish(
            ChartRendered(
                session_id=ctx.session.meta.session_id,
                trace_id=ctx.state.trace_id,
                entry_id=entry.entry_id,
                entry_seq=entry.seq,
                chart=chart,
            )
        )
        return None


def build_handlers() -> tuple[Handler, ...]:
    """造出 charts 要注册的 handler，交给装配根。

    只有一个：位点 8 的那一个。工具本身由 composition 登记进工具表。

    举例：registry = HandlerRegistry(build_handlers())
    """
    return (_ChartHandler(),)

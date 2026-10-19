"""位点 7（tool:during）的 runtime_state 操作：把这条调用标记成执行中。

置位必须在真正执行**之前**：`RUNNING` 的含义是"已派发，从这一刻起副作用可能已经
发生、结果未知"，晚了就没这个含义了。
"""

from __future__ import annotations

from typing import cast

from agent_architecture.contracts import SiteContext, ToolCallStatus, ToolDuringPayload


def apply(ctx: SiteContext) -> None:
    """把正在执行的这条调用置为 `RUNNING`。

    举例：apply(ctx) -> payload.call.status is ToolCallStatus.RUNNING
    """
    payload = cast(ToolDuringPayload, ctx.payload)

    # call 是可空的（payload 的默认值就是 None），真跑起来时内核一定会填。
    if payload.call is not None:
        payload.call.status = ToolCallStatus.RUNNING

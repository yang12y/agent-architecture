"""轨迹时间轴：一次 trace 拆成一条瀑布，给「这个项目自己的自画像」。

**纯函数，从日志现算** —— 每一格都是 trace_end 条目里带来的（位点管线记位点、内核记模型调用），
这里只负责配对起点与结尾、按时间排好。正在跑的那次还没收尾，没有时间轴。

只有收过尾的 trace 才有；最新的一批排在前面（面板一打开就想看刚才那次）。
"""

from __future__ import annotations

from collections.abc import Sequence

from agent_frame.contracts import (
    SessionEntry,
    SessionEntryType,
    TraceEndEntryPayload,
    TraceStartEntryPayload,
)

from .schemas import TraceOut

# 一次给多少条 trace。瀑布面板要的是"刚跑完的那几次",不是整部历史。
#
# 举例：20
TRACE_MAX = 20


def trace_outlines(entries: Sequence[SessionEntry], *, limit: int = TRACE_MAX) -> list[TraceOut]:
    """把日志折成一条条 trace（最新的在前）。

    trace_start 提供起点与发起时刻，trace_end 提供结局、总耗时与各格明细。
    没收尾的那次 trace 只有前者，不出现 —— 它的耗时还没有答案。

    举例：trace_outlines(entries) -> [TraceOut(trace_id="trace_9c8b7a", duration_ms=8421.5, ...)]
    """
    starts: dict[str, SessionEntry] = {}
    found: list[TraceOut] = []

    for entry in entries:
        if entry.type is SessionEntryType.TRACE_START and isinstance(
            entry.payload, TraceStartEntryPayload
        ):
            starts[entry.trace_id] = entry
        elif entry.type is SessionEntryType.TRACE_END and isinstance(
            entry.payload, TraceEndEntryPayload
        ):
            begin = starts.get(entry.trace_id)
            found.append(
                TraceOut.from_entry(
                    entry,
                    started_at=begin.timestamp if begin is not None else entry.timestamp,
                )
            )

    return list(reversed(found))[:limit]

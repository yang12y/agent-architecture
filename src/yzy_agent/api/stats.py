"""会话级的几个数字：从日志数出来，不另存计数器。

**没有计数器字段**，因为计数器一旦落盘就有了第二个真相源 —— 它和日志对不上时，
没有任何办法判断该信谁。所以这三个数要么当场数得出来，要么在收尾那一刻已经写进日志。
"""

from __future__ import annotations

from collections.abc import Sequence

from yzy_agent.contracts import SessionEntry, SessionEntryType, TraceEndEntryPayload

from .schemas import SessionStatsOut


def session_stats(entries: Sequence[SessionEntry], *, max_tokens: int) -> SessionStatsOut:
    """数出这条会话的战绩。

    三个数的口径：
        1. traces —— `trace_start` 条目数（已经开始的，含还没收尾的那次）；
        2. turns —— 已收尾 trace 的 `turns` 之和，正在跑的那次要等它结束才进来；
        3. context_tokens —— **最后一次有记录的值**。崩溃后补出来的那条 trace_end 记的是 0，
           不拿它覆盖上一次真实的占用，否则界面上的圈会在恢复之后突然空掉。

    举例：session_stats(live.session.entries(), max_tokens=128000)
          -> SessionStatsOut(traces=3, turns=11, context_tokens=42815, max_tokens=128000)
    """
    traces = 0
    turns = 0
    context_tokens = 0
    for entry in entries:
        if entry.type is SessionEntryType.TRACE_START:
            traces += 1
        elif entry.type is SessionEntryType.TRACE_END and isinstance(
            entry.payload, TraceEndEntryPayload
        ):
            turns += entry.payload.turns
            if entry.payload.context_tokens:
                context_tokens = entry.payload.context_tokens

    return SessionStatsOut(
        traces=traces,
        turns=turns,
        context_tokens=context_tokens,
        max_tokens=max_tokens,
    )

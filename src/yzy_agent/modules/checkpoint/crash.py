"""崩溃现场的判定：耐久日志的末尾属于哪一种尾部。

崩溃点可以发生在任何两条条目之间，但**恢复只关心尾部是哪一类**，因为要做的事只有两件：
补悬空工具调用的结果、补一条 trace_end。按尾部归类就够，不必枚举崩溃点。

三种尾部：

    DANGLING   最后一条 trace_start 之后没有 trace_end —— 这次执行根本没走完位点 10
    ABORTED    末尾是 trace_end(aborted) —— 位点 10 正常走到了，但工具调用被故意留成悬空
    CLOSED     末尾是正常收尾的 trace_end —— 什么都不用做

后两种的区别就是 D6 里那句判据的另一半：**只看 aborted，不看 failed / budget_exceeded**，
那两个是位点 10 的正常收尾，留不下悬空调用。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from yzy_agent.contracts import (
    SessionEntry,
    SessionEntryType,
    TraceEndEntryPayload,
    TraceStatus,
)


class TailKind(StrEnum):
    """耐久日志末尾属于哪一类。"""

    # 最后一条 trace_start 之后没有 trace_end，这次执行没走完位点 10。
    DANGLING = "dangling"

    # 末尾是 trace_end(aborted)：位点 10 走到了，但工具调用留成了悬空。
    ABORTED = "aborted"

    # 末尾是正常收尾的 trace_end，或者日志本来就是空的。
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class Tail:
    """尾部判定结果。

    举例：classify_tail(entries) -> Tail(kind=TailKind.DANGLING, trace_id="trace_x")
    """

    # 属于哪一类。
    #
    # 举例：TailKind.DANGLING
    kind: TailKind

    # 要配平的是哪一次 trace；不需要配平时为 None。
    #
    # 举例："trace_9c8b7a"
    trace_id: str | None

    @property
    def needs_repair(self) -> bool:
        """要不要走配平。CLOSED 之外都要。

        举例：classify_tail(entries).needs_repair -> True
        """
        return self.kind is not TailKind.CLOSED


def open_trace_id(entries: Sequence[SessionEntry]) -> str | None:
    """找出"开了但没收"的那次 trace：最后一条 trace_start 之后没有 trace_end。

    从末尾往回扫：先碰到 trace_end 就说明没有开着的 trace；先碰到 trace_start 就是它。

    举例：open_trace_id(entries) -> "trace_9c8b7a"；都已收尾时 -> None
    """
    for entry in reversed(entries):
        if entry.type is SessionEntryType.TRACE_END:
            return None
        if entry.type is SessionEntryType.TRACE_START:
            return entry.trace_id
    return None


def classify_tail(entries: Sequence[SessionEntry]) -> Tail:
    """判定耐久日志的尾部，并给出要配平的那次 trace。

    举例：classify_tail(entries) -> Tail(kind=TailKind.ABORTED, trace_id="trace_x")
    """
    opened = open_trace_id(entries)
    if opened is not None:
        return Tail(kind=TailKind.DANGLING, trace_id=opened)

    last_end = _last_trace_end(entries)
    if last_end is None:
        return Tail(kind=TailKind.CLOSED, trace_id=None)
    if last_end.payload.status is TraceStatus.ABORTED:  # type: ignore[union-attr]
        return Tail(kind=TailKind.ABORTED, trace_id=last_end.trace_id)
    return Tail(kind=TailKind.CLOSED, trace_id=None)


def _last_trace_end(entries: Sequence[SessionEntry]) -> SessionEntry | None:
    """日志里最后一条 trace_end；一条都没有返回 None。

    举例：_last_trace_end(entries) -> SessionEntry(type=TRACE_END, ...)
    """
    for entry in reversed(entries):
        if entry.type is SessionEntryType.TRACE_END and isinstance(
            entry.payload, TraceEndEntryPayload
        ):
            return entry
    return None

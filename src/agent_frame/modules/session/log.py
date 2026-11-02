"""会话日志：只追加，不改写、不删除。

它管三件事：给新条目分配 **seq**（会话内全局递增，决定重放顺序）与 **entry_id**、
按 id 找条目、取某条之后的全部条目。**不管投影，也不管落盘** —— 那是 projection 与 checkpoint 的事。

分叉点的切法也在这里（`fork_prefix`）：那是对日志结构的一次查询，不该散到装配层去。

seq 从已有条目的最后一条往后续，不从头开始：从磁盘恢复出来的日志接着写，顺序才不会乱。
"""

from __future__ import annotations

from collections.abc import Sequence

from agent_frame.contracts import (
    IdGenerator,
    Message,
    MessageRole,
    SessionEntry,
    SessionEntryPayload,
    SessionEntryType,
    Timestamp,
)


class SessionLog:
    """一个会话的内存日志。

    举例：log = SessionLog(ids=UuidGenerator())
          log.append(trace_id="trace_1", type=SessionEntryType.MESSAGE, payload=msg, now=now)
    """

    def __init__(self, entries: Sequence[SessionEntry] = (), *, ids: IdGenerator) -> None:
        """由已有条目建日志；空序列表示全新会话。

        举例：SessionLog([], ids=UuidGenerator())、SessionLog(loaded_entries, ids=gen)
        """
        # 条目本体，按 seq 升序。**只由 append 追加**，外部拿到的永远是快照。
        #
        # 举例：[SessionEntry(entry_id="e_4f2a91bc0d3e", seq=1, ...)]
        self._entries: list[SessionEntry] = list(entries)

        # entry_id 的来源。它也是 trace_id、tool_call_id 的来源，统一走端口。
        #
        # 举例：UuidGenerator()
        self._ids = ids

        # 下一条条目的 seq。已有条目时从最后一条续，否则从 1 开始。
        #
        # 举例：42
        self._next_seq = (self._entries[-1].seq + 1) if self._entries else 1

    def entries(self) -> list[SessionEntry]:
        """全部条目的快照，按 seq 升序。

        给的是新列表，调用方改它不会影响日志本身。

        举例：log.entries() -> [SessionEntry(seq=1, ...), SessionEntry(seq=2, ...)]
        """
        return list(self._entries)

    def append(
        self,
        *,
        trace_id: str,
        type: SessionEntryType,
        payload: SessionEntryPayload,
        now: Timestamp,
    ) -> SessionEntry:
        """造一条条目并追加到末尾，返回它。

        seq 与 entry_id 都在这里分配，调用方不用关心。

        举例：entry = log.append(trace_id="trace_1", type=SessionEntryType.MESSAGE,
                                payload=msg, now=now)
        """
        entry = SessionEntry(
            entry_id=self._ids.new_id("e"),
            trace_id=trace_id,
            seq=self._next_seq,
            timestamp=now,
            type=type,
            payload=payload,
        )
        self._entries.append(entry)
        self._next_seq += 1
        return entry

    def add(self, entry: SessionEntry) -> None:
        """追加一条**已经造好**的条目（恢复、分叉这类场景用）。

        它不会重排或改写任何字段；只把 next_seq 顶到这条之后，免得后面自动分配的
        seq 跟它撞上。

        举例：log.add(SessionEntry(entry_id="e_1", seq=1, ...))
        """
        self._entries.append(entry)
        self._next_seq = max(self._next_seq, entry.seq + 1)

    def find(self, entry_id: str) -> SessionEntry | None:
        """按 entry_id 找条目；找不到返回 None。

        作用：context_cursor 与 compaction.entry_ids 都是按 id 引用条目的。

        举例：log.find("e_4f2a91bc0d3e") -> SessionEntry(seq=42, ...)
        """
        for entry in self._entries:
            if entry.entry_id == entry_id:
                return entry
        return None

    def last(self, type: SessionEntryType) -> SessionEntry | None:
        """取某一类条目里 seq 最大的那条；一条都没有时返回 None。

        作用：投影要找"最近一次 compaction"，恢复要找"末尾有没有 trace_end"。

        举例：log.last(SessionEntryType.COMPACTION) -> SessionEntry(seq=88, ...)
        """
        for entry in reversed(self._entries):
            if entry.type is type:
                return entry
        return None

    def index_of(self, entry_id: str) -> int | None:
        """条目在日志里的下标；找不到返回 None。

        举例：log.index_of("e_4f2a91bc0d3e") -> 41
        """
        for index, entry in enumerate(self._entries):
            if entry.entry_id == entry_id:
                return index
        return None

    def __len__(self) -> int:
        """条目条数。

        举例：len(log) -> 42
        """
        return len(self._entries)


def fork_prefix(entries: Sequence[SessionEntry], turn_seq: int) -> list[SessionEntry] | None:
    """切出"分叉到第 turn_seq 那一轮结束"的前缀：从第一条到这一轮的 trace_end（含）。

    **必须以 trace_end 收尾。** 下一轮的 trace_start 是跟着它的用户消息一起写的，所以
    "截到下一轮之前"那种切法会留下一条没有收尾的 trace_start，恢复侧会把它当成
    "崩在 trace 中途"补一条假的 trace_end。切在 trace_end 上就什么都不用管。

    turn_seq 不是用户消息、或者这一轮还没收尾（没有 trace_end）时返回 None。

    举例：fork_prefix(entries, 42) -> [SessionEntry(seq=1, ...), ..., SessionEntry(seq=57, ...)]
    """
    start = next((entry for entry in entries if entry.seq == turn_seq), None)
    if start is None or not _is_user_message(start):
        return None

    end = next(
        (
            entry
            for entry in entries
            if entry.seq >= turn_seq and entry.type is SessionEntryType.TRACE_END
        ),
        None,
    )
    if end is None:
        return None
    return [entry for entry in entries if entry.seq <= end.seq]


def _is_user_message(entry: SessionEntry) -> bool:
    """这条条目是不是"用户说的一句话" —— 分叉点只认这个。

    举例：_is_user_message(SessionEntry(type=MESSAGE, payload=user_message("在吗"))) -> True
    """
    return (
        entry.type is SessionEntryType.MESSAGE
        and isinstance(entry.payload, Message)
        and entry.payload.role is MessageRole.USER
    )

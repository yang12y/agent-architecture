"""只读的会话视图：对一段 entries 做查询，**绝不写回**。

历史列表、审计这类只读观察方用它；S3 的恢复侧也先用它看一眼，再决定写不写。

它和内存 Session 的区别只有一条：**没有游标、没有草稿槽、没有任何可变状态**。
投影规则完全共用 projection.py —— 两个视图看到的 messages 必须一模一样，
否则"只读方看到的"和"模型看到的"就会分叉。
"""

from __future__ import annotations

from collections.abc import Sequence

from agent_architecture.contracts import Message, SessionEntry, SessionEntryType

from .projection import project


class SessionReader:
    """一段日志的只读视图。

    举例：view = SessionReader(entries)
          view.messages() -> [Message(role=MessageRole.USER, ...)]
          view.is_closed() -> True
    """

    def __init__(self, entries: Sequence[SessionEntry]) -> None:
        """按给定条目建视图；条目按引用持有，本类不会改它们中的任何一个。

        举例：SessionReader(store.load("sess_ab12")[1])
        """
        # 被观察的条目。
        #
        # 举例：[SessionEntry(entry_id="e_4f2a91bc0d3e", seq=1, ...)]
        self._entries: tuple[SessionEntry, ...] = tuple(entries)

    def entries(self) -> list[SessionEntry]:
        """全部条目，按 seq 升序。

        举例：view.entries() -> [SessionEntry(seq=1, ...)]
        """
        return list(self._entries)

    def messages(self, *, draft_text: str = "") -> list[Message]:
        """套用与模型完全相同的投影规则算出 messages。

        draft_text 由调用方给 —— 草稿槽不在日志里，只读视图看不见它。

        举例：view.messages() -> [Message(role=MessageRole.TOOL, ...)]
        """
        return project(self._entries, draft_text=draft_text)

    def find(self, entry_id: str) -> SessionEntry | None:
        """按 entry_id 找条目；找不到返回 None。

        举例：view.find("e_4f2a91bc0d3e") -> SessionEntry(seq=42, ...)
        """
        for entry in self._entries:
            if entry.entry_id == entry_id:
                return entry
        return None

    def last(self, type: SessionEntryType) -> SessionEntry | None:
        """取某一类条目里 seq 最大的那条；没有就返回 None。

        举例：view.last(SessionEntryType.TRACE_END) -> SessionEntry(seq=97, ...)
        """
        for entry in reversed(self._entries):
            if entry.type is type:
                return entry
        return None

    def last_seq(self) -> int:
        """最大 seq；空日志返回 0。恢复侧靠它决定续写从哪开始。

        举例：view.last_seq() -> 97
        """
        return self._entries[-1].seq if self._entries else 0

    def is_closed(self) -> bool:
        """上一次执行是不是正常收过尾 —— 只看末尾那条是不是 trace_end。

        作用：恢复时判断"日志结束得干不干净"。末尾没有 trace_end 说明进程是在执行中途没的。
        注意它只看**最后一条**：中间那些 trace_end 属于更早的、已经收过尾的 trace。

        举例：view.is_closed() -> False（说明要做恢复处理）
        """
        return bool(self._entries) and self._entries[-1].type is SessionEntryType.TRACE_END

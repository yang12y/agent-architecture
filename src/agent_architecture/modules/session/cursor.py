"""上下文游标：**投影的起点**。

游标只有一个含义 —— 投影从哪里开始取：

    空串    → 从头，取全部条目
    否则    → 取游标指向的那一条（**含它**）直到末尾

它指向的一定是**最近一次 compaction**。所以有压缩时投影的第一条就是那条压缩条目，
再由 projection.py 展开成"摘要 + entry_ids 引用的消息 + 它之后的消息"。

**游标不是"每轮前进的读指针"。** 它只在重建时算一次，之后就不动了。重建发生在三处：

    Session 构造 / 崩溃恢复 / 每次压缩之后

"已经并到哪了"是另一回事 —— 那是内存里的增量记账，不进 meta、不落盘、随时可重算。
两件事分开，`context_cursor` 才能一直表示"投影起点"。
"""

from __future__ import annotations

from collections.abc import Sequence

from agent_architecture.contracts import Message, SessionEntry, SessionEntryType

# 投影从头的游标值。
#
# 举例：CURSOR_START == ""
CURSOR_START = ""


def projection_start(entries: Sequence[SessionEntry]) -> str:
    """算出投影该从哪一条开始 —— 最近一次 compaction 的 entry_id；没有压缩就是空串。

    重建游标时调它：Session 构造、崩溃恢复、每次压缩之后。

    举例：projection_start(entries) -> "e_0042"；没有 compaction 时 -> ""
    """
    for entry in reversed(entries):
        if entry.type is SessionEntryType.COMPACTION:
            return entry.entry_id
    return CURSOR_START


def entries_from(entries: Sequence[SessionEntry], cursor_id: str) -> list[SessionEntry]:
    """取投影范围内的条目：**从游标指向的那一条开始（含它）**，直到末尾。

    游标为空串时返回全部。游标在本日志里找不到时也返回全部 —— 找不到只可能是拿着别的
    会话的游标，从头来是唯一不会漏内容的做法。

    举例：entries_from(entries, "e_0042") -> [SessionEntry(seq=42, ...), ...]
    """
    if not cursor_id:
        return list(entries)
    for index, entry in enumerate(entries):
        if entry.entry_id == cursor_id:
            return list(entries[index:])
    return list(entries)


def merge(context: list[Message], entries: Sequence[SessionEntry]) -> None:
    """把一批条目的 message 并进物化上下文。

    只有 message 类型会变成上下文里的一条消息。其余类型本身不进 messages：
    边界标记与 tool_start 不是消息，而 compaction 换的是投影起点，得走重建那条路。

    举例：merge(context, entries[3:5]) -> context 多出两条消息
    """
    context.extend(
        entry.payload
        for entry in entries
        if entry.type is SessionEntryType.MESSAGE and isinstance(entry.payload, Message)
    )

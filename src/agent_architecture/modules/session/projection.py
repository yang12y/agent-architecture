"""上下文投影：从 Session 日志算出"这一次要发给模型的 messages"。

它是**确定性视图**：同一份 entries 永远算出同一份 messages，不含任何隐藏状态。
增量并入（见 cursor.py）只是它的加速路径，结果必须与全量重算逐条相等 —— 这条由测试钉住。

三条来源，从**最近一次 compaction** 起：
    ① 摘要本身，作为一条消息；
    ② 它 entry_ids 指向、且仍然存在的 message 条目（压缩时判定要保留的那几条）；
    ③ 它之后的所有 message 条目。
没有 compaction 时，从第一条 message 起。

trace_start / trace_end / tool_start **不进 messages**：它们是边界标记，
内容要么推得出来（耗时、turn 数），要么已经体现在别的条目里（工具结果就是一条 message）。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from agent_architecture.contracts import (
    CompactionEntryPayload,
    Message,
    MessageRole,
    SessionEntry,
    SessionEntryType,
    TextPart,
)

from .cursor import entries_from, projection_start

# 摘要进上下文时扮演哪个角色。
#
# 为什么只能是 USER：MessageRole 只有 user / assistant / tool 三个取值（契约与上游一致，
# 没有 system）。剩下两个里不能选 assistant —— 那会让模型以为这段话是自己说过的，
# 进而对用户说"如上所述"，而用户从没看到过这段摘要。
SUMMARY_ROLE = MessageRole.USER

# 摘要消息的开头一句。摘要必须能独立阅读，这句话负责交代它的来历。
#
# 举例："以下是之前对话的摘要，请基于它继续："
SUMMARY_PREFIX = "以下是之前对话的摘要，请基于它继续："

# 草稿并入时的开头一句。半截输出直接接在上下文末尾会显得像模型在自言自语。
#
# 举例："上一轮输出被打断，以下是已产生的部分："
DRAFT_PREFIX = "上一轮输出被打断，以下是已产生的部分："


def project(entries: Sequence[SessionEntry], *, draft_text: str = "") -> list[Message]:
    """把整份日志投影成 messages。**全量重算**，用于 trace 初始化、压缩之后、进程重启恢复。

    举例：project(entries) -> [Message(role=MessageRole.USER, ...), ...]
    """
    compaction, sources = _relevant_entries(entries)
    messages = [_summary_message(compaction)] if compaction is not None else []
    messages.extend(_messages_of(sources))

    if draft_text:
        messages.append(draft_message(draft_text))
    return messages


def project_entries(entries: Sequence[SessionEntry]) -> list[SessionEntry]:
    """当前投影里那些**有条目**的消息，按出现顺序。

    摘要消息是合成的、没有条目，所以它不在结果里。压缩靠它拿到"保留哪几条"的 entry_id。

    举例：project_entries(entries) -> [SessionEntry(type=MESSAGE, ...)]
    """
    return _relevant_entries(entries)[1]


def _relevant_entries(
    entries: Sequence[SessionEntry],
) -> tuple[SessionEntry | None, list[SessionEntry]]:
    """算出投影的来源：最近一次 compaction（没有就是 None）+ 范围内的消息条目。

    举例：_relevant_entries(entries) -> (compaction_entry, [SessionEntry(...)])
    """
    relevant = entries_from(entries, projection_start(entries))

    # 投影起点是一条 compaction 时，从它展开三条来源；否则它就是普通条目，全取 message。
    if relevant and relevant[0].type is SessionEntryType.COMPACTION:
        compaction = relevant[0]
        return compaction, [*_kept_entries(entries, compaction), *message_entries(relevant[1:])]
    return None, message_entries(relevant)


def message_entries(entries: Iterable[SessionEntry]) -> list[SessionEntry]:
    """按顺序取出 message 类型的条目。

    顺手用 isinstance 兜了一下：条目的 type 说自己是一条消息，payload 就该真的是 Message。
    不匹配的条目宁可漏掉，也不能让一个别的 payload 混进 messages。

    历史接口要的是条目（它要 entry_id 给前端做列表 key），投影要的是 payload，
    所以这个筛选单独提出来，两边共用同一条规则。

    举例：message_entries(entries) -> [SessionEntry(type=MESSAGE, ...)]
    """
    return [
        entry
        for entry in entries
        if entry.type is SessionEntryType.MESSAGE and isinstance(entry.payload, Message)
    ]


def _messages_of(entries: Iterable[SessionEntry]) -> list[Message]:
    """把**已经筛过**的 message 条目摊成 payload 列表。

    举例：_messages_of(entries) -> [Message(role=MessageRole.TOOL, ...)]
    """
    return [entry.payload for entry in entries]  # type: ignore[misc]


def _kept_entries(entries: Sequence[SessionEntry], compaction: SessionEntry) -> list[SessionEntry]:
    """取出压缩时判定要保留的那几条 message 条目，按 seq 排序。

    entry_ids 只引用、不内嵌，所以这里要回日志里查。查不到（或已经不是 message）的直接跳过 ——
    日志是 append-only，正常不会出现，出现了也不该让投影整个失败。

    举例：_kept_entries(entries, compaction) -> [SessionEntry(type=MESSAGE, ...)]
    """
    if not isinstance(compaction.payload, CompactionEntryPayload):
        return []

    by_id = {entry.entry_id: entry for entry in entries}
    kept = [by_id[entry_id] for entry_id in compaction.payload.entry_ids if entry_id in by_id]
    kept.sort(key=lambda entry: entry.seq)
    return message_entries(kept)


def _summary_message(compaction: SessionEntry) -> Message:
    """把摘要包成一条消息。

    举例：_summary_message(e) -> Message(role=MessageRole.USER,
                                        content=[TextPart(text="以下是之前对话的摘要……")])
    """
    payload = compaction.payload
    summary = payload.summary if isinstance(payload, CompactionEntryPayload) else ""
    return Message(
        role=SUMMARY_ROLE,
        content=[TextPart(text=f"{SUMMARY_PREFIX}\n\n{summary}")],
    )


def draft_message(draft_text: str) -> Message:
    """把草稿槽里的半截输出包成一条 assistant 消息，接在投影末尾。

    它单独暴露出来，是因为草稿**不在日志里**：内存 Session 需要在读 messages 的时候
    临时把它叠上去，而它不属于 project() 的输入。

    举例：draft_message("我来帮你读") -> Message(role=MessageRole.ASSISTANT, ...)
    """
    return Message(
        role=MessageRole.ASSISTANT,
        content=[TextPart(text=f"{DRAFT_PREFIX}\n\n{draft_text}")],
    )

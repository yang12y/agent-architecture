"""只读视图的测试。"""

from __future__ import annotations

from fakes import assistant_message, make_entry, tool_message, user_message
from yzy_agent.contracts import CompactionEntryPayload, SessionEntryType
from yzy_agent.modules.session import SessionReader, project


def _entries():
    """造一段有代表性的日志。

    举例：_entries() -> [SessionEntry(seq=1, ...), ...]
    """
    return [
        make_entry(1, user_message("帮我读 a.py")),
        make_entry(2, assistant_message("好的")),
        make_entry(3, tool_message("文件内容")),
    ]


def test_messages_match_the_full_projection() -> None:
    """只读视图与模型看到的必须一模一样，否则两边会分叉。"""
    entries = _entries()

    assert SessionReader(entries).messages() == project(entries)


def test_it_does_not_write_back() -> None:
    """只读就是只读：查完之后条目一个都不能变。"""
    entries = _entries()
    before = [entry.payload for entry in entries]

    reader = SessionReader(entries)
    reader.messages()
    reader.last(SessionEntryType.MESSAGE)
    reader.find("e_0001")

    assert [entry.payload for entry in entries] == before
    assert len(entries) == 3


def test_find_and_last() -> None:
    """按 id 找与按类型找。"""
    reader = SessionReader(_entries())

    assert reader.find("e_0002").seq == 2  # type: ignore[union-attr]
    assert reader.find("e_不存在") is None
    assert reader.last(SessionEntryType.MESSAGE).seq == 3  # type: ignore[union-attr]
    assert reader.last(SessionEntryType.COMPACTION) is None


def test_last_seq_of_an_empty_log_is_zero() -> None:
    """空日志的最大 seq 是 0 —— 恢复侧靠它决定续写从哪开始。"""
    assert SessionReader([]).last_seq() == 0
    assert SessionReader(_entries()).last_seq() == 3


def test_is_closed_looks_only_at_the_tail() -> None:
    """判断"上次收尾干不干净"只看最后一条是不是 trace_end。"""
    from yzy_agent.contracts import TraceEndEntryPayload, TraceStatus

    open_log = _entries()
    closed_log = [
        *_entries(),
        make_entry(4, TraceEndEntryPayload(status=TraceStatus.COMPLETED, reason=None)),
    ]

    assert SessionReader([]).is_closed() is False
    assert SessionReader(open_log).is_closed() is False
    assert SessionReader(closed_log).is_closed() is True


def test_compaction_is_respected() -> None:
    """只读视图套用的是同一套投影规则，压缩之后也少不掉。"""
    entries = [
        make_entry(1, user_message("原始的话")),
        make_entry(2, CompactionEntryPayload(summary="摘要", entry_ids=[])),
    ]

    messages = SessionReader(entries).messages()

    assert len(messages) == 1
    assert "摘要" in messages[0].content[0].text  # type: ignore[union-attr]

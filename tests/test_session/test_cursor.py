"""游标与投影起点的测试。"""

from __future__ import annotations

from agent_architecture.contracts import CompactionEntryPayload
from agent_architecture.modules.session import cursor
from fakes import assistant_message, make_entry, user_message


def _entries():
    """造三段能区分开的日志。

    举例：entries = _entries(); entries[1].entry_id -> "e_0002"
    """
    return [
        make_entry(1, user_message("第一条")),
        make_entry(2, assistant_message("第二条")),
        make_entry(3, user_message("第三条")),
    ]


def test_projection_starts_from_the_top_when_there_is_no_compaction() -> None:
    """没有压缩时投影从第一条开始 —— 游标是空串。"""
    assert cursor.projection_start(_entries()) == cursor.CURSOR_START


def test_projection_starts_at_the_latest_compaction() -> None:
    """有压缩时游标指向**最近一次** compaction，不是第一次。"""
    entries = [
        make_entry(1, user_message("旧")),
        make_entry(2, CompactionEntryPayload(summary="第一次摘要", entry_ids=[]), entry_id="e_c1"),
        make_entry(3, user_message("中间")),
        make_entry(4, CompactionEntryPayload(summary="第二次摘要", entry_ids=[]), entry_id="e_c2"),
    ]

    assert cursor.projection_start(entries) == "e_c2"


def test_entries_from_includes_the_cursor_entry_itself() -> None:
    """从游标开始取，**含游标那一条** —— 它的 summary 就是投影的第一条消息。"""
    entries = _entries()

    taken = cursor.entries_from(entries, "e_0002")

    assert [entry.seq for entry in taken] == [2, 3]


def test_empty_cursor_takes_everything() -> None:
    """游标为空 = 从头，取全部。"""
    entries = _entries()

    assert cursor.entries_from(entries, cursor.CURSOR_START) == entries


def test_unknown_cursor_falls_back_to_everything() -> None:
    """游标不属于这份日志时从头来 —— 这是唯一不会漏内容的做法。"""
    entries = _entries()

    assert cursor.entries_from(entries, "e_别的会话") == entries


def test_merge_only_takes_messages() -> None:
    """只有 message 进上下文；compaction 换的是投影起点，得走重建那条路。"""
    context: list = []
    entries = [
        make_entry(1, user_message("进来")),
        make_entry(2, CompactionEntryPayload(summary="摘要", entry_ids=[])),
    ]

    cursor.merge(context, entries)

    assert len(context) == 1


def test_merge_on_nothing_is_a_no_op() -> None:
    """空批次什么都不做。"""
    context: list = []

    cursor.merge(context, [])

    assert context == []

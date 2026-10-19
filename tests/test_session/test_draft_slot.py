"""草稿槽的测试。"""

from __future__ import annotations

from fakes import START
from yzy_agent.modules.session import DraftSlot


def _slot() -> DraftSlot:
    """造一个空槽。

    举例：_slot() -> DraftSlot("sess_test")
    """
    return DraftSlot("sess_test")


def test_empty_slot_reads_as_empty_string() -> None:
    """空槽的 text() 是空串，不是 None —— 调用方不用先判空。"""
    slot = _slot()

    assert slot.text() == ""
    assert slot.is_empty() is True


def test_append_accumulates_into_one_draft() -> None:
    """同一段输出的多次增量拼成一条草稿，trace_id 与 turn_index 取第一次的。"""
    slot = _slot()

    slot.append("我来", trace_id="trace_1", turn_index=3, now=START)
    slot.append("帮你读", trace_id="trace_1", turn_index=3, now=START)

    assert slot.text() == "我来帮你读"
    assert slot.snapshot().draft.trace_id == "trace_1"  # type: ignore[union-attr]
    assert slot.snapshot().draft.turn_index == 3  # type: ignore[union-attr]


def test_take_returns_the_draft_and_empties_the_slot() -> None:
    """取出即清空，否则同一段输出会被定稿两遍。"""
    slot = _slot()
    slot.append("我来帮你读", trace_id="trace_1", turn_index=3, now=START)

    draft = slot.take()

    assert draft is not None and draft.content == "我来帮你读"
    assert slot.text() == ""
    assert slot.is_empty() is True


def test_take_on_an_empty_slot_returns_none() -> None:
    """空槽取出来是 None，不报错。"""
    assert _slot().take() is None


def test_a_new_draft_after_take_belongs_to_the_new_trace() -> None:
    """上一段被取走之后再来的增量属于新的一段，不能接到旧草稿上。"""
    slot = _slot()
    slot.append("第一段", trace_id="trace_1", turn_index=1, now=START)
    slot.take()

    slot.append("第二段", trace_id="trace_2", turn_index=1, now=START)

    snapshot = slot.snapshot().draft
    assert snapshot is not None
    assert (snapshot.content, snapshot.trace_id) == ("第二段", "trace_2")


def test_snapshot_carries_the_session_id() -> None:
    """快照带上会话标识，便于别人看"哪个会话的槽里还有东西"。"""
    assert _slot().snapshot().session_id == "sess_test"

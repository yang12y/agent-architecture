"""内存 Session 聚合根的测试。"""

from __future__ import annotations

from agent_architecture.contracts import (
    CompactionEntryPayload,
    MessageRole,
    SessionEntryType,
    TraceEndEntryPayload,
    TraceStatus,
)
from agent_architecture.modules.session import Session, project
from fakes import (
    START,
    SequentialIds,
    assistant_message,
    make_entry,
    make_session_meta,
    user_message,
)


def _session(entries=(), session_id: str = "sess_test") -> Session:
    """造一个用固定 id 生成器的会话。

    举例：_session() -> Session(meta=..., entries=[], ids=SequentialIds())
    """
    return Session(make_session_meta(session_id), entries, ids=SequentialIds())


def test_a_new_session_has_nothing() -> None:
    """全新会话：没有条目、没有上下文、游标是空的。"""
    session = _session()

    assert session.entries() == []
    assert session.messages() == []
    assert session.cursor() == ""


def test_appending_does_not_merge_into_the_context() -> None:
    """追加不等于并入 —— 新条目要等下一次 messages() 才进上下文。"""
    session = _session()
    session.append_entry(
        trace_id="t", type=SessionEntryType.MESSAGE, payload=user_message("你好"), now=START
    )

    assert session.messages() == project(session.entries())


def test_the_cursor_is_the_projection_start_not_a_progress_marker() -> None:
    """没有压缩时投影从头开始，所以游标一直是空串 —— 它不随每轮并入前进。"""
    session = _session()
    session.append_entry(
        trace_id="t", type=SessionEntryType.MESSAGE, payload=user_message("你好"), now=START
    )

    session.messages()
    session.append_entry(
        trace_id="t", type=SessionEntryType.MESSAGE, payload=user_message("再来一条"), now=START
    )
    session.messages()

    assert session.cursor() == ""
    assert session.meta.context_cursor == ""


def test_the_cursor_points_at_the_latest_compaction_and_is_mirrored_to_meta() -> None:
    """压缩之后，游标指向那条 compaction —— 它就是投影的起点。"""
    session = _session()
    session.append_entry(
        trace_id="t", type=SessionEntryType.MESSAGE, payload=user_message("旧消息"), now=START
    )
    session.messages()
    entry = session.append_entry(
        trace_id="t",
        type=SessionEntryType.COMPACTION,
        payload=CompactionEntryPayload(summary="摘要", entry_ids=[]),
        now=START,
    )

    session.messages()

    assert session.cursor() == entry.entry_id
    assert session.meta.context_cursor == entry.entry_id


def test_incremental_merge_equals_a_full_recompute() -> None:
    """核心不变量：增量并入的结果必须与全量重算**逐条相等**。

    增量只是加速路径，不是另一套规则 —— 这条如果破了，"确定性视图"就没了。
    """
    session = _session()
    for index, text in enumerate(["一", "二", "三"], start=1):
        session.append_entry(
            trace_id="t", type=SessionEntryType.MESSAGE, payload=user_message(text), now=START
        )
        session.messages()

    assert session.messages() == project(session.entries())


def test_a_compaction_forces_a_full_rebuild() -> None:
    """压缩换了投影起点，增量路径作废：旧 message 该被摘要取代。"""
    entries = [
        make_entry(1, user_message("原始的话")),
        make_entry(2, assistant_message("原始的回答")),
    ]
    session = _session(entries)
    assert len(session.messages()) == 2

    session.append_entry(
        trace_id="t",
        type=SessionEntryType.COMPACTION,
        payload=CompactionEntryPayload(summary="摘要", entry_ids=[]),
        now=START,
    )

    messages = session.messages()
    assert len(messages) == 1
    assert "摘要" in messages[0].content[0].text  # type: ignore[union-attr]


def test_the_draft_is_layer_on_at_read_time_and_does_not_pile_up() -> None:
    """草稿是读时叠加的：连调两次 messages() 不该出现两条草稿消息。"""
    session = _session()
    session.append_entry(
        trace_id="t", type=SessionEntryType.MESSAGE, payload=user_message("帮我读 a.py"), now=START
    )
    session.append_draft("我来帮你", trace_id="t", turn_index=1, now=START)

    first = session.messages()
    second = session.messages()

    assert len(first) == len(second) == 2
    assert second[-1].role is MessageRole.ASSISTANT


def test_taking_the_draft_removes_it_from_the_next_read() -> None:
    """定稿之后草稿就不该再出现在上下文里。"""
    session = _session()
    session.append_draft("半截输出", trace_id="t", turn_index=1, now=START)
    assert len(session.messages()) == 1

    session.take_draft()

    assert session.messages() == []


def test_a_resumed_session_projects_the_same_as_a_fresh_one() -> None:
    """恢复出来的会话与新建的走同一条路：构造时就全量投影一遍。"""
    entries = [
        make_entry(1, user_message("历史消息")),
        make_entry(2, assistant_message("历史回答")),
    ]

    resumed = _session(entries)

    assert resumed.messages() == project(entries)
    assert resumed.cursor() == ""  # 没有压缩，投影从头开始


def test_boundary_entries_do_not_enter_the_context() -> None:
    """trace_end 这类边界条目只标记边界，不进上下文，也不动游标。"""
    session = _session()
    session.append_entry(
        trace_id="t",
        type=SessionEntryType.TRACE_END,
        payload=TraceEndEntryPayload(status=TraceStatus.COMPLETED, reason=None),
        now=START,
    )

    assert session.messages() == []
    assert session.cursor() == ""


def test_set_title() -> None:
    """标题是给列表展示用的，可以后补。"""
    session = _session()

    session.set_title("重构 session 模块")

    assert session.meta.title == "重构 session 模块"

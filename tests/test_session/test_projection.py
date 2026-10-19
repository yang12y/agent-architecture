"""上下文投影的测试：三条来源规则、四种场景。"""

from __future__ import annotations

from agent_architecture.contracts import (
    CompactionEntryPayload,
    MessageRole,
    TraceEndEntryPayload,
    TraceStatus,
)
from agent_architecture.modules.session import project
from agent_architecture.modules.session.projection import DRAFT_PREFIX, SUMMARY_PREFIX
from fakes import (
    assistant_message,
    make_entry,
    tool_message,
    user_message,
)


def _roles(messages) -> list[MessageRole]:
    """把 messages 的角色抽出来，断言里少写点噪音。

    举例：_roles(msgs) -> [MessageRole.USER, MessageRole.ASSISTANT]
    """
    return [message.role for message in messages]


def test_without_compaction_every_message_enters_in_order() -> None:
    """场景一：没有 compaction，从第一条 message 起按 seq 顺序全进来。"""
    entries = [
        make_entry(1, user_message("帮我读 a.py")),
        make_entry(2, assistant_message("好的")),
        make_entry(3, tool_message("文件内容")),
    ]

    messages = project(entries)

    assert _roles(messages) == [MessageRole.USER, MessageRole.ASSISTANT, MessageRole.TOOL]


def test_boundary_entries_never_enter_messages() -> None:
    """trace_start / trace_end / tool_start 只标记边界，不进上下文。"""
    entries = [
        make_entry(
            1,
            TraceEndEntryPayload(status=TraceStatus.COMPLETED, reason=None),
        ),
        make_entry(2, user_message("只有这条该进来")),
    ]

    assert _roles(project(entries)) == [MessageRole.USER]


def test_with_compaction_the_summary_comes_first() -> None:
    """场景二：有 compaction 时，摘要作为第一条消息，取代它之前的那些 message。"""
    entries = [
        make_entry(1, user_message("很早以前的话")),
        make_entry(
            2,
            CompactionEntryPayload(summary="用户要求重构 session 模块。", entry_ids=[]),
        ),
        make_entry(3, user_message("压缩之后的话")),
    ]

    messages = project(entries)

    assert len(messages) == 2
    assert SUMMARY_PREFIX in messages[0].content[0].text  # type: ignore[union-attr]
    assert messages[0].role is MessageRole.USER
    assert messages[1].content[0].text == "压缩之后的话"  # type: ignore[union-attr]


def test_kept_messages_are_restored_from_entry_ids() -> None:
    """entry_ids 只引用不内嵌，所以要回日志里查；查到的按 seq 排在摘要之后。"""
    entries = [
        make_entry(1, user_message("这条被压掉了")),
        make_entry(2, user_message("这条要保留"), entry_id="e_keep"),
        make_entry(
            3,
            CompactionEntryPayload(summary="摘要", entry_ids=["e_keep"]),
        ),
        make_entry(4, assistant_message("压缩之后的")),
    ]

    messages = project(entries)

    assert [m.content[0].text for m in messages] == [  # type: ignore[union-attr]
        f"{SUMMARY_PREFIX}\n\n摘要",
        "这条要保留",
        "压缩之后的",
    ]


def test_dangling_entry_ids_are_skipped() -> None:
    """entry_ids 指向不存在的条目时跳过，不让整个投影失败。"""
    entries = [
        make_entry(1, CompactionEntryPayload(summary="摘要", entry_ids=["e_不存在"])),
    ]

    assert len(project(entries)) == 1


def test_only_the_latest_compaction_counts() -> None:
    """压缩会叠加着来，但上下文里永远只有一条摘要 —— 取最近那次。"""
    entries = [
        make_entry(1, user_message("原始的话")),
        make_entry(2, CompactionEntryPayload(summary="第一次摘要", entry_ids=[])),
        make_entry(3, user_message("第一次之后")),
        make_entry(4, CompactionEntryPayload(summary="第二次摘要", entry_ids=[])),
        make_entry(5, user_message("第二次之后")),
    ]

    messages = project(entries)

    assert "第一次摘要" not in messages[0].content[0].text  # type: ignore[union-attr]
    assert "第二次摘要" in messages[0].content[0].text  # type: ignore[union-attr]
    assert [m.content[0].text for m in messages[1:]] == ["第二次之后"]  # type: ignore[union-attr]


def test_draft_is_appended_as_an_assistant_message() -> None:
    """场景四：草稿槽非空时，半截输出作为一条 assistant 消息接在末尾。"""
    entries = [make_entry(1, user_message("帮我读 a.py"))]

    messages = project(entries, draft_text="我来帮你读")

    assert _roles(messages) == [MessageRole.USER, MessageRole.ASSISTANT]
    assert DRAFT_PREFIX in messages[-1].content[0].text  # type: ignore[union-attr]
    assert "我来帮你读" in messages[-1].content[0].text  # type: ignore[union-attr]


def test_empty_draft_adds_nothing() -> None:
    """槽是空的时候不多出任何一条消息。"""
    entries = [make_entry(1, user_message("帮我读 a.py"))]

    assert len(project(entries, draft_text="")) == 1

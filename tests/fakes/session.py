"""内存 Session 句柄与 Session 条目的假件。"""

from __future__ import annotations

from datetime import UTC, datetime

from agent_architecture.contracts import (
    CompactionEntryPayload,
    Draft,
    DraftSlot,
    Message,
    MessageRole,
    Permission,
    SessionEntry,
    SessionEntryPayload,
    SessionEntryType,
    SessionMeta,
    SessionStatus,
    TextPart,
    Timestamp,
    TokenAnchor,
    ToolStartEntryPayload,
    TraceEndEntryPayload,
    TraceStartEntryPayload,
)

# 测试里默认的那个用户。要验隔离时传别的值（比如 "user_别的"）。
#
# 举例：USER == "user_test"
USER = "user_test"

# 所有假条目的固定时刻，让断言可以写死。
#
# 举例：datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
START = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)

# payload 类型 -> 条目类型。造条目时按它推断，省得每个测试都手写 type。
#
# 举例：_TYPE_OF[Message] -> SessionEntryType.MESSAGE
_TYPE_OF: dict[type, SessionEntryType] = {
    TraceStartEntryPayload: SessionEntryType.TRACE_START,
    TraceEndEntryPayload: SessionEntryType.TRACE_END,
    ToolStartEntryPayload: SessionEntryType.TOOL_START,
    CompactionEntryPayload: SessionEntryType.COMPACTION,
    Message: SessionEntryType.MESSAGE,
}


def make_entry(
    seq: int,
    payload: SessionEntryPayload,
    *,
    entry_id: str | None = None,
    trace_id: str = "trace_test",
    type: SessionEntryType | None = None,
) -> SessionEntry:
    """造一条 Session 条目；type 缺省时按 payload 的类型推断。

    缺省 entry_id 是 `e_{seq:04d}`（4 位），和 SequentialIds 的 `e_{n:06d}`（6 位）
    **故意不同宽** —— 测试经常把"预制条目"和"Session 自己追加的条目"混在同一份日志里，
    同宽就会撞 id，让按 id 定位的逻辑（`entries_from`）找错条目。
    生产用的是 UuidGenerator，不存在这个问题。

    举例：make_entry(1, user_message("你好")) -> SessionEntry(entry_id="e_0001", ...)
    """
    return SessionEntry(
        entry_id=entry_id if entry_id is not None else f"e_{seq:04d}",
        trace_id=trace_id,
        seq=seq,
        timestamp=START,
        # 用 payload.__class__ 而不是 type(payload)：参数名 type 把内置的 type 遮住了。
        type=type if type is not None else _TYPE_OF[payload.__class__],
        payload=payload,
    )


def user_message(text: str) -> Message:
    """造一条用户消息。

    举例：user_message("帮我读 a.py")
    """
    return Message(role=MessageRole.USER, content=[TextPart(text=text)])


def assistant_message(text: str) -> Message:
    """造一条模型消息。

    举例：assistant_message("好的")
    """
    return Message(role=MessageRole.ASSISTANT, content=[TextPart(text=text)])


def tool_message(text: str, *, tool_call_id: str = "call_1") -> Message:
    """造一条工具结果消息，metadata 里带 tool_call_id。

    举例：tool_message("文件内容……", tool_call_id="call_7f3a91")
    """
    return Message(
        role=MessageRole.TOOL,
        content=[TextPart(text=text)],
        metadata={"tool_call_id": tool_call_id},
    )


def make_session_meta(
    session_id: str = "sess_test",
    user_id: str = USER,
    *,
    workspace: str = "D:/tmp/workspace",
    tool_result_max_chars: int = 200,
) -> SessionMeta:
    """造一个字段齐全的 SessionMeta。

    workspace 是沙箱的文件边界，测边界时要指到真的临时目录上。

    举例：make_session_meta("sess_ab12").session_id -> "sess_ab12"
    """
    stamp = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
    return SessionMeta(
        session_id=session_id,
        user_id=user_id,
        created_at=stamp,
        updated_at=stamp,
        parent_session_id=None,
        title=None,
        workspace=workspace,
        model="fake-model",
        permission=Permission.EDITABLE,
        tool_result_max_chars=tool_result_max_chars,
        status=SessionStatus.ACTIVE,
        context_cursor="",
    )


class StubSession:
    """只做追加与投影的 SessionHandle 假件，给不关心投影规则的模块当替身用。

    真正的投影规则（最近一次 compaction 起的三条来源）由 modules/session 实现，
    这里只把 message 类型的条目按顺序取出来。
    举例：session = StubSession(); session.messages()
    """

    def __init__(
        self, session_id: str = "sess_test", *, workspace: str = "D:/tmp/workspace"
    ) -> None:
        # 会话元信息，字段齐全但值都写死；workspace 可换，沙箱测试要指到临时目录。
        #
        # 举例：SessionMeta(session_id="sess_test", workspace="D:/proj", ...)
        self._meta = make_session_meta(session_id, workspace=workspace)

        # 按追加顺序保存的条目，测试可直接断言。
        #
        # 举例：[SessionEntry(entry_id="e_000001", type=SessionEntryType.MESSAGE, ...)]
        self._entries: list[SessionEntry] = []

        # 会话级草稿槽；模型输出的唯一落脚点。
        #
        # 举例：DraftSlot(session_id="sess_test", draft=Draft(content="你好", ...))
        self._draft = DraftSlot(session_id=session_id, draft=None)

        # 下一条条目的 seq，从 1 开始。
        #
        # 举例：3
        self._next_seq = 1

        # 上一次请求的真实用量；桩件只管存着。
        #
        # 举例：TokenAnchor(messages=(...), input_tokens=10, output_tokens=2)
        self._token_anchor = None

    @property
    def meta(self) -> SessionMeta:
        """见 contracts.SessionHandle.meta。

        举例：session.meta.session_id -> "sess_test"
        """
        return self._meta

    def append(self, entry: SessionEntry) -> None:
        """见 contracts.SessionHandle.append。

        举例：session.append(entry)
        """
        self._entries.append(entry)

    def append_entry(
        self,
        *,
        trace_id: str,
        type: SessionEntryType,
        payload: SessionEntryPayload,
        now: Timestamp,
    ) -> SessionEntry:
        """见 contracts.SessionHandle.append_entry。

        举例：session.append_entry(trace_id="t", type=SessionEntryType.MESSAGE,
                                  payload=msg, now=now)
        """
        entry = SessionEntry(
            entry_id=f"e_{self._next_seq:04d}",
            trace_id=trace_id,
            seq=self._next_seq,
            timestamp=now,
            type=type,
            payload=payload,
        )
        self._next_seq += 1
        self._entries.append(entry)
        return entry

    def entries(self) -> list[SessionEntry]:
        """见 contracts.SessionHandle.entries。

        举例：session.entries() -> [SessionEntry(seq=1, ...)]
        """
        return list(self._entries)

    def token_anchor(self) -> TokenAnchor | None:
        """见 contracts.SessionHandle.token_anchor。

        举例：session.token_anchor() -> None
        """
        return self._token_anchor

    def set_token_anchor(self, anchor: TokenAnchor) -> None:
        """见 contracts.SessionHandle.set_token_anchor。

        举例：session.set_token_anchor(anchor)
        """
        self._token_anchor = anchor

    def context_entries(self) -> list[SessionEntry]:
        """见 contracts.SessionHandle.context_entries。桩件不做投影规则，就是 message 条目。

        举例：session.context_entries() -> [SessionEntry(type=MESSAGE, ...)]
        """
        return [e for e in self._entries if e.type is SessionEntryType.MESSAGE]

    def messages(self) -> list[Message]:
        """把 message 类型的条目 payload 依序取出来。

        举例：session.messages() -> [Message(role=MessageRole.USER, ...)]
        """
        return [entry.payload for entry in self._entries if entry.type is SessionEntryType.MESSAGE]  # type: ignore[misc]

    def append_draft(self, text: str, *, trace_id: str, turn_index: int, now: Timestamp) -> None:
        """见 contracts.SessionHandle.append_draft。

        举例：session.append_draft("你好", trace_id="t", turn_index=1, now=now)
        """
        if self._draft.draft is None:
            self._draft.draft = Draft(
                trace_id=trace_id, turn_index=turn_index, created_at=now, content=""
            )
        self._draft.draft.content += text

    def append_reasoning(
        self, text: str, *, trace_id: str, turn_index: int, now: Timestamp
    ) -> None:
        """见 contracts.SessionHandle.append_reasoning。

        举例：session.append_reasoning("先看看", trace_id="t", turn_index=1, now=now)
        """
        if self._draft.draft is None:
            self._draft.draft = Draft(
                trace_id=trace_id, turn_index=turn_index, created_at=now, content=""
            )
        self._draft.draft.reasoning += text

    def draft_text(self) -> str:
        """见 contracts.SessionHandle.draft_text。

        举例：session.draft_text() -> "你好"
        """
        draft = self._draft.draft
        return draft.content if draft is not None else ""

    def draft_reasoning(self) -> str:
        """见 contracts.SessionHandle.draft_reasoning。

        举例：session.draft_reasoning() -> "先看看"
        """
        draft = self._draft.draft
        return draft.reasoning if draft is not None else ""

    def take_draft(self) -> Draft | None:
        """见 contracts.SessionHandle.take_draft。

        举例：session.take_draft() -> Draft(content="你好")
        """
        draft = self._draft.draft
        self._draft.draft = None
        return draft

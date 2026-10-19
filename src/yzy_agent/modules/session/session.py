"""内存 Session：meta + entries + 物化上下文 + 游标 + 草稿槽。

它实现 `SessionHandle` 端口，是别的模块唯一能看到的那一面。

**投影不每次重算，但结果与全量重算逐条相等。** `messages()` 只把游标之后的条目接上去并
推进游标；这就是"确定性视图"的含义 —— 增量只是加速路径，不是另一套规则。
游标之后一旦出现 compaction（压缩换了投影起点），就走全量重算。

**返回的 messages 是新列表**，不是物化上下文本身。原因：草稿槽里的半截输出是**读时叠加**的
（它不在日志里），如果直接叠进物化上下文，下一次调用就会多出一条重复的草稿消息。
"""

from __future__ import annotations

from collections.abc import Sequence

from yzy_agent.contracts import (
    Draft,
    IdGenerator,
    Message,
    SessionEntry,
    SessionEntryPayload,
    SessionEntryType,
    SessionMeta,
    Timestamp,
    TokenAnchor,
)

from . import cursor as cursor_module
from .draft_slot import DraftSlot
from .log import SessionLog
from .projection import draft_message, project, project_entries


class Session:
    """一个会话的内存形态。

    举例：session = Session(meta, entries, ids=UuidGenerator())
          session.messages() -> [Message(role=MessageRole.USER, ...)]
    """

    def __init__(
        self,
        meta: SessionMeta,
        entries: Sequence[SessionEntry] = (),
        *,
        ids: IdGenerator,
    ) -> None:
        """由元信息与已有条目建会话；空条目表示全新会话。

        举例：Session(make_session_meta(), [], ids=UuidGenerator())
        """
        # 会话元信息。它和 entries 一起构成磁盘形态；本类会就地更新它的
        # updated_at 与 context_cursor。
        #
        # 举例：SessionMeta(session_id="sess_ab12", updated_at=..., ...)
        self._meta = meta

        # 只追加的内存日志，管 seq 与 entry_id。
        #
        # 举例：SessionLog([], ids=UuidGenerator())
        self._log = SessionLog(entries, ids=ids)

        # 会话级草稿槽，单槽、永不落盘。
        #
        # 举例：DraftSlot("sess_ab12")
        self._draft = DraftSlot(meta.session_id)

        # 物化上下文：entries 的投影，**不含草稿**。由 messages() 增量维护。
        #
        # 举例：[Message(role=MessageRole.USER, content=[TextPart(text="帮我读 a.py")])]
        self._context: list[Message] = []

        # 投影的起点：最近一次 compaction 的 entry_id；没有压缩就是空串。
        # 只在重建时更新（构造、崩溃恢复、每次压缩之后），平时不动。
        #
        # 举例："e_4f2a91bc0d3e"
        self._cursor = cursor_module.CURSOR_START

        # 物化上下文已经并到 entries 的第几条。**纯内存的增量记账**，不进 meta、不落盘，
        # 与上面那个游标是两件事。
        #
        # 举例：3
        self._merged_upto = 0

        # 上一次请求的真实用量。**不落盘**：它是一次测量的缓存，不是事实。
        # 装载出来的会话从 None 开始，打下一次模型就有了。
        #
        # 举例：TokenAnchor(messages=(...), input_tokens=42815, output_tokens=312)
        self._token_anchor: TokenAnchor | None = None

        # 建好就把已有条目全量投影一遍 —— 恢复出来的会话与新建的走同一条路。
        self.rebuild()

    # ---- SessionHandle 端口 ----

    @property
    def meta(self) -> SessionMeta:
        """当前会话的元信息。

        举例：session.meta.session_id -> "sess_ab12"
        """
        return self._meta

    def append(self, entry: SessionEntry) -> None:
        """追加一条**已经造好**的条目。

        正常路径是 append_entry —— 它负责分配 seq 与 entry_id。这个方法留着是为了
        恢复、分叉这类手里已经有完整条目的场景。

        注意：追加**不等于并入上下文**。新条目要等下一次 messages() 才会进上下文。

        举例：session.append(SessionEntry(entry_id="e_1", seq=1, ...))
        """
        self._log.add(entry)

    def messages(self) -> list[Message]:
        """当前上下文投影：最近一次 compaction 之后、需要进 messages 的那些条目。

        草稿槽非空时，末尾会多一条"上一轮输出被打断"的 assistant 消息。

        举例：session.messages() -> [Message(role=MessageRole.USER, ...)]
        """
        entries = self._log.entries()

        # 投影起点变了 = 期间发生过压缩，增量路径作废，全量重算。
        if cursor_module.projection_start(entries) != self._cursor:
            self.rebuild()
        else:
            cursor_module.merge(self._context, entries[self._merged_upto :])
            self._merged_upto = len(entries)

        messages = list(self._context)
        draft_text = self._draft.text()
        if draft_text:
            messages.append(draft_message(draft_text))
        return messages

    def token_anchor(self) -> TokenAnchor | None:
        """上一次请求的真实用量。见 SessionHandle.token_anchor。

        举例：session.token_anchor() -> TokenAnchor(input_tokens=42815, ...)
        """
        return self._token_anchor

    def set_token_anchor(self, anchor: TokenAnchor) -> None:
        """记下这一次请求的真实用量。见 SessionHandle.set_token_anchor。

        举例：session.set_token_anchor(anchor)
        """
        self._token_anchor = anchor

    def context_entries(self) -> list[SessionEntry]:
        """当前投影里那些有条目的消息。见 SessionHandle.context_entries。

        举例：session.context_entries() -> [SessionEntry(type=MESSAGE, ...)]
        """
        return project_entries(self._log.entries())

    def append_draft(self, text: str, *, trace_id: str, turn_index: int, now: Timestamp) -> None:
        """往草稿槽追加一段模型输出增量；槽是空的就新建一条草稿。

        作用：模型输出只存这一处，不另开第二份缓冲。

        举例：session.append_draft("你好", trace_id="t", turn_index=1, now=now)
        """
        self._draft.append(text, trace_id=trace_id, turn_index=turn_index, now=now)

    def append_reasoning(
        self, text: str, *, trace_id: str, turn_index: int, now: Timestamp
    ) -> None:
        """往草稿槽追加一段思考增量。**和正文分开记**，它不进下一轮请求。

        举例：session.append_reasoning("先看看", trace_id="t", turn_index=1, now=now)
        """
        self._draft.append_reasoning(text, trace_id=trace_id, turn_index=turn_index, now=now)

    def draft_text(self) -> str:
        """当前草稿槽里的正文；槽是空的就返回空串。**只读**。

        举例：session.draft_text() -> "我来帮你读"
        """
        return self._draft.text()

    def draft_reasoning(self) -> str:
        """当前草稿槽里的思考；槽是空的就返回空串。**只读**。

        举例：session.draft_reasoning() -> "先看看有没有这个文件"
        """
        return self._draft.reasoning()

    def take_draft(self) -> Draft | None:
        """取出草稿并**清空**槽；槽本来就是空的就返回 None。

        举例：session.take_draft().content -> "我来帮你读 a.py"
        """
        return self._draft.take()

    # ---- 本模块自用 ----

    def entries(self) -> list[SessionEntry]:
        """全部条目的快照。

        举例：session.entries() -> [SessionEntry(seq=1, ...)]
        """
        return self._log.entries()

    def append_entry(
        self,
        *,
        trace_id: str,
        type: SessionEntryType,
        payload: SessionEntryPayload,
        now: Timestamp,
    ) -> SessionEntry:
        """造一条条目并追加；seq 与 entry_id 由日志分配。

        举例：session.append_entry(trace_id="trace_1", type=SessionEntryType.MESSAGE,
                                  payload=msg, now=now)
        """
        return self._log.append(trace_id=trace_id, type=type, payload=payload, now=now)

    def cursor(self) -> str:
        """投影的起点：最近一次 compaction 的 entry_id；没有压缩就是空串。

        举例：session.cursor() -> "e_4f2a91bc0d3e"
        """
        return self._cursor

    def rebuild(self) -> None:
        """丢掉物化上下文，按 projection.project() 全量重算，游标推到日志末尾。

        三处会用到它：trace 初始化、压缩之后、进程重启恢复。

        举例：session.rebuild() -> session.cursor() == 最近一次 compaction 的 entry_id
        """
        entries = self._log.entries()
        self._context = project(entries)
        self._merged_upto = len(entries)
        self._set_cursor(cursor_module.projection_start(entries))

    def set_title(self, title: str) -> None:
        """设置会话标题，用于列表展示。

        举例：session.set_title("重构 session 模块")
        """
        self._meta.title = title

    def _set_cursor(self, entry_id: str) -> None:
        """更新投影起点，并把同一个值镜像到 meta.context_cursor 上。

        举例：_set_cursor("e_4f2a91bc0d3e") -> meta.context_cursor == "e_4f2a91bc0d3e"
        """
        self._cursor = entry_id
        self._meta.context_cursor = entry_id

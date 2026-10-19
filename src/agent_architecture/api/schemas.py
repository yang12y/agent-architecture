"""对外 DTO：请求与响应的形状。内部一律用 contracts 里的类型。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Literal, cast

from pydantic import BaseModel, Field

from agent_architecture.composition import LiveSession
from agent_architecture.contracts import (
    ApprovalDecision,
    ApprovalRequest,
    ContentPart,
    ImagePart,
    JsonValue,
    Message,
    MessageRole,
    Permission,
    SessionEntry,
    SessionEntryType,
    SessionMeta,
    SessionStatus,
    TextPart,
    ThinkingPart,
    ToolCallPart,
)

# --------------------------------------------------------------------------
# 内容块
# --------------------------------------------------------------------------


class TextPartIn(BaseModel):
    """一段文本。"""

    type: Literal["text"] = "text"
    text: str


class ImagePartIn(BaseModel):
    """一张图片，data_base64 与 url 二选一。"""

    type: Literal["image"] = "image"
    media_type: str
    data_base64: str | None = None
    url: str | None = None


# 请求里能出现的内容块。**故意没有 tool_call** —— 客户端不能伪造一次工具调用，
# 那等于绕过位点 6 的整条检查链。类型上就收不进来。
PartIn = Annotated[TextPartIn | ImagePartIn, Field(discriminator="type")]


class TextPartOut(BaseModel):
    """一段文本。"""

    type: Literal["text"] = "text"
    text: str


class ThinkingPartOut(BaseModel):
    """模型的思考过程。**只出现在响应里** —— 请求不收它（客户端不能伪造推理）。"""

    type: Literal["thinking"] = "thinking"
    text: str


class ImagePartOut(BaseModel):
    """一张图片。"""

    type: Literal["image"] = "image"
    media_type: str
    data_base64: str | None = None
    url: str | None = None


class ToolCallPartOut(BaseModel):
    """模型请求的一次工具调用。"""

    type: Literal["tool_call"] = "tool_call"
    tool_call_id: str
    name: str
    arguments: JsonValue


# 响应里可能出现的内容块：比请求多一个 tool_call（模型请求的那些）。
PartOut = Annotated[
    TextPartOut | ThinkingPartOut | ImagePartOut | ToolCallPartOut, Field(discriminator="type")
]


def to_parts(parts: Sequence[TextPartIn | ImagePartIn]) -> list[ContentPart]:
    """请求里的内容块转成契约类型。

    举例：to_parts([TextPartIn(text="在吗")]) -> [TextPart(text="在吗")]
    """
    converted: list[ContentPart] = []
    for part in parts:
        if isinstance(part, TextPartIn):
            converted.append(TextPart(text=part.text))
        else:
            converted.append(
                ImagePart(
                    media_type=part.media_type,
                    data_base64=part.data_base64,
                    url=part.url,
                )
            )
    return converted


def from_parts(parts: Sequence[ContentPart]) -> list[dict]:
    """契约类型转成对外形状。日志里有什么就回什么，不做删减。

    举例：from_parts([TextPart(text="在吗")]) -> [{"type": "text", "text": "在吗"}]
    """
    converted: list[dict] = []
    for part in parts:
        if isinstance(part, TextPart):
            converted.append({"type": "text", "text": part.text})
        elif isinstance(part, ThinkingPart):
            converted.append({"type": "thinking", "text": part.text})
        elif isinstance(part, ImagePart):
            converted.append(
                {
                    "type": "image",
                    "media_type": part.media_type,
                    "data_base64": part.data_base64,
                    "url": part.url,
                }
            )
        elif isinstance(part, ToolCallPart):
            converted.append(
                {
                    "type": "tool_call",
                    "tool_call_id": part.tool_call_id,
                    "name": part.name,
                    "arguments": part.arguments,
                }
            )
    return converted


# --------------------------------------------------------------------------
# 会话
# --------------------------------------------------------------------------


class HealthOut(BaseModel):
    """健康检查。"""

    status: str


class ErrorBody(BaseModel):
    """§7.8 的错误体。"""

    code: str
    message: str


class ErrorOut(BaseModel):
    """错误响应。"""

    error: ErrorBody


class CreateSessionIn(BaseModel):
    """新建会话的请求体，可以整个省略。"""

    title: str | None = None


class MessageIn(BaseModel):
    """发消息的请求体。"""

    content: list[PartIn] = Field(min_length=1)
    client_message_id: str | None = None


def session_fields(meta: SessionMeta) -> dict:
    """SessionMeta 里对外可见的那几个字段。

    举例：session_fields(make_session_meta())["session_id"] -> "sess_test"
    """
    return {
        "session_id": meta.session_id,
        "user_id": meta.user_id,
        "created_at": meta.created_at,
        "updated_at": meta.updated_at,
        "title": meta.title,
        "model": meta.model,
        "permission": meta.permission,
        "status": meta.status,
    }


class SessionOut(BaseModel):
    """会话元信息。"""

    session_id: str
    user_id: str
    created_at: datetime
    updated_at: datetime
    title: str | None
    model: str
    permission: Permission
    status: SessionStatus

    @classmethod
    def from_meta(cls, meta: SessionMeta) -> SessionOut:
        """按 SessionMeta 填。

        举例：SessionOut.from_meta(live.session.meta)
        """
        return cls(**session_fields(meta))


class SessionListOut(BaseModel):
    """会话列表。"""

    sessions: list[SessionOut]


class MessageOut(BaseModel):
    """历史里的一条消息，对应日志里的一条 message 条目。"""

    entry_id: str
    entry_seq: int
    timestamp: datetime
    role: MessageRole
    content: list[PartOut]

    # 工具结果靠它和那次调用配对；其它消息是 None。正文本身没有工具名，
    # 想显示「工具结果 · read」就得回头查 assistant 消息里的同名 id。
    tool_call_id: str | None = None

    @classmethod
    def from_entry(cls, entry: SessionEntry) -> MessageOut:
        """按条目填。传进来的必须真是 message 条目，message_entries 已经筛过。

        举例：MessageOut.from_entry(session.entries()[0])
        """
        message = cast(Message, entry.payload)
        tool_call_id = message.metadata.get("tool_call_id")
        return cls(
            entry_id=entry.entry_id,
            entry_seq=entry.seq,
            timestamp=entry.timestamp,
            role=message.role,
            content=from_parts(message.content),
            tool_call_id=tool_call_id if isinstance(tool_call_id, str) else None,
        )


def message_entries(entries: Sequence[SessionEntry]) -> list[SessionEntry]:
    """历史只取 message 条目 —— 边界标记不是给人看的消息。

    举例：message_entries(session.entries()) -> [SessionEntry(type=MESSAGE, ...)]
    """
    return [
        entry
        for entry in entries
        if entry.type is SessionEntryType.MESSAGE and isinstance(entry.payload, Message)
    ]


# 历史默认一页多少条。客户端首屏看到的就是这一屏，往上滚再要更早的。
#
# 举例：50
DEFAULT_PAGE_SIZE = 50


def page_messages(
    entries: Sequence[SessionEntry],
    *,
    limit: int,
    before_seq: int | None = None,
    after_seq: int | None = None,
) -> tuple[list[SessionEntry], bool]:
    """从一串条目里切一页 message 条目，返回（这一页, 还有没有更多）。

    两个游标方向相反，都按条目号排他：

    - 不给游标：给**最后** limit 条 —— 打开会话先看最新的一屏，has_more 说明还有更早的。
    - before_seq：往前翻，给 seq 小于它的最后 limit 条。
    - after_seq：往后补，给 seq 大于它的前 limit 条 —— 增量刷新只回新增的那几条。

    举例：page_messages(entries, limit=50) -> (最后 50 条, True)
    """
    kept = message_entries(entries)
    if after_seq is not None:
        rest = [entry for entry in kept if entry.seq > after_seq]
        return rest[:limit], len(rest) > limit
    if before_seq is not None:
        rest = [entry for entry in kept if entry.seq < before_seq]
        return rest[-limit:], len(rest) > limit
    return kept[-limit:], len(kept) > limit


class SessionStatsOut(BaseModel):
    """一条会话的几个数字。**全部从日志数出来**，没有单独的计数器字段。"""

    # 已经开始的 trace 数（含还没收尾的那次）。
    traces: int

    # 已收尾 trace 的轮数之和 —— 正在跑的那次结束后才算进来。
    turns: int

    # 最后一次有记录的上下文占用。
    context_tokens: int

    # 上下文窗口上限，算百分比用。
    max_tokens: int


class SessionDetailOut(SessionOut):
    """会话详情 + 一页消息 + 统计。**前端渲染历史只用它。**"""

    is_running: bool
    messages: list[MessageOut]

    # 这一页之外还有没有更多。前端翻历史靠它决定还拉不拉，别拿"这一页满了"去猜。
    #
    # 举例：True
    has_more: bool = False

    stats: SessionStatsOut

    @classmethod
    def from_live(
        cls,
        live: LiveSession,
        *,
        stats: SessionStatsOut,
        entries: Sequence[SessionEntry],
        has_more: bool,
    ) -> SessionDetailOut:
        """按活状态填：元信息 + 是不是在跑 + 这一页消息 + 统计。

        举例：SessionDetailOut.from_live(live, stats=..., entries=[...], has_more=True)
        """
        return cls(
            **session_fields(live.session.meta),
            is_running=live.is_running,
            messages=[MessageOut.from_entry(e) for e in entries],
            has_more=has_more,
            stats=stats,
        )


class MessageAcceptedOut(BaseModel):
    """消息已受理。**内容从 WS 的事件流拿**，这里只回受理结果。"""

    session_id: str
    accepted: bool
    queued: bool


class InterruptOut(BaseModel):
    """中断请求的结果。"""

    session_id: str
    interrupted: bool


class SessionDeletedOut(BaseModel):
    """会话删掉了。消息记录一起没的，不可恢复。"""

    session_id: str
    deleted: bool


class ApprovalPendingOut(BaseModel):
    """一份等着人工确认的请求。**审批不进日志**，所以它只能从待审批表里来。"""

    request_id: str
    session_id: str
    trace_id: str
    tool_call_id: str
    tool_name: str
    arguments: JsonValue
    reason: str

    @classmethod
    def from_request(cls, request: ApprovalRequest) -> ApprovalPendingOut:
        """按审批请求填。参数是**补齐并校验过的最终参数**，和判定用的是同一份。

        举例：ApprovalPendingOut.from_request(request)
        """
        return cls(
            request_id=request.request_id,
            session_id=request.session_id,
            trace_id=request.trace_id,
            tool_call_id=request.tool_call_id,
            tool_name=request.tool_name,
            arguments=request.arguments,
            reason=request.reason,
        )


class ApprovalListOut(BaseModel):
    """待审批列表。重连之后拉一次（§7.6）。"""

    approvals: list[ApprovalPendingOut]


class ApprovalDecisionIn(BaseModel):
    """回执。**没有"还没想好"这个取值** —— 关掉弹窗也得明确表态（§9）。"""

    decision: ApprovalDecision
    comment: str | None = None


class ApprovalAcceptedOut(BaseModel):
    """回执收下了。"""

    request_id: str
    decision: ApprovalDecision
    accepted: bool

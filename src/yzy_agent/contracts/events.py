"""会话事件：给前端看的东西。

事件**不是事实本身** —— 事实是 Session 日志。事件是日志的投影，加上少量只有内存里才知道的
字段（草稿增量、turn 序号、耗时）。逐条对照见文档 §7.5。

**谁写日志谁顺手发**：session 是日志写入的唯一场所，条目派生的事件都在那里发；
没有对应条目的那两个（`TurnStart` / `AssistantDelta`）由内核发。

`session_id` 是路由键，前端按它把事件分到对应会话。
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import ContentPart, JsonValue, Permission, TraceStatus


@dataclass(frozen=True, slots=True)
class MessageAccepted:
    """用户消息已受理。

    举例：MessageAccepted(session_id="sess_ab12", entry_id="e_000002", entry_seq=2, ...)
    """

    session_id: str

    # 落进日志的那条用户消息。
    #
    # 举例："e_000002" / 2
    entry_id: str
    entry_seq: int

    # 消息正文，原样回带给前端对账。
    #
    # 举例：[TextPart(text="帮我读一下 a.py")]
    content: list[ContentPart]

    # 前端自己生成的对账标识；没带就是 None。
    #
    # 举例："web-7f3a91"
    client_message_id: str | None

    # True 表示这次是入队（当前 trace 还在跑），没有开新的 trace。
    #
    # 举例：False
    queued: bool


@dataclass(frozen=True, slots=True)
class TraceStart:
    """一次 trace 开始。

    举例：TraceStart(session_id="sess_ab12", trace_id="trace_x", model="deepseek-chat", ...)
    """

    session_id: str
    trace_id: str

    # 本次生效的模型与权限档位。
    #
    # 举例："deepseek-chat" / Permission.EDITABLE
    model: str
    permission: Permission

    # 本次的 turn 数上限；None 表示不限。
    #
    # 举例：50
    max_turns: int | None

    # 触发本次 trace 的用户消息。**回带正文**，前端才能和乐观渲染对账。
    #
    # 举例："e_000002" / 2 / [TextPart(text="帮我读一下 a.py")]
    user_message_entry_id: str
    user_message_entry_seq: int
    user_message: list[ContentPart]


@dataclass(frozen=True, slots=True)
class TurnStart:
    """一轮开始。**没有对应条目**，只有内核知道。

    举例：TurnStart(session_id="sess_ab12", trace_id="trace_x", turn_index=1)
    """

    session_id: str
    trace_id: str

    # 这一轮的序号，从 1 开始。
    #
    # 举例：1
    turn_index: int


@dataclass(frozen=True, slots=True)
class AssistantDelta:
    """模型输出的一个增量片段。**没有对应条目** —— 草稿槽永不落盘。

    举例：AssistantDelta(session_id="sess_ab12", trace_id="trace_x", turn_index=1, text="好的")
    """

    session_id: str
    trace_id: str
    turn_index: int

    # 这一个片段新吐出来的几个字，**不是**累计全文。
    #
    # 举例："好的"
    text: str


@dataclass(frozen=True, slots=True)
class AssistantThought:
    """思考过程的一个增量片段。**没有对应条目** —— 草稿槽永不落盘。

    和 AssistantDelta 一样是"补不回来"的那一类：断线期间的思考，重连之后只剩
    定稿消息里的完整 ThinkingPart，中间过程没了。

    举例：AssistantThought(session_id="sess_ab12", trace_id="trace_x", turn_index=1, text="先看看")
    """

    session_id: str
    trace_id: str
    turn_index: int

    # 这一个片段新吐出来的几个字，**不是**累计全文。
    #
    # 举例："先看看"
    text: str


@dataclass(frozen=True, slots=True)
class AssistantMessage:
    """本轮 assistant 消息定稿。**全量** —— 前端拿它覆盖增量拼出来的那条。

    举例：AssistantMessage(session_id="sess_ab12", entry_id="e_000003", ...)
    """

    session_id: str
    trace_id: str
    turn_index: int

    # 落进日志的那条 assistant 消息。
    #
    # 举例："e_000003" / 3
    entry_id: str
    entry_seq: int

    # 消息正文，含它请求的工具调用。
    #
    # 举例：[TextPart(text="好的，我看一下")]
    content: list[ContentPart]


@dataclass(frozen=True, slots=True)
class ToolCallStart:
    """一条工具调用已派发，副作用可能已经发生。

    举例：ToolCallStart(session_id="sess_ab12", tool_call_id="call_1", name="read", ...)
    """

    session_id: str
    trace_id: str

    # 对应 assistant 消息里的那次调用。
    #
    # 举例："call_7f3a91" / "read"
    tool_call_id: str
    name: str

    # 补齐默认值、通过校验之后的最终参数。
    #
    # 举例：{"path": "a.py"}
    arguments: JsonValue


@dataclass(frozen=True, slots=True)
class ToolResult:
    """一条工具调用的结果回来了。

    举例：ToolResult(session_id="sess_ab12", tool_call_id="call_1", ok=True, ...)
    """

    session_id: str
    trace_id: str
    tool_call_id: str
    name: str

    # 这次调用成功了没有。**被拒的调用不算失败也不算成功**，它走不到这里。
    #
    # 举例：True
    ok: bool

    # 结果开头的摘要，给列表用；全文在 Session 日志里。
    #
    # 举例："import os..."
    preview: str


@dataclass(frozen=True, slots=True)
class Compaction:
    """一段旧消息被一条摘要取代。

    举例：Compaction(session_id="sess_ab12", trace_id="trace_x", summary_preview="用户要求…")
    """

    session_id: str
    trace_id: str

    # 摘要的开头，给列表用；全文在 compaction 条目里。
    #
    # 举例："用户要求重构 session 模块。已确定…"
    summary_preview: str

    # 没被压掉、仍然保留的 message 条目。
    #
    # 举例：["e_0042", "e_0043"]
    kept_entry_ids: list[str]


@dataclass(frozen=True, slots=True)
class TraceEnd:
    """一次 trace 收尾。**必发**。

    举例：TraceEnd(session_id="sess_ab12", trace_id="trace_x", status=TraceStatus.COMPLETED, ...)
    """

    session_id: str
    trace_id: str

    # 按什么状态收尾，以及为什么。
    #
    # 举例：TraceStatus.COMPLETED / None
    status: TraceStatus
    reason: str | None

    # 一共跑了几轮、花了多久。两者都推不出来，所以随事件带上。
    #
    # 举例：3 / 8421.5
    turns: int
    duration_ms: float


@dataclass(frozen=True, slots=True)
class ApprovalRequired:
    """有工具调用在等人工放行。**没有对应条目** —— 审批不进日志。

    举例：ApprovalRequired(session_id="sess_ab12", request_id="apr_1", tool_name="write", ...)
    """

    session_id: str

    # 回执时带回来的那个标识。
    #
    # 举例："apr_7f3a91"
    request_id: str

    # 要放行的那次调用。
    #
    # 举例："write" / {"path": "a.py"}
    tool_name: str
    arguments: JsonValue

    # 为什么要审批，会展示给用户。
    #
    # 举例："会覆盖已有文件"
    reason: str

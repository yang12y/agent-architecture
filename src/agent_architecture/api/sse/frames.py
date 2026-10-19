"""帧：类型与文本形。

帧都是 JSON 对象，由 `type` 决定形状。控制帧由接口层自己造（`ready` / `subscribed` /
`error`）；`event` 帧由会话事件转过来，一张表查完，加事件时只改这一处。

出去之前一律写成 SSE 的一条消息（`to_text`）：`data: {json}\n\n`。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

from agent_architecture.contracts import (
    ApprovalRequired,
    AssistantDelta,
    AssistantMessage,
    AssistantThought,
    Compaction,
    MessageAccepted,
    ToolCallStart,
    ToolResult,
    TraceEnd,
    TraceStart,
    TurnStart,
)

from ..schemas import from_parts

# 空闲多久写一次心跳（毫秒）。**空闲超时就写**，不是定时写 —— 有事件在流的时候不重复发。
#
# 它同时是客户端唯一能确认"这条流还活着"的凭据，所以这个值会随 ready 帧告诉客户端。
#
# 举例：2000
HEARTBEAT_MS = 2000

# 心跳的一行文本。以冒号开头是 SSE 的注释行，客户端会直接丢掉，只用来占住连接。
#
# 举例：": ping\n\n"
PING = ": ping\n\n"


def to_text(frame: dict) -> str:
    """把一帧写成 SSE 的一条消息。

    举例：to_text({"type": "ready", ...}) -> 'data: {"type": "ready", ...}\n\n'
    """
    return f"data: {json.dumps(frame, ensure_ascii=False)}\n\n"


def ready_frame(*, client_id: str, server_time: datetime, heartbeat_ms: int) -> dict:
    """连上之后的第一条，恒发。在它到达之前客户端不算连上。

    举例：ready_frame(client_id="c_1", server_time=now, heartbeat_ms=2000)
    """
    return {
        "type": "ready",
        "client_id": client_id,
        "server_time": server_time.isoformat(),
        "heartbeat_ms": heartbeat_ms,
    }


def subscribed_frame(
    *, session_id: str, is_running: bool, draft_text: str, latest_entry_seq: int
) -> dict:
    """这条流已经看着这个会话了。**先发这一条，再推事件。**

    draft_text 是关键：草稿槽不落盘，补拉补不到，只能靠这里整段带过去。

    举例：subscribed_frame(session_id="sess_x", is_running=True, draft_text="好的", ...)
    """
    return {
        "type": "subscribed",
        "session_id": session_id,
        "is_running": is_running,
        "draft_text": draft_text,
        "latest_entry_seq": latest_entry_seq,
    }


def error_frame(*, code: str, message: str, session_id: str | None = None) -> dict:
    """这条流要收尾了，带上原因。session_id 缺省表示是整条流的错。

    举例：error_frame(code="stream_overflowed", message="事件积压，重连后会全量重拉")
    """
    frame: dict = {"type": "error", "code": code, "message": message}
    if session_id is not None:
        frame["session_id"] = session_id
    return frame


def event_frame(*, session_id: str, event: object) -> dict | None:
    """把一个会话事件转成 event 帧；不是已知事件就返回 None。

    session_id 跟着帧走 —— 一条流可以带多个会话，客户端靠它分派。

    举例：event_frame(session_id="sess_x", event=TurnStart(...)) -> {"type": "event", ...}
    """
    serialize = _SERIALIZERS.get(type(event))
    if serialize is None:
        return None
    name, data = serialize(event)
    return {"type": "event", "session_id": session_id, "name": name, "data": data}


# 事件类型 -> 怎么把它摊成 (name, data)。
#
# 举例：_SERIALIZERS[TurnStart](TurnStart(...)) -> ("turn_start", {...})
_SERIALIZERS: dict[type, Callable[[Any], tuple[str, dict]]] = {
    MessageAccepted: lambda e: (
        "message_accepted",
        {"entry_id": e.entry_id, "entry_seq": e.entry_seq, "content": from_parts(e.content)},
    ),
    TraceStart: lambda e: (
        "trace_start",
        {
            "trace_id": e.trace_id,
            "model": e.model,
            "permission": e.permission,
            "max_turns": e.max_turns,
            "user_message": {
                "entry_id": e.user_message_entry_id,
                "entry_seq": e.user_message_entry_seq,
                "content": from_parts(e.user_message),
            },
        },
    ),
    TurnStart: lambda e: ("turn_start", {"trace_id": e.trace_id, "turn_index": e.turn_index}),
    AssistantDelta: lambda e: (
        "assistant_delta",
        {"trace_id": e.trace_id, "turn_index": e.turn_index, "text": e.text},
    ),
    AssistantThought: lambda e: (
        "assistant_thinking",
        {"trace_id": e.trace_id, "turn_index": e.turn_index, "text": e.text},
    ),
    AssistantMessage: lambda e: (
        "assistant_message",
        {
            "trace_id": e.trace_id,
            "turn_index": e.turn_index,
            "entry_id": e.entry_id,
            "entry_seq": e.entry_seq,
            "content": from_parts(e.content),
        },
    ),
    ToolCallStart: lambda e: (
        "tool_call_start",
        {
            "trace_id": e.trace_id,
            "tool_call_id": e.tool_call_id,
            "name": e.name,
            "arguments": e.arguments,
        },
    ),
    ToolResult: lambda e: (
        "tool_result",
        {
            "trace_id": e.trace_id,
            "tool_call_id": e.tool_call_id,
            "name": e.name,
            "ok": e.ok,
            "preview": e.preview,
        },
    ),
    Compaction: lambda e: (
        "compaction",
        {
            "trace_id": e.trace_id,
            "summary_preview": e.summary_preview,
            "kept_entries": e.kept_entry_ids,
        },
    ),
    TraceEnd: lambda e: (
        "trace_end",
        {
            "trace_id": e.trace_id,
            "status": e.status,
            "reason": e.reason,
            "turns": e.turns,
            "duration_ms": e.duration_ms,
        },
    ),
    ApprovalRequired: lambda e: (
        "approval_required",
        {
            "request_id": e.request_id,
            "tool_name": e.tool_name,
            "arguments": e.arguments,
            "reason": e.reason,
        },
    ),
}

# hub 要订阅的事件类型。序列化表就是唯一事实来源 —— 加事件时这里跟着变。
#
# 举例：EVENT_TYPES == (MessageAccepted, TraceStart, ...)
EVENT_TYPES: tuple[type, ...] = tuple(_SERIALIZERS)

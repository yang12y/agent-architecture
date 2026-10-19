"""把契约里的 Message 翻译成 OpenAI 的消息结构。

单独一个模块，是因为这是纯函数、可以脱网单测：给定一串 Message，
产出什么 JSON，一眼可见。

注意：契约里的 MessageRole 目前只有 user / assistant / tool 三种，
**没有 system**。这里对未知角色一律按"纯文本消息"处理，
所以以后要补 system 角色，这个文件不用改。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from agent_architecture.contracts import (
    ContentPart,
    ImagePart,
    Message,
    MessageRole,
    TextPart,
    ThinkingPart,
    ToolCallPart,
)


def to_openai_messages(messages: Sequence[Message]) -> list[dict[str, Any]]:
    """把一串 Message 翻成 OpenAI 的 messages 数组。

    举例：to_openai_messages([user_msg]) -> [{"role": "user", "content": "你好"}]
    """
    return [_to_one(message) for message in messages]


def _to_one(message: Message) -> dict[str, Any]:
    """翻译一条消息。按角色决定用哪种结构。

    举例：_to_one(tool_msg) -> {"role": "tool", "tool_call_id": "call_1", "content": "..."}
    """
    if message.role is MessageRole.TOOL:
        return {
            "role": "tool",
            "tool_call_id": str(message.metadata.get("tool_call_id", "")),
            "content": _plain_text(message.content),
        }

    if message.role is MessageRole.ASSISTANT:
        payload: dict[str, Any] = {
            "role": "assistant",
            "content": _plain_text(message.content) or None,
        }
        reasoning = _reasoning_text(message.content)
        if reasoning:
            # 带 tools 的请求**必须**把上一轮的思考原样传回去，否则服务端会 400 ——
            # 这是 DeepSeek 明文要求的，不是可选优化。
            payload["reasoning_content"] = reasoning
        calls = [part for part in message.content if isinstance(part, ToolCallPart)]
        if calls:
            payload["tool_calls"] = [
                {
                    "id": call.tool_call_id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in calls
            ]
        return payload

    images = [part for part in message.content if isinstance(part, ImagePart)]
    if images:
        return {"role": message.role.value, "content": _content_parts(message.content)}
    return {"role": message.role.value, "content": _plain_text(message.content)}


def _reasoning_text(parts: Sequence[ContentPart]) -> str:
    """把思考块拼起来；没有思考块就是空串。

    举例：_reasoning_text([ThinkingPart(text="先看看")]) -> "先看看"
    """
    return "".join(part.text for part in parts if isinstance(part, ThinkingPart))


def _plain_text(parts: Sequence[ContentPart]) -> str:
    """把内容块里的文本拼起来，忽略非文本块。

    举例：_plain_text([TextPart(text="你好"), ToolCallPart(...)]) -> "你好"
    """
    return "".join(part.text for part in parts if isinstance(part, TextPart))


def _content_parts(parts: Sequence[ContentPart]) -> list[dict[str, Any]]:
    """翻译成 OpenAI 的多模态内容数组（有图片时才用这种形式）。

    举例：[{"type": "text", "text": "看图"}, {"type": "image_url", "image_url": {...}}]
    """
    out: list[dict[str, Any]] = []
    for part in parts:
        if isinstance(part, TextPart):
            out.append({"type": "text", "text": part.text})
        elif isinstance(part, ImagePart):
            url = part.url or f"data:{part.media_type};base64,{part.data_base64 or ''}"
            out.append({"type": "image_url", "image_url": {"url": url}})
    return out

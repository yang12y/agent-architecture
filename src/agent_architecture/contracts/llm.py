"""模型接入的契约：请求、流式分片、客户端端口。

这里只定义"内核要什么"，不定义"怎么连某家厂商"。厂商差异（SSE 解析、
工具调用的增量 JSON 拼装、重试、超窗判定）全部关在实现里，
内核与 handler 看到的永远是这五种分片。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from .models import JsonValue, Message, StopReason, ToolCallPart, Usage


@dataclass(frozen=True, slots=True)
class LlmRequest:
    """一次模型调用的请求。内核在位点 4 之前组装好它。"""

    # 用哪个模型。
    #
    # 举例："deepseek-chat"
    model: str

    # 要发给模型的完整上下文。取自内存 Session 的上下文投影，
    # 压缩如果生效，改的就是这份列表（见位点 3）。
    #
    # 举例：[Message(role=MessageRole.USER, content=[TextPart(text="帮我读 a.py")])]
    messages: list[Message]

    # 暴露给模型的工具 schema。由 tool_calling 在位点 3 填。
    #
    # 举例：[{"name": "read", "description": "...", "parameters": {...}}]
    tools_schema: list[dict[str, JsonValue]] = field(default_factory=list)

    # 采样温度；None 表示用服务端默认值。
    #
    # 举例：0.2
    temperature: float | None = None

    # 是否让模型开思考模式。厂商怎么表达这个开关是实现的事，内核只管传。
    #
    # 举例：True
    thinking: bool = False


class LlmChunkKind(StrEnum):
    """流式分片的类型。一次收流会依次收到若干片。"""

    # 文本增量 —— 模型新吐出的几个字，直接追加进草稿槽。
    TEXT_DELTA = "text_delta"

    # 思考增量 —— 推理模型在正文之前吐的那一段。**只给人看，不回传给模型。**
    #
    # 举例：LlmChunkKind.REASONING_DELTA
    REASONING_DELTA = "reasoning_delta"

    # 一次完整的工具调用请求。
    # 注意：增量 JSON 的拼装由 LlmClient 负责，内核收到的永远是拼好的整条。
    TOOL_CALL = "tool_call"

    # 用量统计 —— 一般出现在流的末尾。
    USAGE = "usage"

    # 流结束 —— 带上这次为什么停下来。每轮收流的最后一片。
    DONE = "done"


@dataclass(frozen=True, slots=True)
class LlmChunk:
    """流里的一片。哪种字段有值由 kind 决定。"""

    # 这一片是什么。
    #
    # 举例：LlmChunkKind.TEXT_DELTA
    kind: LlmChunkKind

    # 增量文本；kind 为 TEXT_DELTA / REASONING_DELTA 时有值，其余为空串。
    #
    # 举例："我来帮你读"
    text: str = ""

    # 一条完整的工具调用；kind 为 TOOL_CALL 时有值。
    #
    # 举例：ToolCallPart(tool_call_id="call_7f3a91", name="read",
    #                    arguments={"path": "a.py"})
    tool_call: ToolCallPart | None = None

    # 用量统计；kind 为 USAGE 时有值。
    #
    # 举例：Usage(input_tokens=15230, output_tokens=418)
    usage: Usage | None = None

    # 为什么停下来；kind 为 DONE 时有值。
    #
    # 举例：StopReason.TOOL_CALLS
    stop_reason: StopReason | None = None


async def collect_text(client: LlmClient, request: LlmRequest) -> str:
    """把一次流式调用收成完整文本 —— 旁路调用用它，那些地方不需要流式呈现。

    只拼 TEXT_DELTA，其余分片（工具调用、用量、结束）忽略。

    举例：summary = await collect_text(client, LlmRequest(model="m", messages=[...]))
    """
    parts: list[str] = []
    async for chunk in client.stream(request):
        if chunk.kind is LlmChunkKind.TEXT_DELTA:
            parts.append(chunk.text)
    return "".join(parts)


class LlmClient(Protocol):
    """模型接入端口。内核在位点 4 直接用它收流。"""

    def stream(self, request: LlmRequest) -> AsyncIterator[LlmChunk]:
        """发起一次流式调用，逐片吐出结果。

        约定：
            - 实现必须是异步生成器；调用方用 async for 消费。
            - 拼装增量 JSON、重试、超时都在实现内部完成，调用方只看到完整分片。
            - 超出上下文窗口时必须抛 ContextOverflowError。
            - 其余失败抛 LlmCallError 并带上 LlmErrorKind —— 内核按种类决定重试还是上抛。

        举例：async for chunk in client.stream(request): ...
        """
        ...

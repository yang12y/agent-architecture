"""OpenAI 兼容的模型接入实现。

把 OpenAI 的流式响应翻译成契约里的四种分片：
文本增量、完整工具调用、用量、结束。

两件事在这里做完，内核不用管：
    1. **增量工具调用 JSON 的拼装** —— OpenAI 把一次调用的 id / name / arguments
       拆成很多片，这里按 index 归并，等流结束再交出完整的一条。
    2. **错误翻译** —— 各家的错误码归一成 LlmCallError.kind 与 ContextOverflowError。

**重试不在这里做**：重试要维护 Runtime State 里的 llm_attempt_number，
那是主循环的职责；所以 SDK 自带的重试被显式关掉（max_retries=0），免得两边各retry一次。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionChunk

from yzy_agent.contracts import (
    AgentError,
    AppConfig,
    ConfigError,
    JsonValue,
    LlmChunk,
    LlmChunkKind,
    LlmRequest,
    StopReason,
    ToolCallPart,
    Usage,
)

from .errors import translate_error
from .messages import to_openai_messages

# OpenAI 的 finish_reason 到契约层 StopReason 的映射。
#
# 认不出来的取值一律按 END_TURN 处理，不因为厂商加了个新值就把整轮搞挂。
# 举例：FINISH_REASON_TO_STOP["tool_calls"] -> StopReason.TOOL_CALLS
FINISH_REASON_TO_STOP: dict[str, StopReason] = {
    "stop": StopReason.END_TURN,
    "tool_calls": StopReason.TOOL_CALLS,
    "function_call": StopReason.TOOL_CALLS,
    "length": StopReason.MAX_TOKENS,
    "content_filter": StopReason.END_TURN,
}


class OpenAiCompatibleClient:
    """用 OpenAI 接口连模型的 LlmClient 实现。

    举例：client = OpenAiCompatibleClient(api_key="sk-...", base_url="https://api.deepseek.com/v1")
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str | None = None,
        timeout: float = 60.0,
        sdk: AsyncOpenAI | None = None,
    ) -> None:
        # 底层的 SDK 客户端。
        #
        # 作用：留出注入点——测试传一个假的进来就能脱网跑完整条链路。
        # 举例：AsyncOpenAI(api_key="sk-...", max_retries=0)
        self._sdk = sdk or AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=0,
        )

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmChunk]:
        """见 contracts.LlmClient.stream。

        举例：async for chunk in client.stream(request): ...
        """
        payload = build_payload(request)
        assembler = _ToolCallAssembler()
        try:
            raw = await self._sdk.chat.completions.create(**payload)
            async for chunk in raw:
                for piece in assembler.feed(chunk):
                    yield piece
            for piece in assembler.finish():
                yield piece
        except AgentError:
            raise
        # 刻意捕获所有异常：SDK 抛的类型五花八门，统一翻译成契约层异常再往上抛，
        # 上层只需要认识 contracts.errors 里那几种。
        except Exception as exc:
            raise translate_error(exc) from exc


def build_openai_client(config: AppConfig) -> OpenAiCompatibleClient:
    """按全局配置建一个 OpenAI 兼容客户端。

    密钥缺失时在建客户端这一步就报错，而不是等第一次请求才失败——
    启动阶段报错比跑到一半报错好排查。密钥只在这里读一次，不写日志、不落盘。

    举例：client = build_openai_client(load_app_config())
    出错：没有配 YZY_LLM_API_KEY 时抛 ConfigError。
    """
    if not config.llm_api_key:
        raise ConfigError("缺少 YZY_LLM_API_KEY：请在项目根目录的 .env 里填上模型服务的密钥")
    return OpenAiCompatibleClient(
        api_key=config.llm_api_key,
        base_url=config.llm_base_url,
        timeout=config.llm_timeout_seconds,
    )


def build_payload(request: LlmRequest) -> dict[str, Any]:
    """把契约请求翻成 OpenAI 的请求体。

    举例：build_payload(LlmRequest(model="m", messages=[]))["stream"] -> True
    """
    payload: dict[str, Any] = {
        "model": request.model,
        "messages": to_openai_messages(request.messages),
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if request.tools_schema:
        payload["tools"] = [
            {"type": "function", "function": schema} for schema in request.tools_schema
        ]
    if request.temperature is not None:
        payload["temperature"] = request.temperature
    if request.thinking:
        # DeepSeek 的思考开关不是 OpenAI 标准参数，只能走 extra_body。
        # 它默认 effort 是 high；要调就从这里再传 reasoning_effort。
        payload["extra_body"] = {"thinking": {"type": "enabled"}}
    return payload


def parse_arguments(raw: str) -> JsonValue:
    """把模型给的 arguments 字符串解析成 JSON。

    解析失败时**原样返回字符串**，交给位点 6 的参数校验去报"参数非法"——
    总好过在这里吞掉异常，让模型以为自己调对了。

    举例：parse_arguments('{"path": "a.py"}') -> {"path": "a.py"}
    """
    text = raw.strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return raw


class _ToolCallAssembler:
    """把散落在流里的工具调用增量拼成完整的一条。

    OpenAI 对一次工具调用的 id / name / arguments 是分片下发的：
    id 只在第一片出现，name 可能也只在第一片，arguments 则是一片一片追加。
    这里按 index 归并，等流读完再统一交出去。
    """

    def __init__(self) -> None:
        # 按 index 归并中的工具调用。
        #
        # 举例：{0: {"id": "call_1", "name": "read", "arguments": '{"path":"a.py"}'}}
        self._slots: dict[int, dict[str, str]] = {}

        # 流结束时模型给的原始 finish_reason；还没收到就是 None。
        #
        # 举例："tool_calls"
        self.finish_reason: str | None = None

    def feed(self, chunk: ChatCompletionChunk) -> list[LlmChunk]:
        """喂一片 SDK 的 chunk，吐出 0 到多个契约分片。

        文本增量是即时吐出的；工具调用要等 finish() 才吐，因为那时才拼完。
        举例：assembler.feed(chunk) -> [LlmChunk(kind=TEXT_DELTA, text="你")]
        """
        out: list[LlmChunk] = []

        if chunk.usage is not None:
            out.append(
                LlmChunk(
                    kind=LlmChunkKind.USAGE,
                    usage=Usage(
                        input_tokens=chunk.usage.prompt_tokens,
                        output_tokens=chunk.usage.completion_tokens,
                    ),
                )
            )

        if not chunk.choices:
            return out

        choice = chunk.choices[0]
        # 思考增量：推理模型（DeepSeek-R1 这类）把它放在 reasoning_content 里。
        # SDK 的 delta 允许扩展字段，所以这里取得到；不认识的厂商就当没有。
        reasoning = getattr(choice.delta, "reasoning_content", None)
        if reasoning:
            out.append(LlmChunk(kind=LlmChunkKind.REASONING_DELTA, text=reasoning))
        if choice.delta.content:
            out.append(LlmChunk(kind=LlmChunkKind.TEXT_DELTA, text=choice.delta.content))

        for piece in choice.delta.tool_calls or ():
            slot = self._slots.setdefault(piece.index, {"id": "", "name": "", "arguments": ""})
            if piece.id:
                slot["id"] = piece.id
            if piece.function is not None:
                if piece.function.name:
                    slot["name"] = piece.function.name
                if piece.function.arguments:
                    slot["arguments"] += piece.function.arguments

        if choice.finish_reason:
            self.finish_reason = choice.finish_reason

        return out

    def finish(self) -> list[LlmChunk]:
        """流读完之后收尾：先吐拼好的工具调用，再吐结束分片。

        举例：assembler.finish() -> [TOOL_CALL 分片, DONE 分片]
        """
        out: list[LlmChunk] = []
        for index in sorted(self._slots):
            slot = self._slots[index]
            out.append(
                LlmChunk(
                    kind=LlmChunkKind.TOOL_CALL,
                    tool_call=ToolCallPart(
                        tool_call_id=slot["id"] or f"call_{index}",
                        name=slot["name"],
                        arguments=parse_arguments(slot["arguments"]),
                    ),
                )
            )
        out.append(
            LlmChunk(
                kind=LlmChunkKind.DONE,
                stop_reason=FINISH_REASON_TO_STOP.get(
                    self.finish_reason or "stop", StopReason.END_TURN
                ),
            )
        )
        return out

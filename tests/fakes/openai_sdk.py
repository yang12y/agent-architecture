"""假的 OpenAI SDK：把预先写好的分片当成流吐出来。

它接在 OpenAiCompatibleClient 的 sdk 注入点上，所以**真的客户端代码、真的收流逻辑**
能在不联网的情况下跑完整条链路 —— 测客户端本身（test_infra）与测它接进循环
（test_integration）用的是同一个假件。

举例：sdk = FakeOpenAiSdk([chunk(content="你好"), chunk(finish="stop")])
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from types import SimpleNamespace
from typing import Any

import httpx
from openai.types.chat import ChatCompletionChunk

# 造 SDK 异常时要用到的假请求对象。
#
# 举例：REQUEST.url -> https://example.com/v1/chat/completions
REQUEST = httpx.Request("POST", "https://example.com/v1/chat/completions")


def http_response(status: int) -> httpx.Response:
    """造一个指定状态码的假响应 —— SDK 异常的构造函数要用它。

    举例：http_response(429).status_code -> 429
    """
    return httpx.Response(status, request=REQUEST)


def chunk(
    *,
    content: str | None = None,
    reasoning: str | None = None,
    tool: dict[str, Any] | None = None,
    finish: str | None = None,
    usage: dict[str, int] | None = None,
    with_choice: bool = True,
) -> ChatCompletionChunk:
    """造一个 SDK 的流式分片。

    tool 里的字段就是 OpenAI 那套：index 必给，id / name / arguments 可以分片下发。
    with_choice=False 用来造"只有用量、没有正文"的那种分片。

    举例：chunk(content="你") -> 一片文本增量
    """
    delta: dict[str, Any] = {}
    if content is not None:
        delta["content"] = content
    if reasoning is not None:
        # 推理模型的思考走这个扩展字段（DeepSeek / Qwen 都是这个名字）。
        delta["reasoning_content"] = reasoning
    if tool is not None:
        piece: dict[str, Any] = {"index": tool["index"], "type": "function"}
        if tool.get("id"):
            piece["id"] = tool["id"]
        function: dict[str, Any] = {}
        if tool.get("name"):
            function["name"] = tool["name"]
        if tool.get("arguments"):
            function["arguments"] = tool["arguments"]
        if function:
            piece["function"] = function
        delta["tool_calls"] = [piece]

    payload: dict[str, Any] = {
        "id": "chunk-1",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "fake-model",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}] if with_choice else [],
    }
    if usage is not None:
        payload["usage"] = usage
    return ChatCompletionChunk.model_validate(payload)


class FakeOpenAiSdk:
    """假的 SDK 客户端，只实现 chat.completions.create。

    三种坏法，正好覆盖一次模型调用的三种失败姿态：
        - error，fail_calls 为 None：**每次调用**都在建连阶段就抛（密钥错、没网）
        - error，fail_calls 为 n：前 n 次抛、之后正常 —— 用来测重试真的救得回来
        - error，error_after 为 n：流吐了 n 片之后才抛 —— 输出到一半断了

    举例：sdk = FakeOpenAiSdk([chunk(content="你")], error=RateLimitError(...))
    """

    def __init__(
        self,
        chunks: Sequence[ChatCompletionChunk] = (),
        *,
        error: Exception | None = None,
        fail_calls: int | None = None,
        error_after: int | None = None,
    ) -> None:
        """绑定分片与失败方式。

        举例：FakeOpenAiSdk([chunk(finish="stop")], error=None)
        """
        # 成功那几次调用要吐的分片。
        #
        # 举例：[chunk(content="你"), chunk(finish="stop")]
        self.chunks = list(chunks)

        # 要抛的 SDK 异常；None 表示这一路都正常。
        #
        # 举例：RateLimitError("slow down", response=http_response(429), body=None)
        self.error = error

        # 前几次调用在**建连阶段**就抛；None 表示每次都抛。
        #
        # 举例：1
        self.fail_calls = fail_calls

        # 流吐到第几片之后抛；None 表示在流开始之前就抛。
        #
        # 举例：1
        self.error_after = error_after

        # 每次收到的请求体 —— 断言"发出去的上下文对不对""到底调了几次"都靠它。
        #
        # 举例：[{"model": "fake-model", "stream": True, ...}]
        self.payloads: list[dict[str, Any]] = []

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **payload: Any) -> AsyncIterator[ChatCompletionChunk]:
        """假装发起请求：该抛就抛，否则返回一个可异步迭代的流。

        举例：流 = await sdk.chat.completions.create(model="m", stream=True)
        """
        self.payloads.append(payload)
        # 给了 error_after 就表示这次调用一开始是好的，所以不在这里抛，留给流吐到一半再抛。
        if self.error is not None and self.error_after is None:
            if self.fail_calls is None or len(self.payloads) <= self.fail_calls:
                raise self.error

        async def stream() -> AsyncIterator[ChatCompletionChunk]:
            for index, item in enumerate(self.chunks):
                if self.error is not None and index == self.error_after:
                    raise self.error
                yield item
            if self.error is not None and self.error_after is not None:
                raise self.error

        return stream()

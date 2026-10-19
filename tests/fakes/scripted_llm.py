"""按脚本吐分片的 LlmClient 假件。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence

from yzy_agent.contracts import (
    CancelToken,
    JsonValue,
    LlmChunk,
    LlmChunkKind,
    LlmRequest,
    StopReason,
    ToolCallPart,
    Usage,
)


class ScriptedLlm:
    """按事先写好的脚本回话的假模型。

    turns 里第 n 个元素，就是第 n 次调用要吐的分片序列。脚本用完再被调用会**直接断言失败**——
    宁可测试当场红，也不要因为主循环不收敛而静默跑飞。

    举例：fake = ScriptedLlm([text_turn("你好")])
    """

    def __init__(self, turns: Sequence[Sequence[LlmChunk]] = ()) -> None:
        # 调用脚本，按轮存放。
        #
        # 举例：[[LlmChunk(kind=LlmChunkKind.TEXT_DELTA, text="你好"), ...]]
        self.turns: list[list[LlmChunk]] = [list(turn) for turn in turns]

        # 每次收到的请求，按顺序记下来，便于断言"发出去的上下文对不对"。
        #
        # 举例：[LlmRequest(model="fake-model", messages=[...])]
        self.requests: list[LlmRequest] = []

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmChunk]:
        """吐出脚本里的下一轮分片。

        举例：async for chunk in fake.stream(request): ...
        """
        self.requests.append(request)
        index = len(self.requests) - 1
        assert index < len(self.turns), (
            f"脚本只写了 {len(self.turns)} 轮，这是第 {index + 1} 次调用——"
            "多半是主循环没收敛，或者测试少写了一段脚本"
        )
        for chunk in self.turns[index]:
            yield chunk


class FailingLlm:
    """前几次调用直接抛错、之后按脚本成功的假模型；用来测重试。

    举例：fake = FailingLlm(RateLimitError(...), fail_times=1, then=text_turn("好了"))
    """

    def __init__(
        self,
        error: Exception,
        *,
        fail_times: int = 1,
        then: Sequence[LlmChunk] = (),
    ) -> None:
        # 前 fail_times 次调用要抛的异常。
        #
        # 举例：LlmCallError("被限流", kind=LlmErrorKind.RATE_LIMIT)
        self.error = error

        # 失败几次之后开始成功。
        #
        # 举例：1
        self.fail_times = fail_times

        # 成功之后要吐的分片。
        #
        # 举例：text_turn("好了")
        self.then = list(then)

        # 收到的请求，用来断言"到底调了几次"。
        #
        # 举例：[request, request]
        self.requests: list[LlmRequest] = []

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmChunk]:
        """见 contracts.LlmClient.stream。"""
        self.requests.append(request)
        if len(self.requests) <= self.fail_times:
            raise self.error
        for chunk in self.then:
            yield chunk


class PartialThenFailLlm:
    """先吐一段文本、再抛错的假模型；模拟"输出中途断流"。

    举例：fake = PartialThenFailLlm("我来帮你", LlmCallError("断了", kind=LlmErrorKind.CONNECTION))
    """

    def __init__(self, text: str, error: Exception) -> None:
        # 抛错之前先吐出来的那段文本。
        #
        # 举例："我来帮你"
        self.text = text

        # 吐完文本之后要抛的异常。
        #
        # 举例：LlmCallError("断了", kind=LlmErrorKind.CONNECTION)
        self.error = error

        # 收到的请求，用来断言"没有重试"。
        #
        # 举例：[request]
        self.requests: list[LlmRequest] = []

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmChunk]:
        """见 contracts.LlmClient.stream。"""
        self.requests.append(request)
        yield LlmChunk(kind=LlmChunkKind.TEXT_DELTA, text=self.text)
        raise self.error


class BlockingLlm:
    """永远不吐分片的假模型：调用会一直挂着，直到任务被取消。

    用来测"进程级取消（CancelledError）也要走收尾"。

    举例：fake = BlockingLlm()
    """

    def __init__(self) -> None:
        # 收到的请求，用来断言"确实调到了模型"。
        #
        # 举例：[LlmRequest(model="fake-model", messages=[])]
        self.requests: list[LlmRequest] = []

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmChunk]:
        """见 contracts.LlmClient.stream。

        举例：async for chunk in fake.stream(request): ...   # 永远等不到
        """
        self.requests.append(request)
        await asyncio.Event().wait()
        yield  # pragma: no cover - 上面那行不会返回，这里只是让它是异步生成器


class StopDuringStreamLlm:
    """吐完第一段增量就替"用户"按下停止的假模型。

    模拟的是"模型还在生成，用户点了停止"：第一段已经落进草稿槽，第二段不该再被收下。

    举例：fake = StopDuringStreamLlm("我来帮你", token)
    """

    def __init__(self, first: str, signal: CancelToken, *, second: str = "不该被收下") -> None:
        """记下第一段、取消信号、以及那段落空的第二段。

        举例：StopDuringStreamLlm("我来帮你", token, second="这段不该出现")
        """
        # 取消信号。吐完第一段之后由它 set。
        #
        # 举例：CancelToken()
        self.signal = signal

        # 第一段增量，会落进草稿槽。
        #
        # 举例："我来帮你"
        self.first = first

        # 第二段增量，正常情况下不该被收下。
        #
        # 举例："这段不该出现"
        self.second = second

        # 收到的请求。
        #
        # 举例：[LlmRequest(model="fake-model", messages=[])]
        self.requests: list[LlmRequest] = []

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmChunk]:
        """见 contracts.LlmClient.stream。

        举例：async for chunk in fake.stream(request): ...
        """
        self.requests.append(request)
        yield LlmChunk(kind=LlmChunkKind.TEXT_DELTA, text=self.first)
        self.signal.set()
        yield LlmChunk(kind=LlmChunkKind.TEXT_DELTA, text=self.second)
        yield LlmChunk(kind=LlmChunkKind.DONE, stop_reason=StopReason.END_TURN)


def text_turn(text: str) -> list[LlmChunk]:
    """造一轮"只说话、不调工具"的回复。

    举例：text_turn("你好") -> [文本增量, 用量, 结束]
    """
    return [
        LlmChunk(kind=LlmChunkKind.TEXT_DELTA, text=text),
        LlmChunk(
            kind=LlmChunkKind.USAGE,
            usage=Usage(input_tokens=10, output_tokens=len(text)),
        ),
        LlmChunk(kind=LlmChunkKind.DONE, stop_reason=StopReason.END_TURN),
    ]


def thought_turn(reasoning: str, text: str) -> list[LlmChunk]:
    """造一轮"先思考、再回答"的回复 —— 推理模型就是这个形状。

    举例：thought_turn("先想想", "好了") -> [思考增量, 文本增量, 用量, 结束]
    """
    return [LlmChunk(kind=LlmChunkKind.REASONING_DELTA, text=reasoning), *text_turn(text)]


def tool_turn(*calls: tuple[str, str, JsonValue]) -> list[LlmChunk]:
    """造一轮"请求调用若干工具"的回复。

    calls 里每项是 (tool_call_id, 工具名, 参数)。
    举例：tool_turn(("call_1", "read", {"path": "a.py"}))
    """
    return [
        LlmChunk(
            kind=LlmChunkKind.TOOL_CALL,
            tool_call=ToolCallPart(tool_call_id=call_id, name=name, arguments=arguments),
        )
        for call_id, name, arguments in calls
    ] + [LlmChunk(kind=LlmChunkKind.DONE, stop_reason=StopReason.TOOL_CALLS)]

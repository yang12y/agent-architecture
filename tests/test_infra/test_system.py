"""底层能力（时间、标识、token 计数）的测试。"""

from __future__ import annotations

import json

from yzy_agent.contracts import Message, MessageRole, TextPart, ThinkingPart, ToolCallPart
from yzy_agent.infra.system import (
    MESSAGE_OVERHEAD_TOKENS,
    HeuristicTokenCounter,
    SystemClock,
    UuidGenerator,
)


def test_system_clock_now_is_timezone_aware() -> None:
    """时间必须带时区，否则跨时区比较会出错。"""
    now = SystemClock().now()

    assert now.tzinfo is not None


def test_system_clock_monotonic_is_non_decreasing() -> None:
    """单调时钟只增不减，适合算耗时。"""
    clock = SystemClock()

    assert clock.monotonic() <= clock.monotonic()


def test_uuid_generator_uses_prefix_and_stays_unique() -> None:
    """标识带前缀，且批量生成不重复。"""
    gen = UuidGenerator()

    first = gen.new_id("trace")
    assert first.startswith("trace_")
    assert len({gen.new_id("e") for _ in range(200)}) == 200


def test_token_counter_counts_cjk_one_per_char() -> None:
    """中日韩字符按 1 字 1 token 估算。"""
    assert HeuristicTokenCounter().count_text("你好世界") == 4


def test_token_counter_counts_other_chars_by_quarter() -> None:
    """其他字符按 4 字 1 token 估算。"""
    assert HeuristicTokenCounter().count_text("abcdefgh") == 2


def test_token_counter_mixes_cjk_and_latin() -> None:
    """混合文本按两类分别算再相加。"""
    assert HeuristicTokenCounter().count_text("你好world") == 2 + 1


def test_token_counter_counts_empty_text_as_zero() -> None:
    """空文本是 0，不是 1。"""
    assert HeuristicTokenCounter().count_text("") == 0


def test_token_counter_adds_message_overhead() -> None:
    """每条消息都要加固定开销，空消息也不例外。"""
    counter = HeuristicTokenCounter()
    message = Message(role=MessageRole.USER, content=[TextPart(text="你好")])

    assert counter.count_messages([message]) == 2 + MESSAGE_OVERHEAD_TOKENS
    assert counter.count_messages([]) == 0
    assert counter.count_messages([message, message]) == 2 * (2 + MESSAGE_OVERHEAD_TOKENS)


def test_token_counter_counts_tool_call_arguments() -> None:
    """工具调用的参数按 JSON 文本算 —— 它可能是要写出去的整个文件正文。"""
    counter = HeuristicTokenCounter()
    arguments = {"path": "a.py", "body": "x" * 400}
    call = Message(
        role=MessageRole.ASSISTANT,
        content=[ToolCallPart(tool_call_id="call_1", name="write", arguments=arguments)],
    )
    same_text = Message(
        role=MessageRole.ASSISTANT,
        content=[TextPart(text=json.dumps(arguments, ensure_ascii=False))],
    )

    assert counter.count_messages([call]) == counter.count_messages([same_text])


def test_token_counter_counts_thinking() -> None:
    """思考要原样传回去，所以它照样占上下文 —— 不算就会低估到被模型挡回来。"""
    counter = HeuristicTokenCounter()
    message = Message(
        role=MessageRole.ASSISTANT,
        content=[ThinkingPart(text="你好"), TextPart(text="你好")],
    )

    assert counter.count_messages([message]) == 2 * 2 + MESSAGE_OVERHEAD_TOKENS


def test_token_counter_does_not_ignore_a_large_tool_call() -> None:
    """参数多长，估算就得跟着多长 —— 漏算会让请求带着超标的上下文出门。"""
    counter = HeuristicTokenCounter()

    def call_with(body: str) -> Message:
        return Message(
            role=MessageRole.ASSISTANT,
            content=[ToolCallPart(tool_call_id="call_1", name="write", arguments={"body": body})],
        )

    assert counter.count_messages([call_with("x" * 4000)]) > 900

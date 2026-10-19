"""一级压缩：长工具结果占位替换。**只改这一轮发出去的内容，不动日志。**"""

from __future__ import annotations

from agent_architecture.contracts import Message, TextPart, ToolCallPart
from agent_architecture.infra.system import HeuristicTokenCounter
from agent_architecture.modules.compression import over_budget, shrink
from fakes import assistant_message, tool_message, user_message

# 一个"很长"的工具结果。
#
# 举例："x" * 2000
LONG = "x" * 2000

# 测试统一用这个计数器，省得每处都写一遍。
#
# 举例：COUNTER.count_text("你好") -> 2
COUNTER = HeuristicTokenCounter()


def _text(message: Message) -> str:
    """一条消息的纯文本。

    举例：_text(tool_message("x")) -> "x"
    """
    return "".join(part.text for part in message.content if isinstance(part, TextPart))


def _messages() -> list[Message]:
    """一次工具调用的完整来回：用户问、助手调、工具回。

    举例：_messages()[2].role -> MessageRole.TOOL
    """
    call = assistant_message("我来读")
    call.content.append(
        ToolCallPart(tool_call_id="call_1", name="read", arguments={"path": "a.py"})
    )
    return [user_message("帮我读 a.py"), call, tool_message(LONG, tool_call_id="call_1")]


def test_a_short_tool_result_is_left_alone() -> None:
    """没到门槛的结果原样留着，也一分钱都没省。"""
    messages = [tool_message("短的", tool_call_id="call_1")]

    shrunk, saved = shrink(messages, tokens=COUNTER, max_tokens=100, keep_recent=0)

    assert shrunk == messages
    assert saved == 0


def test_a_long_tool_result_is_replaced_with_a_placeholder() -> None:
    """超门槛的换成一行占位词。"""
    shrunk, _ = shrink(_messages(), tokens=COUNTER, max_tokens=10, keep_recent=0)

    assert "已省略" in _text(shrunk[2])
    assert LONG not in _text(shrunk[2])


def test_the_savings_are_reported_so_nobody_has_to_recount() -> None:
    """省下的量当场算给调用方 —— 它做减法就够了，不用把整份上下文重数一遍。"""
    messages = _messages()

    shrunk, saved = shrink(messages, tokens=COUNTER, max_tokens=10, keep_recent=0)

    expected = COUNTER.count_messages([messages[2]]) - COUNTER.count_messages([shrunk[2]])
    assert saved == expected
    assert saved > 0


def test_the_placeholder_keeps_the_tool_name_and_arguments() -> None:
    """占位词要留下"是谁、什么参数、原来多长"。"""
    shrunk, _ = shrink(_messages(), tokens=COUNTER, max_tokens=10, keep_recent=0)

    text = _text(shrunk[2])
    assert "read" in text
    assert "a.py" in text
    assert str(len(LONG)) in text


def test_the_last_few_long_results_keep_their_text() -> None:
    """最近几条长结果保留原文 —— 那多半是这一轮正要用的。"""
    messages = [
        tool_message(LONG, tool_call_id="call_1"),
        tool_message(LONG, tool_call_id="call_2"),
    ]

    shrunk, saved = shrink(messages, tokens=COUNTER, max_tokens=10, keep_recent=1)

    assert "已省略" in _text(shrunk[0])
    assert _text(shrunk[1]) == LONG
    # 只换了第一条，所以省下的也就是第一条的量。
    assert saved == COUNTER.count_messages([messages[0]]) - COUNTER.count_messages([shrunk[0]])


def test_non_tool_messages_are_never_touched() -> None:
    """用户与助手消息不在一级的射程里。"""
    long_user = user_message(LONG)

    shrunk, saved = shrink([long_user], tokens=COUNTER, max_tokens=10, keep_recent=0)

    assert shrunk == [long_user]
    assert saved == 0


def test_the_original_list_is_not_mutated() -> None:
    """返回的是新列表，调用方原来那份不动。"""
    messages = _messages()

    shrink(messages, tokens=COUNTER, max_tokens=10, keep_recent=0)

    assert _text(messages[2]) == LONG


def test_over_budget_edges() -> None:
    """到阈值就算到；阈值没配（0 或负数）就当没配。"""
    assert over_budget(100, 100) is True
    assert over_budget(99, 100) is False
    assert over_budget(100, 0) is False

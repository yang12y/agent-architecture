"""一级压缩：把较早的长工具结果正文换成占位词。

**只改这一轮发出去的内容，一个字节都不落盘** —— entries 没动，下一轮投影拿到的还是原文。
所以它是"先便宜试一把"的手段，不是对事实的改写。
"""

from __future__ import annotations

from collections.abc import Sequence

from yzy_agent.contracts import (
    JsonValue,
    Message,
    MessageRole,
    TextPart,
    TokenCounter,
    ToolCallPart,
)


def shrink(
    messages: Sequence[Message],
    *,
    tokens: TokenCounter,
    max_tokens: int,
    keep_recent: int,
) -> tuple[list[Message], int]:
    """换掉较早的长工具结果；最近 keep_recent 条长结果保持原文。

    返回的第一项是新列表，第二项是**省下了多少 token** —— 只数被换掉的那几条，
    调用方拿它做减法就行，不必把整份上下文重数一遍。

    举例：shrink(messages, tokens=counter, max_tokens=2000, keep_recent=3)
          -> (messages, 1234)
    """
    calls = _call_index(messages)
    long_ones = [
        index
        for index, message in enumerate(messages)
        if _is_long_result(message, tokens=tokens, max_tokens=max_tokens)
    ]
    kept = set(long_ones[-keep_recent:]) if keep_recent > 0 else set()

    shrunk: list[Message] = []
    saved = 0
    for index, message in enumerate(messages):
        if index not in long_ones or index in kept:
            shrunk.append(message)
            continue
        replaced = _placeholder(message, calls.get(_call_id(message)))
        # 消息开销两边一样，只有正文变了，所以差值就是这一条省下来的量。
        saved += tokens.count_messages([message]) - tokens.count_messages([replaced])
        shrunk.append(replaced)
    return shrunk, saved


def _is_long_result(message: Message, *, tokens: TokenCounter, max_tokens: int) -> bool:
    """这条是不是"该省掉"的长工具结果。

    举例：_is_long_result(tool_message("……"), tokens=counter, max_tokens=2000) -> True
    """
    return message.role is MessageRole.TOOL and tokens.count_text(_text(message)) > max_tokens


def _placeholder(message: Message, call: tuple[str, JsonValue] | None) -> Message:
    """换成一行占位词，保留工具名、关键参数与原文规模。

    举例：_placeholder(tool_message("……"), ("read", {"path": "a.py"}))
    """
    name, arguments = call if call is not None else ("未知工具", None)
    text = f"[工具 {name} 的结果已省略：原文约 {len(_text(message))} 字"
    if arguments is not None:
        text += f"，参数 {arguments}"
    return Message(
        role=message.role,
        content=[TextPart(text=f"{text}]")],
        metadata=message.metadata,
    )


def _call_index(messages: Sequence[Message]) -> dict[str, tuple[str, JsonValue]]:
    """工具调用 id -> (工具名, 参数)，从 assistant 消息里收集。

    举例：_call_index(messages) -> {"call_1": ("read", {"path": "a.py"})}
    """
    index: dict[str, tuple[str, JsonValue]] = {}
    for message in messages:
        for part in message.content:
            if isinstance(part, ToolCallPart):
                index[part.tool_call_id] = (part.name, part.arguments)
    return index


def _call_id(message: Message) -> str:
    """工具结果消息配对的调用 id；没有就是空串。

    举例：_call_id(tool_message("x", tool_call_id="call_1")) -> "call_1"
    """
    value = message.metadata.get("tool_call_id")
    return value if isinstance(value, str) else ""


def _text(message: Message) -> str:
    """一条消息的纯文本部分。

    举例：_text(tool_message("文件内容")) -> "文件内容"
    """
    return "".join(part.text for part in message.content if isinstance(part, TextPart))

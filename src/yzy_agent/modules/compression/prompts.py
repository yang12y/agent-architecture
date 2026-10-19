"""摘要提示词：把要压掉的消息摊成文本，填进模板。"""

from __future__ import annotations

from collections.abc import Sequence

from yzy_agent.contracts import Message, MessageRole, TextPart, ToolCallPart

# 每个角色在提示词里怎么称呼。
#
# 举例：_LABELS[MessageRole.USER] == "用户"
_LABELS = {
    MessageRole.USER: "用户",
    MessageRole.ASSISTANT: "助手",
    MessageRole.TOOL: "工具",
}


def render(messages: Sequence[Message]) -> str:
    """把一段消息摊成提示词里的对话正文。

    工具结果与工具调用都要带上 —— 路径、参数、报错原文都在那里，而提示词要求逐字保留。

    举例：render([user_message("帮我读 a.py")]) -> "用户：帮我读 a.py"
    """
    return "\n".join(
        f"{_LABELS.get(message.role, message.role.value)}：{_body(message)}" for message in messages
    )


def summary_prompt(template: str, messages: Sequence[Message]) -> str:
    """按模板生成摘要提示词。

    举例：summary_prompt("……{history}……", messages)
    """
    return template.format(history=render(messages))


def _body(message: Message) -> str:
    """一条消息的正文：文本块直出，工具调用写成一行。

    举例：_body(assistant_message("好的")) -> "好的"
    """
    parts: list[str] = []
    for part in message.content:
        if isinstance(part, TextPart):
            parts.append(part.text)
        elif isinstance(part, ToolCallPart):
            parts.append(f"[调用工具 {part.name}，参数 {part.arguments}]")
    return "".join(parts) or "（空）"

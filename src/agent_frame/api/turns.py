"""轮次大纲：右侧导航栏那一行一行的数据。

一行 = **一条用户消息**，不是一次模型往返 —— 后者太碎（用户问一句、模型调五个工具就是六行），
拿它导航等于没导航。

回复取该用户消息之后、下一条用户消息之前**最后一条带文本的 assistant 消息**：模型常把结论
放在最后一段，中间的正文只是过程。

纯函数，和 api/stats.py 同一条路子：从日志现算，不存任何东西。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from agent_frame.contracts import (
    Message,
    MessageRole,
    SessionEntry,
    SessionEntryType,
    TextPart,
)

from .schemas import TurnOut

# 大纲最多给多少行。导航栏放不下更多，多出来的也只是把每次响应撑大。
#
# 举例：200
TURN_OUTLINE_MAX = 200

# 提示词与回答预览各留多少字。截断时省略号算在上限内（与标题同一条规矩）。
#
# 举例：50 / 120
PROMPT_CHARS = 50
RESPONSE_CHARS = 120

# 截断时补的那个字符。
#
# 举例："…"
ELLIPSIS = "…"


@dataclass(slots=True)
class _Row:
    """折的过程中临时攒的一行；对外只出 TurnOut。"""

    # 这条用户消息的 seq，也是跳转目标。
    #
    # 举例：42
    seq: int

    # 提示词预览。
    #
    # 举例："帮我看看 1# 空压机昨天的出口压力"
    prompt: str

    # 回答预览；这一轮还没有定稿正文时是空串。
    #
    # 举例："昨天平均 0.64 MPa"
    response: str


def turn_outline(entries: Sequence[SessionEntry]) -> list[TurnOut]:
    """把一段日志折成轮次大纲，返回最后 TURN_OUTLINE_MAX 行（行号仍是绝对序号）。

    举例：turn_outline(entries) -> [TurnOut(turn=1, seq=2, prompt="帮我读 a.py", response="…")]
    """
    rows: list[_Row] = []
    for entry in entries:
        message = _message_of(entry)
        if message is None:
            continue
        text = _text_of(message)
        if message.role is MessageRole.USER:
            rows.append(_Row(seq=entry.seq, prompt=_preview(text, PROMPT_CHARS), response=""))
        elif message.role is MessageRole.ASSISTANT and text and rows:
            # 同一轮里常有好几条 assistant 消息（两次工具往返之间的过渡语），
            # 只留最后一条有正文的 —— 结论在后面。
            rows[-1].response = _preview(text, RESPONSE_CHARS)

    kept = rows[-TURN_OUTLINE_MAX:]
    offset = len(rows) - len(kept)
    return [
        TurnOut(turn=offset + index + 1, seq=row.seq, prompt=row.prompt, response=row.response)
        for index, row in enumerate(kept)
    ]


def _message_of(entry: SessionEntry) -> Message | None:
    """是 message 条目就取它的 payload，否则 None。

    举例：_message_of(SessionEntry(type=MESSAGE, ...)) -> Message(role=USER, ...)
    """
    if entry.type is SessionEntryType.MESSAGE and isinstance(entry.payload, Message):
        return entry.payload
    return None


def _text_of(message: Message) -> str:
    """一条消息里全部文本块拼起来。思考、图片、工具调用都不算 —— 它们不是给人读的回答。

    举例：_text_of(Message(role=USER, content=[TextPart(text="你好")])) -> "你好"
    """
    return " ".join(part.text for part in message.content if isinstance(part, TextPart))


def _preview(text: str, limit: int) -> str:
    """把一段文本压成一行并限长；截断时把省略号算在上限内。

    举例：_preview("a\nb", 10) -> "a b"
    """
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1] + ELLIPSIS

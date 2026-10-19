"""压完之后的预算判定：这次到底放不放得下。"""

from __future__ import annotations

from collections.abc import Sequence

from agent_architecture.contracts import ContextOverflowError, Message, TokenCounter

from .trigger import over_budget


def count(messages: Sequence[Message], *, tokens: TokenCounter) -> int:
    """这批 messages 占多少 token。

    举例：count(payload.messages, tokens=counter) -> 42815
    """
    return tokens.count_messages(messages)


def guard(total: int, *, compact_tokens: int) -> None:
    """压完还超就抛 —— 内核按 budget_exceeded 收尾。

    收的是**算好的数**：调用方刚数过一遍，不在这里再数第二遍。

    这是"这次真的放不下"的判据：能压的都压过了，再往下只能失败。

    举例：guard(120000, compact_tokens=100000) -> 抛 ContextOverflowError
    """
    if over_budget(total, compact_tokens):
        raise ContextOverflowError(f"压缩之后上下文仍有 {total} token，阈值是 {compact_tokens}")

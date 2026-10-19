"""触发判定：什么时候该压缩。"""

from __future__ import annotations


def over_budget(context_tokens: int, compact_tokens: int) -> bool:
    """上下文触达压缩阈值了没有。

    阈值配成 0 或负数就当没配 —— 免得它变成一个"永远触发"的开关。

    举例：over_budget(100_000, 100_000) -> True
    """
    return compact_tokens > 0 and context_tokens >= compact_tokens

"""标识生成器的实现。"""

from __future__ import annotations

import uuid


class UuidGenerator:
    """用 uuid4 生成标识的 IdGenerator 实现。

    前缀让人一眼看出这个 id 是什么，后缀取 12 位就够用且便于阅读。
    举例：gen = UuidGenerator(); gen.new_id("trace") -> "trace_9c8b7a1d2e3f"
    """

    def new_id(self, prefix: str) -> str:
        """生成 "{prefix}_{12位十六进制}" 形式的唯一标识。

        举例：gen.new_id("e") -> "e_4f2a91bc0d3e"
        """
        return f"{prefix}_{uuid.uuid4().hex[:12]}"

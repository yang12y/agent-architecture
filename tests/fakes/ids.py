"""确定性的标识生成假件。"""

from __future__ import annotations


class SequentialIds:
    """按前缀各自计数，生成可预测的标识。

    举例：ids = SequentialIds(); ids.new_id("e") -> "e_000001"; ids.new_id("e") -> "e_000002"
    """

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}

    def new_id(self, prefix: str) -> str:
        """生成 "{prefix}_{6位序号}"，同一前缀内递增。

        举例：ids.new_id("trace") -> "trace_000001"
        """
        nxt = self._counters.get(prefix, 0) + 1
        self._counters[prefix] = nxt
        return f"{prefix}_{nxt:06d}"

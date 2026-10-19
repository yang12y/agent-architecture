"""D5 压缩：上下文触达阈值时的两级压缩。**先便宜后昂贵。**

一级是纯字符串替换（只改这一轮发出去的内容），二级才调一次摘要模型、落一条 compaction 条目。
压缩是**有损的**：被压掉的正文不保留副本，摘要取代它们。
"""

from .controller import compact_at_llm_before
from .handler import build_handlers
from .level1_placeholder import shrink
from .level2_summary import compact
from .token_budget import count, guard
from .trigger import over_budget

__all__ = [
    "build_handlers",
    "compact",
    "compact_at_llm_before",
    "count",
    "guard",
    "over_budget",
    "shrink",
]

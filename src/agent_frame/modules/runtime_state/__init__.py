"""维度 9：Runtime State。

结构（RuntimeState / ToolStat / ToolCall）定义在 contracts/models.py，
这里放算法：位点操作、由 Session 建状态、待办队列、工具统计。
"""

from .bootstrap import bootstrap_runtime_state
from .handler import build_handlers
from .ops import (
    s01_trace_before,
    s02_turn_before,
    s03_llm_before,
    s04_llm_during,
    s05_llm_after,
    s06_tool_before,
    s07_tool_during,
    s08_tool_after,
    s10_trace_end,
)
from .pending_queue import drain_messages, enqueue_message
from .tool_stats import record_failure, record_success

__all__ = [
    "bootstrap_runtime_state",
    "build_handlers",
    "drain_messages",
    "enqueue_message",
    "record_failure",
    "record_success",
    # 逐位点操作。按位点号导出，测试直接指名哪一位点。
    "s01_trace_before",
    "s02_turn_before",
    "s03_llm_before",
    "s04_llm_during",
    "s05_llm_after",
    "s06_tool_before",
    "s07_tool_during",
    "s08_tool_after",
    "s10_trace_end",
]

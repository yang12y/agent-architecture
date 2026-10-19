"""D6 Checkpoint：决定日志什么时候必须落盘，并在崩溃后把它恢复成完整会话。

它不产生任何结构 —— 不定义新条目类型，也不参与 Runtime State 重建。

两块：

    scheduler  屏障落盘（位点 3/5/6/8/10）
    recovery   恢复七步（位点 1），配平逻辑在 crash / rebalance 里
"""

from .crash import Tail, TailKind, classify_tail, open_trace_id
from .handler import build_handlers
from .rebalance import DanglingCall, dangling_calls, is_retryable, result_message
from .recovery import Repair, apply, plan
from .scheduler import Checkpoint

__all__ = [
    "Checkpoint",
    "DanglingCall",
    "Repair",
    "Tail",
    "TailKind",
    "apply",
    "build_handlers",
    "classify_tail",
    "dangling_calls",
    "is_retryable",
    "open_trace_id",
    "plan",
    "result_message",
]

"""D2 Tool Calling：工具注册、位点 6 检查链、执行、结果回填。

一条调用的一生：**检查链 → 判定 → 执行 → 结果**。

    - 位点 6 分两段（中间隔着 sandbox 的判定）：检查链写 EXECUTE，收尾给不执行的写结果消息；
    - 位点 7 执行，**任何失败都收成结果而不是上抛**；
    - 位点 8 规范化并截断，落成一条 tool 消息。

不变量：**模型发出的每一条 tool_call 都会收到一条 tool 消息**，要么是结果，要么是原因。
"""

from .builtin import build_catalog
from .chain import finalize, screen
from .checks import validate, with_defaults
from .executor import DEFAULT_TIMEOUT_SECONDS, Outcome, run
from .handler import build_handlers
from .output import normalize, truncate
from .registry import Tool, ToolCatalog, ToolContext, ToolFn
from .scheduling import is_parallel_safe, plan_for

__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "Outcome",
    "Tool",
    "ToolCatalog",
    "ToolContext",
    "ToolFn",
    "build_catalog",
    "build_handlers",
    "finalize",
    "is_parallel_safe",
    "normalize",
    "plan_for",
    "run",
    "screen",
    "truncate",
    "validate",
    "with_defaults",
]

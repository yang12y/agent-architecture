"""D8 沙箱与权限：工具执行的安全边界。**不判"危险"，判"越界"。**

把参数解析成"这次调用碰的是什么"，再跟当前权限档位比。判不准的（命令、网络这类能力）
不在参数级硬判 —— 交给唯一判得了的一方：用户。判不了不等于放行，也不等于拒绝。

判定全是纯函数，三档 × 参数组合可以穷举单测。落到实处的动作只有一个：
把三态写进位点 6 的 `decisions` —— 内核只对 `execute` 的调用往下跑，边界就生效了。
"""

from .approval import CAPABILITY_KINDS, STRICTNESS, Verdict, decide, strictest
from .enforcement import enforce
from .handler import PRIORITY_TOOL_BEFORE, build_handlers
from .permission import PERMISSION_LADDER, at_least, rank
from .policies import ARGUMENT_KINDS, ResourceKind, inside, resolve_path, route

__all__ = [
    "build_handlers",
    "decide",
    "enforce",
    "strictest",
    "at_least",
    "rank",
    "route",
    "inside",
    "resolve_path",
    "ARGUMENT_KINDS",
    "CAPABILITY_KINDS",
    "PERMISSION_LADDER",
    "PRIORITY_TOOL_BEFORE",
    "STRICTNESS",
    "ResourceKind",
    "Verdict",
]

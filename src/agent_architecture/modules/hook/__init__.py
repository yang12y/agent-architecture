"""D7 Hook：在固定位点注入用户逻辑的机制。

挂载点**就是十位点**，不新增扩展点。hook 排在维度内置逻辑之后（priority 500 段）、
落盘屏障之前 —— 所以它看得到维度算出来的结果，改的数据也落得下去。

工具守卫（`tools/pre_execute`）**还没做**：它判的是 `decisions`，而那个通道要等 S7
tool_calling 落地才有人写 `execute`。等那时候再连着"整批还是逐条"一起定。

需要一张注册表才能建 handler，所以它是唯一一个 build_handlers 要带参数的模块。
"""

from . import events
from .handler import PRIORITY_HOOKS, build_handlers
from .registry import Hook, HookRegistry, HookSource, SiteHook
from .runner import run_hooks

__all__ = [
    "build_handlers",
    "events",
    "run_hooks",
    "Hook",
    "HookRegistry",
    "HookSource",
    "SiteHook",
    "PRIORITY_HOOKS",
]

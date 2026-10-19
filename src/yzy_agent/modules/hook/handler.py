"""hook 在位点上的执行。**hook 段位是 500-899。**"""

from __future__ import annotations

from yzy_agent.contracts import Handler, Site, SiteContext

from .registry import HookRegistry
from .runner import run_hooks

# 位点事件的执行次序。取 hook 段位的下界：必须**大于**维度内置逻辑（100-499），
# 这样 hook 才看得到维度算出来的结果；又必须**小于**落盘屏障（900），
# 否则它改的数据落不下去。
#
# 举例：500
PRIORITY_HOOKS = 500


class _HookHandler:
    """把"跑一遍本位点的 hook"包成 Handler。**十个位点共用一个实例。**

    举例：_HookHandler(registry)
    """

    def __init__(self, registry: HookRegistry) -> None:
        """绑上这张注册表。

        举例：_HookHandler(HookRegistry())
        """
        # handler 唯一标识，日志与排错靠它定位。
        #
        # 举例："hook.site"
        self.id = "hook.site"

        # 十个位点全挂：hook 的挂载点就是位点本身。
        #
        # 举例：frozenset({Site.TRACE_BEFORE, ...})
        self.sites: frozenset[Site] = frozenset(Site)

        # 举例：500
        self.priority = PRIORITY_HOOKS

        # 用户注册的那些 hook。
        #
        # 举例：HookRegistry()
        self._registry = registry

    async def run(self, ctx: SiteContext) -> bool | None:
        """跑本位点的 hook。位点 1/6 上返回值就是否决信号，别处无意义。

        举例：await handler.run(ctx)
        """
        return await run_hooks(ctx, self._registry)


def build_handlers(registry: HookRegistry) -> tuple[Handler, ...]:
    """造出 hook 要注册的全部 handler，交给装配根。

    现在只有位点事件这一个。工具守卫（`tools/pre_execute`）等 S7 tool_calling 落地后
    再写 —— 它判的是 `decisions`，而那个通道现在没人写 `execute`。

    举例：registry = HandlerRegistry([*hook.build_handlers(hooks)])
    """
    return (_HookHandler(registry),)

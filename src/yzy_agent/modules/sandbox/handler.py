"""sandbox 在位点上的 SiteHandler。"""

from __future__ import annotations

from yzy_agent.contracts import Handler, Site, SiteContext

from .enforcement import enforce

# 位点 6 的次序：排在 **tool_calling 的检查链之后**（decisions 的前半段由它填）、
# `runtime_state`(450) **之前** —— 那个 handler 读 decisions 把不执行的调用从待执行
# 队列里摘掉，晚一步写下的判定在这个位点就没人读了。
#
# 举例：435
PRIORITY_TOOL_BEFORE = 435


class _BoundaryHandler:
    """把"逐条判工具调用"包成位点 6 的 Handler。

    它**从不否决整批**：结论是逐条写进 decisions 的。不执行的调用由 tool_calling 在位点 6
    自己收尾（I10），本模块只负责说"哪条不执行"。

    举例：_BoundaryHandler()
    """

    def __init__(self) -> None:
        """挂上位点 6。

        举例：_BoundaryHandler()
        """
        # 举例："sandbox.tool_before"
        self.id = "sandbox.tool_before"

        # 举例：frozenset({Site.TOOL_BEFORE})
        self.sites: frozenset[Site] = frozenset({Site.TOOL_BEFORE})

        # 举例：435
        self.priority = PRIORITY_TOOL_BEFORE

    async def run(self, ctx: SiteContext) -> bool | None:
        """判完这一批。没有否决整批这回事。

        举例：await handler.run(ctx)
        """
        await enforce(ctx)
        return None


def build_handlers() -> tuple[Handler, ...]:
    """造出 sandbox 要注册的全部 handler，交给装配根。

    依赖从 `ctx.ports` 取（工具注册表、审批端口），所以跟别的模块一样不用带参数。

    举例：registry = HandlerRegistry([*sandbox.build_handlers()])
    """
    return (_BoundaryHandler(),)

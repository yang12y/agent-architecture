"""compression 在各个位点上的 SiteHandler。**一个实例只挂一个位点。**"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from agent_architecture.contracts import Handler, Site, SiteContext

from .controller import compact_at_llm_before

# 位点 3 的次序：runtime_state(100) 算完 context_tokens → **compression** → session(300) 清草稿槽。
# 必须**小于 session 的 300**：二级压缩要重建上下文，重建时草稿槽得还在。
#
# 举例：200
PRIORITY_LLM_BEFORE = 200

# 一个位点操作的统一签名：异步，因为它要等摘要模型。
#
# 举例：compact_at_llm_before
SiteOp = Callable[[SiteContext], Awaitable[None]]


class _SiteOpHandler:
    """把一个位点操作包成 Handler。

    举例：_SiteOpHandler("compression.llm_before", Site.LLM_BEFORE, 200, compact_at_llm_before)
    """

    def __init__(self, handler_id: str, site: Site, priority: int, op: SiteOp) -> None:
        """绑定一个位点操作。

        举例：_SiteOpHandler("compression.llm_before", Site.LLM_BEFORE, 200, op)
        """
        # handler 唯一标识，日志与排错靠它定位。
        #
        # 举例："compression.llm_before"
        self.id = handler_id

        # 这个 handler 挂在哪一个位点上。
        #
        # 举例：frozenset({Site.LLM_BEFORE})
        self.sites: frozenset[Site] = frozenset({site})

        # 执行优先级，数值越小越先跑。
        #
        # 举例：200
        self.priority = priority

        # 本位点真正要跑的那段逻辑。
        #
        # 举例：compact_at_llm_before
        self._op = op

    async def run(self, ctx: SiteContext) -> bool | None:
        """跑本位点的操作。compression 没有否决语义，返回值一律 None。

        举例：await handler.run(ctx)
        """
        await self._op(ctx)
        return None


def build_handlers() -> tuple[Handler, ...]:
    """造出 compression 要注册的全部 handler，交给装配根。

    举例：registry = HandlerRegistry(build_handlers())
    """
    return (
        _SiteOpHandler(
            "compression.llm_before", Site.LLM_BEFORE, PRIORITY_LLM_BEFORE, compact_at_llm_before
        ),
    )

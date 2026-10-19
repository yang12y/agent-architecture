"""位点 handler 的假件。"""

from __future__ import annotations

from collections.abc import Iterable

from agent_architecture.contracts import AgentError, Site, SiteContext


class RecordingHandler:
    """把"被调用过"记下来的 handler。

    可以指定返回值（放行/否决）或抛出异常，用来测管线的三种收场。
    举例：h = RecordingHandler("a", {Site.TURN_BEFORE}, priority=100)
    """

    def __init__(
        self,
        handler_id: str,
        sites: Iterable[Site],
        *,
        priority: int = 100,
        result: bool | None = None,
        error: AgentError | None = None,
    ) -> None:
        # handler 标识。
        #
        # 举例："fake.a"
        self.id = handler_id

        # 挂载的位点。
        #
        # 举例：frozenset({Site.TURN_BEFORE})
        self.sites = frozenset(sites)

        # 执行优先级，数值越小越先跑。
        #
        # 举例：100
        self.priority = priority

        # run() 的返回值：None/True 继续，False 否决。
        #
        # 举例：False
        self.result = result

        # 非 None 时 run() 直接抛出它，用来测异常传播。
        #
        # 举例：FlushError("磁盘写满")
        self.error = error

        # 每次被调用时收到的上下文，用来断言 payload 是不是同一个对象。
        #
        # 举例：[SiteContext(site=Site.TURN_BEFORE, ...)]
        self.calls: list[SiteContext] = []

    async def run(self, ctx: SiteContext) -> bool | None:
        """记录调用，然后按配置返回或抛错。

        举例：await handler.run(ctx) -> False
        """
        self.calls.append(ctx)
        if self.error is not None:
            raise self.error
        return self.result


class HandlerSpy(RecordingHandler):
    """按调用顺序记进共享列表的 handler，用来断言跨 handler 的执行次序。

    举例：order = []; HandlerSpy("b", sites, order=order, priority=200)
    """

    def __init__(
        self,
        handler_id: str,
        sites: Iterable[Site],
        *,
        order: list[str],
        priority: int = 100,
        result: bool | None = None,
    ) -> None:
        super().__init__(handler_id, sites, priority=priority, result=result)
        self._order = order

    async def run(self, ctx: SiteContext) -> bool | None:
        self._order.append(self.id)
        return await super().run(ctx)

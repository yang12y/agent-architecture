"""checkpoint 在各个位点上的 SiteHandler。

两件事，各挂一处：

    位点 1  (priority 50)   恢复判据：日志尾部不干净就先配平并落盘，再让新 trace 开始
    位点 3/5/6/8/10 (900)  落盘屏障：走到这里，日志必须已经在盘上

priority 只用 50 与 900 两个值，因为它们分别是 recovery 段与 barrier 段的**唯一取值**
（见 PRIORITY_BANDS）。屏障必须排在最后：排在它后面的 handler 改了什么，都落不下去。
"""

from __future__ import annotations

from agent_architecture.contracts import Handler, Site, SiteContext, ToolRegistry

from . import barriers, recovery
from .crash import classify_tail
from .scheduler import Checkpoint

# 位点 1：恢复判据必须抢在所有维度之前 —— 晚一步 session 就已经把新 trace 写下去了，
# 悬空调用再也分不清是哪一次的。
#
# 举例：50
PRIORITY_RECOVERY = 50

# 位点 3/5/6/8/10：落盘屏障，必须最后跑。
#
# 举例：900
PRIORITY_BARRIER = 900


class _RecoveryHandler:
    """位点 1：判定尾部，需要就把悬空调用补成完整日志。

    举例：_RecoveryHandler(checkpoint)
    """

    def __init__(self, checkpoint: Checkpoint, tools: ToolRegistry | None) -> None:
        """绑定调度器与（可选的）工具注册表。

        举例：_RecoveryHandler(checkpoint, registry)
        """
        # handler 唯一标识。
        #
        # 举例："checkpoint.trace_before"
        self.id = "checkpoint.trace_before"

        # 挂在位点 1。
        #
        # 举例：frozenset({Site.TRACE_BEFORE})
        self.sites: frozenset[Site] = frozenset({Site.TRACE_BEFORE})

        # 恢复段。
        #
        # 举例：50
        self.priority = PRIORITY_RECOVERY

        # 落盘调度器。
        #
        # 举例：Checkpoint(store)
        self._checkpoint = checkpoint

        # 工具注册表；没有就按"不可安全重试"补正文。
        #
        # 举例：registry
        self._tools = tools

    async def run(self, ctx: SiteContext) -> bool | None:
        """走到这里时日志里还只有上一次留下的内容，正好判尾部。

        干净就什么都不做（绝大多数 trace 都是这样）。不干净才补，补完立刻落盘。

        举例：await handler.run(ctx)
        """
        tail = classify_tail(ctx.session.entries())
        if not tail.needs_repair:
            return None

        repaired = await recovery.apply(
            self._checkpoint,
            ctx.session,
            tail,
            now=ctx.ports.clock.now(),
            tools=self._tools,
        )
        ctx.ports.logger.warning(
            "checkpoint.recovered",
            session_id=ctx.session.meta.session_id,
            tail=tail.kind.value,
            broken_trace_id=tail.trace_id,
            repaired_entries=repaired,
        )
        return None


class _BarrierHandler:
    """一个位点上的落盘屏障。一个实例只挂一个位点。

    举例：_BarrierHandler(Site.TOOL_BEFORE)
    """

    def __init__(self, site: Site, checkpoint: Checkpoint) -> None:
        """绑定一个屏障位点。

        举例：_BarrierHandler(Site.LLM_BEFORE, checkpoint)
        """
        # handler 唯一标识，形如"checkpoint.llm_before"。
        #
        # 举例："checkpoint.tool_before"
        self.id = f"checkpoint.{site.name.lower()}"

        # 这个 barrier 守的位点。
        #
        # 举例：frozenset({Site.TOOL_BEFORE})
        self.sites: frozenset[Site] = frozenset({site})

        # 屏障段，必须最后跑。
        #
        # 举例：900
        self.priority = PRIORITY_BARRIER

        # 落盘调度器。
        #
        # 举例：Checkpoint(store)
        self._checkpoint = checkpoint

    async def run(self, ctx: SiteContext) -> bool | None:
        """把日志推到末尾。推不动（已经到末尾）就是空操作。

        落盘失败时 FlushError 直接冒出去：位点 3 拦掉模型调用，位点 6 拦掉工具正文。
        这就是"失败即阻止"，由内核统一收尾。

        举例：await handler.run(ctx)
        """
        await self._checkpoint.ensure(ctx.session, reason=ctx.site.value)
        return None


def build_handlers(
    checkpoint: Checkpoint, *, tools: ToolRegistry | None = None
) -> tuple[Handler, ...]:
    """造出 checkpoint 要注册的全部 handler，交给装配根。

    一个位点 1 的恢复判据，加五个落盘屏障。

    举例：registry = HandlerRegistry(build_handlers(checkpoint, tools=registry))
    """
    return (
        _RecoveryHandler(checkpoint, tools),
        *(_BarrierHandler(site, checkpoint) for site in sorted(barriers.BARRIER_SITES)),
    )

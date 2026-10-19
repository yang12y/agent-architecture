"""位点事件的 hook 执行。

它跑的是"这一刻能跑的东西"，返回值照位点语义解释：

    - **位点 1 与位点 6**：hook 返回 False 就是否决，立刻短路本位点。
    - **其余八个位点**：返回值一律不看 —— 位点语义里没有否决这回事，
      hook 在这一刻只能观察与补数据。返回 False 记一条 warning 让它暴露出来。

Hook 自己写挂了（**不静默**）：

    - 抛 SiteAbort / FlushError 这类领域异常：那是 hook 有意的控制流，原样上抛。
    - 抛别的：记一条 hook.failed；有否决语义的位点按**拒绝**处理，其余位点跳过它继续。
      观察型 hook 挂了不该弄死整次 trace，但也不能不留痕迹。
"""

from __future__ import annotations

from yzy_agent.contracts import REJECTION_SITES, AgentError, SiteContext

from .events import event_of
from .registry import HookRegistry


async def run_hooks(ctx: SiteContext, registry: HookRegistry) -> bool:
    """跑完本位点上注册的 hook。

    返回值只有位点 1 与位点 6 的调用方会看：False 表示被否决，下游动作必须跳过。

    举例：allowed = await run_hooks(ctx, registry)
    """
    site = ctx.site
    logger = ctx.ports.logger
    for hook in registry.hooks_for(event_of(site)):
        try:
            verdict = await hook.fn(ctx)
        except AgentError:
            raise
        except Exception as exc:
            logger.error(
                "hook.failed",
                site=site.value,
                hook=hook.name,
                trace_id=ctx.state.trace_id,
                error=f"{type(exc).__name__}: {exc}",
            )
            if site in REJECTION_SITES:
                return False
            continue

        if verdict is not False:
            continue
        if site in REJECTION_SITES:
            logger.info(
                "hook.rejected",
                site=site.value,
                hook=hook.name,
                trace_id=ctx.state.trace_id,
            )
            return False
        logger.warning(
            "hook.reject_out_of_contract",
            site=site.value,
            hook=hook.name,
            trace_id=ctx.state.trace_id,
        )
    return True

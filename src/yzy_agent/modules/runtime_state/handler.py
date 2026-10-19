"""runtime_state 在各个位点上的 SiteHandler。

handler 是「模块 × 位点」的组合，不是「模块」：同一个模块在多个位点各注册一个实例，
各自带自己的 priority。下面四个优先级常量不是随手排的，每个都写了理由。
"""

from __future__ import annotations

from collections.abc import Callable

from yzy_agent.contracts import Handler, Site, SiteContext

from .ops import (
    s01_trace_before,
    s02_turn_before,
    s03_llm_before,
    s04_llm_during,
    s05_llm_after,
    s06_tool_before,
    s07_tool_during,
    s08_tool_after,
    s10_trace_end,
)

# 一个位点操作的统一签名：读入位点上下文，原地改 ctx.state 与它负责的那部分 payload。
#
# 举例：s02_turn_before.apply
SiteOp = Callable[[SiteContext], None]

# 位点 1 必须排在 session **之后**：session 先建或恢复会话，这里才绑得到正确的 session_id。
#
# 举例：300
PRIORITY_AFTER_SESSION = 300

# 位点 2/3/4/5/7 要最先跑 —— 它们产出的东西本位点后面的人要读：
# context_tokens 给 compression，pending_tool_calls 与 pending_messages 给 session。
#
# 举例：100
PRIORITY_FIRST = 100

# 位点 6 相反：它读的是 tool_calling 与 sandbox 填好的 decisions，早一步就看不到判定结果。
# 450 仍在维度段（100-499）内，又早于 hook（500 段）与落盘屏障（900）。
#
# 举例：450
PRIORITY_AFTER_DECISIONS = 450

# 位点 8/10 要等 tool_calling 规范化完输出、session 写完结果条目，再动统计与释放。
#
# 举例：400
PRIORITY_LAST = 400


class _SiteOpHandler:
    """把一个位点操作包成 Handler。一个实例只挂一个位点。

    举例：_SiteOpHandler("runtime_state.turn_before", Site.TURN_BEFORE, 100, s02.apply)
    """

    def __init__(self, handler_id: str, site: Site, priority: int, op: SiteOp) -> None:
        """绑定一个位点操作。

        举例：_SiteOpHandler("runtime_state.turn_before", Site.TURN_BEFORE, 100, s02.apply)
        """
        # handler 唯一标识，形如"模块名.位点名"，日志与排错靠它定位。
        #
        # 举例："runtime_state.turn_before"
        self.id = handler_id

        # 这个 handler 挂在哪一个位点上。一个实例只挂一个，优先级才不会被别的位点带偏。
        #
        # 举例：frozenset({Site.TURN_BEFORE})
        self.sites: frozenset[Site] = frozenset({site})

        # 执行优先级，数值越小越先跑。
        #
        # 举例：100
        self.priority = priority

        # 本位点真正要跑的那段逻辑。
        #
        # 举例：s02_turn_before.apply
        self._op = op

    async def run(self, ctx: SiteContext) -> bool | None:
        """跑本位点的操作。

        runtime_state 没有否决语义 —— 它只改数据，返回值一律 None（表示继续）。
        内核只在位点 1 与位点 6 解释 False（见 contracts 的 REJECTION_SITES）。

        举例：await handler.run(ctx)
        """
        self._op(ctx)
        return None


def build_handlers() -> tuple[Handler, ...]:
    """造出 runtime_state 要注册的全部 handler，交给装配根。

    位点 9 不在这里，因为那个位点不需要改执行态 —— 理由见 ops/__init__.py。

    举例：registry = HandlerRegistry(build_handlers())
    """
    return (
        _SiteOpHandler(
            "runtime_state.trace_before",
            Site.TRACE_BEFORE,
            PRIORITY_AFTER_SESSION,
            s01_trace_before.apply,
        ),
        _SiteOpHandler(
            "runtime_state.turn_before", Site.TURN_BEFORE, PRIORITY_FIRST, s02_turn_before.apply
        ),
        _SiteOpHandler(
            "runtime_state.llm_before", Site.LLM_BEFORE, PRIORITY_FIRST, s03_llm_before.apply
        ),
        _SiteOpHandler(
            "runtime_state.llm_during", Site.LLM_DURING, PRIORITY_FIRST, s04_llm_during.apply
        ),
        _SiteOpHandler(
            "runtime_state.llm_after", Site.LLM_AFTER, PRIORITY_FIRST, s05_llm_after.apply
        ),
        _SiteOpHandler(
            "runtime_state.tool_before",
            Site.TOOL_BEFORE,
            PRIORITY_AFTER_DECISIONS,
            s06_tool_before.apply,
        ),
        _SiteOpHandler(
            "runtime_state.tool_during", Site.TOOL_DURING, PRIORITY_FIRST, s07_tool_during.apply
        ),
        _SiteOpHandler(
            "runtime_state.tool_after", Site.TOOL_AFTER, PRIORITY_LAST, s08_tool_after.apply
        ),
        _SiteOpHandler(
            "runtime_state.trace_end", Site.TRACE_END, PRIORITY_LAST, s10_trace_end.apply
        ),
    )

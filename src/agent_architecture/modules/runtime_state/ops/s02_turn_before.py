"""位点 2（turn:before）的 runtime_state 操作：开新的一轮。

`turn_index` 的推进**不在这里** —— 它是内核控制流自己的依赖（max_turns 守卫靠它），
由内核推进（不变量 I9）。这里只清掉"上一轮留下的"东西。
"""

from __future__ import annotations

from agent_architecture.contracts import RuntimeState, SiteContext


def apply(ctx: SiteContext) -> None:
    """重试计数归零，本轮的统计与待执行队列清空。

    举例：apply(ctx) -> state.llm_attempt_number == 0 且 state.turn_tool_stats == {}
    """
    state: RuntimeState = ctx.state

    # 新的一轮，模型重新从第一次尝试开始。
    state.llm_attempt_number = 0

    # 统计是按"本轮"聚合的，跨轮会失真。
    state.turn_tool_stats.clear()

    # 兜底：执行过的调用在位点 8 摘掉，不执行的调用在位点 6 摘掉，正常走到这里队列应该是空的。
    # 留这一手是防"某个位点被否决、半路返回"之类的异常路径把残留带进新一轮。
    state.pending_tool_calls.clear()

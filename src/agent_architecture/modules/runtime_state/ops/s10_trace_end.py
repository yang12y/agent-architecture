"""位点 10（trace:end）的 runtime_state 操作：释放执行态。

`is_running` 的置位**不在这里** —— 它是内核控制流自己的依赖，由内核置位（不变量 I9），
而且内核在本位点之前就已经置好了。

"释放"的实际意义：会话注册表这类长命对象可能还攥着这个 state（比如前端查"还在跑吗"），
清掉里面的容器和消息引用，别让一次 trace 的内容跟着它一起留着。
"""

from __future__ import annotations

from agent_architecture.contracts import RuntimeState, SiteContext


def apply(ctx: SiteContext) -> None:
    """清空执行态里的容器与消息引用；状态对象本身随 trace 一起被丢弃。

    举例：apply(ctx) -> state.pending_queue == deque() 且 state.user_message is None
    """
    state: RuntimeState = ctx.state

    state.user_message = None
    state.turn_tool_stats.clear()
    state.pending_tool_calls.clear()
    state.pending_queue.clear()

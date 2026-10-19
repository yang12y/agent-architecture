"""位点 1（trace:before）的 runtime_state 操作：把执行态对齐到"这次 trace 刚开始"。

**为什么 bootstrap 之后还要做一遍**：bootstrap 造的是一个全新对象，而这里保证的是
"无论这个对象是怎么来的（新建 / 恢复 / 复用），进 trace 时它都是对的"。恢复路径与
状态复用路径都靠它兜底。

**为什么排在 session 之后**：session 的 handler 在本位点先跑，它可能新建了一个会话、
也可能恢复了一个已有会话。会话身份要以它定下来的为准。
"""

from __future__ import annotations

from typing import cast

from agent_architecture.contracts import RuntimeState, SiteContext, TraceBeforePayload


def apply(ctx: SiteContext) -> None:
    """绑定身份与上限，并把所有游标归零。

    举例：apply(ctx) -> state.turn_index == 0 且 state.session_id == ctx.session.meta.session_id
    """
    payload = cast(TraceBeforePayload, ctx.payload)
    state: RuntimeState = ctx.state

    # 身份以 Session 为准，而不是以调用方传进来的 payload.session_id 为准 ——
    # session 的 handler 在本位点先跑，它可能已经换了会话。
    state.session_id = ctx.session.meta.session_id
    state.parent_trace_id = payload.parent_trace_id

    # 模型与权限取本次 trace 的意图值；上限一律取冻结快照，
    # 这样运行中改全局配置不会影响已经开始的那次执行。
    state.model = payload.model
    state.permission = payload.permission
    state.max_tokens = ctx.config.max_tokens
    state.max_turns = ctx.config.max_turns
    state.compact_tokens = ctx.config.compact_tokens

    # 本次要处理的用户消息落位。
    state.user_message = payload.user_message

    # 游标归零：不管这个状态对象上一轮跑到哪里，新 trace 都从头开始。
    state.turn_index = 0
    state.llm_attempt_number = 0
    state.context_tokens = 0
    state.turn_tool_stats.clear()
    state.pending_tool_calls.clear()
    state.pending_queue.clear()

    # 起跑：置 is_running 与开始时刻。
    state.is_running = True
    state.started_at = ctx.ports.clock.now()

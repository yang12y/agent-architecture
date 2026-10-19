"""位点 1 用的初始化：由内存 Session 与冻结配置建出本次 trace 的 Runtime State。

它是**纯函数、不碰磁盘**，也不关心 Session 是怎么来的：
新会话与刚从磁盘恢复的会话走的是同一条路。
"""

from __future__ import annotations

from collections import deque

from yzy_agent.contracts import FrozenConfig, Message, RuntimeState, Timestamp


def bootstrap_runtime_state(
    *,
    session_id: str,
    trace_id: str,
    parent_trace_id: str | None,
    user_message: Message | None,
    config: FrozenConfig,
    started_at: Timestamp,
) -> RuntimeState:
    """造出一次 trace 的 Runtime State：游标归零，上限与策略取快照值。

    举例：state = bootstrap_runtime_state(
              session_id="sess_1", trace_id="trace_1", parent_trace_id=None,
              user_message=msg, config=frozen, started_at=now)
    """
    return RuntimeState(
        session_id=session_id,
        trace_id=trace_id,
        parent_trace_id=parent_trace_id,
        turn_index=0,
        llm_attempt_number=0,
        context_tokens=0,
        max_tokens=config.max_tokens,
        max_turns=config.max_turns,
        turn_tool_stats={},
        compact_tokens=config.compact_tokens,
        started_at=started_at,
        user_message=user_message,
        pending_queue=deque(),
        is_running=True,
        pending_tool_calls=[],
        model=config.model,
        permission=config.permission,
    )

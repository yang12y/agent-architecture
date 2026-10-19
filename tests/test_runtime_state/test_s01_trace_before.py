"""位点 1（trace:before）的操作测试。"""

from __future__ import annotations

from collections import deque

from agent_architecture.contracts import (
    Message,
    MessageRole,
    Permission,
    Site,
    TextPart,
    TraceBeforePayload,
    TraceTrigger,
)
from agent_architecture.modules.runtime_state import s01_trace_before
from fakes import (
    StubSession,
    make_frozen_config,
    make_runtime_state,
    make_site_context,
    make_trace_context,
)


def _payload(**overrides: object) -> TraceBeforePayload:
    """造一份位点 1 的 payload，个别项可覆盖。

    举例：_payload(parent_trace_id="trace_parent")
    """
    base: dict[str, object] = {
        "session_id": "sess_from_payload",
        "trigger": TraceTrigger.USER,
        "parent_trace_id": None,
        "user_message": Message(role=MessageRole.USER, content=[TextPart(text="帮我读 a.py")]),
        "model": "payload-model",
        "permission": Permission.READ_ONLY,
    }
    base.update(overrides)
    return TraceBeforePayload(**base)  # type: ignore[arg-type]


def test_session_id_comes_from_the_session_not_the_payload() -> None:
    """身份以 Session 为准 —— session 的 handler 先跑，它可能换了会话。"""
    trace = make_trace_context(
        state=make_runtime_state(session_id="sess_stale"),
        session=StubSession("sess_from_session"),
    )
    ctx = make_site_context(Site.TRACE_BEFORE, _payload(), trace=trace)

    s01_trace_before.apply(ctx)

    assert ctx.state.session_id == "sess_from_session"


def test_cursors_are_reset_on_a_dirty_state() -> None:
    """不管这个状态对象上一轮跑到哪，进 trace 时游标全部归零。"""
    dirty = make_runtime_state(
        turn_index=7,
        llm_attempt_number=2,
        context_tokens=99999,
        user_message=Message(role=MessageRole.USER, content=[TextPart(text="上一轮的")]),
    )
    dirty.pending_tool_calls.append(None)  # type: ignore[arg-type]
    dirty.pending_queue.append(Message(role=MessageRole.USER, content=[]))
    ctx = make_site_context(Site.TRACE_BEFORE, _payload(), trace=make_trace_context(state=dirty))

    s01_trace_before.apply(ctx)

    assert ctx.state.turn_index == 0
    assert ctx.state.llm_attempt_number == 0
    assert ctx.state.context_tokens == 0
    assert ctx.state.turn_tool_stats == {}
    assert ctx.state.pending_tool_calls == []
    assert list(ctx.state.pending_queue) == []


def test_limits_come_from_the_frozen_snapshot() -> None:
    """上限与阈值取冻结快照，运行中改全局配置不影响已经开始的那次执行。"""
    config = make_frozen_config(max_tokens=777, max_turns=3, compact_tokens=666)
    ctx = make_site_context(Site.TRACE_BEFORE, _payload(), trace=make_trace_context(config=config))

    s01_trace_before.apply(ctx)

    assert ctx.state.max_tokens == 777
    assert ctx.state.max_turns == 3
    assert ctx.state.compact_tokens == 666


def test_identity_and_user_message_are_placed() -> None:
    """模型、权限、父 trace 与本次要处理的用户消息都落位。"""
    message = Message(role=MessageRole.USER, content=[TextPart(text="这次的消息")])
    ctx = make_site_context(
        Site.TRACE_BEFORE,
        _payload(user_message=message, parent_trace_id="trace_parent"),
    )

    s01_trace_before.apply(ctx)

    assert ctx.state.user_message is message
    assert ctx.state.parent_trace_id == "trace_parent"
    assert ctx.state.model == "payload-model"
    assert ctx.state.permission is Permission.READ_ONLY


def test_running_flag_and_start_time_are_set() -> None:
    """起跑：is_running 置 True，开始时刻刷新成位点 1 的当前时间。"""
    ctx = make_site_context(
        Site.TRACE_BEFORE,
        _payload(),
        trace=make_trace_context(state=make_runtime_state(is_running=False)),
    )

    s01_trace_before.apply(ctx)

    assert ctx.state.is_running is True
    assert ctx.state.started_at == ctx.ports.clock.now()


def test_pending_queue_is_a_fresh_deque() -> None:
    """清空队列用的是 clear()，队列对象本身还是那个 deque（别处可能持有它的引用）。"""
    dirty = make_runtime_state()
    dirty.pending_queue.append(Message(role=MessageRole.USER, content=[]))
    held = dirty.pending_queue
    ctx = make_site_context(Site.TRACE_BEFORE, _payload(), trace=make_trace_context(state=dirty))

    s01_trace_before.apply(ctx)

    assert held == deque()
    assert ctx.state.pending_queue is held

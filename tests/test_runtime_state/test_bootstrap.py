"""Runtime State 初始化的测试。"""

from __future__ import annotations

from datetime import UTC, datetime

from fakes import make_frozen_config
from yzy_agent.contracts import Message, MessageRole, Permission, TextPart
from yzy_agent.modules.runtime_state import bootstrap_runtime_state

# 固定的起始时刻，让断言可以写死。
STARTED = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)


def _bootstrap(**overrides: object):  # type: ignore[no-untyped-def]
    """按默认参数造一份状态，个别项可覆盖。"""
    kwargs: dict[str, object] = {
        "session_id": "sess_test",
        "trace_id": "trace_test",
        "parent_trace_id": None,
        "user_message": None,
        "config": make_frozen_config(),
        "started_at": STARTED,
    }
    kwargs.update(overrides)
    return bootstrap_runtime_state(**kwargs)  # type: ignore[arg-type]


def test_cursors_start_at_zero() -> None:
    """游标全部归零，不管上一个 trace 跑到哪了。"""
    state = _bootstrap()

    assert state.turn_index == 0
    assert state.llm_attempt_number == 0
    assert state.context_tokens == 0
    assert state.turn_tool_stats == {}
    assert state.pending_tool_calls == []
    assert state.is_running is True


def test_limits_come_from_the_frozen_snapshot() -> None:
    """上限与策略取快照值——运行中改配置不影响已经开始的那次。"""
    config = make_frozen_config(max_tokens=999, max_turns=3, compact_tokens=888)

    state = _bootstrap(config=config)

    assert state.max_tokens == 999
    assert state.max_turns == 3
    assert state.compact_tokens == 888
    assert state.model == config.model
    assert state.permission is config.permission


def test_user_message_and_identity_are_placed() -> None:
    """身份标识与本次要处理的用户消息都落位。"""
    message = Message(role=MessageRole.USER, content=[TextPart(text="你好")])

    state = _bootstrap(user_message=message, parent_trace_id="trace_parent")

    assert state.session_id == "sess_test"
    assert state.trace_id == "trace_test"
    assert state.parent_trace_id == "trace_parent"
    assert state.user_message is message
    assert state.started_at == STARTED


def test_two_calls_do_not_share_mutable_containers() -> None:
    """两次初始化不能共用同一个 dict / list —— 否则两个 trace 会互相串数据。"""
    first = _bootstrap(trace_id="t1")
    second = _bootstrap(trace_id="t2")

    first.turn_tool_stats["read"] = None  # type: ignore[assignment]
    first.pending_tool_calls.append(None)  # type: ignore[arg-type]

    assert second.turn_tool_stats == {}
    assert second.pending_tool_calls == []
    assert first.pending_queue is not second.pending_queue


def test_permission_defaults_to_config_value() -> None:
    """权限取快照值。"""
    config = make_frozen_config(permission=Permission.READ_ONLY)

    assert _bootstrap(config=config).permission is Permission.READ_ONLY

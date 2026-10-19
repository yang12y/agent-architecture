"""组装 TraceContext 的工厂，避免每个测试重复写样板。"""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime

from yzy_agent.contracts import (
    AppConfig,
    ApprovalProvider,
    CancelToken,
    FrozenConfig,
    Permission,
    Ports,
    RuntimeState,
    Site,
    SiteContext,
    SitePayload,
    ToolRegistry,
    TraceContext,
)
from yzy_agent.infra.observability import InProcessEventBus, RecordingLogger
from yzy_agent.infra.system import HeuristicTokenCounter

from .clock import FakeClock
from .ids import SequentialIds
from .scripted_llm import ScriptedLlm
from .session import StubSession


def make_ports(
    *,
    clock: FakeClock | None = None,
    ids: SequentialIds | None = None,
    logger: RecordingLogger | None = None,
    events: InProcessEventBus | None = None,
    tokens: HeuristicTokenCounter | None = None,
    llm: ScriptedLlm | None = None,
    tools: ToolRegistry | None = None,
    approvals: ApprovalProvider | None = None,
) -> Ports:
    """造一个端口齐备的 Ports。

    模型端口默认给一个**空脚本**的假件：不写脚本就调用它，会立刻断言失败，
    这样"意外打到了模型"这种事跑不掉。工具与审批默认**不接**（None）——
    那正是沙箱「没有工具来源 / 没有应答方」时的两条保守路径。

    举例：ports = make_ports(); ports.logger.info("x")
    """
    return Ports(
        clock=clock or FakeClock(),
        ids=ids or SequentialIds(),
        logger=logger or RecordingLogger(),
        events=events or InProcessEventBus(),
        tokens=tokens or HeuristicTokenCounter(),
        llm=llm or ScriptedLlm(),
        tools=tools,
        approvals=approvals,
    )


def make_frozen_config(**overrides: object) -> FrozenConfig:
    """造一个字段齐全的 FrozenConfig，可逐个覆盖。

    举例：make_frozen_config(max_turns=3)
    """
    base: dict[str, object] = {
        "model": "fake-model",
        "summary_model": None,
        "permission": Permission.EDITABLE,
        "max_tokens": 1000,
        "max_turns": 5,
        "llm_max_attempts": 3,
        "thinking": False,
        "compact_tokens": 800,
        "keep_recent": 2,
        "long_tool_result_max_tokens": 100,
        "long_tool_result_keep_recent": 1,
        "summary_prompt_template": "摘要：{history}",
        "tool_result_max_chars": 200,
    }
    base.update(overrides)
    return FrozenConfig(**base)  # type: ignore[arg-type]


def make_runtime_state(**overrides: object) -> RuntimeState:
    """造一个字段齐全的 RuntimeState，可逐个覆盖。

    举例：make_runtime_state(turn_index=3)
    """
    base: dict[str, object] = {
        "session_id": "sess_test",
        "trace_id": "trace_test",
        "parent_trace_id": None,
        "turn_index": 0,
        "llm_attempt_number": 0,
        "context_tokens": 0,
        "max_tokens": 1000,
        "max_turns": 5,
        "turn_tool_stats": {},
        "compact_tokens": 800,
        "started_at": datetime(2025, 1, 1, 12, 0, tzinfo=UTC),
        "user_message": None,
        "pending_queue": deque(),
        "is_running": True,
        "pending_tool_calls": [],
        "model": "fake-model",
        "permission": Permission.EDITABLE,
    }
    base.update(overrides)
    return RuntimeState(**base)  # type: ignore[arg-type]


def make_trace_context(
    *,
    state: RuntimeState | None = None,
    session: StubSession | None = None,
    config: FrozenConfig | None = None,
    ports: Ports | None = None,
    signal: CancelToken | None = None,
) -> TraceContext:
    """造一个可用的 TraceContext。

    举例：trace = make_trace_context(); trace.state.turn_index -> 0
    """
    frozen = config or make_frozen_config()
    return TraceContext(
        # 没显式给 state 时，按 config 的限额推一份 —— 否则 state.max_turns 与
        # config.max_turns 会各写各的，测试里给 config 设的上限根本不生效。
        state=state
        or make_runtime_state(
            max_tokens=frozen.max_tokens,
            max_turns=frozen.max_turns,
            compact_tokens=frozen.compact_tokens,
            model=frozen.model,
            permission=frozen.permission,
        ),
        session=session or StubSession(),
        config=frozen,
        ports=ports or make_ports(),
        signal=signal or CancelToken(),
    )


def make_app_config(**overrides: object) -> AppConfig:
    """造一个字段齐全的 AppConfig，可逐个覆盖。

    `llm_api_key` 给一个假值：构造 OpenAI 客户端时只要非空即可，测试不联网。

    举例：make_app_config(model="another-model")
    """
    base: dict[str, object] = {
        "workspace": "D:/tmp/workspace",
        "session_dir": "D:/tmp/sessions",
        "tool_result_max_chars": 200,
        "model": "fake-model",
        "summary_model": None,
        "llm_api_key": "sk-test",
        "llm_base_url": None,
        "llm_timeout_seconds": 60.0,
        "llm_max_attempts": 3,
        "thinking": False,
        "permission": Permission.EDITABLE,
        # 与真实默认值对齐：预算小到跟工具 schema 一个量级时，压缩会被无意触发，
        # 而那些用例测的是别的东西。
        #
        # 举例：1000000
        "max_tokens": 1_000_000,
        "max_turns": 5,
        "compact_ratio": 0.7,
        "keep_recent": 2,
        "long_tool_result_max_tokens": 100,
        "long_tool_result_keep_recent": 1,
        "summary_prompt_template": "摘要：{history}",
        "log_level": "INFO",
    }
    base.update(overrides)
    return AppConfig(**base)  # type: ignore[arg-type]


def make_site_context(
    site: Site,
    payload: SitePayload,
    *,
    trace: TraceContext | None = None,
) -> SiteContext:
    """造一个位点上下文：把 payload 挂到指定位点上，执行现场用默认的。

    逐位点操作的测试全用它；传 trace 可以换掉执行现场（比如换一个 session_id）。

    举例：ctx = make_site_context(Site.TURN_BEFORE, TurnBeforePayload(turn_index=1))
    """
    return SiteContext(trace=trace or make_trace_context(), site=site, payload=payload)

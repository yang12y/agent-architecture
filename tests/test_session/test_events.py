"""session 广播的事件：写完日志顺手发，字段要和刚写下的条目对得上。"""

from __future__ import annotations

from agent_architecture.contracts import (
    AssistantMessage,
    LlmAfterPayload,
    Permission,
    Site,
    TraceBeforePayload,
    TraceEnd,
    TraceEndPayload,
    TraceStart,
    TraceStatus,
    TraceTrigger,
)
from agent_architecture.infra.observability import InProcessEventBus
from agent_architecture.modules.session import Session, build_handlers
from fakes import (
    SequentialIds,
    assistant_message,
    make_ports,
    make_session_meta,
    make_site_context,
    make_trace_context,
    user_message,
)


def _collect(events: InProcessEventBus) -> list[object]:
    """把 session 会发的三类事件收进一个列表。

    举例：seen = _collect(events); await handler.run(ctx)
    """
    seen: list[object] = []
    for kind in (TraceStart, AssistantMessage, TraceEnd):
        events.subscribe(kind, seen.append)
    return seen


def _session() -> Session:
    """一个空 Session。

    举例：_session().entries() -> []
    """
    return Session(make_session_meta(), [], ids=SequentialIds())


def _ctx(site: Site, payload, session: Session, events: InProcessEventBus):
    """把 payload 挂到位点上，执行现场用给定的那根事件总线。

    举例：ctx = _ctx(Site.TRACE_BEFORE, payload, session, events)
    """
    trace = make_trace_context(session=session, ports=make_ports(events=events))
    return make_site_context(site, payload, trace=trace)


def _handler(handler_id: str):
    """按 id 取一个 session handler。

    举例：_handler("session.trace_before")
    """
    return next(h for h in build_handlers() if h.id == handler_id)


def _trace_before_payload(session: Session, text: str = "帮我读一下 a.py") -> TraceBeforePayload:
    """位点 1 的 payload。

    举例：_trace_before_payload(_session())
    """
    return TraceBeforePayload(
        session_id=session.meta.session_id,
        trigger=TraceTrigger.USER,
        parent_trace_id=None,
        user_message=user_message(text),
        model="deepseek-chat",
        permission=Permission.EDITABLE,
    )


async def test_trace_before_broadcasts_trace_start() -> None:
    """trace_start 事件里的 entry_id / seq 就是刚写下的那条用户消息。"""
    session, events = _session(), InProcessEventBus()
    seen = _collect(events)

    await _handler("session.trace_before").run(
        _ctx(Site.TRACE_BEFORE, _trace_before_payload(session), session, events)
    )

    assert len(seen) == 1
    event = seen[0]
    assert isinstance(event, TraceStart)
    assert event.session_id == session.meta.session_id
    assert event.user_message_entry_id == session.entries()[1].entry_id
    assert event.user_message_entry_seq == session.entries()[1].seq
    assert event.max_turns == 5  # 来自 FrozenConfig


async def test_trace_start_carries_the_user_message_back() -> None:
    """回带正文，前端才能和乐观渲染对账。"""
    session, events = _session(), InProcessEventBus()
    seen = _collect(events)

    await _handler("session.trace_before").run(
        _ctx(Site.TRACE_BEFORE, _trace_before_payload(session, "帮我看看"), session, events)
    )

    assert seen[0].user_message[0].text == "帮我看看"  # type: ignore[union-attr]


async def test_llm_after_broadcasts_assistant_message() -> None:
    """assistant 消息定稿时广播，带全量正文。"""
    session, events = _session(), InProcessEventBus()
    seen = _collect(events)

    await _handler("session.llm_after").run(
        _ctx(
            Site.LLM_AFTER,
            LlmAfterPayload(assistant_message=assistant_message("好的")),
            session,
            events,
        )
    )

    event = seen[0]
    assert isinstance(event, AssistantMessage)
    assert event.entry_id == session.entries()[0].entry_id
    assert event.content[0].text == "好的"  # type: ignore[union-attr]


async def test_an_empty_assistant_message_broadcasts_nothing() -> None:
    """什么都不产出就什么都不记，也就没有事件。"""
    session, events = _session(), InProcessEventBus()
    seen = _collect(events)

    empty = assistant_message("")
    empty.content = []
    await _handler("session.llm_after").run(
        _ctx(Site.LLM_AFTER, LlmAfterPayload(assistant_message=empty), session, events)
    )

    assert seen == []


async def test_trace_end_broadcasts_trace_end() -> None:
    """收尾事件带状态、轮数与耗时 —— 后两个推不出来，只能随事件带上。"""
    session, events = _session(), InProcessEventBus()
    seen = _collect(events)

    await _handler("session.trace_end").run(
        _ctx(
            Site.TRACE_END,
            TraceEndPayload(status=TraceStatus.ABORTED, reason="用户中断", duration_ms=12.5),
            session,
            events,
        )
    )

    event = seen[0]
    assert isinstance(event, TraceEnd)
    assert (event.status, event.reason, event.duration_ms) == (
        TraceStatus.ABORTED,
        "用户中断",
        12.5,
    )

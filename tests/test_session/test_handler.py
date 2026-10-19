"""session 位点 handler 的测试。

每个位点验证"该写什么、不该写什么"。装配冒烟在最后。
"""

from __future__ import annotations

from fakes import (
    START,
    SequentialIds,
    assistant_message,
    make_session_meta,
    make_site_context,
    make_trace_context,
    user_message,
)
from yzy_agent.contracts import (
    Handler,
    LlmAfterPayload,
    LlmBeforePayload,
    Message,
    MessageRole,
    Permission,
    SessionEntryType,
    Site,
    ToolAfterPayload,
    ToolBeforePayload,
    ToolCall,
    ToolCallStatus,
    ToolDecision,
    TraceBeforePayload,
    TraceEndPayload,
    TraceStatus,
    TraceTrigger,
)
from yzy_agent.modules.agent_loop import HandlerRegistry
from yzy_agent.modules.session import Session, build_handlers


def _session() -> Session:
    """造一个空的内存 Session。

    举例：_session() -> Session(meta=..., entries=[], ids=SequentialIds())
    """
    return Session(make_session_meta(), [], ids=SequentialIds())


def _ctx(site: Site, payload, session: Session):
    """把 payload 挂到位点上，执行现场用一个由该 Session 支撑的 TraceContext。

    举例：ctx = _ctx(Site.TRACE_BEFORE, payload, _session())
    """
    return make_site_context(site, payload, trace=make_trace_context(session=session))


def _types(session: Session) -> list[SessionEntryType]:
    """日志里各条目的类型，按顺序。

    举例：_types(session) -> [TRACE_START, MESSAGE]
    """
    return [entry.type for entry in session.entries()]


def _trace_before_payload() -> TraceBeforePayload:
    """造一份位点 1 的 payload。

    举例：_trace_before_payload().trigger -> TraceTrigger.USER
    """
    return TraceBeforePayload(
        session_id="sess_test",
        trigger=TraceTrigger.USER,
        parent_trace_id=None,
        user_message=user_message("帮我读 a.py"),
        model="fake-model",
        permission=Permission.EDITABLE,
    )


def _handler(site: Site) -> Handler:
    """按位点取 session 的 handler。

    举例：_handler(Site.LLM_AFTER).id -> "session.llm_after"
    """
    return next(h for h in build_handlers() if site in h.sites)


async def test_trace_before_writes_the_boundary_and_the_user_message() -> None:
    """位点 1：先写 trace_start，再写本次的用户消息。"""
    session = _session()

    await _handler(Site.TRACE_BEFORE).run(_ctx(Site.TRACE_BEFORE, _trace_before_payload(), session))

    assert _types(session) == [SessionEntryType.TRACE_START, SessionEntryType.MESSAGE]
    assert session.entries()[1].payload.role is MessageRole.USER  # type: ignore[union-attr]


async def test_trace_before_refreshes_updated_at() -> None:
    """位点 1 也刷新 updated_at —— 会话确实动过了。"""
    session = _session()

    # FakeClock 的 now() 恰好就是 START，所以先把 meta 改成一个更早的值，
    # 才证得动"它被刷新了"，而不是"它本来就是 START"。
    stale = START.replace(year=2024)
    session.meta.updated_at = stale

    await _handler(Site.TRACE_BEFORE).run(_ctx(Site.TRACE_BEFORE, _trace_before_payload(), session))

    assert session.meta.updated_at != stale


async def test_llm_before_clears_the_draft_that_was_just_taken() -> None:
    """位点 3：残留的草稿已经被 messages() 叠进上下文，槽要清掉。"""
    session = _session()
    session.append_draft("上一轮被打断的半截", trace_id="trace_old", turn_index=1, now=START)
    payload = LlmBeforePayload(model="fake-model", messages=session.messages())

    assert len(payload.messages) == 1  # 内核取材时把它叠进来了

    await _handler(Site.LLM_BEFORE).run(_ctx(Site.LLM_BEFORE, payload, session))

    assert session.draft_text() == ""
    assert len(payload.messages) == 1  # 已经并进去的那条不受影响


async def test_a_leftover_draft_cannot_be_glued_onto_the_next_trace() -> None:
    """跨 trace 的那半边 I11：只清位点 5 的话，新的流会直接接在旧残留后面。

    上游原话：「若只清位点 5，新的流会直接接在旧残留后面，而旧残留这一轮已经被当成
    assistant 消息放进上下文了——位点 5 定稿时就会把它提交第二遍。」
    """
    session = _session()
    # trace A 收流中断，半截留在槽里（take_draft 没跑成）
    session.append_draft("我来帮你读", trace_id="trace_a", turn_index=1, now=START)

    # trace B 位点 3：取材之后清空
    await _handler(Site.LLM_BEFORE).run(
        _ctx(Site.LLM_BEFORE, LlmBeforePayload(model="fake-model", messages=[]), session)
    )

    # trace B 位点 4 的第一段增量
    session.append_draft("好的，我看看", trace_id="trace_b", turn_index=1, now=START)

    draft = session.take_draft()
    assert draft is not None
    assert draft.content == "好的，我看看"  # 没有接在 trace A 的残留后面
    assert draft.trace_id == "trace_b"  # 归属也是新 trace 的


async def test_llm_before_on_an_empty_slot_is_a_no_op() -> None:
    """槽本来就是空的时候什么都不做 —— 绝大多数轮次走的是这条路。"""
    session = _session()

    await _handler(Site.LLM_BEFORE).run(
        _ctx(Site.LLM_BEFORE, LlmBeforePayload(model="fake-model", messages=[]), session)
    )

    assert session.take_draft() is None


async def test_llm_after_finalizes_the_assistant_message() -> None:
    """位点 5：模型的输出定稿成一条 message 条目。"""
    session = _session()
    payload = LlmAfterPayload(assistant_message=assistant_message("好的"))

    await _handler(Site.LLM_AFTER).run(_ctx(Site.LLM_AFTER, payload, session))

    assert _types(session) == [SessionEntryType.MESSAGE]
    assert session.entries()[0].payload.role is MessageRole.ASSISTANT  # type: ignore[union-attr]


async def test_llm_after_skips_an_empty_assistant_message() -> None:
    """空内容的 assistant 消息不写 —— 它进上下文会被当成一条空回复。"""
    session = _session()
    payload = LlmAfterPayload(assistant_message=Message(role=MessageRole.ASSISTANT, content=[]))

    await _handler(Site.LLM_AFTER).run(_ctx(Site.LLM_AFTER, payload, session))

    assert session.entries() == []


async def test_llm_after_writes_pending_messages_as_user_messages() -> None:
    """中途消息在这里落成 user 条目 —— 用户插的话不能被吞掉。"""
    session = _session()
    payload = LlmAfterPayload(
        assistant_message=assistant_message("好的"),
        pending_messages=[user_message("顺便看下 b.py")],
    )

    await _handler(Site.LLM_AFTER).run(_ctx(Site.LLM_AFTER, payload, session))

    assert _types(session) == [SessionEntryType.MESSAGE, SessionEntryType.MESSAGE]
    assert session.entries()[1].payload.role is MessageRole.USER  # type: ignore[union-attr]


def _tool_before_payload() -> ToolBeforePayload:
    """造一份位点 6 的 payload：一条放行、一条被拒。

    举例：_tool_before_payload().decisions -> {"call_ok": EXECUTE, "call_no": REJECT}
    """
    return ToolBeforePayload(
        calls=[
            ToolCall("call_ok", "read", {"path": "a.py"}, ToolCallStatus.PENDING),
            ToolCall("call_no", "rm", {}, ToolCallStatus.REJECTED),
        ],
        decisions={
            "call_ok": ToolDecision.EXECUTE,
            "call_no": ToolDecision.REJECT,
        },
        idempotency_keys={"call_ok": "key-1"},
    )


async def test_tool_before_writes_tool_start_only_for_executed_calls() -> None:
    """只给要执行的写 tool_start —— 被拒的从没派发过，写了会让恢复误判。"""
    session = _session()

    await _handler(Site.TOOL_BEFORE).run(_ctx(Site.TOOL_BEFORE, _tool_before_payload(), session))

    assert _types(session) == [SessionEntryType.TOOL_START]
    entry = session.entries()[0]
    assert entry.payload.tool_call_id == "call_ok"  # type: ignore[union-attr]
    assert entry.payload.idempotency_key == "key-1"  # type: ignore[union-attr]


async def test_tool_after_writes_the_normalized_result() -> None:
    """位点 8：tool_calling 规范化好的结果直接落成一条 message。"""
    session = _session()
    payload = ToolAfterPayload(
        call=ToolCall("call_1", "read", {}, ToolCallStatus.RUNNING),
        normalized=Message(
            role=MessageRole.TOOL,
            content=[],
            metadata={"tool_call_id": "call_1"},
        ),
    )

    await _handler(Site.TOOL_AFTER).run(_ctx(Site.TOOL_AFTER, payload, session))

    assert _types(session) == [SessionEntryType.MESSAGE]


async def test_tool_after_falls_back_when_normalization_is_missing() -> None:
    """tool_calling 没给结果时自己拼一条，并记一条 warning。

    少了它，assistant 里的 tool_call 就没有配对结果，下一轮请求直接不合法（I10）。
    """
    session = _session()
    payload = ToolAfterPayload(
        call=ToolCall("call_1", "read", {}, ToolCallStatus.RUNNING), raw_result="原始正文"
    )
    ctx = _ctx(Site.TOOL_AFTER, payload, session)

    await _handler(Site.TOOL_AFTER).run(ctx)

    entry = session.entries()[0]
    assert entry.payload.metadata["tool_call_id"] == "call_1"  # type: ignore[union-attr]
    records = ctx.trace.ports.logger.records  # type: ignore[attr-defined]
    assert [record[1] for record in records] == ["session.tool_result_missing"]


async def test_tool_after_without_a_call_writes_nothing() -> None:
    """payload.call 为空时连兜底都拼不出来，只能什么都不写。"""
    session = _session()

    await _handler(Site.TOOL_AFTER).run(_ctx(Site.TOOL_AFTER, ToolAfterPayload(), session))

    assert session.entries() == []


async def test_trace_end_writes_the_boundary_and_refreshes_updated_at() -> None:
    """位点 10：写 trace_end 并刷新 updated_at。"""
    session = _session()
    stale = START.replace(year=2024)
    session.meta.updated_at = stale

    await _handler(Site.TRACE_END).run(
        _ctx(Site.TRACE_END, TraceEndPayload(status=TraceStatus.COMPLETED), session)
    )

    assert _types(session) == [SessionEntryType.TRACE_END]
    assert session.meta.updated_at != stale


def test_registers_six_handlers_all_in_the_dimension_band() -> None:
    """装配冒烟：六个位点各一个，id 与优先级都合法。"""
    registry = HandlerRegistry(build_handlers())

    assert len(registry.handlers()) == 6
    for handler in build_handlers():
        (site,) = handler.sites
        assert handler.id == f"session.{site.value.replace(':', '_')}"


def test_order_against_runtime_state_at_each_shared_site() -> None:
    """和 runtime_state 的相对顺序是硬要求：它先搬 pending_messages，session 才写得到。"""
    from yzy_agent.modules.runtime_state import build_handlers as runtime_state_handlers

    def priority_at(handlers, site: Site) -> int:
        return next(h for h in handlers if site in h.sites).priority

    session_handlers = build_handlers()
    runtime_handlers = runtime_state_handlers()

    for site in (Site.LLM_BEFORE, Site.LLM_AFTER, Site.TOOL_BEFORE, Site.TOOL_AFTER):
        assert priority_at(session_handlers, site) > priority_at(runtime_handlers, site)


def test_llm_before_stays_in_the_dimension_band() -> None:
    """位点 3 的 session 必须晚于 compression（它可能重建上下文，重建时要看见草稿槽），
    又必须早于 hook（500 段）与落盘屏障（900）。

    100 < priority < 500 就是这个约束的可测形式 —— compression 落地后它的优先级
    必须小于 session 的这个值，否则重建出来的 messages 会漏掉"被打断的输出"。
    """
    priority = _handler(Site.LLM_BEFORE).priority

    assert 100 < priority < 500


async def test_run_never_vetoes() -> None:
    """session 没有否决语义：只写日志，返回值一律 None。"""
    session = _session()
    ctx = _ctx(Site.TRACE_END, TraceEndPayload(status=TraceStatus.COMPLETED), session)

    assert await _handler(Site.TRACE_END).run(ctx) is None

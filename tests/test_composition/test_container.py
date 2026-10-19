"""装配根的测试。"""

from __future__ import annotations

from pathlib import Path

from agent_architecture.composition import Container, build_container
from agent_architecture.contracts import (
    AssistantDelta,
    AssistantMessage,
    MessageRole,
    SessionEntryType,
    Site,
    TraceEnd,
    TraceStart,
    TraceStatus,
    TurnStart,
)
from agent_architecture.infra.storage import FileSessionStore
from fakes import USER, ScriptedLlm, make_app_config, text_turn, user_message


def _build(scratch_dir: Path, llm=None) -> Container:
    """按一份把会话目录指到临时目录的配置装出容器。

    举例：container = _build(scratch_dir, llm=ScriptedLlm())
    """
    config = make_app_config(session_dir=str(scratch_dir / "sessions"))
    return build_container(config, llm=llm if llm is not None else ScriptedLlm())


def test_container_wires_every_handler(scratch_dir: Path) -> None:
    """runtime_state 九 + session 六 + compression 一 + tool_calling 五 + hook 一 + 沙箱一 + checkpoint 六。"""
    container = _build(scratch_dir)

    assert len(container.pipeline.registry.handlers()) == 29


def test_checkpoint_covers_every_barrier_site(scratch_dir: Path) -> None:
    """checkpoint 挂满五个屏障位点，外加位点 1 的恢复判据。"""
    registry = _build(scratch_dir).pipeline.registry

    assert registry.sites_of("checkpoint.trace_before") == frozenset({Site.TRACE_BEFORE})
    for site in (
        Site.LLM_BEFORE,
        Site.LLM_AFTER,
        Site.TOOL_BEFORE,
        Site.TOOL_AFTER,
        Site.TRACE_END,
    ):
        assert registry.sites_of(f"checkpoint.{site.name.lower()}") == frozenset({site})


def test_the_recovery_handler_gets_the_tool_registry(scratch_dir: Path) -> None:
    """恢复要按工具声明判"能不能安全重试"，所以装配时得把工具表交进去。"""
    container = _build(scratch_dir)
    recovery = next(
        handler
        for handler in container.pipeline.registry.handlers()
        if handler.id == "checkpoint.trace_before"
    )

    assert recovery._tools is container.ports.tools  # type: ignore[attr-defined]


def test_container_exposes_an_agent_and_a_registry(scratch_dir: Path) -> None:
    """门面与注册表都装好了，api 直接拿。"""
    container = _build(scratch_dir)

    assert isinstance(container, Container)
    assert container.agent.pipeline is container.pipeline
    assert container.sessions.live_ids() == []


def test_the_event_bus_gets_the_real_logger(scratch_dir: Path) -> None:
    """事件总线的订阅者异常要能被记下来，所以装配时得把真 logger 传进去。"""
    container = _build(scratch_dir)

    assert container.ports.events.logger is container.ports.logger  # type: ignore[attr-defined]


async def test_a_created_session_lands_under_the_configured_dir(scratch_dir: Path) -> None:
    """会话目录取自 AppConfig，不是一个写死的路径。"""
    container = _build(scratch_dir)

    live = await container.sessions.create(user_id=USER)

    assert (scratch_dir / "sessions" / live.session_id / "meta.json").is_file()


async def test_a_trace_runs_end_to_end_through_the_container(scratch_dir: Path) -> None:
    """集成冒烟：容器装出来的这一套能跑完一次真 trace，日志留下来四条条目。"""
    llm = ScriptedLlm([text_turn("在的")])
    container = _build(scratch_dir, llm=llm)
    live = await container.sessions.create(user_id=USER)

    outcome = await container.agent.run(session=live.session, user_message=user_message("在吗"))

    assert outcome.status is TraceStatus.COMPLETED
    assert [entry.type for entry in live.session.entries()] == [
        SessionEntryType.TRACE_START,
        SessionEntryType.MESSAGE,
        SessionEntryType.MESSAGE,
        SessionEntryType.TRACE_END,
    ]
    assert live.session.entries()[1].payload.role is MessageRole.USER  # type: ignore[union-attr]
    assert live.session.entries()[2].payload.role is MessageRole.ASSISTANT  # type: ignore[union-attr]


async def test_a_finished_trace_is_already_on_disk(scratch_dir: Path) -> None:
    """trace 跑完 = 位点 10 的屏障已经推过了，不需要谁来手动保存一次。"""
    container = _build(scratch_dir, llm=ScriptedLlm([text_turn("在的")]))
    live = await container.sessions.create(user_id=USER)

    await container.agent.run(session=live.session, user_message=user_message("在吗"))

    fresh = FileSessionStore(scratch_dir / "sessions")
    meta, entries = await fresh.load(live.session_id, USER)  # type: ignore[misc]
    assert meta.session_id == live.session_id
    assert [entry.type for entry in entries] == [
        SessionEntryType.TRACE_START,
        SessionEntryType.MESSAGE,
        SessionEntryType.MESSAGE,
        SessionEntryType.TRACE_END,
    ]


async def test_a_finished_trace_refreshes_the_meta_on_disk(scratch_dir: Path) -> None:
    """会话列表读的就是盘上的 meta —— 它得跟内存里那份对得上，而不是停在创建那一刻。"""
    container = _build(scratch_dir, llm=ScriptedLlm([text_turn("在的")]))
    live = await container.sessions.create(user_id=USER)

    await container.agent.run(session=live.session, user_message=user_message("在吗"))

    fresh = FileSessionStore(scratch_dir / "sessions")
    meta, _ = await fresh.load(live.session_id, USER)  # type: ignore[misc]
    assert meta.updated_at == live.session.meta.updated_at
    assert meta.updated_at != meta.created_at


async def test_the_title_comes_from_the_first_user_message(scratch_dir: Path) -> None:
    """标题是会话的第一句话截出来的，第一次屏障就把它带下去了 —— 第二个 trace 不改它。"""
    container = _build(scratch_dir, llm=ScriptedLlm([text_turn("在的"), text_turn("好的")]))
    live = await container.sessions.create(user_id=USER)

    await container.agent.run(
        session=live.session, user_message=user_message("帮我把 write 换成 read")
    )
    await container.agent.run(session=live.session, user_message=user_message("再说一次"))

    fresh = FileSessionStore(scratch_dir / "sessions")
    meta, _ = await fresh.load(live.session_id, USER)  # type: ignore[misc]
    assert meta.title == "帮我把 write 换成 read"


async def test_a_simple_conversation_emits_the_expected_events(scratch_dir: Path) -> None:
    """跑一次真 trace 看事件序列：内核发两个，session 发三个，顺序也对。"""
    container = _build(scratch_dir, llm=ScriptedLlm([text_turn("好的")]))
    live = await container.sessions.create(user_id=USER)
    seen: list[object] = []
    for kind in (TraceStart, TurnStart, AssistantDelta, AssistantMessage, TraceEnd):
        container.ports.events.subscribe(kind, seen.append)

    await container.agent.run(session=live.session, user_message=user_message("帮我看看"))

    # TraceStart 在最前：位点 1 先跑，turn 1 才刚开始。
    assert [type(event).__name__ for event in seen] == [
        "TraceStart",
        "TurnStart",
        "AssistantDelta",
        "AssistantMessage",
        "TraceEnd",
    ]
    assert seen[2].text == "好的"  # type: ignore[union-attr]
    assert seen[3].content[0].text == "好的"  # type: ignore[union-attr]
    assert seen[4].status is TraceStatus.COMPLETED  # type: ignore[union-attr]


async def test_the_model_actually_sees_the_user_message(scratch_dir: Path) -> None:
    """链路是通的：用户消息经投影进了发给模型的 messages。"""
    llm = ScriptedLlm([text_turn("在的")])
    container = _build(scratch_dir, llm=llm)
    live = await container.sessions.create(user_id=USER)

    await container.agent.run(session=live.session, user_message=user_message("在吗"))

    sent = llm.requests[0].messages
    assert [message.role for message in sent] == [MessageRole.USER]


async def test_the_second_turn_sees_the_first_answer(scratch_dir: Path) -> None:
    """第二轮请求里能看见上一轮的回答 —— 投影确实在往前走。"""
    llm = ScriptedLlm([text_turn("在的"), text_turn("好的")])
    container = _build(scratch_dir, llm=llm)
    live = await container.sessions.create(user_id=USER)

    await container.agent.run(session=live.session, user_message=user_message("在吗"))
    await container.agent.run(session=live.session, user_message=user_message("再说一次"))

    assert [message.role for message in llm.requests[1].messages] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.USER,
    ]

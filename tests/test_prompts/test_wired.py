"""装配之后：系统提示词进请求、不进日志。"""

from __future__ import annotations

from pathlib import Path

from agent_architecture.composition import Container, build_container
from agent_architecture.contracts import MessageRole, SessionEntryType
from fakes import USER, ScriptedLlm, make_app_config, text_turn, user_message


def _build(scratch_dir: Path, llm: ScriptedLlm) -> Container:
    """按一份把会话目录指到临时目录的配置装出容器。

    举例：container = _build(scratch_dir, ScriptedLlm([text_turn("在的")]))
    """
    config = make_app_config(session_dir=str(scratch_dir / "sessions"))
    return build_container(config, llm=llm)  # type: ignore[arg-type]


async def test_the_handler_leaves_the_messages_alone() -> None:
    """提示词不进 messages：那里是 Session 的投影，压缩改的就是它。"""
    from agent_architecture.contracts import LlmBeforePayload, Site
    from agent_architecture.modules.prompts import build_handlers
    from fakes import make_site_context, make_trace_context

    messages = [user_message("在吗")]
    payload = LlmBeforePayload(model="fake-model", messages=list(messages))
    ctx = make_site_context(Site.LLM_BEFORE, payload, trace=make_trace_context())

    await build_handlers()[0].run(ctx)

    assert payload.messages == messages
    assert payload.system_prompt.startswith("你是 agent-architecture")
    assert payload.system_tokens > 0


async def test_the_system_prompt_reaches_the_model(scratch_dir: Path) -> None:
    """发出去的第一条是 system 消息，内容就是拼出来的那份。"""
    llm = ScriptedLlm([text_turn("在的")])
    container = _build(scratch_dir, llm)
    live = await container.sessions.create(user_id=USER)

    await container.agent.run(session=live.session, user_message=user_message("在吗"))

    head = llm.requests[0].messages[0]
    assert head.role is MessageRole.SYSTEM
    assert "agent-architecture" in head.content[0].text  # type: ignore[union-attr]


async def test_it_carries_the_sessions_own_facts(scratch_dir: Path) -> None:
    """工作目录与模型取自这次会话，不是写死的。"""
    llm = ScriptedLlm([text_turn("在的")])
    container = _build(scratch_dir, llm)
    live = await container.sessions.create(user_id=USER)

    await container.agent.run(session=live.session, user_message=user_message("在吗"))

    text = llm.requests[0].messages[0].content[0].text  # type: ignore[union-attr]
    assert container.config.workspace in text
    assert container.config.permission.value in text


async def test_it_never_lands_in_the_session(scratch_dir: Path) -> None:
    """日志里一条 system 消息都不许有 —— 它每次调用现拼。"""
    llm = ScriptedLlm([text_turn("在的"), text_turn("好的")])
    container = _build(scratch_dir, llm)
    live = await container.sessions.create(user_id=USER)

    await container.agent.run(session=live.session, user_message=user_message("在吗"))
    await container.agent.run(session=live.session, user_message=user_message("再来"))

    entries = live.session.entries()
    roles = [
        entry.payload.role  # type: ignore[union-attr]
        for entry in entries
        if entry.type is SessionEntryType.MESSAGE
    ]
    assert MessageRole.SYSTEM not in roles
    assert [entry.type for entry in entries] == [
        SessionEntryType.TRACE_START,
        SessionEntryType.MESSAGE,
        SessionEntryType.MESSAGE,
        SessionEntryType.TRACE_END,
        SessionEntryType.TRACE_START,
        SessionEntryType.MESSAGE,
        SessionEntryType.MESSAGE,
        SessionEntryType.TRACE_END,
    ]


async def test_every_turn_sends_exactly_one(scratch_dir: Path) -> None:
    """第二轮不会把它又叠一条：每条请求最前面恰好一条 system。"""
    llm = ScriptedLlm([text_turn("在的"), text_turn("好的")])
    container = _build(scratch_dir, llm)
    live = await container.sessions.create(user_id=USER)

    await container.agent.run(session=live.session, user_message=user_message("在吗"))
    await container.agent.run(session=live.session, user_message=user_message("再来"))

    for request in llm.requests:
        assert request.messages[0].role is MessageRole.SYSTEM
        assert [m.role for m in request.messages].count(MessageRole.SYSTEM) == 1


async def test_the_prompt_is_the_same_bytes_on_every_turn(scratch_dir: Path) -> None:
    """两轮的系统提示词完全相同 —— token 锚点的前缀比对靠的就是这一点。"""
    llm = ScriptedLlm([text_turn("在的"), text_turn("好的")])
    container = _build(scratch_dir, llm)
    live = await container.sessions.create(user_id=USER)

    await container.agent.run(session=live.session, user_message=user_message("在吗"))
    await container.agent.run(session=live.session, user_message=user_message("再来"))

    first, second = llm.requests[0].messages[0], llm.requests[1].messages[0]
    assert first == second

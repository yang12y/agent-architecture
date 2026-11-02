"""装配之后：目录进请求、不进日志，skill 工具能拿到正文。"""

from __future__ import annotations

from pathlib import Path

from agent_frame.composition import Container, build_container
from agent_frame.contracts import MessageRole, SessionEntryType
from fakes import USER, ScriptedLlm, make_app_config, text_turn, user_message


def _build(scratch_dir: Path, llm: ScriptedLlm) -> Container:
    """把会话目录与工作目录都指到临时目录的容器。

    举例：container = _build(scratch_dir, ScriptedLlm([text_turn("在的")]))
    """
    config = make_app_config(
        session_dir=str(scratch_dir / "sessions"),
        workspace=str(scratch_dir / "ws"),
    )
    return build_container(config, llm=llm)  # type: ignore[arg-type]


async def test_the_catalog_is_injected_right_after_the_system_prompt(scratch_dir: Path) -> None:
    """注入顺序：系统提示词在最前，技能目录紧随其后，然后才是对话。"""
    llm = ScriptedLlm([text_turn("在的")])
    container = _build(scratch_dir, llm)
    live = await container.sessions.create(user_id=USER)

    await container.agent.run(session=live.session, user_message=user_message("在吗"))

    sent = llm.requests[0].messages
    assert [message.role for message in sent[:2]] == [MessageRole.SYSTEM, MessageRole.USER]
    assert "以下是当前可用的技能" in sent[1].content[0].text  # type: ignore[union-attr]


async def test_it_never_lands_in_the_session(scratch_dir: Path) -> None:
    """日志里一条目录都没有 —— 它每次请求现拼。"""
    llm = ScriptedLlm([text_turn("在的")])
    container = _build(scratch_dir, llm)
    live = await container.sessions.create(user_id=USER)

    await container.agent.run(session=live.session, user_message=user_message("在吗"))

    assert [entry.type for entry in live.session.entries()] == [
        SessionEntryType.TRACE_START,
        SessionEntryType.MESSAGE,
        SessionEntryType.MESSAGE,
        SessionEntryType.TRACE_END,
    ]


async def test_every_request_carries_it_again(scratch_dir: Path) -> None:
    """第二条消息照样带上目录 —— 它不是持久消息，是每次注入。"""
    llm = ScriptedLlm([text_turn("在的"), text_turn("好的")])
    container = _build(scratch_dir, llm)
    live = await container.sessions.create(user_id=USER)

    await container.agent.run(session=live.session, user_message=user_message("在吗"))
    await container.agent.run(session=live.session, user_message=user_message("再来"))

    for request in llm.requests:
        assert "以下是当前可用的技能" in request.messages[1].content[0].text  # type: ignore[union-attr]


async def test_the_project_can_sign_override_a_builtin_skill(scratch_dir: Path) -> None:
    """工作目录下的 .skills 覆盖同名内置技能。"""
    from test_skill.helpers import write_skill

    write_skill(scratch_dir / "ws" / ".skills", "run-tests", description="项目自己的版本")
    llm = ScriptedLlm([text_turn("在的")])
    container = _build(scratch_dir, llm)
    live = await container.sessions.create(user_id=USER)

    await container.agent.run(session=live.session, user_message=user_message("在吗"))

    catalog = llm.requests[0].messages[1].content[0].text  # type: ignore[union-attr]
    assert "项目自己的版本" in catalog
    assert "在这个项目里跑格式检查与测试" not in catalog


async def test_the_skill_tool_reads_a_builtin_body(scratch_dir: Path) -> None:
    """`skill` 工具真的登记进了工具表，而且能读到正文。"""
    container = _build(scratch_dir, ScriptedLlm([text_turn("在的")]))
    tool = container.ports.tools.tool("skill")  # type: ignore[union-attr]

    assert tool is not None
    assert "加一个 handler" in await tool.run(object(), {"name": "add-handler"})

"""装配之后：模型能维护待办，日志有、上下文里没有、接口带得出来。"""

from __future__ import annotations

from pathlib import Path

from agent_frame.api.schemas import SessionDetailOut, TodoItemOut
from agent_frame.api.stats import session_stats
from agent_frame.composition import Container, build_container
from agent_frame.contracts import MessageRole, SessionEntryType, TextPart, TodoUpdated
from fakes import USER, ScriptedLlm, make_app_config, text_turn, tool_turn, user_message


def _build(scratch_dir: Path, llm: ScriptedLlm) -> Container:
    """把会话目录与工作目录都指到临时目录的容器。

    举例：container = _build(scratch_dir, ScriptedLlm([...]))
    """
    config = make_app_config(
        session_dir=str(scratch_dir / "sessions"),
        workspace=str(scratch_dir / "ws"),
    )
    return build_container(config, llm=llm)  # type: ignore[arg-type]


def _turn(todos: list[dict] | None = None):
    """模型第一轮请求 todo_write，参数可换。

    举例：_turn([{"content": "甲", "status": "pending"}])
    """
    payload = {"todos": todos if todos is not None else []}
    return tool_turn(("call_1", "todo_write", payload))


async def test_a_todo_write_lands_in_the_log(scratch_dir: Path) -> None:
    """一次调用：日志多一条 todo 条目，清单跟着变。"""
    llm = ScriptedLlm([_turn([{"content": "甲", "status": "pending"}]), text_turn("记下了")])
    container = _build(scratch_dir, llm)
    live = await container.sessions.create(user_id=USER)

    await container.agent.run(session=live.session, user_message=user_message("帮我做两件事"))

    types = [entry.type for entry in live.session.entries()]
    assert SessionEntryType.TODO in types
    assert live.session.todos()[0].content == "甲"


async def test_the_todo_entry_stays_out_of_the_context(scratch_dir: Path) -> None:
    """待办只进日志 —— 下一轮发给模型的消息里一条都不该有它。"""
    llm = ScriptedLlm(
        [_turn([{"content": "只在日志里", "status": "pending"}]), text_turn("记下了")]
    )
    container = _build(scratch_dir, llm)
    live = await container.sessions.create(user_id=USER)

    await container.agent.run(session=live.session, user_message=user_message("帮我做两件事"))

    texts = [
        part.text
        for message in live.session.messages()
        for part in message.content
        if isinstance(part, TextPart)
    ]
    assert not any("只在日志里" in text for text in texts)


async def test_the_event_reaches_the_bus(scratch_dir: Path) -> None:
    """前端靠这条事件刷新面板。"""
    llm = ScriptedLlm([_turn([{"content": "甲", "status": "in_progress"}]), text_turn("记下了")])
    container = _build(scratch_dir, llm)
    seen: list[TodoUpdated] = []
    container.ports.events.subscribe(TodoUpdated, seen.append)  # type: ignore[arg-type]
    live = await container.sessions.create(user_id=USER)

    await container.agent.run(session=live.session, user_message=user_message("帮我做两件事"))

    assert len(seen) == 1
    assert [(todo.content, todo.status.value) for todo in seen[0].todos] == [("甲", "in_progress")]


async def test_the_detail_endpoint_carries_the_list(scratch_dir: Path) -> None:
    """刷新之后界面还得画得出来 —— 详情里带一份当前清单。"""
    llm = ScriptedLlm([_turn([{"content": "甲", "status": "completed"}]), text_turn("记下了")])
    container = _build(scratch_dir, llm)
    live = await container.sessions.create(user_id=USER)

    await container.agent.run(session=live.session, user_message=user_message("帮我做两件事"))

    detail = SessionDetailOut.from_live(
        live,
        stats=session_stats(live.session.entries(), max_tokens=1_000_000),
        entries=[],
        has_more=False,
    )
    assert detail.todos == [TodoItemOut(content="甲", status="completed")]


async def test_a_failed_todo_write_leaves_no_entry(scratch_dir: Path) -> None:
    """两条 in_progress：工具回错误说明，日志里不留东西。"""
    llm = ScriptedLlm(
        [
            _turn(
                [
                    {"content": "甲", "status": "in_progress"},
                    {"content": "乙", "status": "in_progress"},
                ]
            ),
            text_turn("改一下"),
        ]
    )
    container = _build(scratch_dir, llm)
    live = await container.sessions.create(user_id=USER)

    await container.agent.run(session=live.session, user_message=user_message("帮我做两件事"))

    assert live.session.todos() == ()
    assert SessionEntryType.TODO not in [entry.type for entry in live.session.entries()]


async def test_the_tool_result_is_only_counts(scratch_dir: Path) -> None:
    """模型看到的工具结果是计数，不是清单。"""
    llm = ScriptedLlm([_turn([{"content": "甲", "status": "pending"}]), text_turn("记下了")])
    container = _build(scratch_dir, llm)
    live = await container.sessions.create(user_id=USER)

    await container.agent.run(session=live.session, user_message=user_message("帮我做两件事"))

    tool_messages = [
        message for message in live.session.messages() if message.role is MessageRole.TOOL
    ]
    body = tool_messages[0].content[0].text  # type: ignore[union-attr]
    assert body == '{"counts": {"completed": 0, "in_progress": 0, "pending": 1}}'

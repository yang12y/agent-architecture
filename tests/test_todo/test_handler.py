"""位点 8：写一条 todo 条目、发一条事件。"""

from __future__ import annotations

from agent_frame.contracts import (
    AgentError,
    Permission,
    SessionEntryType,
    Site,
    TodoEntryPayload,
    TodoItem,
    TodoStatus,
    TodoUpdated,
    ToolAfterPayload,
    ToolCall,
    ToolCallStatus,
    TraceBeforePayload,
    TraceTrigger,
)
from agent_frame.modules.session import Session
from agent_frame.modules.todo import PRIORITY_TODO, PRIORITY_TODO_RESET, build_handlers
from fakes import (
    SequentialIds,
    make_ports,
    make_session_meta,
    make_site_context,
    make_trace_context,
    user_message,
)


def _session() -> Session:
    """一个空会话。"""
    return Session(make_session_meta(), [], ids=SequentialIds())


def _ctx(
    session: Session,
    *,
    name: str = "todo_write",
    arguments: dict | None = None,
    error: AgentError | None = None,
):
    """造一个位点 8 的上下文，工具调用名与参数可换。

    举例：_ctx(session, arguments={"todos": [...]})
    """
    call = ToolCall(
        id="call_1",
        name=name,
        arguments=arguments if arguments is not None else {"todos": []},
        status=ToolCallStatus.RUNNING,
    )
    payload = ToolAfterPayload(call=call, raw_result="ok", error=error)
    ports = make_ports()
    return make_site_context(
        Site.TOOL_AFTER, payload, trace=make_trace_context(session=session, ports=ports)
    )


def _trace_before():
    """位点 1 那个 handler（本轮开始清空清单）。"""
    return next(handler for handler in build_handlers() if handler.id == "todo.trace_before")


def _tool_after():
    """位点 8 那个 handler（todo_write 的结果落盘）。"""
    return next(handler for handler in build_handlers() if handler.id == "todo.tool_after")


def _seen(ctx) -> list[object]:
    """订阅 TodoUpdated，把收到的都收进列表。

    举例：seen = _seen(ctx)
    """
    events: list[object] = []
    ctx.ports.events.subscribe(TodoUpdated, events.append)
    return events


def _trace_ctx(session: Session):
    """造一个位点 1 的上下文：新一轮开始。

    举例：await build_handlers()[0].run(_trace_ctx(session))
    """
    payload = TraceBeforePayload(
        session_id=session.meta.session_id,
        trigger=TraceTrigger.USER,
        parent_trace_id=None,
        user_message=user_message("下一个问题"),
        model="fake-model",
        permission=Permission.EDITABLE,
    )
    ports = make_ports()
    return make_site_context(
        Site.TRACE_BEFORE, payload, trace=make_trace_context(session=session, ports=ports)
    )


def test_the_handlers_sit_where_the_doc_says() -> None:
    """位点 1 清空（210，session 的 trace_start 之后）；位点 8 落盘（420，工具结果之后）。"""
    assert PRIORITY_TODO_RESET == 210
    assert PRIORITY_TODO == 420
    assert [handler.priority for handler in build_handlers()] == [210, 420]


async def test_a_new_trace_clears_the_previous_list() -> None:
    """上一轮留下的清单随本轮开始作废 —— 否则新问题还挂着旧任务的「进行中」。"""
    session = _session()
    session.append_entry(
        trace_id="t",
        type=SessionEntryType.TODO,
        payload=TodoEntryPayload(todos=(TodoItem("跑测试", TodoStatus.IN_PROGRESS),)),
        now=make_trace_context().ports.clock.now(),
    )
    ctx = _trace_ctx(session)
    seen = _seen(ctx)

    await _trace_before().run(ctx)

    assert session.todos() == ()
    assert [entry.type for entry in session.entries()] == [
        SessionEntryType.TODO,
        SessionEntryType.TODO,
    ]
    assert len(seen) == 1
    assert seen[0].todos == ()  # type: ignore[attr-defined]


async def test_a_new_trace_without_a_list_writes_nothing() -> None:
    """本来就没有清单：不写空条目，也不发事件 —— 日志里不留噪音。"""
    session = _session()
    ctx = _trace_ctx(session)
    seen = _seen(ctx)

    await _trace_before().run(ctx)

    assert session.entries() == []
    assert seen == []


async def test_a_todo_write_appends_an_entry_and_publishes() -> None:
    """正常一条：日志多一条 todo 条目，清单跟着变，事件发出去。"""
    session = _session()
    ctx = _ctx(
        session,
        arguments={"todos": [{"content": "跑测试", "status": "in_progress"}]},
    )
    seen = _seen(ctx)

    await _tool_after().run(ctx)

    assert [entry.type for entry in session.entries()] == [SessionEntryType.TODO]
    assert session.todos() == (TodoItem("跑测试", TodoStatus.IN_PROGRESS),)
    assert len(seen) == 1
    assert seen[0].todos == (TodoItem("跑测试", TodoStatus.IN_PROGRESS),)  # type: ignore[attr-defined]


async def test_another_tool_is_left_alone() -> None:
    """别的工具的结果不归它管。"""
    session = _session()
    ctx = _ctx(session, name="read", arguments={"path": "a.py"})
    seen = _seen(ctx)

    await _tool_after().run(ctx)

    assert session.entries() == []
    assert seen == []


async def test_a_failed_call_is_not_written() -> None:
    """校验没过时模型已经拿到失败说明，坏清单不该进日志。"""
    session = _session()
    ctx = _ctx(
        session,
        arguments={
            "todos": [
                {"content": "甲", "status": "in_progress"},
                {"content": "乙", "status": "in_progress"},
            ]
        },
        error=AgentError("同一时刻至多一条 in_progress"),
    )
    seen = _seen(ctx)

    await _tool_after().run(ctx)

    assert session.entries() == []
    assert seen == []


async def test_an_empty_list_is_written_as_a_clear() -> None:
    """清空也要落一条、也要发事件 —— 否则前端不知道该把面板收起来。"""
    session = _session()
    session.append_entry(
        trace_id="t",
        type=SessionEntryType.TODO,
        payload=TodoEntryPayload(todos=(TodoItem("跑测试", TodoStatus.PENDING),)),
        now=make_trace_context().ports.clock.now(),
    )
    ctx = _ctx(session, arguments={"todos": []})
    seen = _seen(ctx)

    await _tool_after().run(ctx)

    assert session.todos() == ()
    assert [entry.type for entry in session.entries()] == [
        SessionEntryType.TODO,
        SessionEntryType.TODO,
    ]
    assert len(seen) == 1

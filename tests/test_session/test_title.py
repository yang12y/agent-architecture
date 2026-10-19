"""会话标题的测试。

分两层：纯函数（怎么截）与位点（什么时候写、什么时候不写）。
"""

from __future__ import annotations

from fakes import (
    SequentialIds,
    make_session_meta,
    make_site_context,
    make_trace_context,
    user_message,
)
from yzy_agent.contracts import (
    Handler,
    Message,
    MessageRole,
    Permission,
    Site,
    TextPart,
    ToolCallPart,
    TraceBeforePayload,
    TraceTrigger,
)
from yzy_agent.modules.session import (
    TITLE_MAX_CHARS,
    Session,
    build_handlers,
    title_from,
)


def test_a_short_message_becomes_the_title_verbatim() -> None:
    """整句话放得下就原样用，不加任何修饰。"""
    assert title_from(user_message("帮我读一下 src/main.py")) == "帮我读一下 src/main.py"


def test_newlines_collapse_into_single_spaces() -> None:
    """标题是一行，正文里的换行与连续空白都要压平。"""
    assert title_from(user_message("第一行\n\n第二行   第三行")) == "第一行 第二行 第三行"


def test_a_long_message_is_cut_and_marked() -> None:
    """超长就截断，末尾补省略号告诉人「后面还有」。"""
    title = title_from(user_message("很长" * 40))

    assert title is not None
    assert title.endswith("…")
    assert len(title) == TITLE_MAX_CHARS


def test_the_title_never_exceeds_the_limit() -> None:
    """省略号也算在上限里 —— 卡在边界上的长度不能冒出去。"""
    for length in range(TITLE_MAX_CHARS - 2, TITLE_MAX_CHARS + 5):
        title = title_from(user_message("字" * length))
        assert title is not None
        assert len(title) <= TITLE_MAX_CHARS


def test_a_message_without_any_text_has_no_title() -> None:
    """只有工具调用的消息不是标题素材。"""
    message = Message(
        role=MessageRole.USER,
        content=[ToolCallPart(tool_call_id="call_1", name="read", arguments={})],
    )

    assert title_from(message) is None


def test_a_whitespace_only_message_has_no_title() -> None:
    """空白不是标题 —— 宁可没有，也不要一个空串。"""
    assert title_from(user_message("   \n\t  ")) is None


def test_only_the_text_parts_are_used() -> None:
    """混合内容只取文本块。"""
    message = Message(role=MessageRole.USER, content=[TextPart(text="  读文件  ")])

    assert title_from(message) == "读文件"


def _handler(handler_id: str) -> Handler:
    """按 id 取一个 session handler。

    举例：_handler("session.trace_before")
    """
    return next(handler for handler in build_handlers() if handler.id == handler_id)


def _session() -> Session:
    """一个全新的空 Session，title 是 None。

    举例：_session().meta.title -> None
    """
    return Session(make_session_meta(), [], ids=SequentialIds())


def _trace_before(session: Session, text: str = "帮我读一下 src/main.py"):
    """把一条用户消息挂到位点 1 上。

    举例：_trace_before(_session(), "帮我看下日志")
    """
    payload = TraceBeforePayload(
        session_id=session.meta.session_id,
        trigger=TraceTrigger.USER,
        parent_trace_id=None,
        user_message=user_message(text),
        model="fake-model",
        permission=Permission.EDITABLE,
    )
    return make_site_context(Site.TRACE_BEFORE, payload, trace=make_trace_context(session=session))


async def test_trace_before_writes_the_title() -> None:
    """第一次 trace 走到位点 1 时，标题就是这句话。"""
    session = _session()

    await _handler("session.trace_before").run(_trace_before(session))

    assert session.meta.title == "帮我读一下 src/main.py"


async def test_an_existing_title_is_left_alone() -> None:
    """用户显式给过标题的，不许被覆盖。"""
    session = _session()
    session.set_title("用户自己起的")

    await _handler("session.trace_before").run(_trace_before(session, "另一句话"))

    assert session.meta.title == "用户自己起的"


async def test_a_later_trace_does_not_rewrite_the_title() -> None:
    """标题只写一次 —— 第二个 trace 的第一句话不该把它顶掉。"""
    session = _session()
    handler = _handler("session.trace_before")

    await handler.run(_trace_before(session, "第一句"))
    await handler.run(_trace_before(session, "第二句"))

    assert session.meta.title == "第一句"


async def test_no_title_when_the_message_has_no_text() -> None:
    """发了个空的或者只有图片的，就别硬造标题。"""
    session = _session()
    payload = TraceBeforePayload(
        session_id=session.meta.session_id,
        trigger=TraceTrigger.USER,
        parent_trace_id=None,
        user_message=Message(role=MessageRole.USER, content=[]),
        model="fake-model",
        permission=Permission.EDITABLE,
    )
    ctx = make_site_context(Site.TRACE_BEFORE, payload, trace=make_trace_context(session=session))

    await _handler("session.trace_before").run(ctx)

    assert session.meta.title is None

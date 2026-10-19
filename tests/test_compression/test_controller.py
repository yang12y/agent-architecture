"""两级压缩的调度：位点 3 一处入口，先便宜后昂贵。"""

from __future__ import annotations

import json

import pytest

from fakes import (
    START,
    ScriptedLlm,
    SequentialIds,
    assistant_message,
    make_frozen_config,
    make_ports,
    make_session_meta,
    make_site_context,
    make_trace_context,
    text_turn,
    tool_message,
    user_message,
)
from yzy_agent.contracts import (
    Compaction,
    ContextOverflowError,
    LlmBeforePayload,
    Message,
    SessionEntryType,
    Site,
    TextPart,
    ToolCallPart,
)
from yzy_agent.infra.system import HeuristicTokenCounter
from yzy_agent.modules.compression import compact_at_llm_before, shrink
from yzy_agent.modules.session import Session

# 一份工具 schema。它不是 messages 的一部分，重数一遍 messages 数不到它。
#
# 举例：[{"name": "read", "description": "读一个文件", ...}]
SCHEMA = [{"name": "read", "description": "读一个文件", "parameters": {"type": "object"}}]

# 一个"很长"的正文。
#
# 举例："x" * 4000
LONG = "x" * 4000


def _session(count: int, *, body: str | None = None) -> Session:
    """一个装着 count 条用户消息的会话；给了 body 就每条都用它。

    举例：_session(3).entries() -> [SessionEntry(type=MESSAGE, ...)] * 3
    """
    session = Session(make_session_meta(), [], ids=SequentialIds())
    for index in range(count):
        session.append_entry(
            trace_id="t",
            type=SessionEntryType.MESSAGE,
            payload=user_message(body if body is not None else f"第 {index} 条"),
            now=START,
        )
    return session


def _types(session: Session) -> list[SessionEntryType]:
    """日志里各条目的类型。

    举例：_types(session) -> [MESSAGE, COMPACTION]
    """
    return [entry.type for entry in session.entries()]


def _text(message: Message) -> str:
    """一条消息的纯文本。

    举例：_text(user_message("你好")) -> "你好"
    """
    return "".join(part.text for part in message.content if isinstance(part, TextPart))


def _ctx(site: Site, payload, session: Session, llm: ScriptedLlm, *, config=None):
    """把 payload 挂到位点上；端口里的模型换成假件。

    举例：ctx = _ctx(Site.LLM_BEFORE, payload, session, llm)
    """
    ports = make_ports(llm=llm)
    trace = make_trace_context(session=session, ports=ports, config=config)
    return make_site_context(site, payload, trace=trace)


def _before(
    session: Session, *, compact_tokens: int, schema: list | None = None
) -> LlmBeforePayload:
    """位点 3 的 payload。context_tokens 按真计数器算，和运行时是同一个口径 ——
    压缩之后是拿它做减法，随手写个假数就对不上了。

    举例：_before(session, compact_tokens=50)
    """
    messages = session.messages()
    counter = HeuristicTokenCounter()
    tokens = counter.count_messages(messages)
    if schema:
        tokens += counter.count_text(json.dumps(schema, ensure_ascii=False))
    return LlmBeforePayload(
        model="m",
        messages=messages,
        tools_schema=schema or [],
        context_tokens=tokens,
        compact_tokens=compact_tokens,
    )


async def test_nothing_happens_when_under_budget() -> None:
    """没到阈值就什么都不做，一次模型都不调。"""
    session, llm = _session(5), ScriptedLlm([text_turn("摘要")])

    await compact_at_llm_before(
        _ctx(Site.LLM_BEFORE, _before(session, compact_tokens=100), session, llm)
    )

    assert _types(session) == [SessionEntryType.MESSAGE] * 5
    assert llm.requests == []


async def test_level1_alone_is_enough() -> None:
    """一级换掉长结果就下来了 —— 一次模型都不用调，而且日志里一个字没动。"""
    session = _session(1)
    session.append_entry(
        trace_id="t",
        type=SessionEntryType.MESSAGE,
        payload=tool_message(LONG, tool_call_id="call_1"),
        now=START,
    )
    llm = ScriptedLlm([text_turn("摘要")])
    payload = _before(session, compact_tokens=50)
    # 默认配置是"最近 1 条长结果保原文"，那样这条就换不掉；这条用例要的正是换掉它。
    config = make_frozen_config(long_tool_result_keep_recent=0)

    await compact_at_llm_before(_ctx(Site.LLM_BEFORE, payload, session, llm, config=config))

    assert llm.requests == []
    assert "已省略" in _text(payload.messages[1])
    assert _text(session.messages()[1]) == LONG


async def test_level1_subtracts_the_savings_and_keeps_the_schema() -> None:
    """一级不重数上下文：压完的总数 = 进来的总数 − 省下的量。

    关键是 schema —— 它不是 messages 的一部分，重新数一遍 messages 就会把它悄悄抹掉，
    阈值判定跟着失真（以前就是这个毛病：压一次 Context 就凭空少了整个 schema）。
    """
    session = _session(1)
    session.append_entry(
        trace_id="t",
        type=SessionEntryType.MESSAGE,
        payload=tool_message(LONG, tool_call_id="call_1"),
        now=START,
    )
    config = make_frozen_config(long_tool_result_keep_recent=0)
    payload = _before(session, compact_tokens=1000, schema=SCHEMA)
    before = payload.context_tokens
    counter = HeuristicTokenCounter()
    _, saved = shrink(
        session.messages(),
        tokens=counter,
        max_tokens=config.long_tool_result_max_tokens,
        keep_recent=0,
    )

    await compact_at_llm_before(
        _ctx(Site.LLM_BEFORE, payload, session, ScriptedLlm(), config=config)
    )

    assert payload.context_tokens == before - saved
    # 数 messages 只数得到正文 —— 总数比它大，说明 schema 那份还在里面。
    assert payload.context_tokens > counter.count_messages(payload.messages)


async def test_it_falls_through_to_level2() -> None:
    """一级不够就升二级：落一条 compaction，并把发出去的上下文整份换掉。"""
    # 每条都很大：压掉四条之后才真的降到阈值以下，否则一级那步就早退了。
    session = _session(6, body="x" * 1000)
    llm = ScriptedLlm([text_turn("前面聊了六句")])
    payload = _before(session, compact_tokens=800)

    await compact_at_llm_before(_ctx(Site.LLM_BEFORE, payload, session, llm))

    assert SessionEntryType.COMPACTION in _types(session)
    assert len(payload.messages) == 3
    assert "前面聊了六句" in _text(payload.messages[0])


async def test_long_results_in_the_kept_window_are_still_shrunk() -> None:
    """二级之后要再补一次一级 —— 保留窗口里那几条长工具结果不能原样带全文进门。"""
    session = _session(4, body="x" * 4000)
    call = assistant_message("我来读")
    call.content.append(
        ToolCallPart(tool_call_id="call_1", name="read", arguments={"path": "a.py"})
    )
    session.append_entry(trace_id="t", type=SessionEntryType.MESSAGE, payload=call, now=START)
    for _ in range(2):
        session.append_entry(
            trace_id="t",
            type=SessionEntryType.MESSAGE,
            payload=tool_message(LONG, tool_call_id="call_1"),
            now=START,
        )
    llm = ScriptedLlm([text_turn("摘要")])
    payload = _before(session, compact_tokens=4000)

    await compact_at_llm_before(_ctx(Site.LLM_BEFORE, payload, session, llm))

    assert SessionEntryType.COMPACTION in _types(session)  # 确实走了二级
    assert any("已省略" in _text(m) for m in payload.messages)  # 一级又补了一遍


async def test_it_raises_when_still_over_after_both() -> None:
    """两级都压不动就抛 —— 内核按 budget_exceeded 收尾。"""
    session, llm = _session(1, body=LONG), ScriptedLlm([text_turn("摘要")])
    ctx = _ctx(Site.LLM_BEFORE, _before(session, compact_tokens=10), session, llm)

    with pytest.raises(ContextOverflowError):
        await compact_at_llm_before(ctx)


async def test_a_compaction_event_is_published() -> None:
    """压掉了就广播一条事件，前端才知道上下文被换过。"""
    session = _session(6, body="x" * 1000)
    llm = ScriptedLlm([text_turn("摘要")])
    expected = [e.entry_id for e in session.entries()[-2:]]
    ctx = _ctx(Site.LLM_BEFORE, _before(session, compact_tokens=800), session, llm)
    published: list[object] = []
    ctx.ports.events.subscribe(Compaction, published.append)

    await compact_at_llm_before(ctx)

    assert len(published) == 1
    event = published[0]
    assert isinstance(event, Compaction)
    assert event.kept_entry_ids == expected

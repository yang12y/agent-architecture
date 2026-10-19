"""二级压缩：滑动窗口 + 摘要 + compaction 条目。"""

from __future__ import annotations

from typing import cast

from fakes import START, ScriptedLlm, SequentialIds, make_session_meta, text_turn, user_message
from yzy_agent.contracts import (
    CompactionEntryPayload,
    Message,
    SessionEntryType,
    TextPart,
)
from yzy_agent.modules.compression import compact
from yzy_agent.modules.session import SUMMARY_PREFIX, Session


def _session(count: int) -> Session:
    """一个装着 count 条用户消息的会话。

    举例：_session(5).entries() -> [SessionEntry(type=MESSAGE, ...)]
    """
    session = Session(make_session_meta(), [], ids=SequentialIds())
    for index in range(count):
        session.append_entry(
            trace_id="t",
            type=SessionEntryType.MESSAGE,
            payload=user_message(f"第 {index} 条"),
            now=START,
        )
    return session


def _text(message: Message) -> str:
    """一条消息的纯文本。

    举例：_text(user_message("你好")) -> "你好"
    """
    return "".join(part.text for part in message.content if isinstance(part, TextPart))


async def _compact(session: Session, llm: ScriptedLlm, *, keep_recent: int = 2):
    """跑一次二级压缩。

    举例：entry = await _compact(session, ScriptedLlm([text_turn("摘要")]))
    """
    return await compact(
        session,
        llm=llm,
        model="summary-model",
        template="摘要：{history}",
        keep_recent=keep_recent,
        trace_id="t",
        now=START,
    )


async def test_it_appends_a_compaction_entry() -> None:
    """保留最后 n 条，其余压成摘要，追加一条 compaction。"""
    session = _session(5)

    entry = await _compact(session, ScriptedLlm([text_turn("前面聊了五句")]))

    assert entry is not None
    assert entry.type is SessionEntryType.COMPACTION
    assert cast(CompactionEntryPayload, entry.payload).summary == "前面聊了五句"


async def test_the_summary_replaces_the_dropped_messages() -> None:
    """投影变成"摘要 + 保留的那几条"。"""
    session = _session(5)
    await _compact(session, ScriptedLlm([text_turn("前面聊了五句")]))

    messages = session.messages()

    assert len(messages) == 3  # 1 条摘要 + 保留的 2 条
    assert SUMMARY_PREFIX in _text(messages[0])
    assert [_text(m) for m in messages[1:]] == ["第 3 条", "第 4 条"]


async def test_the_kept_entries_are_referenced_by_id() -> None:
    """保留的那几条只留 entry_id 引用，不内嵌内容。"""
    session = _session(5)
    expected = [e.entry_id for e in session.entries()[-2:]]

    entry = await _compact(session, ScriptedLlm([text_turn("摘要")]))

    payload = cast(CompactionEntryPayload, entry.payload)  # type: ignore[arg-type]
    assert payload.entry_ids == expected


async def test_the_previous_summary_is_folded_in_not_stacked() -> None:
    """压第二次时，上一条摘要在被压掉的那一段里 —— 上下文里永远只有一条摘要。"""
    session = _session(5)
    llm = ScriptedLlm([text_turn("第一次摘要"), text_turn("第二次摘要")])
    await _compact(session, llm)
    for index in range(5, 9):
        session.append_entry(
            trace_id="t",
            type=SessionEntryType.MESSAGE,
            payload=user_message(f"第 {index} 条"),
            now=START,
        )

    await _compact(session, llm)

    # 第二次的提示词里带着第一次的摘要 —— 说明它确实被一并压掉了。
    prompt = _text(llm.requests[1].messages[0])
    assert "第一次摘要" in prompt
    summaries = [m for m in session.messages() if SUMMARY_PREFIX in _text(m)]
    assert len(summaries) == 1
    assert "第二次摘要" in _text(summaries[0])


async def test_nothing_happens_when_there_is_not_enough_to_drop() -> None:
    """上下文本来就不比 keep_recent 长，没什么可压的。"""
    session = _session(2)
    before = session.entries()

    entry = await _compact(session, ScriptedLlm([text_turn("摘要")]))

    assert entry is None
    assert session.entries() == before


async def test_an_empty_model_answer_leaves_the_log_alone() -> None:
    """模型什么都没吐，就别写一条空摘要进日志。"""
    session = _session(5)
    before = session.entries()

    entry = await _compact(session, ScriptedLlm([text_turn("")]))

    assert entry is None
    assert session.entries() == before


async def test_the_draft_is_not_summarized() -> None:
    """草稿是这一轮正在生成的半截输出，不是历史。"""
    session = _session(4)
    session.append_draft("半截输出", trace_id="t", turn_index=1, now=START)
    llm = ScriptedLlm([text_turn("摘要")])

    await _compact(session, llm)

    assert "半截输出" not in _text(llm.requests[0].messages[0])
    assert "半截输出" in _text(session.messages()[-1])

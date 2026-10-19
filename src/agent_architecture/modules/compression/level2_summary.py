"""二级压缩：滑动窗口 + 摘要 + 落盘 compaction。

它换一次摘要模型调用，产出一条 compaction 条目 —— 从此**摘要取代那些消息**。
被压掉的正文之后不再有任何副本（压缩是有损的），所以摘要必须能独立阅读。

**这次调用不构成 turn**：不产生 assistant 消息、不进 messages、不加 turn_index，
可以用比主模型更便宜的模型。
"""

from __future__ import annotations

from collections.abc import Sequence

from agent_architecture.contracts import (
    CompactionEntryPayload,
    LlmClient,
    LlmRequest,
    Message,
    MessageRole,
    SessionEntry,
    SessionEntryType,
    SessionHandle,
    TextPart,
    Timestamp,
    collect_text,
)

from .prompts import summary_prompt

# 摘要的采样温度。低一点：这是归纳，不是创作。
#
# 举例：0.2
SUMMARY_TEMPERATURE = 0.2


async def compact(
    session: SessionHandle,
    *,
    llm: LlmClient,
    model: str,
    template: str,
    keep_recent: int,
    trace_id: str,
    now: Timestamp,
) -> SessionEntry | None:
    """保留最后 keep_recent 条，其余压成一条摘要，追加 compaction 条目。

    压不动时返回 None：上下文本来就不比 keep_recent 长，或者模型什么都没吐。
    **上一条摘要在被压掉的那一段里** —— 所以新摘要把它一并吸收，不会叠加成两层。

    举例：entry = await compact(session, llm=client, model="deepseek-chat", ...)
    """
    body = _body(session)
    if len(body) <= keep_recent:
        return None

    summary = await _summarize(llm, model=model, template=template, messages=body[:-keep_recent])
    if not summary:
        return None

    return session.append_entry(
        trace_id=trace_id,
        type=SessionEntryType.COMPACTION,
        payload=CompactionEntryPayload(
            summary=summary,
            entry_ids=[entry.entry_id for entry in session.context_entries()[-keep_recent:]],
        ),
        now=now,
    )


def _body(session: SessionHandle) -> list[Message]:
    """上下文里"会被压掉或保留"的那部分消息 —— **不含草稿**。

    草稿是这一轮正在生成的半截输出，不是历史；把它卷进摘要就等于把它弄丢了。
    它也只是尾巴上多出来的一条，先摘掉才能和 context_entries() 对齐。

    举例：_body(session) -> [Message(role=MessageRole.USER, ...)]
    """
    messages = session.messages()
    return messages[:-1] if session.draft_text() else messages


async def _summarize(
    llm: LlmClient, *, model: str, template: str, messages: Sequence[Message]
) -> str:
    """调一次摘要模型，返回摘要正文。

    举例：await _summarize(llm, model="m", template="…{history}…", messages=[...])
    """
    request = LlmRequest(
        model=model,
        messages=[
            Message(
                role=MessageRole.USER,
                content=[TextPart(text=summary_prompt(template, messages))],
            )
        ],
        temperature=SUMMARY_TEMPERATURE,
    )
    return (await collect_text(llm, request)).strip()

"""位点 3（llm:before）的操作测试。"""

from __future__ import annotations

from agent_architecture.contracts import (
    LlmBeforePayload,
    Message,
    MessageRole,
    Site,
    TextPart,
    TokenAnchor,
)
from agent_architecture.modules.runtime_state import s03_llm_before
from fakes import StubSession, make_site_context, make_trace_context


def _payload(
    messages: list[Message], schema: list[dict[str, object]] | None = None
) -> LlmBeforePayload:
    """造一份位点 3 的 payload。

    举例：_payload([Message(role=MessageRole.USER, content=[TextPart(text="你好")])])
    """
    return LlmBeforePayload(
        model="fake-model",
        messages=messages,
        tools_schema=schema or [],  # type: ignore[arg-type]
    )


def test_context_tokens_is_written_to_both_sides() -> None:
    """payload 给本位点后面的 compression 读，执行态给后续位点与预算守卫读，两边都要写。"""
    messages = [Message(role=MessageRole.USER, content=[TextPart(text="你好")])]
    ctx = make_site_context(Site.LLM_BEFORE, _payload(messages))

    s03_llm_before.apply(ctx)

    expected = ctx.ports.tokens.count_messages(messages)
    assert ctx.payload.context_tokens == expected  # type: ignore[attr-defined]
    assert ctx.state.context_tokens == expected


def test_single_short_message_counts_text_plus_overhead() -> None:
    """口径固定：一个两字的中文消息 = 2 个 token 正文 + 4 个 token 消息开销。"""
    ctx = make_site_context(
        Site.LLM_BEFORE,
        _payload([Message(role=MessageRole.USER, content=[TextPart(text="你好")])]),
    )

    s03_llm_before.apply(ctx)

    assert ctx.state.context_tokens == 6


def test_tool_schema_counts_towards_the_total() -> None:
    """工具 schema 也算上下文占用 —— 漏掉它会让压缩阈值形同虚设。"""
    messages = [Message(role=MessageRole.USER, content=[TextPart(text="读文件")])]
    schema = [{"name": "read", "description": "读一个文件", "parameters": {"type": "object"}}]

    without_schema = make_site_context(Site.LLM_BEFORE, _payload(messages))
    s03_llm_before.apply(without_schema)
    with_schema = make_site_context(Site.LLM_BEFORE, _payload(messages, schema))
    s03_llm_before.apply(with_schema)

    assert with_schema.state.context_tokens > without_schema.state.context_tokens


def test_no_schema_adds_nothing() -> None:
    """没有工具时 schema 贡献 0，不是一条固定开销。"""
    messages = [Message(role=MessageRole.USER, content=[TextPart(text="只聊天")])]
    ctx = make_site_context(Site.LLM_BEFORE, _payload(messages))

    s03_llm_before.apply(ctx)

    assert ctx.state.context_tokens == ctx.ports.tokens.count_messages(messages)


def test_a_trusted_anchor_replaces_the_estimate() -> None:
    """上一次真发出去的是这次的前缀：用提供方的真值当基数，只补算新增的那几条。"""
    sent = Message(role=MessageRole.USER, content=[TextPart(text="帮我读 a.py")])
    reply = Message(role=MessageRole.ASSISTANT, content=[TextPart(text="好的")])
    session = StubSession()
    session.set_token_anchor(TokenAnchor(messages=(sent,), input_tokens=5000, output_tokens=20))
    ctx = make_site_context(
        Site.LLM_BEFORE, _payload([sent, reply]), trace=make_trace_context(session=session)
    )

    s03_llm_before.apply(ctx)

    assert ctx.state.context_tokens == 5000 + 20 + ctx.ports.tokens.count_messages([reply])


def test_a_rewritten_prefix_falls_back_to_the_estimate() -> None:
    """上一次发出去的东西被改写过（压缩换了正文）：前缀对不上，锚点作废。"""
    sent = Message(role=MessageRole.USER, content=[TextPart(text="原文" * 100)])
    rewritten = Message(role=MessageRole.USER, content=[TextPart(text="[已省略]")])
    session = StubSession()
    session.set_token_anchor(TokenAnchor(messages=(sent,), input_tokens=9000, output_tokens=20))
    ctx = make_site_context(
        Site.LLM_BEFORE, _payload([rewritten]), trace=make_trace_context(session=session)
    )

    s03_llm_before.apply(ctx)

    assert ctx.state.context_tokens == ctx.ports.tokens.count_messages([rewritten])


def test_a_shrinking_context_falls_back_to_the_estimate() -> None:
    """上下文比锚点还短：更不可能"从锚点往后加"。"""
    sent = Message(role=MessageRole.USER, content=[TextPart(text="一")])
    kept = Message(role=MessageRole.USER, content=[TextPart(text="二")])
    session = StubSession()
    session.set_token_anchor(
        TokenAnchor(messages=(sent, kept), input_tokens=9000, output_tokens=20)
    )
    ctx = make_site_context(
        Site.LLM_BEFORE, _payload([sent]), trace=make_trace_context(session=session)
    )

    s03_llm_before.apply(ctx)

    assert ctx.state.context_tokens == ctx.ports.tokens.count_messages([sent])


def test_recomputing_overwrites_the_previous_value() -> None:
    """每次调用前重算，而不是把新的加上去。"""
    first = make_site_context(
        Site.LLM_BEFORE,
        _payload([Message(role=MessageRole.USER, content=[TextPart(text="很长" * 100)])]),
    )
    s03_llm_before.apply(first)

    second = make_site_context(
        Site.LLM_BEFORE,
        _payload([Message(role=MessageRole.USER, content=[TextPart(text="短")])]),
    )
    s03_llm_before.apply(second)

    assert second.state.context_tokens < first.state.context_tokens

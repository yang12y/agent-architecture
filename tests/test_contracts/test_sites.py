"""位点契约的规格测试。

这组测试是"十位点不可增删"的守门人：谁不小心加了第十一个位点、
或者改了某个位点的字符串值，这里立刻红。
"""

from __future__ import annotations

from fakes import make_trace_context
from yzy_agent.contracts import (
    CancelToken,
    LlmBeforePayload,
    Message,
    MessageRole,
    Permission,
    Site,
    SiteContext,
    TextPart,
    ToolBeforePayload,
    ToolDecision,
    TraceBeforePayload,
    TraceTrigger,
    TurnBeforePayload,
    TurnEndPayload,
)

# 十位点的字符串值，顺序就是它们的时间顺序。
EXPECTED_SITE_VALUES = [
    "trace:before",
    "turn:before",
    "llm:before",
    "llm:during",
    "llm:after",
    "tool:before",
    "tool:during",
    "tool:after",
    "turn:end",
    "trace:end",
]


def test_site_has_exactly_ten_members() -> None:
    """位点恰好十个，不多不少。"""
    assert len(list(Site)) == 10


def test_site_values_are_stable_and_ordered() -> None:
    """位点的字符串值与时间顺序固定。"""
    assert [site.value for site in Site] == EXPECTED_SITE_VALUES


def test_site_context_exposes_trace_fields() -> None:
    """SiteContext 的语法糖属性都指向同一个 trace。"""
    trace = make_trace_context()
    ctx = SiteContext(trace=trace, site=Site.TURN_BEFORE, payload=TurnBeforePayload(turn_index=1))

    assert ctx.state is trace.state
    assert ctx.session is trace.session
    assert ctx.config is trace.config
    assert ctx.ports is trace.ports
    assert ctx.signal is trace.signal


def test_site_context_keeps_payload_identity() -> None:
    """payload 是原地改的数据通道：ctx 拿到的就是传进来的那个对象。"""
    payload = ToolBeforePayload()
    ctx = SiteContext(trace=make_trace_context(), site=Site.TOOL_BEFORE, payload=payload)

    payload.decisions["call_1"] = ToolDecision.EXECUTE

    assert ctx.payload is payload
    assert payload.decisions["call_1"] is ToolDecision.EXECUTE


def test_llm_before_payload_defaults() -> None:
    """位点 3 的 payload 默认值：schema 为空、token 数为 0，等着别人来填。"""
    payload = LlmBeforePayload(model="fake-model", messages=[])

    assert payload.tools_schema == []
    assert payload.context_tokens == 0
    assert payload.compact_tokens == 0


def test_tool_before_payload_defaults() -> None:
    """位点 6 的 payload 默认值：没有判定、没有编排，内核按"全串行"兜底。"""
    payload = ToolBeforePayload()

    assert payload.calls == []
    assert payload.decisions == {}
    assert payload.plan is None


def test_trace_before_payload_carries_the_user_message() -> None:
    """位点 1 的 payload 必须带着触发它的那条用户消息。"""
    message = Message(role=MessageRole.USER, content=[TextPart(text="帮我读 a.py")])
    payload = TraceBeforePayload(
        session_id="sess_test",
        trigger=TraceTrigger.USER,
        parent_trace_id=None,
        user_message=message,
        model="fake-model",
        permission=Permission.EDITABLE,
    )

    assert payload.user_message.content[0].text == "帮我读 a.py"


def test_turn_end_payload_reports_no_pending_work_by_default() -> None:
    """位点 9 的 payload 默认认为"没活了"，继续与否由内核判定。"""
    payload = TurnEndPayload(turn_index=3)

    assert payload.tool_stats == {}
    assert payload.has_pending_work is False


def test_cancel_token_starts_clear_then_can_be_set() -> None:
    """取消信号默认没触发，set() 之后一直为真，重复 set 不报错。"""
    token = CancelToken()

    assert token.is_set() is False
    token.set()
    assert token.is_set() is True
    token.set()
    assert token.is_set() is True

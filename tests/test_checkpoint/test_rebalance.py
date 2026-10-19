"""语义配平的测试。"""

from __future__ import annotations

from agent_architecture.contracts import MessageRole, SideEffect
from agent_architecture.modules.checkpoint import dangling_calls, is_retryable, result_message
from fakes import make_tool_declaration
from test_checkpoint.traces import TRACE_ID, broken_log

# 三条代表性声明：只读、写但不接受幂等键、写且接受幂等键。
READ = make_tool_declaration("read", side_effect=SideEffect.NONE)
WRITE = make_tool_declaration("write", side_effect=SideEffect.NON_IDEMPOTENT)
WRITE_WITH_KEY = make_tool_declaration(
    "write", side_effect=SideEffect.NON_IDEMPOTENT, accepts_idempotency_key=True
)


def test_a_call_that_never_started_is_dangling() -> None:
    """assistant 请求过、但日志里既没有 tool_start 也没有结果 —— 要补。"""
    calls = dangling_calls(broken_log(), trace_id=TRACE_ID)

    assert [(c.tool_call_id, c.name, c.dispatched) for c in calls] == [
        ("call_1", "read", False),
        ("call_2", "write", False),
    ]


def test_an_answered_call_is_left_alone() -> None:
    """已经有结果消息的调用不该被补第二遍。"""
    entries = broken_log(did_dispatch=("call_1",), did_answer=("call_1",))

    calls = dangling_calls(entries, trace_id=TRACE_ID)

    assert [c.tool_call_id for c in calls] == ["call_2"]


def test_a_dispatched_call_carries_its_idempotency_key() -> None:
    """补出来的正文要能告诉模型"重试会命中同一次副作用"。"""
    entries = broken_log(did_dispatch=("call_1",), idempotency_keys={"call_1": "k1"})

    call = next(c for c in dangling_calls(entries, trace_id=TRACE_ID) if c.tool_call_id == "call_1")

    assert call.dispatched is True
    assert call.idempotency_key == "k1"


def test_calls_are_reported_in_the_order_the_model_asked() -> None:
    """顺序就是模型给出的顺序 —— 补出来的结果消息也按这个顺序排。"""
    entries = broken_log(calls=(("call_b", "read"), ("call_a", "read")))

    assert [c.tool_call_id for c in dangling_calls(entries, trace_id=TRACE_ID)] == [
        "call_b",
        "call_a",
    ]


def test_another_traces_calls_are_left_alone() -> None:
    """只配平出事的那一次 trace，别人家的不动。"""
    entries = broken_log()

    assert dangling_calls(entries, trace_id="trace_别的") == []


def test_reading_is_retryable() -> None:
    """只读工具重试没有代价。"""
    assert is_retryable(READ) is True


def test_writing_without_an_idempotency_key_is_not_retryable() -> None:
    """不可逆且不接受幂等键 —— 先确认外部状态。"""
    assert is_retryable(WRITE) is False


def test_writing_with_an_idempotency_key_is_retryable() -> None:
    """接受幂等键的写操作可以安全重试 —— 与运行时那条规则是同一条。"""
    assert is_retryable(WRITE_WITH_KEY) is True


def test_an_unknown_tool_is_not_retryable() -> None:
    """查不到声明就当不能重试 —— 不知道就不要赌。"""
    assert is_retryable(None) is False


def test_an_undispatched_call_is_described_as_safe_to_retry() -> None:
    """没派发过就没有副作用，正文里要说明白。"""
    call = dangling_calls(broken_log(), trace_id=TRACE_ID)[0]

    text = result_message(call, None).content[0].text  # type: ignore[union-attr]

    assert "开始执行前就被中断" in text
    assert "可以安全重试" in text


def test_a_dispatched_retryable_call_mentions_the_idempotency_key() -> None:
    """结果未知 + 可重试：把幂等键写进去，重试才命中同一次副作用。"""
    entries = broken_log(did_dispatch=("call_1",), idempotency_keys={"call_1": "k1"})
    call = dangling_calls(entries, trace_id=TRACE_ID)[0]

    text = result_message(call, READ).content[0].text  # type: ignore[union-attr]

    assert "结果未知" in text
    assert "k1" in text


def test_a_retryable_call_without_a_key_does_not_say_half_a_sentence() -> None:
    """键是空的时候不能写成"幂等键是 ，"；幂等调用本来就不靠键去重。"""
    entries = broken_log(did_dispatch=("call_1",))  # 没给 idempotency_keys，键就是空串
    call = dangling_calls(entries, trace_id=TRACE_ID)[0]

    text = result_message(call, READ).content[0].text  # type: ignore[union-attr]

    assert "幂等键是" not in text
    assert "可以安全重试" in text


def test_a_dispatched_unretryable_call_tells_the_model_to_verify() -> None:
    """结果未知 + 不可重试：不能自作主张再来一次。"""
    entries = broken_log(did_dispatch=("call_2",))
    call = next(c for c in dangling_calls(entries, trace_id=TRACE_ID) if c.name == "write")

    text = result_message(call, WRITE).content[0].text  # type: ignore[union-attr]

    assert "不能安全重试" in text
    assert "确认外部状态" in text


def test_the_result_message_pairs_by_tool_call_id() -> None:
    """补出来的是 TOOL 消息，metadata 里带上 tool_call_id —— 少一个下一轮就不合法。"""
    call = dangling_calls(broken_log(), trace_id=TRACE_ID)[0]

    message = result_message(call, None)

    assert message.role is MessageRole.TOOL
    assert message.metadata == {"tool_call_id": call.tool_call_id}

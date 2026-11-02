"""编解码的测试。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agent_frame.contracts import (
    Chart,
    ChartEntryPayload,
    ChartKind,
    ChartSeries,
    Message,
    MessageRole,
    Signal,
    SignalPeak,
    Span,
    SpanKind,
    TextPart,
    ThinkingPart,
    TodoEntryPayload,
    TodoItem,
    TodoStatus,
    ToolCallPart,
    TraceEndEntryPayload,
    TraceStatus,
)
from agent_frame.infra.storage import CodecError
from agent_frame.infra.storage.codec import (
    decode,
    dump_entry,
    dump_meta,
    encode,
    load_entry,
    load_meta,
)
from fakes import make_entry, make_session_meta, user_message


def test_enums_are_encoded_as_their_value() -> None:
    """枚举在 JSON 里就是它的字符串值，不另起结构。"""
    assert encode(MessageRole.USER) == "user"


def test_datetimes_are_encoded_as_iso_strings() -> None:
    """时刻编成 ISO 8601，带时区偏移，换机器读也还原得回来。"""
    moment = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)

    assert encode(moment) == moment.isoformat()


def test_a_todo_entry_round_trips() -> None:
    """待办条目的载荷（含状态枚举）编完再解，逐字段相等。"""
    entry = make_entry(
        1,
        TodoEntryPayload(
            todos=(
                TodoItem(content="跑测试", status=TodoStatus.IN_PROGRESS),
                TodoItem(content="写文档", status=TodoStatus.PENDING),
            )
        ),
    )

    assert load_entry(dump_entry(entry)) == entry


def test_a_chart_entry_round_trips() -> None:
    """图条目的载荷（含图种枚举、元组数值）编完再解，逐字段相等。"""
    entry = make_entry(
        1,
        ChartEntryPayload(
            chart=Chart(
                chart_id="c_call_1",
                tool_call_id="call_1",
                kind=ChartKind.LINE,
                title="出口压力",
                x=("08:00", "09:00"),
                series=(ChartSeries(name="压力", values=(0.62, 0.71)),),
                x_title=None,
                y_title="MPa",
            )
        ),
    )

    assert load_entry(dump_entry(entry)) == entry


def test_a_signal_chart_entry_round_trips() -> None:
    """信号图（base64 采样 + 谱峰 + 元组）编完再解，逐字段相等 —— 这一条会永久躺在 jsonl 里。"""
    entry = make_entry(
        1,
        ChartEntryPayload(
            chart=Chart(
                chart_id="c_call_1_2",
                tool_call_id="call_1",
                kind=ChartKind.FREQUENCY_DOMAIN,
                title="驱动端 · 频域",
                x=(),
                series=(),
                x_title="频率（Hz）",
                y_title="mm/s",
                signal=Signal(
                    encoding="base64-float32",
                    unit="mm/s",
                    point_name="驱动端",
                    sample_rate=2560.0,
                    sample_point=1024,
                    data="AAAAAAAAgD8AAABAAABAQA==",
                    axis_step=0.625,
                    rpm=1480.0,
                    peak_time="2025-06-01 09:20:00",
                    peaks=(SignalPeak(frequency=24.7, amplitude=1.83),),
                ),
            )
        ),
    )

    assert load_entry(dump_entry(entry)) == entry


def test_a_trace_end_entry_round_trips_with_its_timeline() -> None:
    """trace_end 的耗时明细（含枚举的 kind 与元组）编完再解，逐字段相等。"""
    entry = make_entry(
        9,
        TraceEndEntryPayload(
            status=TraceStatus.COMPLETED,
            reason=None,
            turns=2,
            context_tokens=1234,
            duration_ms=8421.5,
            spans=(
                Span(kind=SpanKind.SITE, name="llm:before", turn=1, start_ms=0.0, elapsed_ms=3.2),
                Span(
                    kind=SpanKind.LLM,
                    name="llm",
                    turn=1,
                    start_ms=5.0,
                    elapsed_ms=4200.0,
                    detail="第 2 次尝试",
                ),
            ),
        ),
    )

    assert load_entry(dump_entry(entry)) == entry


def test_meta_round_trips() -> None:
    """元信息编完再解，逐字段相等。"""
    meta = make_session_meta("sess_ab12")

    assert load_meta(dump_meta(meta)) == meta


def test_an_entry_round_trips() -> None:
    """条目编完再解，逐字段相等。"""
    entry = make_entry(1, user_message("你好"))

    assert load_entry(dump_entry(entry)) == entry


def test_an_encoded_entry_is_one_line() -> None:
    """正文里带换行也不能把 jsonl 的一行撑成两行。"""
    entry = make_entry(1, user_message("第一行\n第二行"))

    line = dump_entry(entry)

    assert "\n" not in line
    assert load_entry(line).payload.content[0].text == "第一行\n第二行"  # type: ignore[union-attr]


def test_tool_call_arguments_survive() -> None:
    """嵌套的 JSON 参数原样往返，不做任何改写。"""
    message = Message(
        role=MessageRole.ASSISTANT,
        content=[ToolCallPart(tool_call_id="call_1", name="read", arguments={"limit": 3})],
    )
    entry = make_entry(2, message)

    restored = load_entry(dump_entry(entry))

    assert restored.payload.content[0].arguments == {"limit": 3}  # type: ignore[union-attr]


def test_a_thinking_part_round_trips() -> None:
    """思考块要能原样落盘再读回来 —— 它只活在日志与界面里。"""
    message = Message(
        role=MessageRole.ASSISTANT,
        content=[ThinkingPart(text="先看看"), TextPart(text="好了")],
    )
    entry = make_entry(2, message)

    restored = load_entry(dump_entry(entry))

    parts = restored.payload.content  # type: ignore[union-attr]
    assert [type(part).__name__ for part in parts] == ["ThinkingPart", "TextPart"]
    assert parts[0].text == "先看看"  # type: ignore[union-attr]


def test_message_metadata_survives() -> None:
    """metadata 是自由字典，不能被丢掉。"""
    message = Message(role=MessageRole.TOOL, content=[], metadata={"tool_call_id": "call_1"})
    entry = make_entry(2, message)

    restored = load_entry(dump_entry(entry))

    assert restored.payload.metadata == {"tool_call_id": "call_1"}  # type: ignore[union-attr]


def test_an_unregistered_type_is_rejected() -> None:
    """白名单之外的类型名一律拒绝，不去构造任意类。"""
    with pytest.raises(CodecError):
        decode({"__type__": "不认识的类型"})


def test_a_broken_line_is_rejected() -> None:
    """半截 JSON 报 CodecError，而不是把 JSONDecodeError 漏给上层。"""
    with pytest.raises(CodecError):
        load_entry("{不是 json")


def test_an_illegal_enum_value_is_rejected() -> None:
    """枚举取值对不上时报错，不悄悄退回默认值。"""
    payload = encode(make_session_meta("sess_ab12"))
    payload["permission"] = "god_mode"

    with pytest.raises(CodecError):
        decode(payload)


def test_the_wrong_type_at_the_top_is_rejected() -> None:
    """一行 jsonl 解出来不是 SessionEntry 就算坏。"""
    with pytest.raises(CodecError):
        load_entry('{"__type__":"TextPart","text":"hi"}')


def test_a_missing_required_field_is_rejected() -> None:
    """user_id 是必填的：没有这个键的 meta 读不回来。

    现在还没有既有的会话日志，所以这是刻意选的行为 —— 会话必须有主。
    """
    payload = encode(make_session_meta("sess_ab12"))
    del payload["user_id"]

    with pytest.raises(CodecError):
        decode(payload)


def test_a_missing_optional_field_falls_back_to_the_default() -> None:
    """缺字段时交给 dataclass 自己的默认值，老日志才有得升。"""
    payload = encode(user_message("你好"))
    del payload["metadata"]

    assert decode(payload).metadata == {}  # type: ignore[union-attr]

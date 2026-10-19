"""编解码的测试。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from fakes import make_entry, make_session_meta, user_message
from yzy_agent.contracts import Message, MessageRole, TextPart, ThinkingPart, ToolCallPart
from yzy_agent.infra.storage import CodecError
from yzy_agent.infra.storage.codec import (
    decode,
    dump_entry,
    dump_meta,
    encode,
    load_entry,
    load_meta,
)


def test_enums_are_encoded_as_their_value() -> None:
    """枚举在 JSON 里就是它的字符串值，不另起结构。"""
    assert encode(MessageRole.USER) == "user"


def test_datetimes_are_encoded_as_iso_strings() -> None:
    """时刻编成 ISO 8601，带时区偏移，换机器读也还原得回来。"""
    moment = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)

    assert encode(moment) == moment.isoformat()


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

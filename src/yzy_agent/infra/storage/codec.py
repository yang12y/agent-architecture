"""条目与元信息的编解码：dataclass <-> JSON 兼容的 Python 值。

磁盘上只有两样东西要编解码：SessionMeta 与 SessionEntry（含它的 payload）。
本模块只认 PERSISTED_TYPES 里登记的类型 —— 它同时是一张白名单：手改过的日志里
冒出别的类型名一律拒绝，不会去构造任意类。

编码规则（自描述，解码不靠字段类型注解）：

    - dataclass 编成 {"__type__": 类名, ...各字段}，靠这个标记还原
    - StrEnum 编成它的 value
    - datetime 编成 ISO 8601 字符串（带时区偏移）
    - dict / list 原样递归

字段类型注解只在还原枚举时用得上 —— 字符串本身看不出它该是个枚举。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, get_args, get_type_hints

from yzy_agent.contracts import (
    BudgetSnapshot,
    CompactionEntryPayload,
    CompactionSnapshot,
    ImagePart,
    Message,
    SessionEntry,
    SessionMeta,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolStartEntryPayload,
    TraceEndEntryPayload,
    TraceStartEntryPayload,
)

# dataclass 在 JSON 里的类型标记。
#
# 举例：{"__type__": "Message", "role": "user", "content": [...]}
TYPE_KEY = "__type__"

# 允许编解码的全部类型。不在其中的类型名一律拒绝。
#
# 举例：PERSISTED_TYPES 含 SessionMeta、SessionEntry、Message
PERSISTED_TYPES: tuple[type, ...] = (
    SessionMeta,
    SessionEntry,
    Message,
    TextPart,
    ThinkingPart,
    ImagePart,
    ToolCallPart,
    BudgetSnapshot,
    CompactionSnapshot,
    TraceStartEntryPayload,
    TraceEndEntryPayload,
    ToolStartEntryPayload,
    CompactionEntryPayload,
)

_BY_NAME = {cls.__name__: cls for cls in PERSISTED_TYPES}


class CodecError(ValueError):
    """日志内容解不开：文件被改坏、格式对不上、类型没登记。"""


def encode(value: object) -> Any:
    """把 dataclass / 枚举 / datetime 编成 JSON 能序列化的结构。

    举例：encode(MessageRole.USER) -> "user"
    出错：遇到没登记的类型时抛 CodecError。
    """
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        payload: dict[str, Any] = {TYPE_KEY: value.__class__.__name__}
        for item in fields(value):
            payload[item.name] = encode(getattr(value, item.name))
        return payload
    if isinstance(value, Mapping):
        return {str(key): encode(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode(item) for item in value]
    raise CodecError(f"不能编解码的类型：{type(value).__name__}")


def decode(value: Any) -> Any:
    """把 encode 出来的结构还原成对象。

    举例：decode({"__type__": "TextPart", "text": "你好"}).text -> "你好"
    出错：类型名没登记、字段对不上、枚举取值非法时抛 CodecError。
    """
    if isinstance(value, Mapping):
        if TYPE_KEY in value:
            return _decode_dataclass(value)
        return {key: decode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [decode(item) for item in value]
    return value


def dump_entry(entry: SessionEntry) -> str:
    """把一条条目编成一行 JSON，**不含换行**，直接拼进 jsonl 就行。

    举例：dump_entry(SessionEntry(seq=1, ...)) -> '{"__type__":"SessionEntry",...}'
    """
    return json.dumps(encode(entry), ensure_ascii=False, separators=(",", ":"))


def load_entry(line: str) -> SessionEntry:
    """把 jsonl 的一行还原成条目。

    举例：load_entry('{"__type__":"SessionEntry",...}').seq -> 1
    出错：这一行不是合法 JSON 或类型对不上时抛 CodecError。
    """
    entry = decode(_loads(line, "条目"))
    if not isinstance(entry, SessionEntry):
        raise CodecError(f"这一行不是 SessionEntry：{line[:80]!r}")
    return entry


def dump_meta(meta: SessionMeta) -> str:
    """把元信息编成便于人读的 JSON 文本。

    举例：dump_meta(make_session_meta()) -> '{\n  "__type__": "SessionMeta",\n  ...'
    """
    return json.dumps(encode(meta), ensure_ascii=False, indent=2)


def load_meta(text: str) -> SessionMeta:
    """把元信息文本还原成 SessionMeta。

    举例：load_meta(dump_meta(meta)).session_id -> "sess_ab12"
    出错：内容不是合法 JSON 或类型对不上时抛 CodecError。
    """
    meta = decode(_loads(text, "元信息"))
    if not isinstance(meta, SessionMeta):
        raise CodecError("meta.json 的内容不是 SessionMeta")
    return meta


def _loads(text: str, what: str) -> Any:
    """json.loads 的包装：把 JSONDecodeError 统一成 CodecError。

    举例：_loads("{}", "元信息") -> {}
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise CodecError(f"{what}不是合法 JSON：{exc}") from exc


def _decode_dataclass(obj: Mapping[str, Any]) -> object:
    """按 __type__ 找到类，逐字段还原。

    举例：_decode_dataclass({"__type__": "TextPart", "text": "你好"})
    """
    name = obj[TYPE_KEY]
    cls = _BY_NAME.get(name)
    if cls is None:
        raise CodecError(f"日志里出现了未登记的类型：{name!r}")

    hints = get_type_hints(cls)
    kwargs: dict[str, Any] = {}
    for item in fields(cls):
        # 缺字段就交给 dataclass 自己的默认值；必填字段缺失会在构造函数里报出来。
        if item.name in obj:
            kwargs[item.name] = _revive(hints.get(item.name), obj[item.name])
    try:
        return cls(**kwargs)
    except TypeError as exc:
        raise CodecError(f"{name} 的字段对不上：{exc}") from exc


def _revive(hint: object, raw: Any) -> Any:
    """按字段注解把原始值还原：枚举转回成员，其它递归。

    举例：_revive(MessageRole, "user") -> MessageRole.USER
          _revive(Timestamp, "2025-01-01T12:00:00+00:00") -> datetime(...)
    """
    if raw is None:
        return None
    enum_type = _enum_in(hint)
    if enum_type is not None:
        try:
            return enum_type(raw)
        except ValueError as exc:
            raise CodecError(f"{enum_type.__name__} 没有取值 {raw!r}") from exc
    if _has_datetime(hint):
        try:
            return datetime.fromisoformat(raw)
        except (TypeError, ValueError) as exc:
            raise CodecError(f"时刻格式不对：{raw!r}") from exc
    if isinstance(raw, Mapping):
        return decode(raw)
    if isinstance(raw, list):
        return [decode(item) for item in raw]
    return raw


def _has_datetime(hint: object) -> bool:
    """字段注解里有没有 datetime；Optional / Union 会展开。

    举例：_has_datetime(Timestamp) -> True；_has_datetime(str) -> False
    """
    for candidate in (hint, *get_args(hint)):
        if candidate is datetime:
            return True
    return False


def _enum_in(hint: object) -> type[StrEnum] | None:
    """在字段注解里找枚举类型；Optional / Union 会展开。

    举例：_enum_in(MessageRole | None) -> MessageRole；_enum_in(str) -> None
    """
    for candidate in (hint, *get_args(hint)):
        if isinstance(candidate, type) and issubclass(candidate, StrEnum):
            return candidate
    return None

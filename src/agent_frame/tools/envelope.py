"""业务报文容器 → (明细行, 总数)。

下游的容器只认三种形状：**data 直接是列表 / data 里再包一层 list|rows / 顶层 list|rows**。
总数在顶层 count 或 data.count；两边都没有、又没有列表，才算业务失败。

这不是"猜容器"：认不出的形状一律报错，宁可让模型看见"接口返回了什么"，
也不要把一段乱七八糟的报文当成空结果。
"""

from __future__ import annotations

from agent_frame.contracts import DownstreamError, JsonValue

# 明细列表可能挂的键，按优先级。
#
# 举例：{"data": [...]} -> 命中 "data"
LIST_KEYS = ("data", "list", "rows")

# data 是个对象时，再往下一层找列表的键。
#
# 举例：{"data": {"list": [...]}} -> 命中 "list"
NESTED_KEYS = ("list", "rows", "data")


def rows_of(payload: JsonValue) -> list[dict[str, JsonValue]]:
    """取出明细列表；取不到就抛 DownstreamError。

    举例：rows_of({"data": [{"a": 1}]}) -> [{"a": 1}]
    出错：报文里既没有列表也没有 count 时抛 DownstreamError。
    """
    if isinstance(payload, list):
        return _dicts(payload)
    if not isinstance(payload, dict):
        raise DownstreamError(f"接口返回的不是对象或数组：{type(payload).__name__}")

    for key in LIST_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            return _dicts(value)
        if isinstance(value, dict):
            for nested_key in NESTED_KEYS:
                nested = value.get(nested_key)
                if isinstance(nested, list):
                    return _dicts(nested)

    if count_of(payload) is not None:
        # count-only 报文（只回总数、不回明细）是合法的空列表。
        return []
    raise DownstreamError(_message_of(payload))


def count_of(payload: JsonValue) -> int | None:
    """取出总数：顶层 count → data.count → 都没有返回 None。

    举例：count_of({"count": 238}) -> 238
    """
    if not isinstance(payload, dict):
        return None
    value = _as_int(payload.get("count"))
    if value is not None:
        return value
    data = payload.get("data")
    return _as_int(data.get("count")) if isinstance(data, dict) else None


def _dicts(rows: list[JsonValue]) -> list[dict[str, JsonValue]]:
    """只留对象行；标量行对模型没有意义。

    举例：_dicts([{"a": 1}, None, 3]) -> [{"a": 1}]
    """
    return [row for row in rows if isinstance(row, dict)]


def _as_int(raw: JsonValue) -> int | None:
    """把 count 收成整数：数字或数字字符串都认。

    举例：_as_int("238") -> 238
    """
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw)
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw.strip())
    return None


def _message_of(payload: dict[str, JsonValue]) -> str:
    """从失败报文里取一句给人看的话。

    举例：_message_of({"code": 500, "message": "租户不存在"}) -> "租户不存在"
    """
    for key in ("message", "msg"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return f"业务错误，code={payload.get('code')}"

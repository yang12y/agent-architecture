"""参数 schema 的小 DSL。

只生成检查链认的三样（required / type / enum）加给模型看的 description，
不产生 anyOf、$ref、default: null —— 那三种正是"pydantic schema 转出来还得再洗一遍"的来源。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from agent_frame.contracts import JsonValue

# 字段级必填标记。`obj` 把它收进父级的 required 里，schema 里不再出现。
#
# 举例：text("设备 guid", required=True) -> {"type": "string", "description": …, "x-required": True}
_REQUIRED = "x-required"


def text(description: str, *, required: bool = False) -> dict[str, JsonValue]:
    """一个字符串参数。

    举例：text("设备名称或编码，模糊匹配")
    """
    return _field({"type": "string", "description": description}, required)


def integer(
    description: str, *, required: bool = False, minimum: int | None = None
) -> dict[str, JsonValue]:
    """一个整数参数；minimum 只是给模型看的约束，检查链不校验它。

    举例：integer("条数上限", minimum=1)
    """
    spec: dict[str, JsonValue] = {"type": "integer", "description": description}
    if minimum is not None:
        spec["minimum"] = minimum
    return _field(spec, required)


def flag(description: str, *, required: bool = False) -> dict[str, JsonValue]:
    """一个布尔参数。检查链会把 "true"/1 这类冒牌布尔挡在外面。

    举例：flag("true=只查本级机构")
    """
    return _field({"type": "boolean", "description": description}, required)


def choice(
    values: Sequence[str], description: str, *, required: bool = False
) -> dict[str, JsonValue]:
    """一个枚举参数。**检查链会真的拦非法值**，所以能枚举就别用自由文本。

    举例：choice(["0", "1", "2"], "处理状态：0 处理中 / 1 已完成 / 2 作废")
    """
    spec: dict[str, JsonValue] = {
        "type": "string",
        "enum": list(values),
        "description": description,
    }
    return _field(spec, required)


def list_of(item: dict[str, JsonValue], description: str, *, required: bool = False) -> dict:
    """一个数组参数，元素形状由 item 给。

    举例：list_of({"type": "string"}, "分类 guid 列表")
    """
    spec: dict[str, JsonValue] = {"type": "array", "items": item, "description": description}
    return _field(spec, required)


def obj(properties: Mapping[str, dict[str, JsonValue]]) -> dict[str, JsonValue]:
    """把一组字段收成一个对象 schema，required 由各字段的标记汇总。

    举例：obj({"name": text("名称", required=True)}) -> {"type": "object", "properties": {...}, "required": ["name"]}
    """
    cleaned: dict[str, JsonValue] = {}
    required: list[str] = []
    for name, spec in properties.items():
        cleaned[name] = {key: value for key, value in spec.items() if key != _REQUIRED}
        if spec.get(_REQUIRED):
            required.append(name)

    schema: dict[str, JsonValue] = {"type": "object", "properties": cleaned}
    if required:
        schema["required"] = required
    return schema


def _field(spec: dict[str, JsonValue], required: bool) -> dict[str, JsonValue]:
    """给字段打个必填标记；父级 obj 会把它摘掉。

    举例：_field({"type": "string"}, True) -> {"type": "string", "x-required": True}
    """
    return {**spec, _REQUIRED: True} if required else spec

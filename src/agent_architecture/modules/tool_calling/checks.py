"""位点 6 检查链的前三步：存在性、补默认、参数校验。

三步都是**纯函数**，不碰端口、不碰时钟 —— 每一步的失败分支才能单独穷举。

校验只认 `required` / `type` / `enum` 三样：内置工具的 schema 就用到这些。
完整 JSON Schema 见 ARCHITECTURE.md §9。
"""

from __future__ import annotations

from agent_architecture.contracts import JsonValue, ToolDeclaration

# JSON Schema 的类型名 -> Python 类型。只列我们用得到的几种。
#
# 举例：_TYPES["string"] -> str
_TYPES: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


def with_defaults(declaration: ToolDeclaration, arguments: JsonValue) -> dict[str, JsonValue]:
    """按 schema 把缺省参数补上。补完的参数才是"最终参数"，审批与幂等键都用它。

    举例：with_defaults(decl, {"path": "a.py"}) -> {"path": "a.py", "limit": 2000}
    """
    given = dict(arguments) if isinstance(arguments, dict) else {}
    schema = declaration.parameters_schema
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return given
    for name, spec in properties.items():
        if name in given or not isinstance(spec, dict) or "default" not in spec:
            continue
        given[name] = spec["default"]
    return given


def validate(declaration: ToolDeclaration, arguments: JsonValue) -> str | None:
    """校验最终参数；通过返回 None，不通过返回一句给人看的说明。

    只查必填、类型、枚举三样。**不认识的键一律放过** —— 那是工具自己的事，
    框架收紧它只会让以后加参数就得改这里。

    举例：validate(decl, {}) -> "缺少必填参数：path"
    """
    if not isinstance(arguments, dict):
        return f"参数必须是一个对象，收到 {type(arguments).__name__}"

    schema = declaration.parameters_schema
    properties = schema.get("properties")
    properties = properties if isinstance(properties, dict) else {}

    for name in schema.get("required", ()):
        if name not in arguments:
            return f"缺少必填参数：{name}"

    for name, spec in properties.items():
        if name not in arguments or not isinstance(spec, dict):
            continue
        problem = _check_one(name, arguments[name], spec)
        if problem is not None:
            return problem
    return None


def _check_one(name: str, value: JsonValue, spec: dict[str, JsonValue]) -> str | None:
    """查一个参数的类型与枚举。

    举例：_check_one("limit", "很多", {"type": "integer"}) -> "参数 limit 应为 integer，收到 str"
    """
    expected = spec.get("type")
    if isinstance(expected, str) and expected in _TYPES:
        # bool 是 int 的子类，`true` 不该被当成 1 放过去。
        python_type = _TYPES[expected]
        if isinstance(value, bool) and expected != "boolean":
            return f"参数 {name} 应为 {expected}，收到 boolean"
        if not isinstance(value, python_type):
            return f"参数 {name} 应为 {expected}，收到 {type(value).__name__}"

    allowed = spec.get("enum")
    if isinstance(allowed, list) and value not in allowed:
        return f"参数 {name} 只能是 {allowed} 之一，收到 {value!r}"
    return None

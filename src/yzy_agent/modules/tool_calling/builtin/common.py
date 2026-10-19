"""内置工具共用的小件：参数取值与路径显示。

取值不合法时抛 `ToolArgumentError`，抛的是**给人看的话** —— 执行器会把它原样收成
一条工具结果交给模型，所以措辞要能指导它改。
"""

from __future__ import annotations

from pathlib import Path

from yzy_agent.contracts import JsonValue, ToolArgumentError


def require_string(arguments: JsonValue, key: str, *, allow_empty: bool = False) -> str:
    """取一个必须是字符串的参数。

    allow_empty 是给 write 的 content 留的 —— 空文件是合法内容，不是缺参数。

    举例：require_string({"path": "a.py"}, "path") -> "a.py"
    出错：缺失、类型不对，或不允许空却是空串时，抛 ToolArgumentError。
    """
    value = _get(arguments, key)
    wanted = "字符串" if allow_empty else "非空字符串"
    if not isinstance(value, str) or (not allow_empty and not value):
        raise ToolArgumentError(f"参数 {key} 必须是{wanted}，收到 {value!r}")
    return value


def optional_string(arguments: JsonValue, key: str) -> str | None:
    """取一个可选的字符串参数；没给、或给了空串，都返回 None。

    举例：optional_string({"include": "*.py"}, "include") -> "*.py"
    """
    value = _get(arguments, key)
    return value if isinstance(value, str) and value else None


def positive_int(arguments: JsonValue, key: str, fallback: int) -> int:
    """取一个正整数参数；没给就用缺省值。

    举例：positive_int({"max_lines": 10}, "max_lines", 2000) -> 10
    出错：不是正整数时抛 ToolArgumentError。
    """
    value = _get(arguments, key)
    if value is None:
        return fallback
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ToolArgumentError(f"参数 {key} 必须是正整数，收到 {value!r}")
    return value


def display_path(path: Path, workspace: str) -> str:
    """把路径显示成相对工作目录的形式。

    **要能被原样传回来**：模型看到的就是它下一次传给 read / edit 的那个值，
    所以只有越界的那些（用户审批过的）才退回绝对路径。

    举例：display_path(Path("D:/proj/a.py"), "D:/proj") -> "a.py"
    """
    try:
        return path.relative_to(Path(workspace).resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _get(arguments: JsonValue, key: str) -> JsonValue:
    """从参数对象里取一个键；参数压根不是对象时当作没给。

    举例：_get({"path": "a.py"}, "path") -> "a.py"
    """
    return arguments.get(key) if isinstance(arguments, dict) else None

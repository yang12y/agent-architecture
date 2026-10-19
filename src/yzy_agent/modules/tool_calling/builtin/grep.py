"""内置的 grep：按正则表达式搜文件内容。"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

from yzy_agent.contracts import (
    JsonValue,
    Permission,
    SideEffect,
    ToolArgumentError,
    ToolDeclaration,
    resolve_in_workspace,
)

from ..registry import ToolContext
from .common import display_path, optional_string, require_string

# 一次最多报几处匹配。
#
# 举例：MAX_MATCHES == 200
MAX_MATCHES = 200

# 单个文件超过这么大就不读：真去读依赖目录里那些几 MB 的文件，一次调用能跑到超时。
#
# 举例：MAX_FILE_BYTES == 1_000_000
MAX_FILE_BYTES = 1_000_000

# 声明。
#
# 举例：GREP.name == "grep"
GREP = ToolDeclaration(
    name="grep",
    description=(
        "按正则表达式搜索文件内容，返回「路径:行号:内容」。path 可以是目录（递归）或单个文件；"
        "include 限定文件名模式（如 *.py），不限定会翻遍整个目录树。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "正则表达式"},
            "path": {
                "type": "string",
                "default": ".",
                "description": "搜哪个目录或文件，默认工作目录",
            },
            "include": {"type": "string", "description": "只搜名字匹配它的文件，如 *.py"},
        },
        "required": ["pattern"],
    },
    output_contract="每行一处匹配，最多 200 行。",
    side_effect=SideEffect.NONE,
    accepts_idempotency_key=False,
    required_permission=Permission.READ_ONLY,
    scope=None,
)


async def grep(ctx: ToolContext, arguments: JsonValue) -> JsonValue:
    """在文件内容里搜正则，返回命中的行。

    举例：await grep(ctx, {"pattern": "def read", "include": "*.py"})
          -> "src/a.py:12:def read(path):"
    出错：正则不合法、路径不存在时抛 ToolArgumentError。
    """
    pattern = require_string(arguments, "pattern")
    raw = optional_string(arguments, "path") or "."
    include = optional_string(arguments, "include")
    try:
        expression = re.compile(pattern)
    except re.error as exc:
        raise ToolArgumentError(f"正则不合法：{pattern}（{exc}）") from None

    target = resolve_in_workspace(raw, ctx.workspace)
    if not target.exists():
        raise ToolArgumentError(f"路径不存在：{raw}")

    hits: list[str] = []
    total = 0
    for path in _candidates(target, include):
        for number, line in _lines(path):
            if expression.search(line) is None:
                continue
            total += 1
            if total > MAX_MATCHES:
                break
            hits.append(f"{display_path(path, ctx.workspace)}:{number}:{line.rstrip()}")
        if total > MAX_MATCHES:
            break

    if not hits:
        return f"（没有匹配 {pattern} 的行）"
    if total > MAX_MATCHES:
        hits.append(f"…（匹配超过 {MAX_MATCHES} 处，只列了前 {MAX_MATCHES} 处）")
    return "\n".join(hits)


def _candidates(target: Path, include: str | None) -> list[Path]:
    """这次要读哪些文件。

    目录按 include（缺省 *）递归收集，**并按路径排序**：文件系统的遍历顺序不保证稳定，
    排一遍才能让同样的输入给出同样的输出。

    举例：_candidates(Path("D:/proj"), "*.py") -> [Path("D:/proj/a.py")]
    """
    if target.is_file():
        return [target]
    try:
        return sorted(path for path in target.rglob(include or "*") if path.is_file())
    except (OSError, ValueError):
        return []


def _lines(path: Path) -> Iterator[tuple[int, str]]:
    """读一个文件的行号与内容；读不了、或太大，都当作没有内容。

    举例：list(_lines(Path("a.py"))) -> [(1, "import os")]
    """
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    yield from enumerate(text.splitlines(), start=1)

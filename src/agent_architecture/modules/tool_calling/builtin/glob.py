"""内置的 glob：按文件名模式递归找文件。"""

from __future__ import annotations

from agent_architecture.contracts import (
    JsonValue,
    Permission,
    SideEffect,
    ToolArgumentError,
    ToolDeclaration,
    resolve_in_workspace,
)

from ..registry import ToolContext
from .common import display_path, optional_string, require_string

# 一次最多列几个。超出的只报总数 —— 满屏的匹配对模型没用，还白占上下文。
#
# 举例：MAX_MATCHES == 200
MAX_MATCHES = 200

# 声明。
#
# 举例：GLOB.name == "glob"
GLOB = ToolDeclaration(
    name="glob",
    description=(
        "按文件名模式在工作目录里递归查找文件，如 *.py、test_*.py。"
        "要按文件内容搜索用 grep，要看一个目录里有什么用 list_dir。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "文件名模式，如 *.py"},
            "path": {
                "type": "string",
                "default": ".",
                "description": "从哪个目录开始找，默认工作目录",
            },
        },
        "required": ["pattern"],
    },
    output_contract="每行一个相对工作目录的路径，最多 200 行。",
    side_effect=SideEffect.NONE,
    accepts_idempotency_key=False,
    required_permission=Permission.READ_ONLY,
    scope=None,
)


async def glob(ctx: ToolContext, arguments: JsonValue) -> JsonValue:
    """递归找文件，返回相对工作目录的路径。

    举例：await glob(ctx, {"pattern": "*.py"}) -> "src/a.py\nsrc/b.py"
    出错：起点目录不存在、模式用不了时抛 ToolArgumentError。
    """
    pattern = require_string(arguments, "pattern")
    raw = optional_string(arguments, "path") or "."
    root = resolve_in_workspace(raw, ctx.workspace)
    if not root.is_dir():
        raise ToolArgumentError(f"目录不存在：{raw}")

    try:
        found = sorted(path for path in root.rglob(pattern) if path.is_file())
    except (OSError, ValueError) as exc:
        raise ToolArgumentError(f"模式用不了：{pattern}（{exc}）") from None

    if not found:
        return f"（没有找到匹配 {pattern} 的文件）"
    listed = [display_path(path, ctx.workspace) for path in found[:MAX_MATCHES]]
    if len(found) > MAX_MATCHES:
        listed.append(f"…（共 {len(found)} 个，只列了前 {MAX_MATCHES} 个）")
    return "\n".join(listed)

"""内置的 list_dir：列出一个目录里的条目。"""

from __future__ import annotations

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
from .common import optional_string

# 声明。path 有缺省值，所以它不必填 —— 但**名字必须叫 path**：沙箱按参数名判越界。
#
# 举例：LIST_DIR.name == "list_dir"
LIST_DIR = ToolDeclaration(
    name="list_dir",
    description=(
        "列出一个目录里的条目，一行一个，目录名后面带 /。默认列工作目录本身。"
        "想知道文件里写了什么用 read，想按名字找文件用 glob。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "default": ".", "description": "相对工作目录的路径，默认 ."}
        },
        "required": [],
    },
    output_contract="每行一个条目，目录带尾斜杠；条目多时由位点 8 截断。",
    side_effect=SideEffect.NONE,
    accepts_idempotency_key=False,
    required_permission=Permission.READ_ONLY,
    scope=None,
)


async def list_dir(ctx: ToolContext, arguments: JsonValue) -> JsonValue:
    """列目录，目录在前、文件在后，同组按名字排。

    举例：await list_dir(ctx, {"path": "src"}) -> "pkg/\nmain.py"
    出错：目录不存在、或给的路径其实是个文件时抛 ToolArgumentError。
    """
    raw = optional_string(arguments, "path") or "."
    target = resolve_in_workspace(raw, ctx.workspace)

    try:
        entries = sorted(target.iterdir(), key=_order)
    except FileNotFoundError:
        raise ToolArgumentError(f"目录不存在：{raw}") from None
    except NotADirectoryError:
        raise ToolArgumentError(f"{raw} 是文件，不是目录") from None
    except OSError as exc:
        raise ToolArgumentError(f"列不了 {raw}：{exc}") from None

    if not entries:
        return f"（{raw} 是空目录）"
    return "\n".join(f"{entry.name}/" if entry.is_dir() else entry.name for entry in entries)


def _order(entry: Path) -> tuple[bool, str]:
    """排序键：目录排前面，同组按小写名字排。

    举例：_order(Path("b.py")) -> (True, "b.py")
    """
    try:
        is_dir = not entry.is_dir()
    except OSError:
        is_dir = True
    return (is_dir, entry.name.lower())

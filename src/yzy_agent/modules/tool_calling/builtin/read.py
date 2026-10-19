"""内置的 read：读工作目录里的一个文本文件。"""

from __future__ import annotations

from yzy_agent.contracts import (
    JsonValue,
    Permission,
    SideEffect,
    ToolArgumentError,
    ToolDeclaration,
    resolve_in_workspace,
)

from ..registry import ToolContext
from .common import positive_int, require_string

# 声明。模型按 description 决定用不用它，沙箱按 required_permission 与参数名判越界。
#
# 举例：READ.name == "read"
READ = ToolDeclaration(
    name="read",
    description="读取工作目录里的一个文本文件，返回带行号的内容。需要查看某个文件的现有内容时使用。",
    parameters_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "相对工作目录的路径"},
            "start_line": {"type": "integer", "default": 1, "description": "从第几行开始，从 1 数"},
            "max_lines": {"type": "integer", "default": 2000, "description": "最多读多少行"},
        },
        "required": ["path"],
    },
    output_contract="带行号的文本；超过上限的部分由位点 8 截断。",
    side_effect=SideEffect.NONE,
    accepts_idempotency_key=False,
    required_permission=Permission.READ_ONLY,
    scope=None,
)


async def read(ctx: ToolContext, arguments: JsonValue) -> JsonValue:
    """读文件，返回带行号的文本。

    **读不了就抛 `ToolArgumentError`，抛的是给人看的话**（"文件不存在：a.py"）——
    执行器会把它收成一条工具结果交给模型，所以措辞要能指导它改。

    举例：await read(ctx, {"path": "a.py"}) -> "1\timport os"
    """
    raw = require_string(arguments, "path")
    target = resolve_in_workspace(raw, ctx.workspace)
    start = positive_int(arguments, "start_line", 1)
    limit = positive_int(arguments, "max_lines", 2000)

    # 先自己判一次目录：Windows 上打开目录读会抛 PermissionError，
    # 落到下面那条 OSError 分支就成了一句看不出所以然的"读不了"。
    if target.is_dir():
        raise ToolArgumentError(f"{raw} 是一个目录，不是文件")

    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        raise ToolArgumentError(f"文件不存在：{raw}") from None
    except OSError as exc:
        raise ToolArgumentError(f"读不了 {raw}：{exc}") from None

    lines = text.splitlines()
    window = lines[start - 1 : start - 1 + limit]
    if not window:
        return f"（{raw} 在这个范围内没有内容，全文共 {len(lines)} 行）"
    return "\n".join(f"{number}\t{line}" for number, line in enumerate(window, start=start))

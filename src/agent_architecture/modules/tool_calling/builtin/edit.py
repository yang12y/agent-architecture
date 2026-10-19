"""内置的 edit：把文件里唯一的一段文本替换掉。"""

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
from .common import require_string

# 声明。**要求只出现一次**：出现多处时改哪一处是猜的，猜错比不改更糟。
#
# 举例：EDIT.name == "edit"
EDIT = ToolDeclaration(
    name="edit",
    description=(
        "把文件里的一段文本替换成另一段。old_string 必须在文件里只出现一次，"
        "零次或多次都不改，只回报出现次数。整份重写请用 write。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "相对工作目录的路径"},
            "old_string": {"type": "string", "description": "要被替换掉的原文，必须唯一"},
            "new_string": {"type": "string", "description": "替换成什么，可以是空串（等于删掉）"},
        },
        "required": ["path", "old_string", "new_string"],
    },
    output_contract="一句话确认（替换了几处）；不回显改后的文件。",
    side_effect=SideEffect.IDEMPOTENT,
    accepts_idempotency_key=False,
    required_permission=Permission.EDITABLE,
    scope=None,
)


async def edit(ctx: ToolContext, arguments: JsonValue) -> JsonValue:
    """替换唯一命中的那一段，返回一句确认。

    举例：await edit(ctx, {"path": "a.py", "old_string": "x = 1", "new_string": "x = 2"})
          -> "已改 a.py：替换 1 处"
    出错：文件不存在、读不了写不了、原文不唯一时抛 ToolArgumentError。
    """
    raw = require_string(arguments, "path")
    old = require_string(arguments, "old_string")
    new = require_string(arguments, "new_string", allow_empty=True)
    if old == new:
        raise ToolArgumentError("old_string 与 new_string 一样，没有可改的地方")
    target = resolve_in_workspace(raw, ctx.workspace)
    if target.is_dir():
        raise ToolArgumentError(f"{raw} 是一个目录，不是文件")

    try:
        # 不做换行归一：文件原本是 \r\n 就照原样写回，改一处不该让整份 diff 变样。
        text = target.read_text(encoding="utf-8", newline="")
    except FileNotFoundError:
        raise ToolArgumentError(f"文件不存在：{raw}") from None
    except IsADirectoryError:
        raise ToolArgumentError(f"{raw} 是一个目录，不是文件") from None
    except OSError as exc:
        raise ToolArgumentError(f"读不了 {raw}：{exc}") from None

    count = text.count(old)
    if count == 0:
        raise ToolArgumentError(f"{raw} 里没有找到 old_string，一个字都没改")
    if count > 1:
        raise ToolArgumentError(
            f"old_string 在 {raw} 里出现了 {count} 次，无法确定改哪一处；多带几行上下文再试"
        )

    try:
        target.write_text(text.replace(old, new, 1), encoding="utf-8", newline="")
    except OSError as exc:
        raise ToolArgumentError(f"写不了 {raw}：{exc}") from None
    return f"已改 {raw}：替换 1 处"

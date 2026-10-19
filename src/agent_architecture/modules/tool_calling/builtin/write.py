"""内置的 write：把一个文本文件整份写成给定内容。"""

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

# 声明。**副作用是幂等**：同样的内容写两次，文件还是那个样子，所以失败可以重试。
#
# 举例：WRITE.name == "write"
WRITE = ToolDeclaration(
    name="write",
    description=(
        "把一个文本文件整体写成给定内容。文件不存在就新建（缺的父目录一并建好），"
        "存在就整份替换。只改文件里的一小段请用 edit。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "相对工作目录的路径"},
            "content": {"type": "string", "description": "要写入的完整内容，可以是空串"},
        },
        "required": ["path", "content"],
    },
    output_contract="一句话确认（新建还是覆盖、写了多少字符）；不回显内容。",
    side_effect=SideEffect.IDEMPOTENT,
    accepts_idempotency_key=False,
    required_permission=Permission.EDITABLE,
    scope=None,
)


async def write(ctx: ToolContext, arguments: JsonValue) -> JsonValue:
    """整份写文件，返回一句确认。

    举例：await write(ctx, {"path": "a.py", "content": "print(1)"})
          -> "已新建 a.py（9 个字符）"
    出错：路径是个目录、写不进去时抛 ToolArgumentError。
    """
    raw = require_string(arguments, "path")
    content = require_string(arguments, "content", allow_empty=True)
    target = resolve_in_workspace(raw, ctx.workspace)

    # 先自己判一次目录：Windows 上打开目录写会抛 PermissionError，
    # 落到下面那条 OSError 分支就成了一句看不出所以然的"写不了"。
    if target.is_dir():
        raise ToolArgumentError(f"{raw} 是一个目录，不是文件")
    existed = target.is_file()

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # 换行原样写：模型给的 \n 不该被平台改写成 \r\n，否则整份 diff 都会变样。
        target.write_text(content, encoding="utf-8", newline="\n")
    except OSError as exc:
        raise ToolArgumentError(f"写不了 {raw}：{exc}") from None

    return f"已{'覆盖' if existed else '新建'} {raw}（{len(content)} 个字符）"

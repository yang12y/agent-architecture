"""工具契约里**跨模块共用**的两条规则。

它们放在 contracts 而不是某个模块里，因为有两个模块要按**同一条**规则办事，
而模块之间禁止互相 import：

    - 路径参数怎么解释：沙箱按它判越界，工具按它开文件。两边不一致的话，
      沙箱放行的是 A、工具打开的是 B —— 那是安全洞，不是风格问题。
    - 什么时候可以重试：执行器按它决定重试，崩溃恢复按它决定重放。
      "此规则与恢复判定同一条"是明写的不变量，两份实现迟早走岔。
"""

from __future__ import annotations

from pathlib import Path

from .errors import ToolArgumentError
from .models import SideEffect, ToolDeclaration


def resolve_in_workspace(raw: str, workspace: str) -> Path:
    """把工具参数里的路径解析成工作目录下的绝对路径。

    **规范化不是可选的**：`workspace/../../etc/passwd`、软链接、`~` 全靠它挡。
    相对路径按 workspace 展开，展开后再 resolve。

    举例：resolve_in_workspace("a.py", "D:/proj") -> Path("D:/proj/a.py")
    出错：不是非空字符串、或路径非法时抛 ToolArgumentError。
    """
    if not isinstance(raw, str) or not raw:
        raise ToolArgumentError(f"路径不是非空字符串：{raw!r}")
    try:
        target = Path(raw).expanduser()
        if not target.is_absolute():
            target = Path(workspace) / target
        return target.resolve()
    except (OSError, ValueError) as exc:
        raise ToolArgumentError(f"路径解析失败：{raw}") from exc


def is_retryable(declaration: ToolDeclaration | None) -> bool:
    """这条工具的调用失败之后能不能自动重试。

    规则：`none` / `idempotent` 可以；`non_idempotent` **只有在接受幂等键时**才可以
    （键让下游能认出"这是同一次调用"，重复执行不会产生第二份影响）。
    查不到声明的一律不重试 —— 判不了就别赌。

    举例：is_retryable(write_decl) -> False
    """
    if declaration is None:
        return False
    if declaration.side_effect is not SideEffect.NON_IDEMPOTENT:
        return True
    return declaration.accepts_idempotency_key

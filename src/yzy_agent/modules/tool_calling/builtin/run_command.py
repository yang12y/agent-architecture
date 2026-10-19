"""内置的 run_command：执行一条命令并取回输出。

它是唯一碰**命令**这个资源类的工具。命令里是什么无法静态判定，所以沙箱对它一律按
`full_access` 判（策略定死，不看这里声明什么）—— 低于这一档就走人工审批，而不是硬拒。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil

from yzy_agent.contracts import (
    JsonValue,
    Permission,
    SideEffect,
    ToolArgumentError,
    ToolDeclaration,
)

from ..registry import ToolContext
from .common import require_string

# 声明。**非幂等**：同一条命令跑两次可能产生两份影响（发一封邮件、追加一行日志），
# 所以失败与超时都不自动重试。
#
# 举例：RUN_COMMAND.name == "run_command"
RUN_COMMAND = ToolDeclaration(
    name="run_command",
    description=(
        "在工作目录下执行一条命令，返回它的输出（标准错误也并进来）。"
        "命令交给系统的外壳执行：Windows 上是 pwsh，其它平台是 sh。"
        "命令能碰到的远不止工作目录，所以要 full_access 权限，低档位时每次都要人工确认。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {"command": {"type": "string", "description": "要执行的命令行"}},
        "required": ["command"],
    },
    output_contract="命令的输出；退出码非零时在末尾标注。",
    side_effect=SideEffect.NON_IDEMPOTENT,
    accepts_idempotency_key=False,
    required_permission=Permission.FULL_ACCESS,
    scope=None,
)


async def run_command(ctx: ToolContext, arguments: JsonValue) -> JsonValue:
    """跑一条命令，返回它的输出。

    超时由执行器管（到点它会掐掉这次调用），被掐时这里负责把子进程一起收掉。

    举例：await run_command(ctx, {"command": "python -V"}) -> "Python 3.13.3"
    出错：起不了进程时抛 ToolArgumentError。
    """
    command = require_string(arguments, "command")
    try:
        process = await asyncio.create_subprocess_exec(
            *_shell(command),
            cwd=ctx.workspace,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as exc:
        raise ToolArgumentError(f"起不了进程：{exc}") from None

    try:
        output, _ = await process.communicate()
    except asyncio.CancelledError:
        # 用户中断或执行器超时。**必须把子进程收掉**，否则它会一直在后台跑下去。
        process.kill()
        with contextlib.suppress(Exception):
            await process.wait()
        raise

    text = output.decode("utf-8", errors="replace").rstrip()
    if process.returncode:
        note = f"（退出码 {process.returncode}）"
        return f"{text}\n{note}" if text else note
    return text or "（命令没有输出）"


def _shell(command: str) -> list[str]:
    """把一条命令行按平台拼成 argv。

    Windows 上优先用 pwsh：这个项目里的命令都是 PowerShell 写的，cmd.exe 跑不了那些写法。
    POSIX 上用 sh。

    举例：_shell("echo hi") -> ["/bin/sh", "-c", "echo hi"]
    """
    if os.name != "nt":
        return ["/bin/sh", "-c", command]
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        return [os.environ.get("COMSPEC", "cmd.exe"), "/c", command]
    return [shell, "-NoProfile", "-NonInteractive", "-Command", command]

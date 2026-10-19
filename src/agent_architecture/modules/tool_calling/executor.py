"""位点 7 的执行：跑一个工具，带超时与按规则的重试。

**这里最重要的性质是"不抛"**：工具超时、参数错、文件不存在、乃至工具自己写挂，
一律收成 `Outcome` 交回去。原因有两条：

    1. 模型必须拿到"为什么没成"才能改，上抛只会让整次 trace 挂掉；
    2. 同一组里可能还有别的工具在跑，一条挂了不该影响其他几条。

只有 `SiteAbort` 与 `CancelledError` 直穿 —— 那是内核的控制流，不是工具失败。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from agent_architecture.contracts import AgentError, JsonValue, SiteAbort

from .output import normalize
from .registry import Tool, ToolContext

# 一次工具调用的默认超时（秒）。工具自己可以声明更长的 —— 等声明字段落地（见 §9）。
#
# 举例：60.0
DEFAULT_TIMEOUT_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class Outcome:
    """一次执行的结果。**成功与失败都走这一个形状**，调用方不必分两条路。"""

    # 给模型看的正文：成功是工具的输出，失败是失败说明。
    #
    # 举例："1\timport os\n2\t..." / "read 超过 60 秒没有返回，已放弃这次调用。"
    text: str

    # 失败时的领域异常；成功为 None。
    #
    # 举例：ToolTimeoutError("read 超过 60 秒没有返回")
    error: AgentError | None = None

    @property
    def failed(self) -> bool:
        """这次是不是失败了。

        举例：Outcome("好了").failed -> False
        """
        return self.error is not None


async def run(
    tool: Tool, context: ToolContext, arguments: JsonValue, *, timeout: float | None = None
) -> Outcome:
    """跑一次工具。**任何失败都收成 Outcome，绝不外抛**（除了 SiteAbort）。

    举例：outcome = await run(catalog.tool("read"), ctx, {"path": "a.py"})
    """
    limit = timeout or DEFAULT_TIMEOUT_SECONDS
    try:
        result = await asyncio.wait_for(tool.run(context, arguments), timeout=limit)
    except SiteAbort:
        raise
    except TimeoutError:
        return Outcome(
            text=f"{tool.declaration.name} 超过 {limit:g} 秒没有返回，已放弃这次调用。",
            error=AgentError(f"{tool.declaration.name} 执行超时（{limit:g} 秒）"),
        )
    except AgentError as exc:
        # 领域错误（参数不合法、文件不存在、超时…）带的是**给人看的话**，直接交给模型。
        return Outcome(text=exc.message, error=exc)
    # 刻意捕获所有异常：工具自己写挂了也该让模型知道，而不是掀翻整次 trace。
    except Exception as exc:
        context.logger.error(
            "tool.failed",
            tool=tool.declaration.name,
            tool_call_id=context.call_id,
            error=f"{type(exc).__name__}: {exc}",
        )
        return Outcome(
            text=f"{tool.declaration.name} 执行失败：{type(exc).__name__}: {exc}",
            error=AgentError(f"{tool.declaration.name} 执行失败：{exc}"),
        )
    return Outcome(text=normalize(result))

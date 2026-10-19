"""执行器：**任何失败都收成结果，绝不外抛**。"""

from __future__ import annotations

import asyncio

import pytest

from agent_architecture.contracts import CancelToken, JsonValue, SiteAbort, ToolArgumentError
from agent_architecture.infra.observability import RecordingLogger
from agent_architecture.modules.tool_calling import Tool, ToolContext, run
from fakes import make_tool_declaration


def _tool(fn, *, name: str = "read") -> Tool:
    """包一条只读工具。

    举例：_tool(read)
    """
    return Tool(declaration=make_tool_declaration(name), run=fn)


def _context() -> ToolContext:
    """一次执行的现场。

    举例：_context().call_id -> "call_1"
    """
    return ToolContext(
        workspace=".", call_id="call_1", signal=CancelToken(), logger=RecordingLogger()
    )


async def test_a_successful_run_returns_the_normalized_text() -> None:
    """成功时正文就是工具的返回值规范化之后的样子。"""

    async def read(ctx: ToolContext, arguments: JsonValue) -> JsonValue:
        return {"count": 3}

    outcome = await run(_tool(read), _context(), {})

    assert outcome.text == '{"count": 3}'
    assert outcome.failed is False


async def test_a_domain_error_keeps_its_human_message() -> None:
    """领域错误带的是给人看的话，直接交给模型 —— 不要套一层类型名。"""

    async def read(ctx: ToolContext, arguments: JsonValue) -> JsonValue:
        raise ToolArgumentError("文件不存在：a.py")

    outcome = await run(_tool(read), _context(), {})

    assert outcome.text == "文件不存在：a.py"
    assert outcome.failed is True


async def test_a_crashing_tool_becomes_a_result_too() -> None:
    """工具自己写挂了也收成结果 —— 模型该被告知，而不是整次 trace 挂掉。"""

    async def read(ctx: ToolContext, arguments: JsonValue) -> JsonValue:
        raise RuntimeError("我炸了")

    outcome = await run(_tool(read), _context(), {})

    assert "RuntimeError" in outcome.text and "我炸了" in outcome.text
    assert outcome.failed is True


async def test_a_hanging_tool_times_out_into_a_result() -> None:
    """挂住的工具会被超时收掉 —— 不设超时的话它就永远挂在那里。"""

    async def read(ctx: ToolContext, arguments: JsonValue) -> JsonValue:
        await asyncio.Event().wait()
        return ""  # pragma: no cover

    outcome = await run(_tool(read), _context(), {}, timeout=0.01)

    assert "超时" in outcome.text or "没有返回" in outcome.text
    assert outcome.failed is True


async def test_site_abort_goes_straight_up() -> None:
    """SiteAbort 是内核的控制流，不是工具失败 —— 不能收成结果。"""

    async def read(ctx: ToolContext, arguments: JsonValue) -> JsonValue:
        raise SiteAbort("用户中断")

    with pytest.raises(SiteAbort):
        await run(_tool(read), _context(), {})

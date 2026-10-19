"""tool_calling 在各位点上的 SiteHandler。

四个位点、五个 handler（位点 6 挂两个，中间隔着 sandbox 的判定）。
"""

from __future__ import annotations

from typing import cast

from yzy_agent.contracts import (
    Handler,
    Message,
    MessageRole,
    Site,
    SiteContext,
    TextPart,
    ToolAfterPayload,
    ToolCall,
    ToolDuringPayload,
    is_retryable,
)

from .chain import finalize, screen
from .executor import DEFAULT_TIMEOUT_SECONDS, run
from .output import truncate
from .registry import ToolCatalog, ToolContext

# 位点 3 的次序：**整套最早** —— runtime_state(100) 算上下文量时要把它填的 schema 一起算进去，
# 排在它后面就等于 schema 不算数：阈值和界面上的占用比例都会低报一整块。
#
# 举例：50
PRIORITY_LLM_BEFORE = 50

# 位点 6 的检查链：**本模块最先跑** —— 存在性与参数校验不过的调用，后面的审批没有意义。
#
# 举例：100
PRIORITY_TOOL_BEFORE = 100

# 位点 6 的收尾：**必须晚于 sandbox（435）**，因为它的结论也要收成结果消息；
# 又必须早于 runtime_state（450），那位点上队列已经被摘过一轮。
#
# 举例：445
PRIORITY_TOOL_FINALIZE = 445

# 位点 7/8 的次序：执行与收结果都在维度段里最早的一档。
#
# 举例：100
PRIORITY_TOOL_RUN = 100

# 一次调用最多试几次。超时与失败走同一条重试规则，见 contracts.is_retryable。
#
# 举例：2
MAX_ATTEMPTS = 2


class _SiteOpHandler:
    """把一个位点操作包成 Handler。一个实例只挂一个位点。

    举例：_SiteOpHandler("tool_calling.tool_before", Site.TOOL_BEFORE, 100, op, catalog)
    """

    def __init__(
        self, handler_id: str, site: Site, priority: int, op, catalog: ToolCatalog
    ) -> None:
        """绑定一个位点操作。

        举例：_SiteOpHandler("tool_calling.tool_before", Site.TOOL_BEFORE, 100, screen, catalog)
        """
        # 举例："tool_calling.tool_before"
        self.id = handler_id

        # 举例：frozenset({Site.TOOL_BEFORE})
        self.sites: frozenset[Site] = frozenset({site})

        # 举例：100
        self.priority = priority

        # 装配期定下来的工具表，运行期只读。
        #
        # 举例：ToolCatalog()
        self._catalog = catalog

        # 本位点真正要跑的那段。
        #
        # 举例：screen
        self._op = op

    async def run(self, ctx: SiteContext) -> bool | None:
        """跑本位点的操作。tool_calling 没有否决语义，返回值一律 None。

        举例：await handler.run(ctx)
        """
        await self._op(ctx, self._catalog)
        return None


async def _load_schema(ctx: SiteContext, catalog: ToolCatalog) -> None:
    """位点 3：把可见的工具 schema 填进请求。

    举例：await _load_schema(ctx, catalog)
    """
    payload = ctx.payload
    payload.tools_schema = [  # type: ignore[attr-defined]
        {
            "name": declaration.name,
            "description": declaration.description,
            "parameters": declaration.parameters_schema,
        }
        for declaration in catalog.declarations(_scope_of(ctx))
    ]


def _scope_of(ctx: SiteContext) -> str | None:
    """这次 trace 的工具可见性作用域。现在只有一个默认作用域，取 None（全部可见）。

    举例：_scope_of(ctx) -> None
    """
    return None


async def _chain(ctx: SiteContext, catalog: ToolCatalog) -> None:
    """位点 6 第一段：检查链。

    举例：await _chain(ctx, catalog)
    """
    screen(ctx, catalog)


async def _finalize(ctx: SiteContext, catalog: ToolCatalog) -> None:
    """位点 6 第二段：给不执行的调用写结果消息。

    举例：await _finalize(ctx, catalog)
    """
    finalize(ctx)


async def _execute(ctx: SiteContext, catalog: ToolCatalog) -> None:
    """位点 7：跑这条调用，把结果写进 payload 交给内核搬运。

    **失败也写进 payload，不抛** —— 位点 8 靠它决定写正文还是写失败说明。

    举例：await _execute(ctx, catalog)
    """
    payload = cast(ToolDuringPayload, ctx.payload)
    call = payload.call
    if call is None:
        return
    tool = catalog.tool(call.name)
    if tool is None:
        # 检查链已经拦过一道，走到这里说明有人绕过了它 —— 收成结果而不是炸掉。
        payload.raw_result = f"没有叫 {call.name} 的工具。"
        return

    timeout = payload.timeout_seconds or DEFAULT_TIMEOUT_SECONDS
    declaration = tool.declaration
    context = _context(ctx, call)

    outcome = await run(tool, context, call.arguments, timeout=timeout)
    while outcome.failed and payload.attempt + 1 < MAX_ATTEMPTS and is_retryable(declaration):
        payload.attempt += 1
        ctx.ports.logger.warning(
            "tool.retry",
            tool=call.name,
            tool_call_id=call.id,
            attempt=payload.attempt,
        )
        outcome = await run(tool, context, call.arguments, timeout=timeout)

    call.status = call.status.RUNNING
    payload.raw_result = outcome.text
    payload.error = outcome.error


def _context(ctx: SiteContext, call: ToolCall) -> ToolContext:
    """造一次工具执行的现场。

    举例：_context(ctx, call) -> ToolContext(workspace="D:/proj", ...)
    """
    return ToolContext(
        workspace=ctx.session.meta.workspace,
        call_id=call.id,
        signal=ctx.signal,
        logger=ctx.ports.logger,
    )


async def _collect(ctx: SiteContext, catalog: ToolCatalog) -> None:
    """位点 8：把结果截断成一条给模型看的 tool 消息。

    **成功与失败都写**：失败时正文是失败说明（位点 7 已经填好），模型据此改。
    截断只针对成功的正文 —— 失败说明本来就不长。

    举例：await _collect(ctx, catalog)
    """
    payload = cast(ToolAfterPayload, ctx.payload)
    call = payload.call
    if call is None:
        return

    text = payload.raw_result or f"{call.name} 没有返回内容。"
    if payload.error is None:
        text = truncate(text, max_chars=ctx.config.tool_result_max_chars)

    payload.normalized = Message(
        role=MessageRole.TOOL,
        content=[TextPart(text=text)],
        metadata={"tool_call_id": call.id},
    )


def build_handlers(catalog: ToolCatalog) -> tuple[Handler, ...]:
    """造出 tool_calling 要注册的全部 handler，交给装配根。

    举例：registry = HandlerRegistry([*tool_calling.build_handlers(catalog)])
    """
    return (
        _SiteOpHandler(
            "tool_calling.llm_before", Site.LLM_BEFORE, PRIORITY_LLM_BEFORE, _load_schema, catalog
        ),
        _SiteOpHandler(
            "tool_calling.tool_before", Site.TOOL_BEFORE, PRIORITY_TOOL_BEFORE, _chain, catalog
        ),
        _SiteOpHandler(
            "tool_calling.tool_finalize",
            Site.TOOL_BEFORE,
            PRIORITY_TOOL_FINALIZE,
            _finalize,
            catalog,
        ),
        _SiteOpHandler(
            "tool_calling.tool_during", Site.TOOL_DURING, PRIORITY_TOOL_RUN, _execute, catalog
        ),
        _SiteOpHandler(
            "tool_calling.tool_after", Site.TOOL_AFTER, PRIORITY_TOOL_RUN, _collect, catalog
        ),
    )

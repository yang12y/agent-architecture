"""位点 3：拼出这次请求的系统提示词，写进 payload。

**它不在 messages 里。** messages 是 Session 的投影，压缩改的就是它；系统提示词进去就会被
压缩改写、被重建丢掉。所以它单独占一个字段，由内核发请求时才放到最前面 ——
既不算会话内容，也不会被任何一段压缩逻辑碰到；日志里更是一个字都不留。
"""

from __future__ import annotations

import platform
from typing import cast

from agent_architecture.contracts import Handler, LlmBeforePayload, Site, SiteContext

from .prompt import render_system_prompt

# 必须早于 runtime_state（110）算上下文占用：晚一步，这一份提示词的 token 就不进预算，
# 压缩阈值与 trace_end 报的数一起偏小。也必须早于 compression（200）与 session（300）。
#
# 举例：100
PRIORITY_ASSEMBLY = 100


class _SystemPromptHandler:
    """把系统提示词写进 payload.system_prompt。一个实例只挂位点 3。

    举例：_SystemPromptHandler()
    """

    def __init__(self) -> None:
        # handler 唯一标识。
        #
        # 举例："prompts.llm_before"
        self.id = "prompts.llm_before"

        # 只挂位点 3：别的位置上还没有"这一次请求"可以填。
        #
        # 举例：frozenset({Site.LLM_BEFORE})
        self.sites: frozenset[Site] = frozenset({Site.LLM_BEFORE})

        # 维度段的最前一位，见上面的 PRIORITY_ASSEMBLY。
        #
        # 举例：100
        self.priority = PRIORITY_ASSEMBLY

    async def run(self, ctx: SiteContext) -> bool | None:
        """拼一次，写进 payload。**不进 messages，不碰日志。**

        举例：await handler.run(ctx) -> payload.system_prompt == "你是 agent-architecture……"
        """
        payload = cast(LlmBeforePayload, ctx.payload)
        text = render_system_prompt(_variables(ctx))

        # 提示词与它占的 token 一起交出去：内核发起请求时才用到它，别处数不到它的长度。
        payload.system_prompt = text
        payload.system_tokens = ctx.ports.tokens.count_text(text) if text else 0
        return None


def _variables(ctx: SiteContext) -> dict[str, str]:
    """这一次要填进提示词的事实。

    四个值在会话期内都不变 —— 提示词因此逐字稳定，前缀才能被复用。

    举例：_variables(ctx) -> {"workspace": "D:/proj", "platform": "Windows", ...}
    """
    return {
        "workspace": ctx.session.meta.workspace,
        "platform": platform.system(),
        "permission": ctx.config.permission.value,
        "model": ctx.config.model,
    }


def build_handlers() -> tuple[Handler, ...]:
    """造出提示词组装要注册的 handler，交给装配根。

    只有一个：位点 3 的那一个。

    举例：registry = HandlerRegistry(build_handlers())
    """
    return (_SystemPromptHandler(),)

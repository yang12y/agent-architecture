"""位点 3：把可用的技能目录追加进这次请求的注入消息。

**它的改动只活这一次请求。** 目录不写 Session、不改执行态 —— 和系统提示词走同一条路：
放在 `payload.injected_messages` 里，由内核发请求时才拼到最前面。
"""

from __future__ import annotations

from typing import cast

from agent_frame.contracts import (
    Handler,
    LlmBeforePayload,
    Message,
    MessageRole,
    Site,
    SiteContext,
    TextPart,
)

from .catalog import render_catalog
from .registry import SkillRegistry

# 排在 prompts（100）之后：系统提示词在前、技能目录紧随其后，
# 但都要早于 runtime_state（110）算上下文占用 —— 晚一步这两段就不进预算。
#
# 举例：105
PRIORITY_CATALOG = 105


class _CatalogHandler:
    """把技能目录追加进 payload.injected_messages。一个实例只挂位点 3。

    举例：_CatalogHandler(registry)
    """

    def __init__(self, registry: SkillRegistry) -> None:
        """绑定技能表。

        举例：_CatalogHandler(SkillRegistry.load(default_roots("D:/proj")))
        """
        # handler 唯一标识。
        #
        # 举例："skill.llm_before"
        self.id = "skill.llm_before"

        # 只挂位点 3：别的位置上还没有"这一次请求"可以填。
        #
        # 举例：frozenset({Site.LLM_BEFORE})
        self.sites: frozenset[Site] = frozenset({Site.LLM_BEFORE})

        # 见上面的 PRIORITY_CATALOG。
        #
        # 举例：105
        self.priority = PRIORITY_CATALOG

        # 装配期建好的技能表，运行期只读。
        #
        # 举例：SkillRegistry(...)
        self._registry = registry

    async def run(self, ctx: SiteContext) -> bool | None:
        """渲染一次目录，追加一条消息。**不碰日志，不进 messages。**

        举例：await handler.run(ctx) -> payload.injected_messages 多一条 user 消息
        """
        payload = cast(LlmBeforePayload, ctx.payload)
        text = render_catalog(self._registry)
        if not text:
            return None

        payload.injected_messages.append(
            Message(role=MessageRole.USER, content=[TextPart(text=text)])
        )
        return None


def build_handlers(registry: SkillRegistry) -> tuple[Handler, ...]:
    """造出 skill 要注册的 handler，交给装配根。

    只有一个：位点 3 的那一个。

    举例：registry = HandlerRegistry(build_handlers(skills))
    """
    return (_CatalogHandler(registry),)

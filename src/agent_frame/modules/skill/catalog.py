"""目录渲染：把现在能用的技能写成一段文本。

这段文本**不进 Session**：它由 handler 在每次模型调用前追加到 `payload.injected_messages`，
与系统提示词走同一条路。所以技能变了也不用改写任何历史记录 —— 下一次请求换一份就行。

为什么不做成系统提示词的一部分：那是 prompts 模块的地盘，两者职责不同；
放在同一条注入链里、排在系统提示词之后，既保持"静态的在前、会变的在后"的前缀形状，
又让两个模块互不依赖。
"""

from __future__ import annotations

from .registry import SkillRegistry

# 目录消息的开头一句：交代它是什么、以及正文怎么拿。
#
# 举例："以下是当前可用的技能。要用其中某一个时，调用 skill 工具取它的正文。"
CATALOG_PREFIX = (
    "以下是当前可用的技能。要用其中某一个时，调用 skill 工具取它的正文，正文里是具体做法。"
)


def render_catalog(registry: SkillRegistry) -> str:
    """把目录渲染成一段文本；一个可调用的技能都没有时返回空串。

    返回空串而不是"当前没有技能"：每次请求都说一句"没有"纯属噪声。

    举例：render_catalog(registry) -> "以下是当前可用的技能……\n\n- add-handler：……"
    """
    visible = registry.model_visible()
    if not visible:
        return ""

    lines = [CATALOG_PREFIX, ""]
    for manifest in visible:
        suffix = f"（什么时候用：{manifest.when_to_use}）" if manifest.when_to_use else ""
        lines.append(f"- {manifest.name}：{manifest.description}{suffix}")
    return "\n".join(lines)

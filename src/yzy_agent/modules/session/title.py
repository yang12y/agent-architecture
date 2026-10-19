"""会话标题：从第一条用户消息里截一段出来。

标题是**给人看的**，列表页上就那么宽，取用户自己写的那句话最直接 —— 不用模型归纳，
零延迟、零失败模式。真要换成归纳，改动点只有 handler 里调它的那一处。

只在 meta.title 还是空的时候写：用户显式给过标题（SessionRegistry.create(title=...)）
就不覆盖，历史上写过一次也不再改。正文里没有可用的文本时返回 None —— 宁可没有标题，
也不要一个空串。
"""

from __future__ import annotations

from yzy_agent.contracts import Message, TextPart

# 标题最长多少个字。列表页一行放得下的量，超过就截断。
#
# 举例：30
TITLE_MAX_CHARS = 30

# 截断时补的那个字。**它算在 TITLE_MAX_CHARS 里面**，所以结果永远不超过上限。
#
# 举例："…"
ELLIPSIS = "…"


def title_from(message: Message) -> str | None:
    """把一条用户消息压成标题；没有可用正文时返回 None。

    只取文本块 —— 图片和工具调用不是给人看的标题素材。换行与连续空白压成一个空格，
    免得标题里出现一串折行。

    举例：title_from(user_message("帮我读一下 src/main.py")) -> "帮我读一下 src/main.py"
          title_from(user_message("第一行\n第二行")) -> "第一行 第二行"
    """
    text = "".join(part.text for part in message.content if isinstance(part, TextPart))
    collapsed = " ".join(text.split())
    if not collapsed:
        return None
    if len(collapsed) <= TITLE_MAX_CHARS:
        return collapsed
    return collapsed[: TITLE_MAX_CHARS - len(ELLIPSIS)] + ELLIPSIS

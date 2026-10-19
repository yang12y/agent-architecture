"""token 计数的实现。

这是一个**启发式估算**，不是精确分词：精确分词要绑定具体模型的分词器，
而位点 3 需要的只是一个足以判断"要不要压缩"的量级。

**宁可高估，不要低估**：位点 3 是唯一的防线，估少了就会有请求带着超标的上下文出门，
被模型挡回来，整次 trace 以 budget_exceeded 收尾。所以结构化的部分也照算，
图片则是已知的缺口（见 ARCHITECTURE.md §9）。
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from agent_architecture.contracts import Message, TextPart, ThinkingPart, ToolCallPart

# 每条消息的固定开销（角色标记、分隔符等）。
#
# 举例：4
MESSAGE_OVERHEAD_TOKENS = 4


def _is_cjk(char: str) -> bool:
    """判断是不是中日韩表意文字。

    举例：_is_cjk("中") -> True；_is_cjk("a") -> False
    """
    return "\u4e00" <= char <= "\u9fff"


class HeuristicTokenCounter:
    """按字符类型估算 token 数的 TokenCounter 实现。

    估算规则：
        - 中日韩字符：1 个字约等于 1 个 token
        - 其他字符：4 个字符约等于 1 个 token
        - 工具调用的参数：先序列化成 JSON 文本，再按上面两条算
        - 每条消息再加 MESSAGE_OVERHEAD_TOKENS 的固定开销
        - 图片不计入（真实开销取决于模型怎么切图，逐张精算做不到）

    举例：counter = HeuristicTokenCounter(); counter.count_text("你好world") -> 3
    """

    def count_text(self, text: str) -> int:
        """估算一段文本的 token 数。

        举例：count_text("你好world") -> 2 + 1 = 3
        """
        cjk = sum(1 for char in text if _is_cjk(char))
        others = len(text) - cjk
        return cjk + others // 4

    def count_messages(self, messages: Sequence[Message]) -> int:
        """估算一串消息的总 token 数。

        举例：count_messages([user_msg, tool_msg]) -> 42815
        """
        total = 0
        for message in messages:
            for part in message.content:
                if isinstance(part, (TextPart, ThinkingPart)):
                    # 思考也照算：带 tools 的请求要把它原样传回去，它确实占上下文。
                    total += self.count_text(part.text)
                elif isinstance(part, ToolCallPart):
                    # 模型给的参数可以是很大的 JSON（比如要写一整份文件的正文）。
                    total += self.count_text(json.dumps(part.arguments, ensure_ascii=False))
            total += MESSAGE_OVERHEAD_TOKENS
        return total

"""位点 8 的输出处理：规范化 + 超限截断。

**只截断，不留原文、不留引用** —— 超过上限的部分直接丢弃。要全文的场景由工具自己
写进工作区，框架不再存第二份。
"""

from __future__ import annotations

import json

from yzy_agent.contracts import JsonValue

# 截断之后缀在末尾的那句话的模板。把原文总长告诉模型，它才好判断要不要换个法子取。
#
# 举例：TRUNCATED_NOTE.format(total=12345) -> "\n…（已截断，原文共 12345 个字符）"
TRUNCATED_NOTE = "\n…（已截断，原文共 {total} 个字符）"


def normalize(result: JsonValue) -> str:
    """把工具的返回值收成给模型看的文本。

    字符串原样用；其余（数字、布尔、列表、字典）转成 JSON —— 那是模型读得最准的形式。
    None 表示工具没有产出，给一句空的说明而不是"null"。

    举例：normalize({"count": 3}) -> '{"count": 3}'
    """
    if result is None:
        return "(工具没有返回内容)"
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False, sort_keys=True)


def truncate(text: str, *, max_chars: int) -> str:
    """超过上限就截断，并在末尾标注原文总长。没超就原样返回。

    标注也算进上限里 —— 否则"贴着上限"的结果会越界一点点，压缩那边就少算了。

    举例：truncate("x" * 500, max_chars=100) -> "x"*80 + "…（已截断，原文共 500 个字符）"
    """
    if len(text) <= max_chars:
        return text

    note = TRUNCATED_NOTE.format(total=len(text))
    keep = max(0, max_chars - len(note))
    return text[:keep] + note

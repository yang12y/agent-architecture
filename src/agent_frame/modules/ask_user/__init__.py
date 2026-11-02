"""向用户提问：模型用 `ask_user` 问一句，人在界面上选一个。

**问题不进日志，答案进** —— 答案是这次工具调用的结果，跟别的工具结果一样落盘。
所以重连之后历史里看得见问了什么、答了什么；只有"正挂在等"的那一条要靠接口补拉。
"""

from .tool import ANSWER_WAIT_SECONDS, ASK_USER_TOOL, encode_answers, make_runner, parse_questions

__all__ = [
    "ANSWER_WAIT_SECONDS",
    "ASK_USER_TOOL",
    "encode_answers",
    "make_runner",
    "parse_questions",
]

"""提示词组装：每次模型调用前拼一份系统提示词，**不进 Session**。

它不是维度 —— 不写日志、不改执行态，只在位点 3 往这次请求的 messages 最前面插一条。
"""

from .handler import PRIORITY_ASSEMBLY, build_handlers
from .prompt import render_system_prompt, section_names

__all__ = ["PRIORITY_ASSEMBLY", "build_handlers", "render_system_prompt", "section_names"]

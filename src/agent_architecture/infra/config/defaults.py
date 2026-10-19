"""配置的默认值。集中放在这里，loader 与应用代码都引用同一份。"""

from __future__ import annotations

# 默认主模型标识。
#
# 举例："deepseek-flash"
DEFAULT_MODEL = "deepseek-flash"

# 默认上下文窗口上限。deepseek-flash 的窗口是 1M。
#
# 举例：1000000
DEFAULT_MAX_TOKENS = 1_000_000

# 默认压缩阈值：占上下文窗口的比例。到了窗口的七成就开始压，留三成给后续轮次。
#
# 举例：0.7
DEFAULT_COMPACT_RATIO = 0.7

# 二级压缩默认保留的最后 n 条 message 条目数。
#
# 举例：20
DEFAULT_KEEP_RECENT = 20

# 单条工具结果的字符数硬上限。超出部分直接丢弃。
#
# 按 1M 窗口给：仓库里最大的源文件三万字，一次读得完才算"能读项目"。
#
# 举例：60000
DEFAULT_TOOL_RESULT_MAX_CHARS = 60_000

# 单条工具结果超过多少 token 算"长"。
#
# 举例：2000
DEFAULT_LONG_TOOL_RESULT_MAX_TOKENS = 2_000

# 长工具结果默认保留最近几条不替换。留得太少，读过的文件很快变成占位词，只能重读。
#
# 举例：10
DEFAULT_LONG_TOOL_RESULT_KEEP_RECENT = 10

# 单次模型请求的默认超时秒数。
#
# 举例：60.0
DEFAULT_LLM_TIMEOUT_SECONDS = 60.0

# 单轮模型调用默认最多尝试几次（含首次）。
#
# 举例：3
DEFAULT_LLM_MAX_ATTEMPTS = 3

# 默认关掉模型的思考模式：它多花 token，而且只在推理模型上有意义。
#
# 举例：False
DEFAULT_THINKING = False

# 默认日志级别。
#
# 举例："INFO"
DEFAULT_LOG_LEVEL = "INFO"

# 默认的摘要提示词模板。{history} 会被替换成被压掉的历史内容。
#
# 举例："请把下面这段对话压缩成一段可独立阅读的摘要。……{history}……摘要："
DEFAULT_SUMMARY_PROMPT = """请把下面这段对话压缩成一段可独立阅读的摘要。

读者是下一轮模型调用，它看不到被压掉的原文，所以摘要里出现"如前所述""上面提到的"
这类指代就是不合格的。必须逐条保留：

1. 任务目标：用户最初要什么、现在正在做什么。
2. 已拍板的决定：用户明确选定的方案、命名、约束。
3. 已完成的工作与结果：改了哪些文件、跑了什么、结论是什么。
4. 当前状态与下一步：进行到哪一步、还剩什么没做。
5. 失败的尝试与原因：免得下一轮重复踩同一个坑。
6. 必须逐字保留的内容：路径、标识符、参数、数字、报错原文——不许改写、不许概括。

同时请舍弃：客套话、已被推翻的中间结论、重复的过程叙述。

对话内容：
{history}

摘要："""

"""系统提示词：文案与组装都在这一份里。

一段一个常量，`order` 决定它在最终提示词里的位置。摆法只有一条硬要求：
**静态的在前、随会话变的在后。** 同一个会话里拼出来的文本必须逐字不变 ——
token 锚点靠「这次发出去的是上次的严格扩展」才认得上一次的实测用量，
模型侧的前缀缓存也按同一段前缀复用。

所以这里**不写时间、不写随机量**：一句话带上当前分钟，整个前缀每次调用都变，
锚点与缓存一起失效，而代价藏在每次调用的输入里。

组装规则只有四条：按 order 排序、填变量、丢掉空段、空行连接。
引用没给的变量直接报错 —— 提示词写坏了，模型会照着乱猜，而报错一眼就能改。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from agent_architecture.contracts import AgentError


@dataclass(frozen=True, slots=True)
class Section:
    """提示词里的一段。

    举例：Section(name="identity", order=-1000, text="你是 agent-architecture……")
    """

    # 段名。只用来定位与测试，不写进提示词。
    #
    # 举例："identity"
    name: str

    # 拼装顺序，小的在前。
    #
    # 举例：-1000
    order: int

    # 段正文，里面可以带变量占位符。
    #
    # 举例："- 工作目录：{{workspace}}"
    text: str


# 各段的顺序值。留出空档，以后插新段不必重排已有数字。
#
# 举例：IDENTITY_ORDER == -1000
IDENTITY_ORDER = -1000
WORKING_ORDER = 100
TOOLS_ORDER = 1000
STYLE_ORDER = 2000
ENVIRONMENT_ORDER = 10000

# 身份。一句话说清是谁、以及用户看到的是什么。
IDENTITY = """你是 agent-architecture，一个用工具把任务做完的通用智能体。
用户看到的是你的回答，不是你调用了哪个工具。"""

# 干活的方式。这几条是行为约束，不是建议。
WORKING = """- 动手之前先看清楚：要改一个文件，先用 read 读它，不要凭文件名猜里面的内容。
- 不确定就问。编一个看起来合理的答案，比说"不知道"代价大得多。
- 改完要验证：能跑就跑一遍；跑不了就说明你验证了什么、没验证什么。
- 说"完成"之前，确认改动真的落到文件里了 —— 写进去和以为写进去是两件事。
- 多步任务先说打算，再一步步做；中途发现走不通就停下来讲清楚卡在哪。
- 同一条命令失败两次就换办法。反复重试同一个动作不是坚持，是没读报错。"""

# 工具用法。工具本身能干什么由 schema 说，这里只说"什么时候该用它"。
TOOLS = """- 找文件用 glob，找内容用 grep。不要用 run_command 一路 ls / dir。
- 读长文件用 start_line 与 max_lines 分段读；同一份文件不要整份读两遍。
- 改一处用 edit：它要求 old_string 在文件里唯一，不唯一时它不改，只回报出现次数。
- write 是整份覆盖，只在你确实要重写整个文件时用。
- run_command 在 Windows 上走 pwsh，写 PowerShell 语法；命令要能在合理时间内自己结束。
- 工具报错时先读错误本身 —— 它通常直接说了该怎么改。"""

# 表达。回答会被渲染成 Markdown。
STYLE = """- 用中文回答，除非用户用了别的语言。
- 先给结论，再给理由。不要复述你刚执行的命令，用户看得见。
- 代码用围栏并标出语言；路径、函数名、标识符用反引号。
- 不要客套，不要说"作为一个 AI"。"""

# 环境。这些事实随会话而定，所以排在最后：它们一变，只有尾部的前缀失效。
ENVIRONMENT = """- 工作目录：{{workspace}}
- 平台：{{platform}}
- 权限档位：{{permission}}。read_only 只能读，editable 可以改工作目录内的文件，full_access 还能执行命令。
- 当前模型：{{model}}"""

# 全部段。顺序由 order 决定，这里的书写顺序不影响结果。
SECTIONS: tuple[Section, ...] = (
    Section(name="identity", order=IDENTITY_ORDER, text=IDENTITY),
    Section(name="working", order=WORKING_ORDER, text=WORKING),
    Section(name="tools", order=TOOLS_ORDER, text=TOOLS),
    Section(name="style", order=STYLE_ORDER, text=STYLE),
    Section(name="environment", order=ENVIRONMENT_ORDER, text=ENVIRONMENT),
)

# 一个完整的变量占位符。只认这一种形状，其余原样留着。
#
# 举例：_GROUP.findall("{{workspace}}") -> ["workspace"]
_GROUP = re.compile(r"\{\{([^{}]*)\}\}")

# 合法的变量名：小写字母开头，只含小写字母、数字与下划线。
#
# 举例：_NAME.match("workspace") -> 匹配；_NAME.match("工作目录") -> None
_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


def render_system_prompt(variables: Mapping[str, str]) -> str:
    """拼出这一次要发给模型的系统提示词。

    **未知变量直接报错**，不静默留一个占位符给模型看：提示词写坏了，
    模型会照着乱猜，而报错一眼就能改。

    举例：render_system_prompt({"workspace": "D:/proj", ...}) -> "你是 agent-architecture……"
    出错：引用了没给的变量、或变量名不合法时抛 AgentError。
    """
    rendered = [_fill(section, variables) for section in sorted(SECTIONS, key=_sort_key)]
    return "\n\n".join(text for text in rendered if text)


def section_names() -> tuple[str, ...]:
    """最终提示词里各段的顺序，测试与排错用。

    举例：section_names() -> ("identity", "working", "tools", "style", "environment")
    """
    return tuple(section.name for section in sorted(SECTIONS, key=_sort_key))


def _sort_key(section: Section) -> tuple[int, str]:
    """先按 order，再按段名 —— 同 order 时结果也不取决于书写顺序。

    举例：_sort_key(Section(name="identity", order=-1000, text="")) -> (-1000, "identity")
    """
    return (section.order, section.name)


def _fill(section: Section, variables: Mapping[str, str]) -> str:
    """填掉一段里的变量，去掉首尾空白。填完是空串的段会被丢掉。

    举例：_fill(section, {"workspace": "D:/proj", ...}) -> "- 工作目录：D:/proj……"
    """

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        # 占位符原样拼出来，不用 f-string：花括号在 f-string 里要写四层。
        shown = "{{" + name + "}}"
        if not _NAME.match(name):
            raise AgentError(f"提示词变量名不合法：{shown}（段 {section.name}）")
        if name not in variables:
            known = "、".join(sorted(variables)) or "（无）"
            raise AgentError(
                f"提示词里引用了未知变量：{shown}（段 {section.name}；已知变量：{known}）"
            )
        return variables[name]

    return _GROUP.sub(replace, section.text).strip()

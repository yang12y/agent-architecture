"""正文按需加载：`skill` 工具就是这一份。

发现阶段只读声明（manifest.py），正文在这里才读 —— 装了十几个技能时，
模型只看目录，只有真用到的那个才把正文读进来。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from agent_frame.contracts import (
    AgentError,
    JsonValue,
    Permission,
    SideEffect,
    ToolDeclaration,
)

from .manifest import SkillManifest, split_body
from .registry import SkillRegistry

# `skill` 工具的声明。只读、幂等 —— 同一个名字反复取，结果一样。
#
# 举例：SKILL_TOOL.name == "skill"
SKILL_TOOL = ToolDeclaration(
    name="skill",
    description=(
        "取一个技能的做法（正文）。先看上下文里的技能目录，按名字取你要用的那一个。"
        "正文里是具体步骤、命令与清单，照着做。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {"name": {"type": "string", "description": "技能名，kebab-case，见技能目录"}},
        "required": ["name"],
    },
    output_contract="技能的正文（Markdown）；没有这个技能时返回一句说明与可用的名字。",
    side_effect=SideEffect.NONE,
    accepts_idempotency_key=False,
    required_permission=Permission.READ_ONLY,
    scope=None,
)

# 工具实现的形状：拿到执行现场与已校验的参数，返回任意 JSON。
#
# 举例：run = make_runner(registry); await run(ctx, {"name": "add-handler"})
ToolRunner = Callable[[object, JsonValue], Awaitable[JsonValue]]


def read_body(manifest: SkillManifest) -> str:
    """读入口文件里 front-matter 之后的正文。

    举例：read_body(registry.get("add-handler")) -> "# 加一个 handler……"
    出错：文件读不了时抛 AgentError。
    """
    try:
        text = manifest.path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AgentError(f"技能正文读不了：{manifest.path}（{exc}）") from exc
    return split_body(text)


def make_runner(registry: SkillRegistry) -> ToolRunner:
    """把技能表包成 `skill` 工具的实现：给一个名字，返回它的正文。

    装配期调一次，返回一个闭包 —— 技能文件在"模型真的调用"时才读，装配期只扫声明。
    **它只读不写**：有什么技能由扫描决定，模型改不了技能表，也造不出新技能。
    名字不认识时不报错，把可用的名字回给模型，它自己会换一个。

    举例：tools.register(SKILL_TOOL, make_runner(registry))
    """

    async def run(ctx: object, arguments: JsonValue) -> JsonValue:
        """按名字取正文。

        举例：await run(ctx, {"name": "add-handler"}) -> "# 加一个 handler……"
        """
        del ctx  # 只读技能包，不看工作目录与取消信号
        name = _name_of(arguments)
        if not name:
            return f"没有给技能名。可用的有：{_names(registry)}"

        manifest = registry.get(name)
        if manifest is None:
            return f"没有这个技能：{name}。可用的有：{_names(registry)}"

        body = read_body(manifest)
        return body or f"技能 {name} 没有正文。"

    return run


def _name_of(arguments: JsonValue) -> str:
    """从参数里取出技能名；不是非空字符串就是空串。

    举例：_name_of({"name": " add-handler "}) -> "add-handler"
    """
    if not isinstance(arguments, dict):
        return ""
    raw = arguments.get("name")
    return raw.strip() if isinstance(raw, str) else ""


def _names(registry: SkillRegistry) -> str:
    """可调用的技能名，逗号分隔；一个都没有时给一句话。

    举例：_names(registry) -> "add-handler、run-tests"
    """
    names = [manifest.name for manifest in registry.model_visible()]
    return "、".join(names) if names else "（当前没有可用的技能）"

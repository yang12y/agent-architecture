"""技能声明：SKILL.md 的 front-matter。

发现阶段**只读声明**，正文留到模型真要这个技能时再读（见 loader.py）。

格式故意做成最土的那种（和 `dotenv.py` 一个路子）：`---` 夹一段 `key: value`，
单行、不嵌套、不玩缩进。写错了当场报错，别等到模型拿到半份说明才发现。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from agent_frame.contracts import AgentError

# front-matter 的分隔行。
#
# 举例："---"
FENCE = "---"

# 技能名：kebab-case。模型按这个名字调 `skill` 工具，所以不许有空格与下划线。
#
# 举例："code-review" 合法；"Code_Review" 不合法
NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# 声明里认的"假"值。
#
# 举例：FALSE_VALUES == frozenset({"false", "no", "0"})
FALSE_VALUES = frozenset({"false", "no", "0"})


@dataclass(frozen=True, slots=True)
class SkillManifest:
    """一个技能的声明。**不含正文。**

    举例：SkillManifest(name="add-handler", description="加一个 handler", ...)
    """

    # 技能名，kebab-case，也是模型调 `skill` 工具时用的那个名字。
    #
    # 举例："add-handler"
    name: str

    # 一句话描述，进目录。
    #
    # 举例："给这个项目加一个位点 handler：选位点、写实现、装配、补文档"
    description: str

    # 什么时候该用它；空串表示声明里没写。
    #
    # 举例："要往某个位点插逻辑时"
    when_to_use: str

    # 模型能不能主动调用它。**False 的技能不进目录**，也就等于模型看不见。
    #
    # 举例：True
    model_invocable: bool

    # 入口文件的路径，正文按需读它。
    #
    # 举例：Path(".../modules/skill/skills/add-handler/SKILL.md")
    path: Path

    @property
    def directory(self) -> Path:
        """技能所在目录 —— 它随身的资源与脚本都在这里。

        举例：manifest.directory -> Path(".../skills/add-handler")
        """
        return self.path.parent


def load_manifest(path: Path) -> SkillManifest:
    """读一个 SKILL.md，只取声明。

    举例：load_manifest(Path("skills/add-handler/SKILL.md"))
    出错：读不了、没有 front-matter、缺 name / description、名字不是 kebab-case 时抛 AgentError。
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AgentError(f"技能入口读不了：{path}（{exc}）") from exc
    return parse_manifest(text, path=path)


def parse_manifest(text: str, *, path: Path) -> SkillManifest:
    """从文本里解析声明，正文部分原样跳过。

    举例：parse_manifest("---\nname: x\ndescription: y\n---\n正文", path=p)
    出错：格式不对或缺必填项时抛 AgentError。
    """
    fields = _fields(_front_matter(text, path), path)

    name = fields.get("name", "").strip()
    if not name:
        raise AgentError(f"技能缺 name：{path}")
    if not NAME_PATTERN.match(name):
        raise AgentError(f"技能名必须是 kebab-case（小写、中划线连接）：{name!r} @ {path}")

    description = fields.get("description", "").strip()
    if not description:
        raise AgentError(f"技能缺 description：{name} @ {path}")

    flag = fields.get("model_invocable", fields.get("model-invocable", "true"))
    return SkillManifest(
        name=name,
        description=description,
        when_to_use=fields.get("when_to_use", fields.get("when-to-use", "")).strip(),
        model_invocable=flag.strip().lower() not in FALSE_VALUES,
        path=path,
    )


def split_body(text: str) -> str:
    """取 front-matter 之后的正文（去掉开头空行）。

    举例：split_body("---\nname: x\n---\n\n正文") -> "正文"
    """
    lines = text.splitlines()
    start = _opening_index(lines)
    if start is None:
        return text.strip()
    for index in range(start + 1, len(lines)):
        if lines[index].strip() == FENCE:
            return "\n".join(lines[index + 1 :]).lstrip("\n").rstrip()
    return text.strip()


def _opening_index(lines: list[str]) -> int | None:
    """第一条非空的 `---` 在第几行；没有就 None。

    举例：_opening_index(["", "---", "name: x"]) -> 1
    """
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        return index if stripped == FENCE else None
    return None


def _front_matter(text: str, path: Path) -> list[str]:
    """切出 `---` 之间那几行；没有 front-matter 就报错。

    举例：_front_matter("---\nname: x\n---\n", p) -> ["name: x"]
    """
    lines = text.splitlines()
    opened = False
    header: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not opened:
            if not stripped:
                continue
            if stripped != FENCE:
                raise AgentError(f"技能入口必须以 front-matter 开头（第一行是 ---）：{path}")
            opened = True
            continue
        if stripped == FENCE:
            return header
        header.append(line)
    raise AgentError(f"技能的 front-matter 没有收尾的 ---：{path}")


def _fields(header: list[str], path: Path) -> dict[str, str]:
    """把声明行解析成 key: value；空行与 # 注释跳过。

    举例：_fields(["name: x", "description: y"], p) -> {"name": "x", "description": "y"}
    出错：某一行不是 key: value 时抛 AgentError —— 静默忽略会让人找不到写错的那行。
    """
    fields: dict[str, str] = {}
    for line in header:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, value = stripped.partition(":")
        if not separator or not key.strip():
            raise AgentError(f"技能声明里这行不是 key: value：{stripped!r} @ {path}")
        fields[key.strip()] = value.strip()
    return fields

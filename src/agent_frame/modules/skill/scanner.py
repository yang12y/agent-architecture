"""扫描：一个根目录下，每个含 `SKILL.md` 的子目录就是一个技能。"""

from __future__ import annotations

from pathlib import Path

from .manifest import SkillManifest, load_manifest

# 技能入口的文件名。**每个技能一个目录**，目录里至少要有它。
#
# 举例：Path("skills/add-handler") / SKILL_FILE
SKILL_FILE = "SKILL.md"


def scan(root: Path) -> list[SkillManifest]:
    """扫一个根目录，按目录名排序（顺序不取决于文件系统的枚举顺序）。

    根目录不存在不算错 —— 用户还没建它而已，内置技能照常工作。

    举例：scan(Path("modules/skill/skills")) -> [SkillManifest(name="add-handler", ...)]
    出错：某个技能声明写坏了抛 AgentError —— 装配期就该炸，别拖到第一个用户消息。
    """
    if not root.is_dir():
        return []
    return [
        load_manifest(child / SKILL_FILE)
        for child in sorted(root.iterdir())
        if (child / SKILL_FILE).is_file()
    ]

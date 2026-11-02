"""技能注册表：多来源合并去重。

来源是**有序的根目录**：越靠后优先级越高，同名技能由后面的覆盖前面的。
这样"项目里放一份同名技能"就能盖掉内置的那份，而不必改内置目录。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from .manifest import SkillManifest
from .scanner import scan

# 随包发布的内置技能根目录。
#
# 举例：Path(".../agent_frame/modules/skill/skills")
BUILTIN_ROOT = Path(__file__).resolve().parent / "skills"

# 工作目录下留给项目自己技能的子目录名。
#
# 举例：Path("D:/proj") / PROJECT_DIR -> Path("D:/proj/.skills")
PROJECT_DIR = ".skills"


def default_roots(workspace: str) -> tuple[Path, ...]:
    """默认的两个来源：内置技能在前、工作目录下的 `.skills` 在后（后者覆盖前者）。

    举例：default_roots("D:/proj") -> (Path(".../skill/skills"), Path("D:/proj/.skills"))
    """
    return (BUILTIN_ROOT, Path(workspace) / PROJECT_DIR)


class SkillRegistry:
    """一张按名字索引的技能表。**装配期建一次，运行期只读。**

    举例：registry = SkillRegistry.load(default_roots("D:/proj"))
    """

    def __init__(self, manifests: Mapping[str, SkillManifest]) -> None:
        """按名字装表。

        举例：SkillRegistry({"add-handler": SkillManifest(...)})
        """
        # 名字 -> 声明。同名只会留下最后扫到的那一份。
        #
        # 举例：{"add-handler": SkillManifest(name="add-handler", ...)}
        self._by_name: dict[str, SkillManifest] = dict(manifests)

    @classmethod
    def load(cls, roots: Sequence[Path]) -> SkillRegistry:
        """按顺序扫每个根，后面的覆盖前面的同名技能。

        举例：SkillRegistry.load((BUILTIN_ROOT, Path("D:/proj/.skills")))
        """
        merged: dict[str, SkillManifest] = {}
        for root in roots:
            for manifest in scan(root):
                merged[manifest.name] = manifest
        return cls(merged)

    def all(self) -> tuple[SkillManifest, ...]:
        """全部技能，按名字排序（目录的顺序因此稳定）。

        举例：registry.all() -> (SkillManifest(name="add-handler", ...),)
        """
        return tuple(sorted(self._by_name.values(), key=lambda manifest: manifest.name))

    def model_visible(self) -> tuple[SkillManifest, ...]:
        """模型可以主动调用的那些 —— 目录里只列它们。

        举例：registry.model_visible() -> (SkillManifest(model_invocable=True, ...),)
        """
        return tuple(manifest for manifest in self.all() if manifest.model_invocable)

    def get(self, name: str) -> SkillManifest | None:
        """按名字取一个技能；没有就返回 None。

        举例：registry.get("add-handler") -> SkillManifest(...)
        """
        return self._by_name.get(name)

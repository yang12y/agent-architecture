"""注册表：多来源合并去重。"""

from __future__ import annotations

from pathlib import Path

from agent_frame.modules.skill import BUILTIN_ROOT, SkillRegistry
from test_skill.helpers import write_skill


def _two_roots(scratch_dir: Path) -> tuple[Path, Path]:
    """两个来源目录：内置位与"项目位"。

    举例：low, high = _two_roots(scratch_dir)
    """
    low = scratch_dir / "builtin"
    high = scratch_dir / "project"
    low.mkdir(parents=True)
    high.mkdir(parents=True)
    return low, high


def test_a_later_root_overrides_an_earlier_one(scratch_dir: Path) -> None:
    """同名技能由后面的来源覆盖 —— 项目里能盖掉内置的。"""
    low, high = _two_roots(scratch_dir)
    write_skill(low, "demo", description="内置的")
    write_skill(high, "demo", description="项目里的")

    registry = SkillRegistry.load((low, high))

    assert len(registry.all()) == 1
    assert registry.get("demo").description == "项目里的"  # type: ignore[union-attr]


def test_skills_from_both_roots_are_kept(scratch_dir: Path) -> None:
    """不同名的技能各留各的，按名字排序。"""
    low, high = _two_roots(scratch_dir)
    write_skill(low, "alpha")
    write_skill(high, "beta")

    assert [manifest.name for manifest in SkillRegistry.load((low, high)).all()] == [
        "alpha",
        "beta",
    ]


def test_model_visible_skips_user_only_skills(scratch_dir: Path) -> None:
    """模型不可调用的技能不进目录。"""
    write_skill(scratch_dir, "visible")
    write_skill(scratch_dir, "hidden", model_invocable=False)

    registry = SkillRegistry.load((scratch_dir,))

    assert [manifest.name for manifest in registry.all()] == ["hidden", "visible"]
    assert [manifest.name for manifest in registry.model_visible()] == ["visible"]


def test_get_returns_none_for_an_unknown_name() -> None:
    """没有就是 None，让调用方自己去回话。"""
    assert SkillRegistry({}).get("nope") is None


def test_the_builtin_skills_are_valid() -> None:
    """随包发布的那几个技能必须能解析 —— 解析不了装配期就炸。"""
    names = [manifest.name for manifest in SkillRegistry.load((BUILTIN_ROOT,)).all()]

    assert names == ["add-handler", "run-tests"]

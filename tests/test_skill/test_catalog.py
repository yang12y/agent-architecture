"""目录渲染：模型看到的那几行。"""

from __future__ import annotations

from pathlib import Path

from agent_frame.modules.skill import CATALOG_PREFIX, SkillRegistry, render_catalog
from test_skill.helpers import write_skill


def test_an_empty_registry_renders_nothing() -> None:
    """一个技能都没有时返回空串 —— 每次请求说一句"没有技能"纯属噪声。"""
    assert render_catalog(SkillRegistry({})) == ""


def test_each_visible_skill_gets_one_line(scratch_dir: Path) -> None:
    """一行一个技能，名字与描述都在。"""
    write_skill(scratch_dir, "alpha", description="第一件事")
    write_skill(scratch_dir, "beta", description="第二件事")

    text = render_catalog(SkillRegistry.load((scratch_dir,)))

    assert text.startswith(CATALOG_PREFIX)
    assert "- alpha：第一件事" in text
    assert "- beta：第二件事" in text
    assert "skill" in text  # 交代了正文怎么拿


def test_when_to_use_is_appended(scratch_dir: Path) -> None:
    """写了"何时使用"就带上，没写就不带括号。"""
    write_skill(scratch_dir, "alpha", description="第一件事", when_to_use="动手前")
    write_skill(scratch_dir, "beta", description="第二件事")

    text = render_catalog(SkillRegistry.load((scratch_dir,)))

    assert "- alpha：第一件事（什么时候用：动手前）" in text
    assert "- beta：第二件事" in text


def test_user_only_skills_are_not_listed(scratch_dir: Path) -> None:
    """模型调不了的技能不该出现在目录里。"""
    write_skill(scratch_dir, "hidden", description="别列我", model_invocable=False)

    assert render_catalog(SkillRegistry.load((scratch_dir,))) == ""

"""扫描：一个根目录下有哪些技能。"""

from __future__ import annotations

from pathlib import Path

from agent_frame.modules.skill import scan
from test_skill.helpers import write_skill


def test_it_finds_every_skill_directory(scratch_dir: Path) -> None:
    """每个含 SKILL.md 的子目录都是一个技能，按目录名排序。"""
    write_skill(scratch_dir, "beta", description="第二个")
    write_skill(scratch_dir, "alpha", description="第一个")

    found = scan(scratch_dir)

    assert [manifest.name for manifest in found] == ["alpha", "beta"]


def test_a_missing_root_is_empty(scratch_dir: Path) -> None:
    """根目录还没建不算错 —— 用户没建而已。"""
    assert scan(scratch_dir / "nope") == []


def test_a_directory_without_an_entry_file_is_ignored(scratch_dir: Path) -> None:
    """目录里没有 SKILL.md 就不是技能，别把它当坏的。"""
    (scratch_dir / "not-a-skill").mkdir()
    write_skill(scratch_dir, "alpha")

    assert [manifest.name for manifest in scan(scratch_dir)] == ["alpha"]

"""正文按需加载：`skill` 工具。"""

from __future__ import annotations

from pathlib import Path

from agent_frame.modules.skill import SkillRegistry, make_runner, read_body
from test_skill.helpers import write_skill


def _library(scratch_dir: Path) -> SkillRegistry:
    """只含一个 alpha 技能的表。"""
    write_skill(scratch_dir, "alpha", body="第一步：看清楚。\n第二步：再动手。")
    return SkillRegistry.load((scratch_dir,))


def test_read_body_returns_everything_after_the_header(scratch_dir: Path) -> None:
    """正文就是 front-matter 之后的内容。"""
    registry = _library(scratch_dir)

    body = read_body(registry.get("alpha"))  # type: ignore[arg-type]

    assert body == "第一步：看清楚。\n第二步：再动手。"


async def test_the_tool_returns_the_body(scratch_dir: Path) -> None:
    """模型点名要一个技能，拿到的就是正文。"""
    run = make_runner(_library(scratch_dir))

    assert await run(object(), {"name": "alpha"}) == "第一步：看清楚。\n第二步：再动手。"


async def test_an_unknown_name_lists_what_is_available(scratch_dir: Path) -> None:
    """名字不认识时不抛异常，把可用的名字回给模型 —— 它自己会换个名字再试。"""
    run = make_runner(_library(scratch_dir))

    answer = await run(object(), {"name": "nope"})

    assert "没有这个技能：nope" in answer
    assert "alpha" in answer


async def test_a_missing_name_lists_what_is_available(scratch_dir: Path) -> None:
    """没给名字时同样回一份可用清单。"""
    run = make_runner(_library(scratch_dir))

    assert "没有给技能名" in await run(object(), {})


async def test_a_skill_without_a_body_says_so(scratch_dir: Path) -> None:
    """正文是空的时候给一句话，不是空字符串。"""
    write_skill(scratch_dir, "empty", body="")
    run = make_runner(SkillRegistry.load((scratch_dir,)))

    assert (await run(object(), {"name": "empty"})) == "技能 empty 没有正文。"

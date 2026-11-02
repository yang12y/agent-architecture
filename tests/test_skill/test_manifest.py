"""技能声明：front-matter 的解析。"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_frame.contracts import AgentError
from agent_frame.modules.skill import load_manifest, parse_manifest, split_body

# 解析时不碰磁盘，路径只用来报错与定位。
#
# 举例：parse_manifest(text, path=PATH)
PATH = Path("skills/demo/SKILL.md")

# 一份写全了的声明和它的正文。
GOOD = """---
name: demo
description: 演示用
when_to_use: 想演示时
---

正文第一行
正文第二行
"""


def test_every_declared_field_is_parsed() -> None:
    """名称、描述、何时使用、调用策略都从声明里来。"""
    manifest = parse_manifest(GOOD, path=PATH)

    assert (manifest.name, manifest.description, manifest.when_to_use) == (
        "demo",
        "演示用",
        "想演示时",
    )
    assert manifest.model_invocable is True
    assert manifest.directory == Path("skills/demo")


def test_when_to_use_is_optional() -> None:
    """没写"何时使用"就是空串，不是报错。"""
    manifest = parse_manifest("---\nname: demo\ndescription: 说明\n---\n正文", path=PATH)

    assert manifest.when_to_use == ""


def test_model_invocable_can_be_switched_off() -> None:
    """写了 false 就是模型不可调用 —— 这种技能不进目录。"""
    text = "---\nname: demo\ndescription: 说明\nmodel_invocable: false\n---\n正文"

    assert parse_manifest(text, path=PATH).model_invocable is False


def test_the_hyphenated_key_is_accepted_too() -> None:
    """`model-invocable` 与 `model_invocable` 都认。"""
    text = "---\nname: demo\ndescription: 说明\nmodel-invocable: false\n---\n正文"

    assert parse_manifest(text, path=PATH).model_invocable is False


def test_a_name_that_is_not_kebab_case_is_an_error() -> None:
    """名字是模型调用 `skill` 时用的键，不许有下划线或大写。"""
    text = "---\nname: Demo_Skill\ndescription: 说明\n---\n正文"

    with pytest.raises(AgentError):
        parse_manifest(text, path=PATH)


def test_a_missing_name_is_an_error() -> None:
    """没有名字的技能，模型没法点名要它。"""
    with pytest.raises(AgentError):
        parse_manifest("---\ndescription: 说明\n---\n正文", path=PATH)


def test_a_missing_description_is_an_error() -> None:
    """没有描述，目录里那一行就是空的。"""
    with pytest.raises(AgentError):
        parse_manifest("---\nname: demo\n---\n正文", path=PATH)


def test_without_front_matter_is_an_error() -> None:
    """第一行不是 --- 就不是技能入口。"""
    with pytest.raises(AgentError):
        parse_manifest("# 只有正文", path=PATH)


def test_an_unclosed_front_matter_is_an_error() -> None:
    """少了收尾的 --- 时整份文件都会被当成声明，直接报错更省事。"""
    with pytest.raises(AgentError):
        parse_manifest("---\nname: demo\ndescription: 说明\n", path=PATH)


def test_a_line_that_is_not_key_value_is_an_error() -> None:
    """写错一行就报错，别静默跳过让人找不到。"""
    with pytest.raises(AgentError):
        parse_manifest("---\nname demo\n---\n正文", path=PATH)


def test_split_body_drops_the_header_and_leading_blank_lines() -> None:
    """正文就是 --- 之后的内容，开头的空行去掉。"""
    assert split_body(GOOD) == "正文第一行\n正文第二行"


def test_load_manifest_reads_the_file(scratch_dir: Path) -> None:
    """从磁盘读一份声明。"""
    from test_skill.helpers import write_skill

    entry = write_skill(scratch_dir, "demo", description="磁盘上的")

    assert load_manifest(entry).description == "磁盘上的"


def test_load_manifest_reports_a_missing_file(scratch_dir: Path) -> None:
    """文件不在时抛 AgentError，错误里带路径。"""
    with pytest.raises(AgentError):
        load_manifest(scratch_dir / "nope" / "SKILL.md")

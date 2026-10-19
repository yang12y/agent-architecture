"""系统提示词的组装：段序、变量、字节稳定。"""

from __future__ import annotations

import pytest

from agent_architecture.contracts import AgentError
from agent_architecture.modules.prompts import render_system_prompt, section_names
from agent_architecture.modules.prompts.prompt import Section

# 一次组装要用到的全部事实。
#
# 举例：render_system_prompt(VARIABLES)
VARIABLES = {
    "workspace": "D:/proj",
    "platform": "Windows",
    "permission": "editable",
    "model": "deepseek-flash",
}


def test_sections_are_joined_in_order() -> None:
    """身份在最前、环境在最后，段与段之间一个空行。"""
    text = render_system_prompt(VARIABLES)

    assert text.startswith("你是 agent-architecture")
    assert text.endswith("当前模型：deepseek-flash")
    assert "\n\n" in text


def test_section_names_are_the_documented_order() -> None:
    """段的顺序由 order 决定，与书写顺序无关。"""
    assert section_names() == ("identity", "working", "tools", "style", "environment")


def test_every_variable_is_filled() -> None:
    """填进去的都是真值，不留占位符。"""
    text = render_system_prompt(VARIABLES)

    for value in VARIABLES.values():
        assert value in text
    assert "{{" not in text


def test_an_unknown_variable_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """宁可报错，也不要给模型留一个 {{xxx}} 让它自己猜。"""
    monkeypatch.setattr(
        "agent_architecture.modules.prompts.prompt.SECTIONS",
        (Section(name="x", order=0, text="工作目录：{{missing}}"),),
    )

    with pytest.raises(AgentError):
        render_system_prompt(VARIABLES)


def test_a_missing_variable_is_an_error() -> None:
    """少给一个变量就报错，而不是拼出一份缺半截的提示词。"""
    with pytest.raises(AgentError):
        render_system_prompt({"workspace": "D:/proj"})


def test_the_prompt_is_byte_stable() -> None:
    """同样的事实拼出来的字节一模一样 —— 前缀缓存与 token 锚点都靠这个前提。"""
    assert render_system_prompt(VARIABLES) == render_system_prompt(dict(VARIABLES))


def test_an_empty_section_disappears(monkeypatch: pytest.MonkeyPatch) -> None:
    """空段不占位置，也不会留下连续三个换行。"""
    monkeypatch.setattr(
        "agent_architecture.modules.prompts.prompt.SECTIONS",
        (Section(name="blank", order=0, text="   "),),
    )

    assert render_system_prompt(VARIABLES) == ""

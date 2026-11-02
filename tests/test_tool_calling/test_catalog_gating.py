"""装配出来的工具表装不装文件工具：默认装齐，开关关掉就一个不装。"""

from __future__ import annotations

from agent_frame.composition import build_container
from fakes import ScriptedLlm, make_app_config, text_turn


def _schema_names(container) -> set[str]:
    """从装好的工具目录里取全部工具名。

    举例：_schema_names(build_container(make_app_config())) -> {"read", "chart_line", …}
    """
    return {declaration.name for declaration in container.ports.tools.declarations()}


def test_file_tools_are_registered_by_default() -> None:
    """默认装齐七个：通用智能体靠它们干活。"""
    container = build_container(make_app_config(), llm=ScriptedLlm([text_turn("好")]))

    names = _schema_names(container)

    assert {"read", "write", "edit", "glob", "grep", "list_dir", "run_command"} <= names


def test_switching_them_off_leaves_the_catalog_empty() -> None:
    """关掉开关就一个文件工具都不装 —— 实现全留着，只是不给模型看。"""
    container = build_container(
        make_app_config(file_tools=False), llm=ScriptedLlm([text_turn("好")])
    )

    names = _schema_names(container)

    assert not {"read", "write", "edit", "glob", "grep", "list_dir", "run_command"} & names

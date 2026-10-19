"""资源路由与路径解析：要判"越界"，先得知道碰的是什么。"""

from __future__ import annotations

from pathlib import Path

import pytest

from fakes import make_tool_declaration
from yzy_agent.contracts import ToolArgumentError
from yzy_agent.modules.sandbox import (
    ARGUMENT_KINDS,
    ResourceKind,
    inside,
    resolve_path,
    route,
)


def test_route_finds_every_managed_kind_declared() -> None:
    """一个也不漏 —— 在边界上漏检就是没判。"""
    both = make_tool_declaration("fetch", properties=["path", "url"])

    assert route(both) == frozenset({ResourceKind.FILE, ResourceKind.NETWORK})


def test_route_is_empty_when_the_tool_declares_nothing_managed() -> None:
    """纯计算的工具碰不到受管资源。"""
    calc = make_tool_declaration("calc", properties=["expression"])

    assert route(calc) == frozenset()


def test_arguments_do_not_create_resource_kinds() -> None:
    """判的是工具**能**碰什么，不是这一次传了什么。"""
    plain = make_tool_declaration("plain")

    assert route(plain) == frozenset()


def test_the_route_table_keys_are_argument_names() -> None:
    """路由靠参数名约定 —— 这张表就是约定的全部。"""
    assert set(ARGUMENT_KINDS) == {"path", "url", "command"}


def test_a_relative_path_is_resolved_against_the_workspace(scratch_dir: Path) -> None:
    """相对路径按工作目录展开。"""
    target = resolve_path({"path": "sub/a.py"}, workspace=str(scratch_dir))

    assert target == (scratch_dir / "sub" / "a.py").resolve()


def test_dotdot_is_normalized_away(scratch_dir: Path) -> None:
    """`..` 必须在判定之前消掉 —— 不规范化就是白判。"""
    target = resolve_path({"path": "sub/../../outside.txt"}, workspace=str(scratch_dir))

    assert inside(target, workspace=str(scratch_dir)) is False


def test_the_workspace_itself_counts_as_inside(scratch_dir: Path) -> None:
    """工作目录自己也算在里面。"""
    target = resolve_path({"path": "."}, workspace=str(scratch_dir))

    assert inside(target, workspace=str(scratch_dir)) is True


@pytest.mark.parametrize("arguments", [{}, {"path": 42}, {"path": ""}, {"path": None}])
def test_a_path_that_cannot_be_parsed_raises(arguments: dict) -> None:
    """判不出来就要说判不出来，不能默默当成没事。"""
    with pytest.raises(ToolArgumentError):
        resolve_path(arguments, workspace=".")


def test_arguments_that_are_not_an_object_raise() -> None:
    """参数整体不是对象时同样判不了。"""
    with pytest.raises(ToolArgumentError):
        resolve_path([1, 2, 3], workspace=".")

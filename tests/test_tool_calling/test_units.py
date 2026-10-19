"""检查链的前三步、输出处理、注册表。"""

from __future__ import annotations

import pytest

from agent_architecture.modules.tool_calling import (
    ToolCatalog,
    normalize,
    truncate,
    validate,
    with_defaults,
)
from fakes import make_tool_declaration

# 一个有必填、有缺省、有类型的声明。
#
# 举例：_READ.name == "read"
_READ = make_tool_declaration("read", properties=["path"])


def test_defaults_are_filled_from_the_schema() -> None:
    """缺省参数按 schema 补上 —— 补完的才是"最终参数"。"""
    declaration = make_tool_declaration("read", properties=["path"])
    declaration.parameters_schema["properties"]["limit"] = {"type": "integer", "default": 100}

    assert with_defaults(declaration, {"path": "a.py"}) == {"path": "a.py", "limit": 100}


def test_given_arguments_win_over_defaults() -> None:
    """用户给了就用用户的。"""
    declaration = make_tool_declaration("read", properties=["path"])
    declaration.parameters_schema["properties"]["path"] = {"type": "string", "default": "x"}

    assert with_defaults(declaration, {"path": "a.py"})["path"] == "a.py"


def test_a_missing_required_argument_is_reported() -> None:
    """必填缺了要说清缺的是哪个 —— 笼统的说明只会让模型重犯。"""
    assert validate(_READ, {}) == "缺少必填参数：path"


def test_a_wrong_type_is_reported() -> None:
    """类型不对也要说清。"""
    assert validate(_READ, {"path": 42}) == "参数 path 应为 string，收到 int"


def test_a_boolean_is_not_an_integer() -> None:
    """`true` 不该被当成 1 放过去 —— bool 是 int 的子类，得单独挡。"""
    declaration = make_tool_declaration("read", properties=["limit"])
    declaration.parameters_schema["properties"]["limit"] = {"type": "integer"}

    assert validate(declaration, {"limit": True}) == "参数 limit 应为 integer，收到 boolean"


def test_an_unknown_key_is_let_through() -> None:
    """框架不收紧不认识的键 —— 那是工具自己的事。"""
    assert validate(_READ, {"path": "a.py", "whatever": 1}) is None


def test_arguments_that_are_not_an_object_are_reported() -> None:
    """参数整体不是对象时直接说清。"""
    assert validate(_READ, [1, 2]) == "参数必须是一个对象，收到 list"


def test_normalize_turns_values_into_json() -> None:
    """非字符串按 JSON 给模型 —— 那是它读得最准的形式。"""
    assert normalize({"count": 3}) == '{"count": 3}'
    assert normalize("原样") == "原样"
    assert normalize(None) == "(工具没有返回内容)"


def test_truncate_marks_the_original_length() -> None:
    """截断要标注原文总长，模型才好判断要不要换个法子取。"""
    text = truncate("x" * 500, max_chars=100)

    assert len(text) <= 100
    assert "原文共 500 个字符" in text


def test_short_text_is_left_alone() -> None:
    """没超上限就一个字都不动。"""
    assert truncate("短", max_chars=100) == "短"


def test_registering_the_same_name_twice_is_an_error() -> None:
    """同名工具会让模型与排错都无从下手。"""
    catalog = ToolCatalog()

    async def noop(ctx, arguments): ...

    catalog.register(make_tool_declaration("read"), noop)
    with pytest.raises(ValueError, match="工具名重复"):
        catalog.register(make_tool_declaration("read"), noop)

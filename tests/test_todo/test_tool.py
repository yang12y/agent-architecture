"""`todo_write` 的参数校验与返回值。"""

from __future__ import annotations

import pytest

from agent_frame.contracts import TodoItem, TodoStatus, ToolArgumentError
from agent_frame.modules.todo import TODO_TOOL, counts, parse_todos, run


def test_a_clean_list_is_parsed() -> None:
    """trime 之后存进去；状态换成枚举。"""
    todos = parse_todos({"todos": [{"content": "  跑测试 ", "status": "pending"}]})

    assert todos == (TodoItem(content="跑测试", status=TodoStatus.PENDING),)


def test_an_empty_list_clears_the_todos() -> None:
    """空数组是合法的 —— 它表示清空。"""
    assert parse_todos({"todos": []}) == ()


def test_todos_must_be_an_array() -> None:
    """不是数组就报错，别猜模型想干什么。"""
    with pytest.raises(ToolArgumentError):
        parse_todos({"todos": "跑测试"})


def test_a_missing_todos_key_is_an_error() -> None:
    """没给 todos 也是不合法。"""
    with pytest.raises(ToolArgumentError):
        parse_todos({})


def test_each_item_must_be_an_object() -> None:
    """条目必须是对象。"""
    with pytest.raises(ToolArgumentError):
        parse_todos({"todos": ["跑测试"]})


def test_content_must_not_be_blank() -> None:
    """空白内容拒绝：它会让面板出现一行空条目。"""
    with pytest.raises(ToolArgumentError):
        parse_todos({"todos": [{"content": "   ", "status": "pending"}]})


def test_content_must_be_unique() -> None:
    """内容重复就没法按内容认条目了。"""
    with pytest.raises(ToolArgumentError):
        parse_todos(
            {
                "todos": [
                    {"content": "跑测试", "status": "pending"},
                    {"content": "跑测试", "status": "completed"},
                ]
            }
        )


def test_an_unknown_status_is_an_error() -> None:
    """三态之外没有别的状态。"""
    with pytest.raises(ToolArgumentError):
        parse_todos({"todos": [{"content": "跑测试", "status": "doing"}]})


def test_at_most_one_task_may_be_in_progress() -> None:
    """同时只能有一条在跑 —— 两次整表替换并发会互相覆盖，执行顺序也说不清。"""
    with pytest.raises(ToolArgumentError):
        parse_todos(
            {
                "todos": [
                    {"content": "甲", "status": "in_progress"},
                    {"content": "乙", "status": "in_progress"},
                ]
            }
        )


def test_counts_tally_the_three_states() -> None:
    """计数现算，包含零值。"""
    assert counts(
        (
            TodoItem("甲", TodoStatus.PENDING),
            TodoItem("乙", TodoStatus.IN_PROGRESS),
            TodoItem("丙", TodoStatus.COMPLETED),
        )
    ) == {"pending": 1, "in_progress": 1, "completed": 1}


def test_counts_of_an_empty_list_are_all_zero() -> None:
    """清空之后三项都是 0，不是空字典。"""
    assert counts(()) == {"pending": 0, "in_progress": 0, "completed": 0}


async def test_the_tool_returns_only_counts() -> None:
    """给模型的只有计数 —— 清单在他自己的调用参数里。"""
    answer = await run(object(), {"todos": [{"content": "跑测试", "status": "pending"}]})

    assert answer == {"counts": {"pending": 1, "in_progress": 0, "completed": 0}}


def test_the_declaration_is_read_only_and_serial() -> None:
    """只读、幂等：它不碰文件，也不该跟别的调用并跑（整表替换会互相覆盖）。"""
    assert TODO_TOOL.required_permission.value == "read_only"
    assert TODO_TOOL.side_effect is not None
    assert TODO_TOOL.parameters_schema["required"] == ["todos"]

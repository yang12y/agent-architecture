"""装配层的测试：声明怎么变成工具、默认视图、重名。"""

from __future__ import annotations

import pytest

from agent_frame.contracts import (
    CancelToken,
    JsonValue,
    Permission,
    SideEffect,
    ToolContext,
)
from agent_frame.tools.catalog import build, view_of
from agent_frame.tools.params import obj, text
from agent_frame.tools.spec import Call, FetchMode, HttpTool, Page
from fakes.http import FakeTransport


def make_tool(**overrides: object) -> HttpTool:
    """造一条最小可用的声明。

    举例：make_tool(name="demo")
    """
    base: dict[str, object] = {
        "name": "demo",
        "description": "演示工具。",
        "parameters": obj({"name": text("名称")}),
        "path": "/api/demo",
        "fetch": FetchMode.SINGLE,
    }
    base.update(overrides)
    return HttpTool(**base)  # type: ignore[arg-type]


def make_context() -> ToolContext:
    """造一个只够装配层用的执行现场。"""
    return ToolContext(
        workspace="D:/tmp",
        call_id="call_1",
        signal=CancelToken(),
        logger=object(),  # type: ignore[arg-type]
        variables={},
    )


def prepare(_tool: HttpTool, _args: dict[str, JsonValue], _ctx: ToolContext) -> Call:
    """测试用的 prepare：地址固定、头固定。"""
    return Call(
        method="POST",
        url="http://x/api/demo",
        headers={"tenant": "t1"},
        timeout=30.0,
        payload={},
    )


def test_declaration_is_read_only_and_idempotent_by_default() -> None:
    """业务工具都是只读调用：允许并行、不需要幂等键。"""
    declaration, _ = build([make_tool(name="demo")], FakeTransport({}), prepare)[0]

    assert declaration.name == "demo"
    assert declaration.side_effect is SideEffect.NONE
    assert declaration.required_permission is Permission.READ_ONLY
    assert declaration.accepts_idempotency_key is False


def test_duplicate_names_fail_at_assembly() -> None:
    """重名的两个工具会让模型二选一，装配期就炸掉。"""
    with pytest.raises(ValueError, match="demo"):
        build([make_tool(name="demo"), make_tool(name="demo")], FakeTransport({}), prepare)


async def test_runner_projects_records_into_the_default_view() -> None:
    """默认视图：count + 投影后的明细，其余键一律丢。"""
    transport = FakeTransport({"count": 2, "data": [{"deviceName": "H1172", "guid": "g"}]})
    tool = make_tool(
        name="devices",
        list_key="devices",
        fields={"设备名称": "deviceName"},
    )
    _, runner = build([tool], transport, prepare)[0]

    view = await runner(make_context(), {})

    assert view == {"count": 2, "devices": [{"设备名称": "H1172"}]}


async def test_runner_uses_shape_when_given() -> None:
    """有 shape 就完全按它出结果，默认视图不再介入。"""
    transport = FakeTransport({"count": 1, "data": [{"a": 1}]})
    tool = make_tool(shape=lambda page, args: {"total": page.count, "rows": len(page.records)})
    _, runner = build([tool], transport, prepare)[0]

    assert await runner(make_context(), {}) == {"total": 1, "rows": 1}


def test_view_marks_truncated_and_caps_items() -> None:
    """明细超过上限只回前 MAX_ITEMS 行，并标 truncated。"""
    tool = make_tool()
    page = Page(records=[{"a": index} for index in range(80)], count=80, truncated=True)

    view = view_of(page, tool)

    assert view["count"] == 80
    assert len(view["items"]) == 50
    assert view["truncated"] is True

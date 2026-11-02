"""chart_line / chart_bar / chart_pie / chart_box：校验参数、回一句确认。"""

from __future__ import annotations

import pytest

from agent_frame.contracts import (
    CancelToken,
    ChartKind,
    Permission,
    SideEffect,
    ToolArgumentError,
    ToolContext,
)
from agent_frame.infra.observability import NullLogger
from agent_frame.tools import chat_tools

# 一次干净调用的参数。
#
# 举例：_ARGS["x"] == ["08:00", "09:00"]
_ARGS: dict = {
    "title": "出口压力",
    "x": ["08:00", "09:00"],
    "series": [{"name": "压力", "values": [0.62, 0.71]}],
}


# 箱线图的参数：每组给原始样本，分位数由工具算。
#
# 举例：_BOX_ARGS["series"][0]["samples"][0] == [1, 2, 3, 4]
_BOX_ARGS: dict = {
    "title": "修复时长分布",
    "x": ["1#", "2#"],
    "series": [{"name": "修复时长", "samples": [[1, 2, 3, 4], [2, 4, 6, 8, 10]]}],
}


def _ctx(call_id: str = "call_1") -> ToolContext:
    """一次工具现场。画图工具只看 call_id —— 图号由它推出来。

    举例：await _run(ChartKind.LINE, _ctx())
    """
    return ToolContext(
        workspace="D:/proj", call_id=call_id, signal=CancelToken(), logger=NullLogger()
    )


async def _run(kind: ChartKind, ctx: ToolContext, arguments: dict = None):
    """跑一次某个图种的实现。

    举例：await _run(ChartKind.PIE, _ctx(), {"x": [...], "series": [...]})
    """
    runner = chat_tools.make_runner(kind)
    return await runner(ctx, arguments if arguments is not None else _ARGS)


async def test_a_line_answers_with_a_receipt() -> None:
    """回给模型的只有一句确认：图已经画出来了，数据在他自己的参数里。"""
    answer = await _run(ChartKind.LINE, _ctx())

    assert answer == {
        "charted": True,
        "chart_id": "c_call_1",
        "kind": "line",
        "title": "出口压力",
        "points": 2,
        "series": 1,
        # 回执自带一行图位标记：模型把它抄进正文，图就落在那个位置；不抄就不显示。
        "正文位置": "在要插图的地方单独写一行 [[chart:c_call_1]]（不写就不显示）",
    }


async def test_the_chart_id_follows_the_call() -> None:
    """同一调用永远得到同一个图号。"""
    answer = await _run(ChartKind.BAR, _ctx("call_9f"))

    assert answer["chart_id"] == "c_call_9f"
    assert answer["kind"] == "bar"


async def test_a_box_answers_with_per_group_stats() -> None:
    """箱线图除了确认，还回每组统计量：图上看不出 P90，正文要用。"""
    answer = await _run(ChartKind.BOX, _ctx(), _BOX_ARGS)

    assert answer["kind"] == "box"
    assert answer["points"] == 2
    stats = answer["stats"]
    assert isinstance(stats, list) and stats[0]["名称"] == "修复时长"
    group = stats[0]["分组"][0]
    assert (group["标签"], group["样本数"]) == ("1#", 4)
    assert (group["最小值"], group["中位数"], group["最大值"]) == (1.0, 2.5, 4.0)
    assert group["P25"] == 1.75
    assert group["P90"] == 3.7


async def test_a_bad_argument_is_reported_to_the_model() -> None:
    """校验没过就抛错，由执行器收成给模型看的结果。"""
    with pytest.raises(ToolArgumentError):
        await _run(ChartKind.LINE, _ctx(), {**_ARGS, "series": [{"name": "压力", "values": [1.0]}]})


def test_build_returns_four_tools() -> None:
    """四个图种各一个声明，名字固定。"""
    names = [declaration.name for declaration, _ in chat_tools.build()]

    assert names == ["chart_line", "chart_bar", "chart_pie", "chart_box"]


def test_every_declaration_is_read_only_and_harmless() -> None:
    """不碰文件、不产生副作用：写日志与推事件都在位点 8。"""
    for declaration, _ in chat_tools.build():
        assert declaration.required_permission is Permission.READ_ONLY
        assert declaration.side_effect is SideEffect.NONE
        assert declaration.accepts_idempotency_key is False
        assert declaration.scope is None
        assert declaration.parameters_schema["required"] == ["x", "series"]

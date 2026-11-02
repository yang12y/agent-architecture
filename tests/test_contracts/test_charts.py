"""图表的解析规则：工具参数 -> Chart。"""

from __future__ import annotations

import pytest

from agent_frame.contracts import (
    ChartKind,
    ToolArgumentError,
    chart_id_of,
    parse_chart,
)

# 一条干净的折线参数，用例在它上面改一处、验一处。
#
# 举例：_LINE["x"] == ["08:00", "09:00"]
_LINE: dict = {
    "title": "出口压力",
    "x": ["08:00", "09:00"],
    "series": [{"name": "压力", "values": [0.62, 0.71]}],
    "y_title": "MPa",
}


# 一份箱线参数：两组、一条系列，每组给原始样本。
#
# 举例：_BOX["series"][0]["samples"] == [[1, 2, 3, 4], [2, 4, 6, 8, 10]]
_BOX: dict = {
    "title": "修复时长分布",
    "x": ["1#天车", "2#天车"],
    "series": [{"name": "修复时长", "samples": [[1, 2, 3, 4], [2, 4, 6, 8, 10]]}],
}


def _parse(arguments=None, *, kind: ChartKind = ChartKind.LINE):
    """按给定参数解析，工具调用号固定成 call_1。

    举例：_parse() -> Chart(chart_id="c_call_1", ...)
    """
    return parse_chart(
        arguments if arguments is not None else _LINE, tool_call_id="call_1", kind=kind
    )


def test_a_clean_chart_is_parsed() -> None:
    """字段原样收下，数值统一成 float。"""
    chart = _parse()

    assert chart.chart_id == "c_call_1"
    assert chart.kind is ChartKind.LINE
    assert chart.title == "出口压力"
    assert chart.x == ("08:00", "09:00")
    assert chart.series[0].name == "压力"
    assert chart.series[0].values == (0.62, 0.71)
    assert chart.y_title == "MPa"
    assert chart.x_title is None


def test_the_chart_id_comes_from_the_call() -> None:
    """同一调用永远得到同一个图号 —— 重放、重连才认得出是同一幅图。"""
    assert chart_id_of("call_7f3a91") == "c_call_7f3a91"


def test_a_missing_title_falls_back_to_the_kind() -> None:
    """不写标题时按图种给个默认名，不让它空着。"""
    assert _parse({**_LINE, "title": ""}).title == "折线图"
    assert _parse({**_LINE, "title": "  "}, kind=ChartKind.BAR).title == "柱状图"


def test_a_long_title_is_cut() -> None:
    """标题只是给人看的，超了直接截，不必让模型重来一次。"""
    assert len(_parse({**_LINE, "title": "字" * 200}).title) == 80


def test_x_must_be_an_array() -> None:
    """不是数组就报错。"""
    with pytest.raises(ToolArgumentError):
        _parse({**_LINE, "x": "08:00"})


def test_x_needs_at_least_two_points() -> None:
    """一个点画不出趋势，也画不出占比。"""
    with pytest.raises(ToolArgumentError):
        _parse({**_LINE, "x": ["08:00"], "series": [{"name": "压力", "values": [1]}]})


def test_x_is_capped_at_two_hundred_points() -> None:
    """日志是永久的，点数必须封顶。"""
    x = [f"t{index}" for index in range(201)]
    with pytest.raises(ToolArgumentError):
        _parse({**_LINE, "x": x, "series": [{"name": "压力", "values": [1.0] * 201}]})


def test_a_pie_is_capped_at_twenty_slices() -> None:
    """饼图切二十片以上就分不清谁是谁了。"""
    x = [f"类{index}" for index in range(21)]
    with pytest.raises(ToolArgumentError):
        _parse(
            {**_LINE, "x": x, "series": [{"name": "占比", "values": [1.0] * 21}]},
            kind=ChartKind.PIE,
        )


def test_a_blank_label_is_an_error() -> None:
    """空标签在轴上就是一块白。"""
    with pytest.raises(ToolArgumentError):
        _parse({**_LINE, "x": ["08:00", "  "]})


def test_a_long_label_is_an_error() -> None:
    """标签是坐标轴上的事实，超长要报错让模型自己缩。"""
    with pytest.raises(ToolArgumentError):
        _parse({**_LINE, "x": ["08:00", "字" * 41]})


def test_series_must_be_a_non_empty_array() -> None:
    """一条都没有就没什么可画的。"""
    with pytest.raises(ToolArgumentError):
        _parse({**_LINE, "series": []})


def test_a_pie_takes_exactly_one_series() -> None:
    """多条占比没有意义。"""
    two = {
        **_LINE,
        "series": [
            {"name": "甲", "values": [1, 2]},
            {"name": "乙", "values": [3, 4]},
        ],
    }
    with pytest.raises(ToolArgumentError):
        _parse(two, kind=ChartKind.PIE)


def test_a_line_takes_at_most_six_series() -> None:
    """再多就串色、也没法看。"""
    many = {**_LINE, "series": [{"name": f"s{index}", "values": [1, 2]} for index in range(7)]}
    with pytest.raises(ToolArgumentError):
        _parse(many)


def test_series_names_must_be_unique() -> None:
    """图例靠名字区分系列。"""
    same = {
        **_LINE,
        "series": [{"name": "压力", "values": [1, 2]}, {"name": "压力", "values": [3, 4]}],
    }
    with pytest.raises(ToolArgumentError):
        _parse(same)


def test_values_must_match_x() -> None:
    """长度对不上就画不出对应的点。"""
    with pytest.raises(ToolArgumentError):
        _parse({**_LINE, "series": [{"name": "压力", "values": [0.62]}]})


def test_values_must_be_numbers() -> None:
    """字符串、布尔都不是数值 —— True 会被当成 1，那是模型写错了。"""
    with pytest.raises(ToolArgumentError):
        _parse({**_LINE, "series": [{"name": "压力", "values": ["0.62", True]}]})


def test_values_must_be_finite() -> None:
    """NaN / 无穷大写不进 jsonl。"""
    with pytest.raises(ToolArgumentError):
        _parse({**_LINE, "series": [{"name": "压力", "values": [0.62, float("nan")]}]})


def test_axis_titles_are_optional() -> None:
    """不给、给空串都算没写。"""
    chart = _parse({**_LINE, "y_title": "  ", "x_title": None})

    assert chart.x_title is None
    assert chart.y_title is None


def test_a_box_turns_samples_into_five_numbers_per_group() -> None:
    """箱线图收原始样本，摊成 最小值/P25/中位数/P75/最大值，长度是 5 × 格数。"""
    chart = _parse(_BOX, kind=ChartKind.BOX)

    assert chart.kind is ChartKind.BOX
    assert chart.x == ("1#天车", "2#天车")
    assert chart.series[0].values == (1.0, 1.75, 2.5, 3.25, 4.0, 2.0, 4.0, 6.0, 8.0, 10.0)


def test_a_box_accepts_a_single_group() -> None:
    """一格也是一张分布图：单台设备的时长不用硬凑第二组。"""
    chart = _parse(
        {**_BOX, "x": ["1#天车"], "series": [{"name": "修复时长", "samples": [[1, 2, 3, 4]]}]},
        kind=ChartKind.BOX,
    )

    assert chart.x == ("1#天车",)
    assert len(chart.series[0].values) == 5


def test_a_box_needs_one_sample_group_per_label() -> None:
    """组数与 x 对不上就不知道该画在哪一格。"""
    with pytest.raises(ToolArgumentError):
        _parse(
            {**_BOX, "series": [{"name": "修复时长", "samples": [[1, 2, 3]]}]},
            kind=ChartKind.BOX,
        )


def test_a_box_group_needs_at_least_two_samples() -> None:
    """一个数算不出分布。"""
    with pytest.raises(ToolArgumentError):
        _parse(
            {**_BOX, "series": [{"name": "修复时长", "samples": [[1], [1, 2]]}]},
            kind=ChartKind.BOX,
        )


def test_a_box_group_is_capped_at_two_hundred_samples() -> None:
    """每组样本也要封顶：再多就是把明细整列塞进来了。"""
    with pytest.raises(ToolArgumentError):
        _parse(
            {**_BOX, "series": [{"name": "修复时长", "samples": [list(range(201)), [1, 2]]}]},
            kind=ChartKind.BOX,
        )


def test_a_box_rejects_non_numbers() -> None:
    """字符串、布尔都不是样本值。"""
    with pytest.raises(ToolArgumentError):
        _parse(
            {**_BOX, "series": [{"name": "修复时长", "samples": [["1", 2], [1, 2]]}]},
            kind=ChartKind.BOX,
        )


def test_a_box_takes_samples_not_values() -> None:
    """把算好的 values 当 samples 传进来要报错：那是两种数据形状。"""
    with pytest.raises(ToolArgumentError):
        _parse({**_LINE, "x": ["甲", "乙"]}, kind=ChartKind.BOX)


def test_arguments_must_be_an_object() -> None:
    """顶层不是对象直接拒。"""
    with pytest.raises(ToolArgumentError):
        parse_chart(["x"], tool_call_id="call_1", kind=ChartKind.LINE)

"""图表的解析规则：工具参数 -> Chart。

**两个地方要按同一条规则办事**：chart_* 工具校验模型给的参数，位点 8 的 handler 把**同一批**
参数写进日志。两边各写一份的话，迟早出现"工具画得出来、日志里存不下来"。所以规则放 contracts
—— 和 resolve_in_workspace、is_retryable 同一个理由。

上限不是随手定的：每一幅图都会永久躺在会话 jsonl 里，一次几千个点就能把日志撑到几十兆。
"""

from __future__ import annotations

import math
from collections.abc import Mapping

from .errors import ToolArgumentError
from .models import Chart, ChartKind, ChartSeries, JsonValue
from .stats import box_stats

# 四个工具名。handler 靠它认领"这条工具调用画的是一幅图"。
#
# 举例："chart_line" in CHART_TOOL_NAMES -> True
CHART_TOOL_NAMES: frozenset[str] = frozenset({"chart_bar", "chart_box", "chart_line", "chart_pie"})

# 工具名 -> 画哪种图。工具与 handler 都按它认自己那一类。
#
# 举例：KIND_OF_TOOL["chart_pie"] -> ChartKind.PIE
KIND_OF_TOOL: dict[str, ChartKind] = {
    "chart_line": ChartKind.LINE,
    "chart_bar": ChartKind.BAR,
    "chart_pie": ChartKind.PIE,
    "chart_box": ChartKind.BOX,
}

# 类目轴的格数上限 —— 折线与柱状用这个。
#
# 举例：200
CHART_MAX_POINTS = 200

# 饼图最多几片。再多就分不清谁是谁。
#
# 举例：20
CHART_MAX_SLICES = 20

# 一张图最多几条系列。饼图另有一条"恰好一条"的规矩。
#
# 举例：6
CHART_MAX_SERIES = 6

# 箱线图每格摊成几个数：最小值 / P25 / 中位数 / P75 / 最大值。
#
# 举例：BOX_STATS -> 5
BOX_STATS = 5

# 箱线图每组收多少原始样本：太少画不出分布，太多多半是把明细整列塞进来了。
#
# 举例：BOX_MIN_SAMPLES -> 2 / BOX_MAX_SAMPLES -> 200
BOX_MIN_SAMPLES = 2
BOX_MAX_SAMPLES = 200

# 标题与标签的长度上限（字符）。标题超了截断，标签超了报错 —— 标签是坐标轴上的事实。
#
# 举例：80 / 40
CHART_MAX_TITLE = 80
CHART_MAX_LABEL = 40

# 没给标题时用的那句，按图种各说一句。
#
# 举例：CHART_DEFAULT_TITLE[ChartKind.LINE] == "折线图"
CHART_DEFAULT_TITLE: dict[ChartKind, str] = {
    ChartKind.LINE: "折线图",
    ChartKind.BAR: "柱状图",
    ChartKind.PIE: "饼图",
    ChartKind.BOX: "箱线图",
}


def chart_id_of(tool_call_id: str) -> str:
    """由工具调用号推出图的标识。**重放、重连都得到同一个值** —— 前端按它认同一幅图。

    举例：chart_id_of("call_7f3a91") -> "c_call_7f3a91"
    """
    return "c_" + tool_call_id


def parse_chart(arguments: JsonValue, *, tool_call_id: str, kind: ChartKind) -> Chart:
    """把一次工具调用的参数校验成一幅图。工具与 handler 都走这里，结果逐字段相同。

    举例：parse_chart({"x": ["a", "b"], "series": [{"name": "s", "values": [1, 2]}]},
                      tool_call_id="call_1", kind=ChartKind.LINE)
          -> Chart(chart_id="c_call_1", kind=ChartKind.LINE, x=("a", "b"), ...)
    出错：任何一条不合规都抛 ToolArgumentError，正文写清楚该怎么改。
    """
    if not isinstance(arguments, Mapping):
        raise ToolArgumentError("参数必须是一个对象")

    labels = _labels(arguments.get("x"), kind)
    return Chart(
        chart_id=chart_id_of(tool_call_id),
        tool_call_id=tool_call_id,
        kind=kind,
        title=_title(arguments.get("title"), kind),
        x=labels,
        series=(
            _box_series(arguments.get("series"), len(labels))
            if kind is ChartKind.BOX
            else _series(arguments.get("series"), len(labels), kind)
        ),
        x_title=_optional_label(arguments.get("x_title"), "x_title"),
        y_title=_optional_label(arguments.get("y_title"), "y_title"),
    )


def _title(raw: JsonValue, kind: ChartKind) -> str:
    """取标题：没给就用图种的默认名，超长截断（标题短一点不改变图的含义）。

    举例：_title("", ChartKind.PIE) -> "饼图"
    """
    if not isinstance(raw, str) or not raw.strip():
        return CHART_DEFAULT_TITLE[kind]
    return raw.strip()[:CHART_MAX_TITLE]


def _labels(raw: JsonValue, kind: ChartKind) -> tuple[str, ...]:
    """取类目轴：折线/柱状/饼 2 个起步，箱线图 1 个也行；饼图最多 20 片、其余 200 格。

    举例：_labels(["甲", "乙"], ChartKind.BAR) -> ("甲", "乙")
    出错：不是数组、太少、太多、有空串或超长标签时抛 ToolArgumentError。
    """
    if not isinstance(raw, list):
        raise ToolArgumentError("x 必须是数组：一格一个标签，与每条 values 等长")
    limit = CHART_MAX_SLICES if kind is ChartKind.PIE else CHART_MAX_POINTS
    # 箱线图一格就是一组样本：单台设备画一张分布图是合理的，不必硬凑第二格。
    minimum = 1 if kind is ChartKind.BOX else 2
    if len(raw) < minimum:
        raise ToolArgumentError(f"x 至少要有 {minimum} 个标签，收到 {len(raw)} 个")
    if len(raw) > limit:
        raise ToolArgumentError(
            f"x 最多 {limit} 个标签，收到 {len(raw)} 个；先把数据聚合成更少的格子再画"
        )
    return tuple(_text(item, "x 里的标签") for item in raw)


def _series(raw: JsonValue, points: int, kind: ChartKind) -> tuple[ChartSeries, ...]:
    """取系列：至少一条；饼图恰好一条；名字不许重复；每条与 x 等长。

    举例：_series([{"name": "压力", "values": [1, 2]}], 2, ChartKind.LINE)
          -> (ChartSeries(name="压力", values=(1.0, 2.0)),)
    出错：条数不合规、名字重复、数值不是有限数时抛 ToolArgumentError。
    """
    if not isinstance(raw, list) or not raw:
        raise ToolArgumentError('series 必须是非空数组，每项形如 {"name": ..., "values": [...]}')
    if kind is ChartKind.PIE and len(raw) != 1:
        raise ToolArgumentError(f"饼图只画一条系列（收到 {len(raw)} 条）；多项对比请用柱状图")
    if len(raw) > CHART_MAX_SERIES:
        raise ToolArgumentError(f"series 最多 {CHART_MAX_SERIES} 条，收到 {len(raw)} 条")

    series: list[ChartSeries] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            raise ToolArgumentError("series 里每一项都要是对象")
        name = _text(item.get("name"), "series 里的 name")
        if name in seen:
            raise ToolArgumentError(f"系列名重复：{name}")
        seen.add(name)
        series.append(ChartSeries(name=name, values=_values(item.get("values"), points, name)))
    return tuple(series)


def _box_series(raw: JsonValue, points: int) -> tuple[ChartSeries, ...]:
    """取箱线图的系列：每组给一串原始样本，这里算成 5 个数摊平。

    举例：_box_series([{"name": "1#", "samples": [[1, 2, 3], [4, 5, 6]]}], 2)
          -> (ChartSeries(name="1#", values=(1.0, 1.5, 2.0, 2.5, 3.0, 4.0, …)),)
    出错：条数不合规、名字重复、组数或样本数不对、样本不是有限数时抛 ToolArgumentError。
    """
    if not isinstance(raw, list) or not raw:
        raise ToolArgumentError(
            'series 必须是非空数组，每项形如 {"name": ..., "samples": [[数值, …], …]}'
        )
    if len(raw) > CHART_MAX_SERIES:
        raise ToolArgumentError(f"series 最多 {CHART_MAX_SERIES} 条，收到 {len(raw)} 条")

    series: list[ChartSeries] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            raise ToolArgumentError("series 里每一项都要是对象")
        name = _text(item.get("name"), "series 里的 name")
        if name in seen:
            raise ToolArgumentError(f"系列名重复：{name}")
        seen.add(name)
        series.append(ChartSeries(name=name, values=_box_values(item.get("samples"), points, name)))
    return tuple(series)


def _box_values(raw: JsonValue, points: int, name: str) -> tuple[float, ...]:
    """取一条箱线系列的样本，把它们摊成 5 个数一组。

    举例：_box_values([[1, 2]], 1, "1#") -> (1.0, 1.25, 1.5, 1.75, 2.0)
    出错：不是数组、组数与 x 不等、某组样本太少/太多/不是有限数时抛 ToolArgumentError。
    """
    if not isinstance(raw, list):
        raise ToolArgumentError(f"系列 {name} 的 samples 必须是数组：一格一组样本")
    if len(raw) != points:
        raise ToolArgumentError(
            f"系列 {name} 的 samples 有 {len(raw)} 组，x 有 {points} 个标签，两者必须等长"
        )
    values: list[float] = []
    for index, group in enumerate(raw):
        samples = _finite_numbers(group, f"系列 {name} 第 {index + 1} 组的样本")
        if len(samples) < BOX_MIN_SAMPLES:
            raise ToolArgumentError(
                f"系列 {name} 第 {index + 1} 组只有 {len(samples)} 个样本：每组至少 {BOX_MIN_SAMPLES} 个"
            )
        if len(samples) > BOX_MAX_SAMPLES:
            raise ToolArgumentError(
                f"系列 {name} 第 {index + 1} 组有 {len(samples)} 个样本："
                f"每组最多 {BOX_MAX_SAMPLES} 个，先聚合再画"
            )
        stats = box_stats(samples)
        values.extend(
            [
                float(stats["min"]),
                float(stats["p25"]),
                float(stats["p50"]),
                float(stats["p75"]),
                float(stats["max"]),
            ]
        )
    return tuple(values)


def _values(raw: JsonValue, points: int, name: str) -> tuple[float, ...]:
    """取一条系列的数值：每格一个、只能是有限数。

    举例：_values([1, 2.5], 2, "压力") -> (1.0, 2.5)
    出错：不是数组、长度与 x 不等、出现 bool / NaN / 无穷大时抛 ToolArgumentError。
    """
    if not isinstance(raw, list):
        raise ToolArgumentError(f"系列 {name} 的 values 必须是数组")
    if len(raw) != points:
        raise ToolArgumentError(
            f"系列 {name} 的 values 有 {len(raw)} 个数，x 有 {points} 个标签，两者必须等长"
        )
    return tuple(_finite_numbers(raw, f"系列 {name} 的 values "))


def _finite_numbers(raw: JsonValue, what: str) -> list[float]:
    """一串有限数：bool / 非数字 / NaN / 无穷大一律拒收。

    举例：_finite_numbers([1, 2.5], "压力") -> [1.0, 2.5]
    出错：不是数组或出现上述任何一个时抛 ToolArgumentError。
    """
    if not isinstance(raw, list):
        raise ToolArgumentError(f"{what}必须是数组")
    values: list[float] = []
    for value in raw:
        # bool 是 int 的子类，True 会被当成 1 —— 那不是数值，是模型写错了。
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ToolArgumentError(f"{what}里出现了非数字：{value!r}")
        if not math.isfinite(value):
            raise ToolArgumentError(f"{what}里出现了 NaN 或无穷大")
        values.append(float(value))
    return values


def _text(raw: JsonValue, what: str) -> str:
    """取一段非空文本并限长。

    举例：_text("  压力 ", "系列名") -> "压力"
    出错：不是字符串、空串或超长时抛 ToolArgumentError。
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ToolArgumentError(f"{what}必须是非空字符串")
    text = raw.strip()
    if len(text) > CHART_MAX_LABEL:
        raise ToolArgumentError(f"{what}最多 {CHART_MAX_LABEL} 字：{text[:16]}…")
    return text


def _optional_label(raw: JsonValue, what: str) -> str | None:
    """取可选的轴标题：没给、空串都算没写。

    举例：_optional_label(None, "y_title") -> None
    """
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        return None
    return _text(raw, what)

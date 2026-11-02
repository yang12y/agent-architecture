"""chart_line / chart_bar / chart_pie / chart_box：模型把数据画成图。

一次调用只做一件事：**把参数校验成一幅图**，回一句"画好了"。真正把图写进日志、推给界面的
是 modules/charts 在位点 8 的 handler —— 工具不写日志，这条线不破（和 todo_write 一样）。

三个工具共用一份参数 schema，只有描述与图种不同：模型按名字挑图种，比让它填一个 kind 字段准。
"""

from __future__ import annotations

from agent_frame.contracts import (
    ChartKind,
    JsonValue,
    Permission,
    SideEffect,
    ToolContext,
    ToolDeclaration,
    ToolRunner,
    box_stats,
    parse_chart,
)

# 三个工具共用的参数 schema。**只读**：三个声明指向同一份，谁也不改它。
#
# 举例：_PARAMETERS["properties"]["x"]["type"] == "array"
_PARAMETERS: dict[str, JsonValue] = {
    "type": "object",
    "properties": {
        "title": {
            "type": "string",
            "description": "图上面的标题，一句话。超过 80 字会截断；不写就按图种给个默认名。",
        },
        "x": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "类目轴标签，按展示顺序给，一格一个。时刻就写成 08:00 这样的人类可读形式，"
                "由你自己排好序。与每条 values 等长，饼图最多 20 个、其余最多 200 个。"
            ),
        },
        "series": {
            "type": "array",
            "description": "一条或多条系列。饼图只给一条，其余最多 6 条。",
            "items": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "图例上的系列名，同一张图里不许重复。",
                    },
                    "values": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "与 x 一一对应的数值，长度必须与 x 相等，只能是有限数。",
                    },
                },
                "required": ["name", "values"],
            },
        },
        "x_title": {"type": "string", "description": "横轴标题，可选。例如：时刻。"},
        "y_title": {"type": "string", "description": "纵轴标题或单位，可选。例如：MPa。"},
    },
    "required": ["x", "series"],
}

# 箱线图的参数。**和上面那份不共用**：那里一格是一个数，这里一格是一串原始样本 ——
# 分位数由工具算，模型只负责把样本原样递进来。
#
# 举例：_BOX_PARAMETERS["required"] == ["x", "series"]
_BOX_PARAMETERS: dict[str, JsonValue] = {
    "type": "object",
    "properties": {
        "title": {
            "type": "string",
            "description": "图上面的标题，一句话。超过 80 字会截断；不写就按图种给个默认名。",
        },
        "x": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "分组标签，一组一格（比如两台设备、若干周）。2~200 格，与每条 samples 等长，"
                "按展示顺序给，由你自己排好序。"
            ),
        },
        "series": {
            "type": "array",
            "description": "一条或多条系列，最多 6 条。",
            "items": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "图例上的系列名，同一张图里不许重复。",
                    },
                    "samples": {
                        "type": "array",
                        "items": {"type": "array", "items": {"type": "number"}},
                        "description": (
                            "与 x 一一对应：每组给一组**原始数值**（每组 2~200 个）。"
                            "给样本，不要给算好的四分位 —— 分位数由工具算。"
                        ),
                    },
                },
                "required": ["name", "samples"],
            },
        },
        "x_title": {"type": "string", "description": "横轴标题，可选。例如：设备。"},
        "y_title": {"type": "string", "description": "纵轴标题或单位，可选。例如：小时。"},
    },
    "required": ["x", "series"],
}

# 输出契约：回给模型的只有一句确认 —— 图已经在界面上，数据也在它自己的参数里。
#
# 举例："已画图：c_call_7f3a91（折线，3 个点，1 条系列）"
_OUTPUT = (
    "一句确认：图号、图种、几个点、几条系列，外加一行「正文位置」标记。"
    "把标记写进正文，图就落在那一行的位置；不写就挂在回答末尾。正文里不要再用表格重复同一批数据。"
)

# 折线图：看趋势。
#
# 举例：LINE_TOOL.name == "chart_line"
LINE_TOOL = ToolDeclaration(
    name="chart_line",
    description=(
        "把一组数值画成折线图，用来**看趋势**（x 通常是时刻，按时间先排好序）。"
        "同一批类目下可以给多条系列做对比。数据必须已经在手上：本工具不取数，只负责画；"
        "超过 200 个点先聚合，不要为了画图去估数。画完正文里不必再贴一遍同样的数字。"
    ),
    parameters_schema=_PARAMETERS,
    output_contract=_OUTPUT,
    # 工具的副作用只是"校验参数"；写日志与推送都在位点 8，所以是 none。
    side_effect=SideEffect.NONE,
    accepts_idempotency_key=False,
    required_permission=Permission.READ_ONLY,
    scope=None,
)

# 柱状图：比大小。
#
# 举例：BAR_TOOL.name == "chart_bar"
BAR_TOOL = ToolDeclaration(
    name="chart_bar",
    description=(
        "把一组数值画成柱状图，用来**比较不同类别的大小**（x 是类别，比如各台设备、各区段）。"
        "类别控制在 30 个以内最好读，多了先排序取前几名。多系列会并排画，最多 6 条。"
    ),
    parameters_schema=_PARAMETERS,
    output_contract=_OUTPUT,
    side_effect=SideEffect.NONE,
    accepts_idempotency_key=False,
    required_permission=Permission.READ_ONLY,
    scope=None,
)

# 饼图：看占比。
#
# 举例：PIE_TOOL.name == "chart_pie"
PIE_TOOL = ToolDeclaration(
    name="chart_pie",
    description=(
        "把一组数值画成饼图，用来**看占比**。只画一条系列，多条占比没有意义；"
        "最多 20 片，多出来的先并成「其它」。数值不必自己算百分比，界面会算。"
    ),
    parameters_schema=_PARAMETERS,
    output_contract=_OUTPUT,
    side_effect=SideEffect.NONE,
    accepts_idempotency_key=False,
    required_permission=Permission.READ_ONLY,
    scope=None,
)


# 箱线图：看分布。**每组给原始样本**，分位数由这里算。
#
# 举例：BOX_TOOL.name == "chart_box"
BOX_TOOL = ToolDeclaration(
    name="chart_box",
    description=(
        "把每组原始数值画成箱线图，用来**比较分布的离散程度**：箱体是 P25~P75、中间横线是中位数、"
        "须线到最小值与最大值。每组给一组**原始样本**（一组一串数），分位数由工具算，不要自己算好再传。"
        "须线不剔异常点；某组少于 5 个样本时箱体意义有限，结论要谨慎。"
    ),
    parameters_schema=_BOX_PARAMETERS,
    output_contract=(
        "一句确认 + 每组统计量（样本数 / 最小值 / P25 / 中位数 / P75 / P90 / 最大值）。"
        "图已经画在界面上，正文里不要再用表格重复同一批数字。"
    ),
    side_effect=SideEffect.NONE,
    accepts_idempotency_key=False,
    required_permission=Permission.READ_ONLY,
    scope=None,
)


def box_report(arguments: JsonValue, labels: tuple[str, ...]) -> list[dict[str, JsonValue]]:
    """把校验过的样本摊成每组一句统计量（中文键，与其它工具输出一致）。

    分位数在回执里再给一份：模型据此写「P50 2.2h 对 1.1h」，不必自己算。

    举例：box_report({"series": [{"name": "1#", "samples": [[1, 2, 3]]}]}, ("1#",))
          -> [{"名称": "1#", "分组": [{"标签": "1#", "样本数": 3, "中位数": 2.0, …}]}]
    """
    raw = arguments.get("series") if isinstance(arguments, dict) else None
    series = raw if isinstance(raw, list) else []
    rows: list[dict[str, JsonValue]] = []
    for item in series:
        if not isinstance(item, dict):
            continue
        samples = item.get("samples")
        if not isinstance(samples, list):
            continue
        groups: list[dict[str, JsonValue]] = []
        for label, values in zip(labels, samples, strict=False):
            if not isinstance(values, list):
                continue
            stats = box_stats([float(value) for value in values])
            groups.append(
                {
                    "标签": label,
                    "样本数": stats["n"],
                    "最小值": round(float(stats["min"]), 4),
                    "P25": round(float(stats["p25"]), 4),
                    "中位数": round(float(stats["p50"]), 4),
                    "P75": round(float(stats["p75"]), 4),
                    "P90": round(float(stats["p90"]), 4),
                    "最大值": round(float(stats["max"]), 4),
                }
            )
        rows.append({"名称": item.get("name"), "分组": groups})
    return rows


def make_runner(kind: ChartKind) -> ToolRunner:
    """造一个画图的实现：校验参数，回一句确认。

    校验走 contracts 的 parse_chart —— 位点 8 的 handler 用的是同一条规则，两边不会走岔。

    举例：run = make_runner(ChartKind.LINE)
          await run(ctx, {"x": ["a", "b"], "series": [{"name": "s", "values": [1, 2]}]})
          -> {"charted": True, "chart_id": "c_call_1", "kind": "line", "points": 2,
              "series": 1, "正文位置": "…[[chart:c_call_1]]（不写就不显示）"}
    出错：参数不合规时抛 ToolArgumentError，由执行器收成给模型看的结果。
    """

    async def run(ctx: ToolContext, arguments: JsonValue) -> JsonValue:
        chart = parse_chart(arguments, tool_call_id=ctx.call_id, kind=kind)
        answer: dict[str, JsonValue] = {
            "charted": True,
            "chart_id": chart.chart_id,
            "kind": chart.kind.value,
            "title": chart.title,
            "points": len(chart.x),
            "series": len(chart.series),
            # 图**只**出现在这行标记的位置：不写标记就不显示（见 src/web/js/view.js）。
            "正文位置": f"在要插图的地方单独写一行 [[chart:{chart.chart_id}]]（不写就不显示）",
        }
        if kind is ChartKind.BOX:
            # 箱线图多回一份统计量：图上看不出 P90，正文又要用到。
            answer["stats"] = box_report(arguments, chart.x)
        return answer

    return run


def build() -> tuple[tuple[ToolDeclaration, ToolRunner], ...]:
    """装配入口：四个声明各配一个实现。装配根拿它往工具表里登记。

    举例：for declaration, runner in build(): tools.register(declaration, runner)
    """
    return (
        (LINE_TOOL, make_runner(ChartKind.LINE)),
        (BAR_TOOL, make_runner(ChartKind.BAR)),
        (PIE_TOOL, make_runner(ChartKind.PIE)),
        (BOX_TOOL, make_runner(ChartKind.BOX)),
    )

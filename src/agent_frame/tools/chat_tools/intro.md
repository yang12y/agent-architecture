# tools/chat_tools

## 1. 作用

直接产出聊天界面上的东西的工具 —— 现在是四个画图工具（折线 / 柱状 / 饼图 / 箱线）。
这里只放**把数据画出来**的工具。

工具只做一件事：拿 `contracts/charts.py` 的 `parse_chart` 校验参数，回一句确认。
把图写进日志、推给界面是 `modules/charts` 在位点 8 的事 —— 工具不写日志，这条线不破。

## 2. 文件

| 文件 | 作用 |
| --- | --- |
| `__init__.py` | 包标记，对外转出四个声明与 `build()`。 |
| `chart.py` | 四个 `ToolDeclaration`（折线 / 柱状 / 饼图共用一份参数 schema，箱线图单独一份：一格一串原始样本）+ `make_runner(kind)`；`build()` 是把它们配成 `(声明, 实现)` 的装配入口。 |

## 3. 与其它模块的交互

- **本模块调用** `contracts`：`parse_chart`、`ChartKind`、`ToolDeclaration`、`ToolContext`。
- **本模块不碰** infra / modules / api —— 它只依赖 contracts。
- **被调用**：`composition` 在装配时 `for declaration, runner in build(): tools.register(...)`。
- **下游**：`modules/charts` 的位点 8 handler 用同一个 `parse_chart` 把参数落成日志条目。

## 4. 为什么是四个工具而不是一个

模型按名字挑图种比填一个 `kind` 字段准；每个名字还能挂类型专属的规矩
（饼图只收一条系列、最多 20 片）。代价是每次请求多约 1 KB 的 schema。

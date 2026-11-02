# modules/charts

## 1. 作用

把模型画图的那一次工具调用落成一条 `chart` 条目，并把同一幅图广播给界面。

分工和待办一样：**工具只校验参数**（见 `tools/chat_tools`），**写日志与发事件在位点 8**。
这样"事实只写一次"，而且事件能带上 `entry_id` / `entry_seq`，前端按它去重与定位。

## 2. 文件

| 文件 | 作用 |
| --- | --- |
| `__init__.py` | 包标记，对外转出 `PRIORITY_CHARTS` 与 `build_handlers`。 |
| `handler.py` | 位点 8 的 `_ChartHandler`：按 `KIND_OF_TOOL` 认出 `chart_*`（折线 / 柱状 / 饼图 / 箱线）的成功调用，用 `parse_chart` 把参数校验成一幅图，追加 `CHART` 条目，再发 `ChartRendered`。priority 是 **405** —— 早于 session 写工具结果消息（410）。 |

## 3. 与其它模块的交互

- **本模块调用** `contracts`：`KIND_OF_TOOL`、`parse_chart`、`ChartEntryPayload`、`ChartRendered`。
- **本模块调用** `ctx.session.append_entry`（写日志）与 `ctx.ports.events.publish`（发事件）。
- **被调用**：
  - `composition` 把 `build_handlers()` 挂进管线。
  - `api/sse` 订阅它发的 `ChartRendered`，转成 `chart_rendered` 帧。
  - `api/routes/sessions.py` 读日志里的 `CHART` 条目，拼进会话详情的历史。

## 4. 不做

- 不进上下文：投影、压缩、恢复都不认 `CHART` 条目。
- 不校验参数：规则只有一份，在 `contracts/charts.py`。
- 只有类目轴与数值：`Chart.signal`（base64 float32）那一套留给采样信号，模型给的参数
  走 `series`/`labels`，两者不混。

## 5. 为什么排在工具结果之前

图先落盘、工具结果后写。崩在这两步之间，"有图没结果"重跑一次工具就补上了；
反过来"有结果没图"，下一轮不会再跑那次调用，图就永远没了。

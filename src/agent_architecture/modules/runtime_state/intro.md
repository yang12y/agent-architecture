# modules/runtime_state

## 1. 作用

维度 9：Runtime State —— 一次 trace 的执行态，以及它在各个位点上的读写。

## 2. 文件

| 文件 | 作用 |
| --- | --- |
| `__init__.py` | 包标记。 |
| `bootstrap.py` | `bootstrap_runtime_state`：位点 1 由内存 Session 与冻结配置建出这次 trace 的执行态。 |
| `handler.py` | 各点位上的 SiteHandler 注册：首位、中段、末位几档优先级。 |
| `pending_queue.py` | 中途消息队列：入队 `enqueue_message`、取出 `drain_messages`。 |
| `tool_stats.py` | 本轮工具成功 / 失败的聚合 `record_success` / `record_failure`。 |

## 3. 与其它模块的交互

- **本模块调用** `contracts`：`RuntimeState`、`FrozenConfig`、`Message`、`ToolStat`。
- **本模块调用自己的子包** `modules/runtime_state/ops`：一个位点一个操作，`handler.py` 把它们注册到对应位点。
- **本模块是执行态的唯一写者**：其它维度模块经 `ctx.state` 读它算出的结果（上下文占用、游标、本轮工具统计）。
- **被调用**：`modules/agent_loop` 在位点 1 调 `bootstrap_runtime_state`；`composition` 注册 `handler.build_handlers` 的结果。

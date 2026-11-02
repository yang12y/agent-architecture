# modules/runtime_state/ops

## 1. 作用

各个位点各自的执行态操作，一个位点一个文件，每个文件只改 `ctx.state`。

## 2. 文件

| 文件 | 作用 |
| --- | --- |
| `__init__.py` | 包标记。 |
| `s01_trace_before.py` | 位点 1：把执行态对齐到"这次 trace 刚开始"，绑游标与上下文上限。 |
| `s02_turn_before.py` | 位点 2：开新的一轮 —— 轮数加一、重试计数归零、清空本轮工具统计。 |
| `s03_llm_before.py` | 位点 3：算这次请求实际占用多少上下文（含工具 schema 那部分）。 |
| `s04_llm_during.py` | 位点 4：记下这是本 turn 的第几次模型尝试。 |
| `s05_llm_after.py` | 位点 5：收下模型这一轮的产出，并消费中途消息。 |
| `s06_tool_before.py` | 位点 6：把不执行的调用从待执行队列里摘掉。 |
| `s07_tool_during.py` | 位点 7：把这条调用标记成执行中。 |
| `s08_tool_after.py` | 位点 8：记一次统计、摘下这条调用、消费中途消息。 |
| `s10_trace_end.py` | 位点 10：释放执行态。 |

## 3. 与其它模块的交互

- **本模块调用** `contracts`：各位点 payload 类型与 `RuntimeState`、`ToolCallStatus`、`ToolDecision`。
- **本模块调用父包** `modules/runtime_state`：`pending_queue.drain_messages`、`tool_stats.record_success` / `record_failure`。
- **本模块读同一份 payload**：例如位点 6 的 `payload.decisions`，由 `modules/sandbox` 与 `modules/tool_calling` 写在同一个结构里。
- **被调用**：`modules/runtime_state/handler.py` 逐个 `apply` 把它们注册到对应位点。

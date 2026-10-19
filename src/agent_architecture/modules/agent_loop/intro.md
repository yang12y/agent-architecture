# modules/agent_loop

## 1. 作用

维度 1：内核主循环。把一条用户消息跑成一次 trace，并在每个位点上按优先级驱动所有模块的 handler。

## 2. 文件

| 文件 | 作用 |
| --- | --- |
| `__init__.py` | 包标记。 |
| `agent.py` | 对外门面 `Agent`：收一条用户消息，冻结配置、建 trace 现场、交给 runner 跑完。 |
| `pipeline.py` | `SitePipeline` / `HandlerRegistry`：在单一位点上按优先级跑完注册的 handler，处理否决与短路。 |
| `termination.py` | 位点 9 之后的终止判定：继续下一轮、正常结束，或按预算 / 取消收尾。 |
| `trace_runner.py` | 一次 trace 的主循环：位点 1 → while{位点 2..9} → 位点 10，管预算、取消、异常与收尾。 |
| `turn_runner.py` | 一轮 turn：位点 2/3/4/5 跑模型，再按计划跑位点 6/7/8 执行工具，最后位点 9。 |

## 3. 与其它模块的交互

- **本模块调用** `contracts`：`Site`、各位点 payload、`Handler`、`Ports`、`SiteAbort` 等，主循环只认这些形状。
- **本模块调用** `infra/config`：`freeze_config` 在 trace 开始时冻结配置。
- **本模块调用** `modules/runtime_state`：`bootstrap_runtime_state` 在位点 1 建出这次 trace 的执行态。
- **本模块驱动**全部其它维度模块：它不 import 它们，只通过 `SitePipeline` 在位点上调用它们注册的 handler —— 这里同时也是所有维度模块唯一相遇的地方。
- **被调用**：`composition` 建 `Agent`、`HandlerRegistry`、`SitePipeline` 并把各模块的 handler 注册进来；`api` 经 `Container` 用 `Agent` 触发一次 trace。

# modules/checkpoint

## 1. 作用

D6 Checkpoint：决定日志什么时候必须落盘，并在崩溃后把耐久日志修成一份完整会话。

## 2. 文件

| 文件 | 作用 |
| --- | --- |
| `__init__.py` | 包标记。 |
| `barriers.py` | 落盘屏障：位点 3 / 5 / 6 / 8 以及收尾的位点 10，过这一点之前的日志必须已经在盘上。 |
| `crash.py` | 崩溃现场判定：把日志的尾部归成补全、重试、丢弃等情形。 |
| `handler.py` | 各点位上的 SiteHandler：位点 1 的恢复（恢复带）与各位点的屏障（屏障带）。 |
| `rebalance.py` | 语义配平：把崩溃留下的悬空工具调用补成完整条目（成功结果或失败结果）。 |
| `recovery.py` | 恢复：`plan` 算出要补哪些条目，`apply` 把它们写回日志。 |
| `scheduler.py` | `Checkpoint`：落盘调度，记着哪些日志已经耐久、谁在什么时候必须落。 |

## 3. 与其它模块的交互

- **本模块调用** `contracts`：`SessionStore`、`SessionHandle`、`SessionEntry`、`ToolRegistry`、`is_retryable` 等协议与类型。
- **经 `ctx.session`**（`SessionHandle`，由 `modules/session` 实现）读写条目；**经构造时注入的 `SessionStore`**（由 `infra/storage` 实现）把日志推到盘上。
- **本模块不 import 其它维度模块**：工具声明从 `ToolRegistry` 端口取，不直接问 `modules/tool_calling`。
- **被调用**：`composition` 建 `Checkpoint` 并把 `handler.build_handlers` 的结果注册到位点 1 / 3 / 5 / 6 / 8 / 10。

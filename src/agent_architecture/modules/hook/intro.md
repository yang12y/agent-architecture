# modules/hook

## 1. 作用

D7 Hook：在固定位点注入用户逻辑的机制 —— 注册、匹配、按事件执行。

## 2. 文件

| 文件 | 作用 |
| --- | --- |
| `__init__.py` | 包标记。 |
| `events.py` | 八个位点对应的事件名，以及位点到事件的映射。 |
| `handler.py` | hook 的 SiteHandler 注册：落在 hook 带（500–899），八个位点各一个。 |
| `registry.py` | Hook 的注册与匹配：`HookSource`、`Hook`、`HookRegistry`。 |
| `runner.py` | `run_hooks`：把选中的 hook 跑起来，并处理它的否决。 |

## 3. 与其它模块的交互

- **本模块调用** `contracts`：`Site`、`Handler`、`REJECTION_SITES`、`AgentError`。
- **本模块返回值即控制**：在拒绝位点上返回 `False`，由 `modules/agent_loop` 的管线判定为否决，这次 trace 不再往下走。
- **本模块不 import 其它维度模块**：位点数据一律从 `SiteContext` 取。
- **被调用**：`composition` 建 `HookRegistry` 并把 `handler.build_handlers` 的结果注册到八个位点。

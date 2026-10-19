# modules/tool_calling

## 1. 作用

D2 Tool Calling：工具注册、位点 6 的检查链与调度、位点 7 的执行、位点 8 的结果回填。

## 2. 文件

| 文件 | 作用 |
| --- | --- |
| `__init__.py` | 包标记。 |
| `chain.py` | 位点 6 的两段：`screen` 检查链与 `finalize` 收尾，把调用判成执行或跳过。 |
| `checks.py` | 检查链的前三步：存在性、补默认、参数校验。 |
| `executor.py` | 位点 7：跑一个工具，带超时与按规则的重试。 |
| `handler.py` | 各位点上的 SiteHandler 注册：位点 3 填工具 schema、6 检查、7 执行、8 回填。 |
| `output.py` | 位点 8 的输出处理：规范化 + 超限截断。 |
| `registry.py` | `ToolCatalog`：声明与实现放在一起，对外只露声明。 |
| `scheduling.py` | 位点 6 的并行 / 串行编排：把一批调用切成若干「波」。 |

## 3. 与其它模块的交互

- **本模块调用** `contracts`：`ToolDeclaration`、`ToolDecision`、`ExecutionPlan`、`ToolRegistry`、`is_retryable` 等。
- **本模块调用自己的子包** `modules/tool_calling/builtin`：`build_catalog` 给出内置工具集。
- **本模块写 `payload.tools`**（位点 3）：`modules/agent_loop` 据此发请求。
- **本模块与 `modules/sandbox` 共用 `payload.decisions`**：它先标可执行，sandbox 再按权限改写，位点 7 / 8 只认最终那份。
- **本模块经 `ctx.session`** 追加工具调用与工具结果条目，并发 `ToolCallStart` / `ToolResult` 事件。
- **被调用**：`composition` 建工具目录并把 `handler.build_handlers` 的结果注册到位点 3 / 6 / 7 / 8。

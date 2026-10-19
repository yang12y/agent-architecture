# modules/sandbox

## 1. 作用

D8 沙箱与权限：工具执行前的安全边界 —— 按资源与参数判定、必要时转人工审批、把结论落到执行计划上。

## 2. 文件

| 文件 | 作用 |
| --- | --- |
| `__init__.py` | 包标记。 |
| `approval.py` | 工具级 + 参数级审批：算出放行 / 询问 / 拒绝三态中的一态。 |
| `enforcement.py` | 把判定写进 `payload.decisions` —— 这就是"执行前强制"。 |
| `handler.py` | 位点 6 的 SiteHandler 注册。 |
| `permission.py` | `Permission` 三档的排序与取严。 |
| `policies.py` | 资源分类与三类策略（纯函数）：路由、路径解析、是否越界。 |

## 3. 与其它模块的交互

- **本模块调用** `contracts`：`Permission`、`ToolBeforePayload`、`ToolDecision`、`ApprovalDecision`、`ApprovalRequired`、`resolve_in_workspace`。
- **本模块写 `payload.decisions`**：`modules/tool_calling` 与 `modules/session` 读同一份决定，据此跳过不执行的调用。
- **本模块经 `ctx.ports.approvals`** 挂起等人工答复（端口由 `infra/approvals` 实现），并发 `ApprovalRequired` 事件经事件总线到 `api/sse`，答复由 `api/routes/approvals.py` 送回同一个端口。
- **本模块不 import 其它维度模块**：工具声明从 `ToolDeclaration` 取，不直接问 `modules/tool_calling`。
- **被调用**：`composition` 把 `handler.build_handlers` 的结果注册到位点 6。

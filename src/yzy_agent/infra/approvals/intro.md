# infra/approvals

## 1. 作用

人工审批的进程内实现：把「等用户点一下」落成一个挂起的 future，列表与回执都从这里过。

## 2. 文件

| 文件 | 作用 |
| --- | --- |
| `__init__.py` | 包标记，并对外转出 `ApprovalGate`。 |
| `gate.py` | `ApprovalGate`：登记待审批请求、`await` 一个 future 等答复、按 id 回执并唤醒等待方、按会话列出尚未答复的请求。 |

## 3. 与其它模块的交互

- **本模块调用** `contracts`：`ApprovalRequest`、`ApprovalResult`，并实现 `ApprovalProvider` 协议。
- **被调用**：
  - `composition` 建唯一实例，放进 `Container` 与 `Ports.approvals`。
  - `modules/sandbox` 经 `ctx.ports.approvals.request(...)` 挂起等审批。
  - `api/routes/approvals.py` 经 `container.approvals.pending(...)` / `.respond(...)` 列请求与交回执。

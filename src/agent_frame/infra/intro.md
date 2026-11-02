# infra

## 1. 作用

L2 基础设施层：只放端口的实现，把契约里的协议落到真实的时间、标识、网络、磁盘与日志上。

## 2. 文件

| 文件 | 作用 |
| --- | --- |
| `__init__.py` | 包标记，声明本层职责；具体实现都在子包里，由 `composition` 按端口挑选。 |
| `pending.py` | 待办表 `PendingTable`：审批与提问共用的挂起表，登记请求、`await` 回执、按会话列、按 id 唤醒（不落盘）。 |
| `presence.py` | `Presence`：谁在看哪个会话。SSE 流连上/断开时改它，提问前由它判「有没有人能答」。 |

## 3. 与其它模块的交互

- 本模块自身没有代码，交互都发生在子包里（见各子包的 intro.md）。
- **被调用**：`composition` 从 `infra/approvals`、`infra/config`、`infra/llm`、`infra/observability`、`infra/questions`、`infra/storage`、`infra/system` 取实现，装成端口交给上面；`api/sse/hub` 往 `Presence` 里记在线，`infra/questions` 与 `infra/approvals` 共用 `pending.PendingTable`。 |

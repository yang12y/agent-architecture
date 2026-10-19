# api/routes

## 1. 作用

各 HTTP 端点：会话的建、列、查、删，发消息与中断，待审批列表与回执，健康检查。

## 2. 文件

| 文件 | 作用 |
| --- | --- |
| `__init__.py` | 包标记。 |
| `approvals.py` | `GET /approvals` 列某会话待审批的请求、`POST /approvals/{request_id}` 交回执。 |
| `chat.py` | `POST /sessions/{id}/messages` 收一条用户消息并触发一次 trace、`POST /sessions/{id}/interrupt` 打断正在跑的那次。 |
| `health.py` | `GET /health` 健康检查。 |
| `sessions.py` | `POST /sessions` 建、`GET /sessions` 列、`GET /sessions/{id}` 查（消息按游标分页、统计现数）、`DELETE /sessions/{id}` 删。 |

## 3. 与其它模块的交互

- **本模块调用** 上层 `api`：`deps.get_container`、`errors` 里的异常构造、`schemas` 里的 DTO 与 `page_messages`、`stats.session_stats`。
- **本模块调用** `composition`：用 `Container` 取会话注册表、审批口、发消息与中断的入口。
- **本模块调用** `contracts`：`Message`、`MessageRole`、`CancelToken`、`ApprovalResult`。
- **被调用**：只被 `api/app.py` 逐个 `include_router` 挂载，没有别的模块导入它。

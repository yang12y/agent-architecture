# api

## 1. 作用

L4 接入层：把 HTTP 一元调用与 SSE 流翻译成对装配根 Container 的调用，是整个系统与外界之间唯一的出入口。

## 2. 文件

| 文件 | 作用 |
| --- | --- |
| `__init__.py` | 包标记，声明本层职责。 |
| `app.py` | 装配 FastAPI 应用：挂载各组路由、登记统一错误处理、最后挂前端静态目录（每次都带 `Cache-Control: no-cache`）。 |
| `deps.py` | 依赖注入：路由通过它从 `app.state` 取进程级唯一的 Container。 |
| `errors.py` | 接口层异常 `ApiError` 与几个构造函数（会话不存在、审批不存在、会话忙），路由抛它，由 `app.py` 的处理器转成统一错误体。 |
| `main.py` | ASGI 入口：在模块级建 Container 与 app，供 `uvicorn yzy_agent.api.main:app` 启动。 |
| `schemas.py` | 对外 DTO：请求与响应的形状、`Message` 与 DTO 的互转、消息分页函数 `page_messages`。 |
| `stats.py` | 会话统计：从日志条目现算各项数字，不另存计数器。 |

## 3. 与其它模块的交互

- **本模块调用** `composition`：`build_container` 建装配根，`Container` 是全部能力的入口，`LiveSession` 是执行中会话的形状。
- **本模块调用** `contracts`：DTO 字段一律用契约层类型（`Message`、`SessionEntry`、`Permission`、`SessionMeta` 等）。
- **本模块调用子包**：`api/routes`（挂 HTTP 路由）、`api/sse`（挂 `/events`）。
- **被调用**：没有外部模块导入 `api`；只有它自己的子包 `api/routes`、`api/sse` 反过来用它的 `deps`、`errors`、`schemas`、`stats`。

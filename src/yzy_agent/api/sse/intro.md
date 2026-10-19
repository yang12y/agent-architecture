# api/sse

## 1. 作用

SSE 传输层：一条长连接多路复用，把事件总线上的会话事件按会话推给浏览器，并负责心跳与断连收尾。

## 2. 文件

| 文件 | 作用 |
| --- | --- |
| `__init__.py` | 包标记。 |
| `endpoint.py` | `GET /events`：校验会话归属、先写 `ready`、逐会话写 `subscribed`、循环从出队取帧、空闲写心跳、每轮查一次断连。 |
| `frames.py` | 帧的类型与文本形：`ready` / `subscribed` / `event` / `error` 四种帧、心跳注释行 `PING`、`to_text`。 |
| `hub.py` | 订阅登记表：谁在听哪个会话，把事件总线来的事件装进各连接的出队，溢出就标记并收尾。 |

## 3. 与其它模块的交互

- **本模块调用** 上层 `api`：`errors.session_not_found`、`schemas.from_parts`（把契约会话类型翻成 DTO）。
- **本模块调用** `composition`：`Container` 提供会话表、端口与 id 生成。
- **本模块调用** `contracts`：订阅 `EventBus`，并把 `TraceStart`、`AssistantDelta`、`ToolResult`、`ApprovalRequired` 等契约事件装成帧。
- **被调用**：由 `api/app.py` 挂载 `/events` 并在启动时建一个 `Hub` 挂在 `app.state` 上。

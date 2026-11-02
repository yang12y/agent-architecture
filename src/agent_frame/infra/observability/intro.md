# infra/observability

## 1. 作用

日志与事件通道的实现：结构化日志，以及进程内事件总线。

## 2. 文件

| 文件 | 作用 |
| --- | --- |
| `__init__.py` | 包标记，并对外转出 `InProcessEventBus`、`StdLogger`。 |
| `events.py` | `InProcessEventBus`：进程内的发布/订阅，SSE 层从这里取事件。 |
| `logging.py` | `StdLogger`（按级别输出结构化键值）、`NullLogger`（丢弃）、`RecordingLogger`（留在内存里，供测试断言）。 |

## 3. 与其它模块的交互

- **本模块调用** `contracts`：`Logger`、`EventBus` 协议与事件类型，实现这两个端口。
- **被调用**：
  - `composition` 建 `StdLogger` 与 `InProcessEventBus`，放进 `Ports`。
  - `modules/*` 经 `ctx.ports.logger` / `ctx.ports.events` 打日志、发事件。
  - `api/sse.hub` 订阅 `EventBus`，把事件转成帧推给浏览器。

# infra/system

## 1. 作用

底层能力的实现：时间、标识、token 计数三个端口的具体实现。

## 2. 文件

| 文件 | 作用 |
| --- | --- |
| `__init__.py` | 包标记，并对外转出 `SystemClock`、`UuidGenerator`、`HeuristicTokenCounter`。 |
| `clock.py` | `SystemClock`：取当前 UTC 时间戳。 |
| `ids.py` | `UuidGenerator`：生成各类 id（trace / session / entry / 工具调用等）。 |
| `tokens.py` | `HeuristicTokenCounter`：启发式 token 估算（CJK 一字一 token、其它 4 字符一 token，每条消息另加固定开销）。 |

## 3. 与其它模块的交互

- **本模块调用** `contracts`：`Message`、`TextPart`、`ThinkingPart`、`ToolCallPart`，并实现 `Clock`、`IdGenerator`、`TokenCounter` 三个端口。
- **被调用**：
  - `composition` 建三个实例放进 `Ports`。
  - `modules/session` 经 `ctx.ports.ids` 生成 id、经 `ctx.ports.clock` 取时间。
  - `modules/compression` 经 `ctx.ports.tokens` 算上下文占用。

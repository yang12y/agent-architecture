# modules/session

## 1. 作用

维度 4：Session —— 只追加的会话日志、上下文投影、草稿槽与游标。

## 2. 文件

| 文件 | 作用 |
| --- | --- |
| `__init__.py` | 包标记。 |
| `cursor.py` | 上下文游标：投影从哪一条开始，压缩后如何前移与合并。 |
| `draft_slot.py` | 会话级草稿槽：模型流式输出的唯一落脚点。 |
| `handler.py` | 各点位上的 SiteHandler：写用户消息与 trace_start、算投影、收模型产出、写工具结果、收尾。 |
| `log.py` | `SessionLog`：只追加的条目日志。 |
| `projection.py` | 上下文投影：从日志算出这次要发给模型的 messages（含摘要与草稿）。 |
| `reader.py` | 只读的会话视图：对一段 entries 做查询，不写回。 |
| `session.py` | 内存 `Session`：meta + entries + 物化上下文 + 游标 + 草稿槽。 |
| `title.py` | 会话标题：从第一条用户消息里截一段。 |

## 3. 与其它模块的交互

- **本模块调用** `contracts`：`SessionMeta`、`SessionEntry`、`Message`、`TokenAnchor`、`Draft` 等。
- **本模块实现 `SessionHandle` 端口**：其它所有维度模块都经 `ctx.session` 追加条目、读投影、访问草稿槽。
- **本模块发会话事件**：经 `ctx.ports.events` 把会话条目变化推给事件总线，最终到 `api/sse`。
- **本模块读 `payload.decisions`**：位点 8 只回填真正执行了的调用。
- **被调用**：`composition` 建 `Session`，包成 `LiveSession` 放进会话注册表交给 `api`。

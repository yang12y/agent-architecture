# modules/todo

## 1. 作用

模型用 `todo_write` 整份替换的待办清单：清单落进日志、推给前端 —— **不进上下文**。

清单跟着**一次任务**走：位点 1（新一轮开始）把它清掉，所以用户问下一件事时，上一件的
「进行中」不会还挂在那里。清空也是写一条**空清单**条目 —— 日志只追加，读的一侧认的永远是最后一条。

## 2. 文件

| 文件 | 作用 |
| --- | --- |
| `__init__.py` | 包标记，并对外转出工具声明、校验、计数与装配入口。 |
| `tool.py` | `todo_write` 的声明与参数校验：整份列表、三态、至多一条进行中；工具只回计数。 |
| `handler.py` | 位点 1：新一轮开始清空上一轮的清单；位点 8：把 `todo_write` 的清单写成一条 `todo` 条目。两者都广播 `TodoUpdated`。 |

## 3. 与其它模块的交互

- **本模块调用** `contracts`：`ToolDeclaration`、`TodoItem`、`TodoStatus`、`TodoEntryPayload`、`TodoUpdated`、`ToolAfterPayload`、`ToolArgumentError`。
- **工具不写日志**：`tool.py` 只校验参数并回计数；写条目、发事件在 `handler.py` 的位点 8 —— 与 session 写工具结果、checkpoint 落盘是同一种分工（谁拥有事实谁写）。
- **本模块与 `modules/session` 的交接点是条目类型**：`Session` 构造与 `append_entry` 时把最后一条 `todo` 条目折成 `todos()`；它**不进 messages**，投影与压缩都不用改。
- **被调用**：`composition` 把 `tool.TODO_TOOL` 登记进工具表、把 `handler.build_handlers()` 注册到位点 1 与位点 8。
- **前端**：`TodoUpdated` 经 `api/sse/frames` 转成 `todo_updated` 帧；刷新时 `GET /sessions/{id}` 的 `todos` 兜底。

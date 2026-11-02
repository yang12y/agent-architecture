# modules/ask_user

## 1. 作用

向用户提问：模型用 `ask_user` 问一句（可带选项、可多选），人在界面上答，答案作为
**普通工具结果**回到上下文。**问题不进日志，答案进。**

## 2. 文件

| 文件 | 作用 |
| --- | --- |
| `__init__.py` | 包标记，并对外转出工具声明、参数校验、答案编码与装配入口。 |
| `tool.py` | `ask_user` 的声明（含 300 秒超时与不重试）、参数校验、把提问端口给的答案摊成 JSON。 |

## 3. 与其它模块的交互

- **本模块调用** `contracts`：`ToolDeclaration`、`ToolContext`、`ToolRunner`、`Question` / `QuestionOption` / `QuestionAnswer` / `QuestionRequest`、`QuestionProvider`、`ToolArgumentError`。
- **挂起不在这里**：`tool.py` 只是 `await questions.request(...)`；等谁、怎么唤醒是 `infra/questions` 的事。
- **不重试**：副作用记 `non_idempotent` 且不接受幂等键 —— 重试会在界面上多出一张一模一样的卡。
- **超时在声明上**：`ToolDeclaration.timeout = 300` 覆盖内核默认的 60 秒。人不会在一分钟内必答。
- **被调用**：`composition` 用 `container.questions` 生成实现并登记进工具表。
- **前端**：`QuestionRequired` 经 `api/sse/frames` 转成 `question_required` 帧弹出卡片；
  刷新或重连时 `GET /questions` 兜底；回答走 `POST /questions/{request_id}`。

# infra/questions

## 1. 作用

人工提问的进程内实现：把「等用户在界面上选一个」落成一个挂起的 future。与审批共用一张表，
但语义不同 —— 审批决定**放不放行**，提问只**取数据**。

## 2. 文件

| 文件 | 作用 |
| --- | --- |
| `__init__.py` | 包标记，并对外转出 `QuestionGate` 与「没人能答」的那句文案。 |
| `gate.py` | `QuestionGate`：先问在线表再发 `QuestionRequired`（两步之间没有 await），没人连着就抛 `NoAnswererError` 当场返回；有人连着则 `await` future 直到有人交回答。 |

## 3. 与其它模块的交互

- **本模块调用** `contracts`：`QuestionRequest`、`QuestionResult`、`QuestionRequired`、`EventBus`、`NoAnswererError`，并实现 `QuestionProvider` 协议。
- **本模块调用** `infra/pending`（挂起表）与 `infra/presence`（谁在看哪个会话）。
- **被调用**：
  - `composition` 建唯一实例，放进 `Container`。
  - `modules/ask_user` 经 `container.questions.request(...)` 挂起等回答。
  - `api/routes/questions.py` 经 `container.questions.pending(...)` / `.respond(...)` 列提问与交回答。
  - `api/sse/hub` 订阅它发的事件，转成 `question_required` 帧推给浏览器。

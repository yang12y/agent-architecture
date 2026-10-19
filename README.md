# agent-architecture

通用 Agent 后端。九个维度模块化，靠「十个注入位点」解耦：**内核只负责走位点、调模型收流、判定终止**，其余逻辑全部挂载在位点上。

- 形态：纯后端服务，对外只有 HTTP（含一条 SSE 流，无 CLI）
- 技术栈：Python 3.13 / uv / OpenAI 兼容接口
- 依赖方向：`contracts`（零依赖）← 九个维度模块 + `infra` ← `composition` ← `api`

## 文档

| 文件 | 内容 |
|---|---|
| `Agent通用架构.md` | 上游架构依据：十位点 / 九维度 / Runtime State / Session / 压缩 / Checkpoint / Hook / 沙箱 / 草稿槽 |
| `docs/ARCHITECTURE.md` | 本项目的落地设计：模块划分、依赖规则、开发顺序、编码规范、全局不变量（I1–I11） |
| `docs/HANDLERS.md` | 十个位点上现在挂着哪些 handler、按什么次序跑（由 `scripts/dump_handlers.py` 生成，测试守着别写歪） |

## 快速开始

需要 [uv](https://docs.astral.sh/uv/)。

```bash
uv sync                              # 建 .venv 并装依赖
cp .env.example .env                 # 然后填 YZY_MODEL / YZY_LLM_API_KEY / YZY_LLM_BASE_URL
uv run pytest -q                     # 跑测试
uv run python scripts/check_llm.py   # 对真实模型发一条最短消息，验证配置是否可用
```

配置项全部走环境变量，前缀 `YZY_`，详见 `.env.example`。
优先级：**真实环境变量 > `.env` > 代码默认值**。

## 启动后端

```bash
uv run uvicorn agent_architecture.api.main:app --host 127.0.0.1 --port 8000
```

- **打开 <http://127.0.0.1:8000/> 就是页面**：左边会话列表，右边对话，要审批的工具调用会弹卡片
- 交互式接口文档：<http://127.0.0.1:8000/docs>
- **缺 `YZY_LLM_API_KEY` 时启动就报错**（`ConfigError`），不会等到第一个请求才失败 —— 容器在模块导入时建好，配置问题当场暴露
- 会话日志落在 `YZY_SESSION_DIR`（默认 `<workspace>/.data/sessions`），一个会话一个目录（`meta.json` + `entries.jsonl`）

## 前端（`src/web/`）

**没有构建步骤**：HTML + CSS + 原生 ES 模块，由 `api/app.py` 的 `_mount_web` 挂在根路径，
起一个 uvicorn 就全在了。选它而不是 React + Vite，是因为这个 UI 要的只有 `fetch` / `EventSource` /
`localStorage`，而形态是「事件流 + 追加渲染」—— 不值得为它引入第二套工具链。

| 文件 | 职责 |
|---|---|
| `js/api.js` | 只有 fetch：七个 HTTP 端点 |
| `js/stream.js` | 只有 SSE：连接、断了重连、重连照原会话再连一次 |
| `js/store.js` | 唯一状态 + 事件落到状态上（不碰 DOM、不发请求） |
| `js/view.js` | state -> DOM（不读网络、不改状态） |
| `js/main.js` | 装配：只有这一层知道「哪个事件要触发哪个请求」 |

改完前端跑一下 `node scripts/web_smoke.mjs`：它用一个假 DOM 把模块图真跑一遍。
`node --check` 只查语法，查不出"引用了已经被删掉的变量"——那种错误在浏览器里的表现是**页面空白、会话列表不出来**。

输入框下面一行统计（上下文占用圈 / trace 数 / turn 数）读的是 `GET /sessions/{id}` 返回的
`stats` —— 那三个数是**现数的**，没有计数器字段（见 ARCHITECTURE.md §5 D4）。

消息列表**分页**：首屏只拉最后一页（50 条），往上滚再要更早的。
正常运行时**不整份重拉**——`assistant_message` / `turn_start` / `trace_end` 只按 `entry_seq` 补尾巴
（工具结果没有对应事件，只能等位点前进后补），列表不重建；只有换会话、刚连上、
或者压缩过（历史被摘要替换）才整份重来。流式增量只改草稿，不重排整个列表。

## 用 Postman 试一遍

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 健康检查 |
| POST | `/sessions?user_id=u1` | 建会话，body 可省略 |
| GET | `/sessions?user_id=u1` | 会话列表（只列这个用户的） |
| GET | `/sessions/{id}?user_id=u1` | 详情 + 最后一页消息 + 统计 —— **结果从这里看**；`&limit=50&before_seq=…` 翻更早的，`&after_seq=…` 补新增的 |
| DELETE | `/sessions/{id}?user_id=u1` | 删除会话（不可恢复；跑着的时候 409） |
| POST | `/sessions/{id}/messages?user_id=u1` | 发消息，返回 `202` |
| POST | `/sessions/{id}/interrupt?user_id=u1` | 中断正在跑的那次 trace（幂等） |
| GET | `/approvals?user_id=u1&session_id={id}` | 待审批列表（**重连后拉一次**） |
| POST | `/approvals/{request_id}?user_id=u1&session_id={id}` | 审批回执：`{"decision":"approve"}` 或 `"deny"` |
| GET | `/events?user_id=u1&session_id={id}` | 事件流（`text/event-stream`）；浏览器里用 `EventSource`，curl 直接看 |

发消息的请求体（**内容块必须带 `type`**）：

```json
{ "content": [ { "type": "text", "text": "读一下 src 目录里有什么" } ] }
```

三个要知道的行为：

- **`POST messages` 不回内容**，只有 `202`。trace 在后台任务里跑，结果要么听着 `/events`，要么轮询 `GET /sessions/{id}`。
- **需要人工确认的调用会停下来等人**：命令、工作目录之外的路径，会挂在 `GET /approvals` 上一直不返回，同一次 trace 不往下走。用 `POST /approvals/{request_id}` 表态之后它接着跑；**没人表态就一直挂着**，这个会话期间发新消息会 409。
- `user_id` 一律走**查询参数**，不放请求体（`EventSource` 设不了请求头，HTTP 跟着用同一个位置）。它只做分类，不做鉴权。

## 当前进度

| 部分 | 状态 |
|---|---|
| `contracts/`（十位点、共享模型、端口、不变量工具） | 完成 |
| `infra/`（config / observability / system / llm / storage） | 完成 |
| `modules/agent_loop`（主循环 + 位点管线 + 收流 + 失败处置） | 完成 |
| `modules/runtime_state`（位点 1–10） | 完成 |
| `modules/session`（位点 1/5/6/8/10 + 投影 + 草稿槽） | 完成 |
| `modules/compression`（位点 3，两级压缩） | 完成 |
| `modules/checkpoint`（屏障、调度、崩溃恢复） | 完成 |
| `modules/hook`（十个位点的事件出口） | 完成 |
| `modules/sandbox`（位点 6 的权限判定 + 人工审批往返） | 完成 |
| `modules/tool_calling`（位点 3/6/7/8 + 七个内置工具） | 完成（MCP 未做） |
| `api/`（HTTP + SSE + ASGI 入口 + 审批端点） | 完成 |
| `src/web/`（会话列表、流式对话、工具卡片、审批弹窗） | v1 完成 |
| `modules/skill`（D3） | 待做 |

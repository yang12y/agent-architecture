# composition

## 1. 作用

L3 装配层：全系统唯一知道有哪些具体实现的地方，把配置、端口、九个维度模块和存储装成一个可直接使用的 Container。

## 2. 文件

| 文件 | 作用 |
| --- | --- |
| `__init__.py` | 包标记，并对外转出 `Container`、`build_container`、`LiveSession`、`SessionRegistry`。 |
| `container.py` | 装配根：读配置、建端口与存储、建九个模块、注册全部 handler、组装成 `Container`。 |
| `registry.py` | 会话注册表：`session_id -> LiveSession` 的进程内活状态，按需从存储加载、保留正在跑的会话。 |

## 3. 与其它模块的交互

- **本模块调用** 全部 `infra/*`：`load_app_config`、`FileSessionStore`、`build_openai_client`、`ApprovalGate`、`InProcessEventBus` / `StdLogger`、`SystemClock` / `UuidGenerator` / `HeuristicTokenCounter`。
- **本模块调用** `modules/agent_loop`：`Agent`（门面）、`HandlerRegistry` 与 `SitePipeline`（装 handler 的容器）。
- **本模块调用** 七个维度模块与 `prompts` 的装配入口：`session.Session`、`tool_calling.build_catalog`、各模块的 `build_handlers` / `Checkpoint` / `bootstrap_runtime_state`。
- **本模块调用** `contracts`：`Ports` 与各端口协议，用来约束上面这些实现。
- **被调用**：`api` 用 `build_container` 建唯一实例，用 `Container` / `LiveSession` 取能力。
- 反向没有：其它模块都不 import `composition`，它只向下装配。

# contracts

## 1. 作用

L1 契约层：全系统共享的类型、端口协议、十位点定义与异常，是所有跨层数据的唯一形状来源。

## 2. 文件

| 文件 | 作用 |
| --- | --- |
| `__init__.py` | 包标记，并把整个契约层的公开名字汇总转出，外部只从这一处 import。 |
| `config.py` | 全局配置 `AppConfig` 与 trace 级只读快照 `FrozenConfig`。 |
| `errors.py` | 契约层异常：`AgentError` 基类、`ConfigError`、`SiteAbort`、`FlushError`、`ToolArgumentError`、`LlmCallError` 与可重试种类集合。 |
| `events.py` | 会话事件：`MessageAccepted`、`TraceStart`、`TurnStart`、`AssistantDelta`、`AssistantThought`、`AssistantMessage`、`ToolCallStart`、`ToolResult`、`ApprovalRequired`、`Compaction`、`TraceEnd`。 |
| `llm.py` | 模型接入契约：`LlmRequest`、`LlmChunk`、`LlmChunkKind`、`LlmClient` 协议与 `collect_text`。 |
| `models.py` | 共享数据模型：`Message` 与 `ContentPart` 家族、`SessionEntry` 与各 payload、`RuntimeState`、`ToolCall`、`Permission`、`Usage`、`TokenAnchor` 等。 |
| `ports.py` | 端口协议：`Clock`、`IdGenerator`、`Logger`、`EventBus`、`TokenCounter`、`SessionStore`、`SessionHandle`、`ApprovalProvider`、`ToolRegistry` 与端口集合 `Ports`。 |
| `sites.py` | 十位点：`Site` 枚举、各位点 payload、`TraceContext` / `SiteContext`、`Handler` 协议、优先级带与拒绝位点。 |
| `tools.py` | 工具契约里跨模块共用的两条规则：`resolve_in_workspace`（限定在工作目录内解析路径）、`is_retryable`（失败是否可重试）。 |

## 3. 与其它模块的交互

- **本模块不调用任何模块**：只依赖标准库，不碰 `infra`、`modules`、`composition`、`api`。
- **本模块被所有模块调用**：`api`、`composition`、`infra/*`、`modules/*` 全部从这里取类型、端口协议与位点定义。

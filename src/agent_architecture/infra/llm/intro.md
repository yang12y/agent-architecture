# infra/llm

## 1. 作用

模型接入实现：一个 OpenAI 兼容的流式客户端，全项目唯一真的跟模型服务说话的地方。

## 2. 文件

| 文件 | 作用 |
| --- | --- |
| `__init__.py` | 包标记，并对外转出 `build_openai_client`。 |
| `client.py` | `OpenAiCompatibleClient`：拼请求（`build_payload`）、发流式调用、把分片翻成 `LlmChunk`、解析工具参数（`parse_arguments`）；`build_openai_client` 按配置建实例。 |
| `errors.py` | 把 SDK 异常翻成契约异常，并按标记识别上下文溢出。 |
| `messages.py` | `to_openai_messages`：把契约的 `Message` 翻成 OpenAI 的消息结构，含思维链字段的回传。 |

## 3. 与其它模块的交互

- **本模块调用** `contracts`：`LlmRequest`、`LlmChunk`、`LlmChunkKind`、`Message`、`LlmCallError`、`ContextOverflowError` 等，并实现 `LlmClient` 协议。
- **被调用**：
  - `composition` 调 `build_openai_client(config)` 建唯一实例，注入给 `Agent` 与压缩模块。
  - `modules/agent_loop` 在位点 4-5 用它发流式请求、收增量。
  - `modules/compression` 的二级摘要用它发一次请求。

# modules/compression

## 1. 作用

D5 压缩：上下文触达阈值时做两级压缩 —— 先把长工具结果换成占位词，不够再调模型做摘要。

## 2. 文件

| 文件 | 作用 |
| --- | --- |
| `__init__.py` | 包标记。 |
| `controller.py` | 两级压缩的调度 `compact_at_llm_before`：判触发、跑一级、仍超再跑二级、写 compaction 条目。 |
| `handler.py` | compression 的 SiteHandler 注册：只挂位点 3。 |
| `level1_placeholder.py` | 一级压缩 `shrink`：把较早的长工具结果正文换成占位词，返回省下的 token。 |
| `level2_summary.py` | 二级压缩 `compact`：滑动窗口 + 摘要 + 落一条 compaction。 |
| `prompts.py` | 摘要提示词：把要压掉的消息摊成文本、填进模板。 |
| `token_budget.py` | 压完之后的预算判定：`count` 算总量、`guard` 判这次放不放得下。 |
| `trigger.py` | 触发判定 `over_budget`：这次要不要压。 |

## 3. 与其它模块的交互

- **本模块调用** `contracts`：`LlmBeforePayload`、`TokenCounter`、`LlmClient`、`CompactionEntryPayload`、`ContextOverflowError` 等。
- **本模块改 `payload.messages` 与 `payload.schema_tokens`**：位点 3 之后的 handler 与主循环看到的就是压过的上下文（schema 那部分由 `modules/tool_calling` 在同一个位点填入）。
- **本模块调用** `ctx.ports.tokens`（`infra/system` 实现）算占用，用注入的 `LlmClient`（`infra/llm` 实现）做二级摘要。
- **本模块经 `ctx.session`**（`modules/session`）追加 compaction 条目，并发 `Compaction` 事件给事件总线。
- **被调用**：`composition` 把 `handler.build_handlers` 的结果连同 `LlmClient`、`TokenCounter` 一起注入，注册到位点 3。

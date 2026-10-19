# Handler 汇总

全项目注册在十个位点上的 handler，一览。改优先级、加 handler、想看"这个位点上谁先跑"，
都查这里。

**唯一事实来源是代码**：`HandlerRegistry` 装配时按 `PRIORITY_BANDS` 逐个校验，越界直接抛
`ValueError`。这份文档只是把那张表抄下来给人看。

- §2 的"一个位点一张表"由 `uv run python scripts/dump_handlers.py` 生成，改了 handler 就重跑一次。
- `tests/test_composition/test_handler_doc.py` 会检查两件事：文档没漏掉任何 handler、
  每个 handler 的 priority 跟代码一致。忘了更新这里，测试会红。

---

## 1. 优先级段位

（`contracts/sites.py` 的 `PRIORITY_BANDS`，装配期强校验）

| 段位 | 取值 | 谁用 |
|---|---|---|
| `recovery` | `50` | 恢复判据，只有 checkpoint 在位点 1 用 |
| `dimension` | `100–499` | 九个维度模块与提示词组装的内置逻辑 |
| `hook` | `500–899` | 用户 Hook |
| `barrier` | `900` | 落盘屏障，只有 checkpoint 用，必须最后跑 |

同一 priority 内按**注册顺序**（`sorted` 是稳定排序）。

---

## 2. 一个位点一张表

**这一节由脚本生成**：`uv run python scripts/dump_handlers.py`。

### 一个位点一张表（已按 priority 排好）

**`trace:before`**

| priority | 模块 | handler id | 做什么 |
|---|---|---|---|
| 50 | checkpoint | `checkpoint.trace_before` | 恢复判据：上一次是不是崩了、要不要恢复 |
| 200 | session | `session.trace_before` | 写 trace_start 与本次的用户消息，并广播 `TraceStart` |
| 300 | runtime_state | `runtime_state.trace_before` | 把执行态对齐到"这次 trace 刚开始" |
| 500 | hook | `hook.site` | 跑用户挂在 `agent/trace_before` 上的 hook；返回 `False` = 这次 trace 不开始 |

**`turn:before`**

| priority | 模块 | handler id | 做什么 |
|---|---|---|---|
| 110 | runtime_state | `runtime_state.turn_before` | 开新的一轮（`turn_index++`、重试计数归零、清本轮工具统计） |
| 500 | hook | `hook.site` | 跑用户挂在 `agent/turn_before` 上的 hook |

**`llm:before`**

| priority | 模块 | handler id | 做什么 |
|---|---|---|---|
| 50 | tool_calling | `tool_calling.llm_before` | 把可见的工具 schema 填进请求 |
| 100 | prompts | `prompts.llm_before` | 拼系统提示词写进 `payload.system_prompt`（**不进 messages，也不进 Session**） |
| 110 | runtime_state | `runtime_state.llm_before` | 算这次请求实际占用多少上下文（`context_tokens`） |
| 200 | compression | `compression.llm_before` | 到阈值就压：先一级占位替换，不够再二级摘要，最后守预算 |
| 300 | session | `session.llm_before` | 取材之后清空草稿槽 |
| 500 | hook | `hook.site` | 跑用户挂在 `agent/llm_before` 上的 hook |
| 900 | checkpoint | `checkpoint.llm_before` | **落盘屏障**：模型看到压缩后的上下文之前，条目必须已经在盘上 |

**`llm:during`**

| priority | 模块 | handler id | 做什么 |
|---|---|---|---|
| 110 | runtime_state | `runtime_state.llm_during` | 记下这是本 turn 的第几次尝试（`llm_attempt_number`） |
| 500 | hook | `hook.site` | 跑用户挂在 `agent/llm_during` 上的 hook（设超时、挂取消条件的常用位置） |

**`llm:after`**

| priority | 模块 | handler id | 做什么 |
|---|---|---|---|
| 110 | runtime_state | `runtime_state.llm_after` | 收下模型这一轮的产出（`pending_tool_calls`），并消费中途消息 |
| 200 | session | `session.llm_after` | 定稿模型这一轮的输出，把中途消息落成 user 消息 |
| 500 | hook | `hook.site` | 跑用户挂在 `agent/llm_after` 上的 hook |
| 900 | checkpoint | `checkpoint.llm_after` | **落盘屏障** |

**`tool:before`**

| priority | 模块 | handler id | 做什么 |
|---|---|---|---|
| 100 | tool_calling | `tool_calling.tool_before` | 检查链：存在性 → 补默认 → 参数校验 → 写 `EXECUTE`、算编排 |
| 435 | sandbox | `sandbox.tool_before` | 查工具声明与当前档位，逐条判：放行 / 问人 / 拒绝，写进 `decisions` |
| 450 | runtime_state | `runtime_state.tool_before` | 把判定不是 `execute` 的调用从待执行队列里摘掉 |
| 460 | session | `session.tool_before` | 为每条**将要执行**的调用写一条 tool_start |
| 445 | tool_calling | `tool_calling.tool_finalize` | 给每条不是 `EXECUTE` 的调用写一条配对的结果消息（I10） |
| 500 | hook | `hook.site` | 跑用户挂在 `agent/tool_before` 上的 hook；返回 `False` = 这批一条都不执行 |
| 900 | checkpoint | `checkpoint.tool_before` | **落盘屏障**：执行之前 tool_start 必须在盘上 |

**`tool:during`**

| priority | 模块 | handler id | 做什么 |
|---|---|---|---|
| 110 | runtime_state | `runtime_state.tool_during` | 把这条调用标记成执行中 |
| 100 | tool_calling | `tool_calling.tool_during` | 执行这条调用（超时 + 按规则重试），**失败收成结果而不是上抛** |
| 500 | hook | `hook.site` | 跑用户挂在 `agent/tool_during` 上的 hook |

**`tool:after`**

| priority | 模块 | handler id | 做什么 |
|---|---|---|---|
| 100 | tool_calling | `tool_calling.tool_after` | 规范化 + 截断，落成一条给模型看的 tool 消息 |
| 400 | runtime_state | `runtime_state.tool_after` | 记一次统计、摘下这条调用、消费中途消息 |
| 410 | session | `session.tool_after` | 写工具结果消息，把中途消息落成 user 消息 |
| 500 | hook | `hook.site` | 跑用户挂在 `agent/tool_after` 上的 hook |
| 900 | checkpoint | `checkpoint.tool_after` | **落盘屏障** |

**`turn:end`**

| priority | 模块 | handler id | 做什么 |
|---|---|---|---|
| 500 | hook | `hook.site` | 跑用户挂在 `agent/turn_end` 上的 hook |

> 这一位点上**没有**维度 handler：终止判定是内核自己的职责，不进管线（见 §2.3 与 I9）。

**`trace:end`**

| priority | 模块 | handler id | 做什么 |
|---|---|---|---|
| 100 | session | `session.trace_end` | 写 trace_end，并把 `updated_at` 刷成本刻 |
| 400 | runtime_state | `runtime_state.trace_end` | 释放执行态 |
| 500 | hook | `hook.site` | 跑用户挂在 `agent/trace_end` 上的 hook |
| 900 | checkpoint | `checkpoint.trace_end` | **落盘屏障** |

---

## 3. 一行一个 handler

| 模块 | handler id | 位点 | priority | 做什么 |
|---|---|---|---|---|
| checkpoint | `checkpoint.trace_before` | `trace:before` | 50 | 恢复判据 |
| prompts | `prompts.llm_before` | `llm:before` | 100 | 拼系统提示词写进 payload |
| runtime_state | `runtime_state.llm_before` | `llm:before` | 110 | 算 `context_tokens` |
| runtime_state | `runtime_state.llm_during` | `llm:during` | 110 | 记尝试次数 |
| runtime_state | `runtime_state.llm_after` | `llm:after` | 110 | 解析待执行调用、消费中途消息 |
| runtime_state | `runtime_state.tool_before` | `tool:before` | 450 | 摘掉不执行的调用 |
| runtime_state | `runtime_state.tool_during` | `tool:during` | 110 | 标记执行中 |
| runtime_state | `runtime_state.tool_after` | `tool:after` | 400 | 记统计、摘调用、消费中途消息 |
| runtime_state | `runtime_state.trace_before` | `trace:before` | 300 | 绑定游标、上限、权限、模型 |
| runtime_state | `runtime_state.trace_end` | `trace:end` | 400 | 释放执行态 |
| runtime_state | `runtime_state.turn_before` | `turn:before` | 110 | 开新的一轮 |
| session | `session.llm_after` | `llm:after` | 200 | 定稿 assistant 消息 |
| session | `session.llm_before` | `llm:before` | 300 | 清空草稿槽 |
| session | `session.tool_after` | `tool:after` | 410 | 写工具结果消息 |
| session | `session.tool_before` | `tool:before` | 460 | 写 tool_start 条目 |
| session | `session.trace_before` | `trace:before` | 200 | 写 trace_start 与用户消息 |
| session | `session.trace_end` | `trace:end` | 100 | 写 trace_end、刷 `updated_at` |
| compression | `compression.llm_before` | `llm:before` | 200 | 两级压缩 + 预算守卫 |
| sandbox | `sandbox.tool_before` | `tool:before` | 435 | 逐条判工具调用是否越界 |
| tool_calling | `tool_calling.llm_before` | `llm:before` | 50 | 加载工具 schema |
| tool_calling | `tool_calling.tool_after` | `tool:after` | 100 | 规范化 + 截断工具结果 |
| tool_calling | `tool_calling.tool_before` | `tool:before` | 100 | 检查链 |
| tool_calling | `tool_calling.tool_during` | `tool:during` | 100 | 执行工具 |
| tool_calling | `tool_calling.tool_finalize` | `tool:before` | 445 | 给不执行的调用写结果消息 |
| hook | `hook.site` | 十个位点全部 | 500 | 跑用户挂在这个位点上的 hook |
| checkpoint | `checkpoint.llm_before` | `llm:before` | 900 | 落盘屏障 |
| checkpoint | `checkpoint.llm_after` | `llm:after` | 900 | 落盘屏障 |
| checkpoint | `checkpoint.tool_before` | `tool:before` | 900 | 落盘屏障 |
| checkpoint | `checkpoint.tool_after` | `tool:after` | 900 | 落盘屏障 |
| checkpoint | `checkpoint.trace_end` | `trace:end` | 900 | 落盘屏障 |

合计 **30 个**（`hook.site` 一个实例挂满十个位点）。

---

## 4. 加一个 handler 该取什么 priority

**先看 §2 里那个位点现在长什么样** —— priority 改的是全项目的执行顺序，不是局部的小事。

三条硬规则（装配期校验，越界直接抛 `ValueError`）：

1. **落在某个段位里** —— 50 / 100–499 / 500–899 / 900，段位之间没有空隙可钻。
2. **落盘屏障永远最后**（900）。排在它后面等于你的改动落不下去。
3. **hook 段位永远在维度之后**（500 > 499）。排在维度前面，hook 就看不到维度算出来的结果。

段位内怎么排，只看一件事：**你要读谁、谁要读你**。

| 你想干的事 | 取法 | 现成的例子 |
|---|---|---|
| 本位点最先跑，产出给别人读 | 100 | `prompts.llm_before` 先把系统提示词填进 payload，`runtime_state`(110) 才算得到它 |
| 读本位点前面某人的产出 | 排在他后面 | `session.tool_after`(410) 读 `runtime_state.tool_after`(400) 记完的统计 |
| 读"所有判定"再动手 | 排在该位点最后一个判定者之后 | `runtime_state.tool_before`(450) 读 `decisions` |
| 只在旁边看，不参与判定 | 500 段 | `hook.site` |

**留空隙**：现有取值是 50 / 100 / 110 / 200 / 300 / 400 / 410 / 435 / 445 / 450 / 460 / 500 / 900 ——
中间空着很多。要插在两者之间就取中间值（排在 100 与 200 之间取 150），**别去动别人已有的数**。

改完记得：

1. 重跑 `scripts/dump_handlers.py`，把 §2 更新掉；
2. 如果新增了 handler，在 §3 补一行；
3. `uv run pytest tests/test_composition/test_handler_doc.py` —— 漏了会红。

---

## 5. 界外：内核不做 handler 的事

| 内核自己干的 | 为什么不在管线里 |
|---|---|
| 位点 2 推进 `turn_index` | 它是循环计数，`max_turns` 守卫靠它，不该因为某个 handler 忘了注册而失效（I9） |
| 位点 4 调模型收流 | 主循环要拿到收流结果才能继续，不可插拔 |
| 位点 9 的终止判定 | 只做编排，没有模块逻辑 |
| 失败按 `LlmErrorKind` 分流 | 见 `contracts/errors.py` |

完整清单见 `ARCHITECTURE.md` §2.3。

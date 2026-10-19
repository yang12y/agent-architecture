# agent-architecture 架构设计

> 上游依据：`Agent通用架构.md`（十位点 / 九维度 / Runtime State / Session / 压缩 / Checkpoint / Hook / 沙箱 / 草稿槽）
> **内容约束**：只记录**已经确定且正确**的设计，且**只写"做什么"，不写"为什么"** —— 决策理由属于代码注释。
> 被否决、踩坑的做法各用**一句话**带过，不展开。
> 各模块的**实现细节**在开发该模块时单独讨论；本文档固化的是边界、数据、位点、接口与单测口径。

---

## 0. 项目定位与技术栈

| 项 | 取值 |
|---|---|
| 形态 | 纯后端服务，**无 CLI**，对外只有 HTTP（含一条 SSE 流） |
| Web 框架 | FastAPI（承载一元调用） |
| 流式传输 | SSE（`GET /events`）：一条流多路复用，只承载流与事件。上行交互走普通 POST |
| 语言 | Python 3.13（uv 管理解释器；使用 `StrEnum`、`typing.Protocol`、`asyncio`） |
| 数据模型 | pydantic v2（对外 DTO）、dataclass（内部执行态） |
| 持久化 | 现在用**文件实现**：一个会话一个目录，`meta.json` + `entries.jsonl`；最终换成 **PostgreSQL** 的 `sessions` / `session_entries`（append-only）两张表 |
| 测试 | pytest + pytest-asyncio + httpx(ASGI) |
| 包管理 | uv / pyproject.toml，src-layout |

**技术栈里刻意不引入的**：不引入 ORM（Session 日志是 append-only 事实流，不是关系表，直接用 SQL）；不引入消息队列（trace 在同一进程内推进，跨进程恢复由 Checkpoint 负责）。

### 0.1 代码规范

**字段标注写在字段正上方，用 `#` 注释。** 三件事缺一不可：

1. **作用**——它是什么、谁来写、谁来读
2. **举例**——一个具体值，不许写"某字符串"
3. **固定取值全列举**——取值是有限集合时逐个列出，每个值配一句中文解释

适用范围：dataclass 字段、类的实例属性（`self.x`）、枚举成员、模块级别名。
**模块 / 类 / 方法自己的 docstring 仍然是 `"""`，位置不变**——它们是所在体的第一条语句。

```python
@dataclass(slots=True)
class ToolCall:
    """一条工具调用的执行态记录。"""

    # 本次调用的唯一标识（来自模型的 tool_call_id）。
    #
    # 作用：幂等键的构成要素；工具结果靠它回填到对应调用。
    # 举例："call_7f3a91"
    id: str

    # 这条调用当前走到生命周期哪一步。
    #
    # 举例：ToolCallStatus.PENDING
    status: ToolCallStatus


class ToolCallStatus(StrEnum):
    """一条工具调用的生命周期状态。"""

    # 待执行 —— 已通过前置检查，排队等待执行。
    PENDING = "pending"

    # 待审批 —— 需要人工放行，正在等回执，尚未产生任何副作用。
    AWAITING_APPROVAL = "awaiting_approval"

    # 执行中 —— 已派发；从这一刻起副作用可能已经发生，结果未知。
    RUNNING = "running"

    # 未执行即结束 —— 工具不存在、参数非法，或被审批拒绝。它不计入工具统计。
    REJECTED = "rejected"
```

**结构放契约，算法放模块。** `contracts/` 只放数据与 Protocol，不放行为。所以 `RuntimeState`、`SessionEntry` 的定义在 `contracts/models.py`，`modules/runtime_state/` 与 `modules/session/` 只放算法。

**import 约定**：模块内部文件用相对 import；跨层只允许 `from agent_architecture.contracts import ...`，不深层导入。

---

## 1. 全局不变量（全项目最高约束）

违反以下任意一条即为设计错误：

| # | 不变量 |
|---|---|
| I1 | **Session 日志是唯一真相源**。Runtime State、上下文投影、模型输出草稿都可从它重建。 |
| I2 | **Runtime State 不落盘、不参与崩溃恢复**。它由 Session 建立，trace 结束即销毁。 |
| I3 | **跨模块只能通过「十位点 + 端口」耦合**。内核不 import 任何具体维度模块。 |
| I4 | **压缩不改写、不删除历史条目**。一级只改内存视图，二级只追加一条 `compaction`。 |
| I5 | **副作用发生前必须先落盘**。位点 6 的 `tool_start` 落盘失败即阻止工具执行。 |
| I6 | **写入内存 entries ≠ 已落盘**。落盘时机由 Checkpoint 屏障决定。 |
| I7 | **模型输出只存在于草稿槽**，永不落盘、永不进 Runtime State。 |
| I8 | **幂等键只生成与传递，运行时不用它做去重跳过**；去重发生在工具本体或下游。 |
| I9 | **内核拥有"它自己的控制流所依赖的执行态"**：`turn_index` 的推进、`is_running` 的置位由内核自己做，其余 Runtime State 操作交给 handler。理由：预算守卫不能因为某个 handler 忘记注册就失效。 |
| I10 | **被拒的工具调用同样要有一条结果消息**。两个原因：模型协议要求 assistant 里的每个 `tool_call` 都有配对的 tool 结果，缺了下一轮请求直接不合法；不告诉模型"被拒了"，它会一直重问同一个工具。 |
| I11 | **重试的前提是"这次尝试还没有产出"**。草稿槽里一旦有了半截输出就不重试——接上去会拼出"两遍开头"的消息；半截输出留在槽里，等下一次 trace 在位点 3 取材时作为"被打断的输出"并入上下文。 |

---

## 2. 十个注入位点

位点是全系统**唯一**的扩展点与耦合面。顺序固定，不可增删。

| # | 位点常量 | 含义 |
|---|---|---|
| 1 | `trace:before` | trace 开始前 |
| 2 | `turn:before` | turn 开始前 |
| 3 | `llm:before` | 模型调用前 |
| 4 | `llm:during` | 模型调用中（进入收流时触发一次） |
| 5 | `llm:after` | 模型调用后 |
| 6 | `tool:before` | 工具调用前 |
| 7 | `tool:during` | 工具调用中 |
| 8 | `tool:after` | 工具调用后 |
| 9 | `turn:end` | turn 结束 |
| 10 | `trace:end` | trace 结束 |

### 2.1 位点管线契约

**管线不遍历位点。** 位点之间的先后由主循环的代码写死：

```text
位点1 -> while{ 位点2 / 3 / 4 / 5 / 6 / 7 / 8 / 9 } -> 位点10
```

管线只回答一个问题：主循环走到某个位点时，这一刻该跑哪些 handler、按什么顺序跑。

**两条通道分开：数据走 `ctx.payload`（原地改），返回值只表达控制。**

| 返回 | 含义 | 内核在哪解释它 |
|---|---|---|
| `None` / `True` | 继续跑下一个 handler | 所有位点 |
| `False` | 否决：立刻停止本位点后续 handler，下游动作必须跳过 | **只有位点 1 与位点 6** |
| 抛异常 | `SiteAbort` 中止整次 trace；`FlushError` 落盘失败；两者都由主循环统一收尾 | 所有位点 |

**否决只在两个位点有语义。** 内核只在这两处读管线的返回值，其余 8 个位点的返回值一律丢弃：

| 位点 | 否决表达什么 | 内核动作 |
|---|---|---|
| 1 `trace:before` | 这次 trace 不该开始（快照损坏、并发冲突、前置条件不满足） | 直接收尾为 `failed`：**位点 2–9 一次都不跑**，位点 10 照常执行（Runtime State 必须被销毁） |
| 6 `tool:before` | 这批工具一条都不该执行（审批拒绝、参数非法） | 清空待执行队列，**跳过位点 7/8**；turn 循环继续，下一轮把"被拒"喂给模型 |

在其余位点返回 `False` 是 handler 的 bug，内核不赋予它含义——要中止整次 trace 请抛 `SiteAbort`。

**priority 分段**（数值越小越先跑；同值按注册顺序，因为 sorted 是稳定排序）：

| 段位 | 取值 | 谁用 |
|---|---|---|
| `recovery` | `50` | 恢复判据（checkpoint 在位点 1） |
| `dimension` | 100–499 | 维度内置逻辑 |
| `hook` | 500–899 | 用户 Hook |
| `barrier` | `900` | 落盘屏障（checkpoint 在位点 3/5/6/8/10） |

分段是**硬约束**：`PRIORITY_BANDS` 是唯一事实来源，`HandlerRegistry` 在装配期逐个校验，落在任何段位之外的 priority 抛 `ValueError`。

**handler 是「模块 × 位点」的组合，不是「模块」。** checkpoint 在位点 1 用 priority 50，在位点 3/5/6/8/10 用 900。

> **每个 handler 实际取了哪个数、同一个位点上谁先谁后，查 [`HANDLERS.md`](HANDLERS.md)。**
> 那张表由 `scripts/dump_handlers.py` 从装配根上打出来，并有测试盯着不让它过期。

> **位点 4 的粒度是「每次模型调用触发一次」**（进入收流的那一刻），不是每个 token 一次。流式增量由内核直接写草稿槽并推 EventBus，不过管线。

### 2.3 内核固有职责（不可插拔）

内核只做五件事，其余全在位点上挂 handler：

1. **按顺序走位点**，并在位点 2 推进 `turn_index`、在位点 10 置 `is_running=False`（见 I9）；
2. **位点 4 直接调 `ports.llm` 收流** —— 主循环要拿到收流结果才能继续，这不是可插拔的；
3. **按种类处置模型调用的失败**：可重试的种类、且**这次尝试还没有产出**（I11）才重试；其余带着真实失败原因一路冒到收尾。内核不认任何厂商错误码，只认 `LlmErrorKind`。超窗收成 `budget_exceeded`，其余收成 `failed`；
4. **判定终止**：一轮结束后，若这一轮**模型没有请求工具**且没有排队的中途消息，就按 `completed` 收尾；撞到 `max_turns` 则 `budget_exceeded`。
   判据是「这一轮有没有请求工具」，不是「待执行队列空不空」。
5. **在安全点检查取消信号**。三个安全点：每轮开头、收流的每个分片、每条工具调用之前。
   被取消时按 `aborted` 收尾（走完位点 10）再往上抛；进程级取消（`CancelledError`）同样先收尾再抛。

### 2.2 位点 × 模块 责任矩阵

| # | 位点 | 在此挂载的模块 |
|---|---|---|
| 1 | trace:before | checkpoint(恢复判据) → session(meta/投影/trace_start/user_message) → skill(发现注册) → tool_calling(注册) → runtime_state(绑定游标与上限) → infra.config(冻结快照) → hook |
| 2 | turn:before | runtime_state(turn_index++、attempt 归零、清 stats)、hook |
| 3 | llm:before | runtime_state(算 context_tokens：锚点优先，没有就全量估算) → compression(两级压缩+重建) → session(清空草稿槽) → tool_calling(加载 schema) → skill(目录注入) → hook → checkpoint(屏障落盘)。游标推进不在这条管线里：内核构 payload 时调 `session.messages()` 就已经推进了 |
| 4 | llm:during | hook(设超时、挂取消条件)。流式增量不走管线：内核直接写草稿槽 + 推 EventBus |
| 5 | llm:after | runtime_state(解析 pending_tool_calls、消费 pending_queue) → session(assistant/用户消息定稿) → hook → checkpoint(屏障落盘) |
| 6 | tool:before | tool_calling(存在性→补默认→校验→重复检测→并行判定→幂等键→tool_start→**给不执行的调用写结果消息**) → sandbox(**逐条判越界，写 decisions**) → runtime_state(**把不执行的调用从待执行队列里摘掉**) → session(tool_start 条目) → hook(位点事件) → checkpoint(屏障落盘) |
| 7 | tool:during | tool_calling(执行/重试/超时)、sandbox(强制执行)、runtime_state(置 running) |
| 8 | tool:after | tool_calling(规范化/超限落盘/结果回填) → runtime_state(stats、移除、消费队列) → session(结果条目+中途消息) → hook → checkpoint(屏障落盘) |
| 9 | turn:end | agent_loop(终止决策)、hook |
| 10 | trace:end | session(trace_end、updated_at) → runtime_state(释放执行态) → hook → checkpoint(落盘) |

> `turn_index` 的推进、`is_running` 的置位、`max_turns` 判定由内核做，不进管线（见 2.3、I9）。

---

## 3. 模块划分

三层结构，**九个维度模块 + 一层非维度契约 + 一层基础设施 + API 层**。

| 层 | 目录 | 内容 | 能否依赖别的层 |
|---|---|---|---|
| L0 契约 | `contracts/` | 十位点、共享模型、端口 Protocol、错误 | 零依赖，不 import 其他层 |
| L1 维度 | `modules/*`（9 个） | 九个维度，各自实现 `SiteHandler` | 只允许依赖 L0 |
| L2 基础设施 | `infra/*` | llm / storage / config / observability | 只允许依赖 L0 |
| L3 装配 | `composition/` | 唯一装配根，实例化并注入 | 可依赖全部 |
| L4 接入 | `api/` | FastAPI 路由、SSE、DTO | 只依赖 L3 + L0 |

### 3.1 依赖规则（用 import-linter 强制）

| 规则 | 内容 |
|---|---|
| R1 | `contracts` 不得 import `modules` / `infra` / `composition` / `api` |
| R2 | 九个维度模块之间**禁止互相 import**；需要时走 `contracts` 的端口 |
| R3 | 维度模块与 infra 之间**禁止互相 import**；`infra` 实现 `contracts` 端口，由 composition 注入 |
| R4 | `agent_loop` 只依赖 `contracts`，不认识任何具体维度 |
| R5 | `composition` 之外任何地方不得出现跨层 import |
| R6 | 对外只暴露包根 `__init__.py`；禁止深层 import（`from agent_architecture.modules.session.log import ...`） |

### 3.2 目录架构

```text
agent-architecture/
├── pyproject.toml
├── README.md
├── .env.example
├── docs/
│   ├── ARCHITECTURE.md                     # 本文档
│   └── HANDLERS.md                         # 全部 handler 的位点 × 优先级一览
├── src/
│   └── agent_architecture/
│       ├── __init__.py
│       │
│       ├── contracts/                      # L0 共享契约【已完成】
│       │   ├── __init__.py                 # 统一出口，其他层只从这里 import
│       │   ├── sites.py                    # Site 十位点 / 十个位点 payload /
│       │   │                               #   TraceContext / SiteContext / Handler / CancelToken
│       │   ├── models.py                   # 全部共享枚举与数据类：消息、工具、Runtime State、
│       │   │                               #   Session 条目、草稿槽、审批
│       │   ├── config.py                   # AppConfig（进程级）/ FrozenConfig（trace 快照）
│       │   ├── ports.py                    # Protocol 端口 + Ports 聚合
│       │   └── errors.py                   # 领域异常（含 ContextOverflowError）
│       │
│       ├── modules/                        # L1 九个维度模块
│       │   ├── runtime_state/              # D9【已完成】（结构在 contracts/models.py，这里只放算法）
│       │   │   ├── __init__.py
│       │   │   ├── bootstrap.py            # 由内存 Session 建出本次 trace 的 RuntimeState
│       │   │   ├── ops/                    # 位点操作，每个参与的位点一个纯函数模块
│       │   │   │   ├── __init__.py
│       │   │   │   ├── s01_trace_before.py
│       │   │   │   ├── s02_turn_before.py
│       │   │   │   ├── s03_llm_before.py
│       │   │   │   ├── s04_llm_during.py
│       │   │   │   ├── s05_llm_after.py
│       │   │   │   ├── s06_tool_before.py
│       │   │   │   ├── s07_tool_during.py
│       │   │   │   ├── s08_tool_after.py
│       │   │   │   └── s10_trace_end.py    # 位点 9 没有模块，见 §5 D9
│       │   │   ├── pending_queue.py        # 中途消息入队 / 消费（位点 5、8）
│       │   │   ├── tool_stats.py           # 按工具名聚合成功/失败
│       │   │   └── handler.py              # 维度在八个位点上的 SiteHandler
│       │   │
│       │   ├── session/                    # D4【已完成】（SessionMeta / SessionEntry 定义在 contracts）
│       │   │   ├── __init__.py
│       │   │   ├── log.py                  # append-only 内存日志 + seq + entry_id
│       │   │   ├── projection.py           # 全量投影（三条来源规则）+ 草稿消息包装
│       │   │   ├── cursor.py               # context_cursor 增量并入与"该不该重算"
│       │   │   ├── draft_slot.py           # 会话级草稿槽（单槽）
│       │   │   ├── reader.py               # 只读视图（不写回）
│       │   │   ├── session.py              # Session 聚合根（meta+entries+上下文+游标+草稿）
│       │   │   ├── title.py                # 标题：第一条用户消息截断
│       │   │   └── handler.py              # 位点 1/5/6/8/10；所有日志写入都在这里
│       │   │
│       │   ├── checkpoint/                 # D6【已完成】
│       │   │   ├── __init__.py
│       │   │   ├── barriers.py             # 位点 3/5/6/8/10 五个屏障
│       │   │   ├── scheduler.py            # 屏障落盘调度（幂等，水位只是跳过空跑的缓存）
│       │   │   ├── crash.py                # 尾部判定：dangling / aborted / closed
│       │   │   ├── recovery.py             # 恢复七步
│       │   │   ├── rebalance.py            # 语义层：配平（补悬空结果 / 补 trace_end）
│       │   │   └── handler.py              # 位点 1 恢复判据 + 五个落盘屏障
│       │   │
│       │   ├── compression/                # D5【已完成】
│       │   │   ├── __init__.py
│       │   │   ├── controller.py           # 位点 3 的入口：先一级后二级
│       │   │   ├── trigger.py              # context_tokens ≥ compact_tokens
│       │   │   ├── level1_placeholder.py   # 长工具结果占位替换（只改内存）
│       │   │   ├── level2_summary.py       # 滑动窗口 + 摘要 + 落盘 compaction
│       │   │   ├── prompts.py              # 7.4 摘要提示词（保留/舍弃清单）
│       │   │   ├── token_budget.py
│       │   │   └── handler.py
│       │   │
│       │   ├── hook/                       # D7【已完成】
│       │   │   ├── __init__.py
│       │   │   ├── events.py               # 十位点的事件名 + 位点映射
│       │   │   ├── registry.py             # Hook / Guard 的注册与匹配
│       │   │   ├── runner.py               # 位点事件的执行（priority 500 段）
│       │   │   └── handler.py
│       │   │
│       │   ├── sandbox/                    # D8【已完成】
│       │   │   ├── __init__.py
│       │   │   ├── permission.py           # Permission 三档与比较
│       │   │   ├── policies.py             # file / network / command 策略（纯函数）
│       │   │   ├── approval.py             # 工具级 / 参数级审批 → 三态决策
│       │   │   ├── enforcement.py          # 强制执行边界
│       │   │   └── handler.py
│       │   │
│       │   ├── tool_calling/               # D2【已完成】（MCP 待做）
│       │   │   ├── __init__.py
│       │   │   ├── registry.py             # ToolCatalog：声明 + 实现，对外只露声明
│       │   │   ├── checks.py               # 存在性 / 补默认 / 参数校验（返回说明，不抛）
│       │   │   ├── scheduling.py           # 相邻只读调用并一组，**绝不重排**
│       │   │   ├── executor.py             # 执行 + 超时；**任何失败都收成结果**
│       │   │   ├── chain.py                # 位点 6 两段：检查链 + 给不执行的写结果消息
│       │   │   ├── output.py               # 规范化 + 超限截断（只截断，不留原文）
│       │   │   ├── handler.py              # 位点 3 / 6×2 / 7 / 8
│       │   │   └── builtin/                # 内置工具：文件读写与搜索、命令执行
│       │   │       ├── common.py           # 参数取值 + 路径显示
│       │   │       ├── read.py             # 读文件（带行号）
│       │   │       ├── write.py            # 整份写文件
│       │   │       ├── edit.py             # 替换唯一命中的一段
│       │   │       ├── list_dir.py         # 列目录
│       │   │       ├── glob.py             # 按文件名递归找
│       │   │       ├── grep.py             # 按正则搜内容
│       │   │       └── run_command.py      # 跑一条命令
│       │   │
│       │   ├── skill/                      # D3
│       │   │   ├── __init__.py
│       │   │   ├── manifest.py             # 声明解析（front-matter）
│       │   │   ├── scanner.py              # 扫描各 skill 根目录
│       │   │   ├── registry.py             # 来源优先级合并去重
│       │   │   ├── catalog.py              # 目录注入（持久消息、整体替换）
│       │   │   ├── loader.py               # 正文按需加载
│       │   │   └── handler.py
│       │   │
│       │   └── agent_loop/                 # D1【pipeline 已完成】
│       │       ├── __init__.py
│       │       ├── pipeline.py             # HandlerRegistry + SitePipeline（位点内按 priority 调度）
│       │       ├── trace_runner.py         # 一次 trace 的生命周期
│       │       ├── turn_runner.py          # 一次 turn 的生命周期
│       │       ├── termination.py          # completed / failed / aborted / budget_exceeded
│       │       ├── budget.py               # max_turns / max_tokens 守卫
│       │       └── agent.py                # 对外门面：run / resume / enqueue
│       │
│       ├── infra/                          # L2 基础设施（config / observability / system 已完成）
│       │   ├── llm/                        # 【已完成】OpenAI 兼容
│       │   │   ├── client.py               # 收流 + 工具调用增量拼装
│       │   │   ├── messages.py             # Message（契约）→ OpenAI 结构
│       │   │   └── errors.py               # SDK 异常 → LlmErrorKind（分类的唯一入口）
│       │   ├── approvals/                  # 【已完成】ApprovalProvider 的进程内实现
│       │   │   └── gate.py                 # 待审批表：挂起 future、按会话列、交回执（不落盘）
│       │   ├── db/                         # 数据库：连接、表结构、迁移、编解码【待做】
│       │   │   ├── __init__.py
│       │   │   ├── engine.py               # 连接池的建立与关闭（DSN 来自 AppConfig）
│       │   │   ├── schema.py               # 两张表的 DDL 与 schema 版本号
│       │   │   ├── migrate.py              # 建表与版本迁移
│       │   │   └── codec.py                # SessionEntry.payload <-> JSONB
│       │   ├── storage/                    # SessionStore 的实现
│       │   │   ├── __init__.py
│       │   │   ├── codec.py                # 【已完成】SessionEntry / SessionMeta <-> JSON
│       │   │   ├── file_store.py           # 【已完成】文件版：meta.json + entries.jsonl
│       │   │   └── postgres_store.py       # 待做：PG 版 SessionStore
│       │   ├── config/                     # 【已完成】
│       │   │   ├── defaults.py             # 默认值与默认摘要提示词
│       │   │   ├── loader.py               # 环境变量 YZY_* → AppConfig
│       │   │   └── freeze.py               # 位点 1 冻结本次 trace 快照
│       │   ├── observability/              # 【已完成】
│       │   │   ├── logging.py              # StdLogger / NullLogger / RecordingLogger
│       │   │   └── events.py               # InProcessEventBus（按确切类型匹配）
│       │   └── system/                     # 【已完成】与外界打交道的底层能力
│       │       ├── clock.py                # SystemClock
│       │       ├── ids.py                  # UuidGenerator
│       │       └── tokens.py               # HeuristicTokenCounter（启发式估算）
│       │
│       ├── composition/                    # L3 装配根【已完成】
│       │   ├── __init__.py
│       │   ├── container.py                # 构造并注入所有端口与 handler
│       │   ├── registry.py                 # 进程内会话注册表：LiveSession 活状态 + 中断
│       │   └── presets.py                  # 待做：default / coding 预设
│       │
│       └── api/                            # L4 FastAPI(HTTP) + SSE
│           ├── __init__.py
│           ├── app.py                      # FastAPI 实例、异常处理器、生命周期
│           ├── main.py                     # ASGI 入口：uvicorn 找的那个 app（唯一在模块级建 app 的地方）
│           ├── deps.py                     # 依赖注入：Agent / 会话注册表
│           ├── errors.py                   # 【已完成】ApiError：状态码 + 错误码
│           ├── schemas.py                  # 【已完成】请求/响应 DTO（pydantic）
│           ├── routes/
│           │   ├── health.py               # 【已完成】
│           │   ├── sessions.py             # 【已完成】建会话 / 列表 / 详情
│           │   ├── chat.py                 # 【已完成】发消息 / 中断：只收下活，不回内容
│           │   └── approvals.py            # 【已完成】待审批列表 + 回执
│           └── sse/                        # 【已完成】
│               ├── frames.py               # 帧类型与序列化；事件 -> data: 行
│               ├── hub.py                  # 订阅登记表：按会话找订阅者
│               └── endpoint.py             # /events 握手、断开探测、写流
│
└── tests/                                  # 每个模块一个独立测试目录
    ├── conftest.py                         # 全局 fixture（全项目只有这一个 conftest）
    ├── fakes/                              # 测试专用假件与工厂
    │   ├── clock.py                        # FakeClock（时间由测试控制）
    │   ├── ids.py                          # SequentialIds（可预测的 id）
    │   ├── session.py                      # StubSession + make_session_meta
    │   ├── handlers.py                     # RecordingHandler / HandlerSpy
    │   └── context.py                      # make_ports / make_frozen_config / make_trace_context
    ├── test_contracts/                     # 【已完成】位点规格守门测试
    ├── test_infra/                         # 【已完成】config / observability / system
    ├── test_agent_loop/                    # 【已完成】管线行为测试（trace/turn 主循环待 S9）
    ├── test_runtime_state/                 # S1【已完成】
    ├── test_session/                       # S2【已完成】
    ├── test_checkpoint/                    # S3【已完成】
    ├── test_recovery/                      # 崩溃恢复，独立于 test_checkpoint
    ├── test_compression/                   # S4【已完成】
    ├── test_hook/                          # S5【已完成】
    ├── test_sandbox/                       # S6【已完成】
    ├── test_tool_calling/                  # S7【已完成】
    ├── test_skill/                         # S8
    ├── test_api/                           # FastAPI(HTTP) + SSE 流
    ├── test_integration/                   # 跨模块集成
    └── test_recovery/                      # 崩溃注入，独立目录（需要"可失败 store"这类特殊桩）
```

> 前端静态页在 `src/web/`，不在上面的树里（它不是 Python 包）：HTML + CSS + 原生 ES 模块，
> 由 `api/app.py` 的 `_mount_web` 挂在根路径。分层见 §7.9。

---

## 4. 模块开发顺序

原则：**先纯函数、后集成**；每个模块只依赖已完成的模块 + `contracts` 端口，因此每个阶段都能独立跑单元测试。

### 4.1 阶段 0：地基（非维度）

**状态：已完成**（110 个用例全绿）。阶段 0 之后又补上了 `infra/llm` 与主循环，见下表最后三行。

| 产出 | 内容 |
|---|---|
| `contracts/errors.py` | `AgentError` 及 `ConfigError` / `SiteAbort` / `FlushError` / `ContextOverflowError` |
| `contracts/models.py` | 共享枚举与数据类：消息、工具、Runtime State、Session 日志、草稿槽、审批 |
| `contracts/config.py` | `AppConfig`（进程级）与 `FrozenConfig`（trace 级快照） |
| `contracts/ports.py` | Protocol 端口 + 聚合用的 `Ports`（阶段 0 聚合五个基础端口） |
| `contracts/sites.py` | 十位点枚举、十个位点专属 payload、`TraceContext` / `SiteContext` / `Handler`、`CancelToken` |
| `infra/config/` | 环境变量加载（`YZY_*`）、默认值、`freeze_config` |
| `infra/observability/` | `StdLogger` / `NullLogger` / `RecordingLogger`、`InProcessEventBus` |
| `infra/system/` | `SystemClock`、`UuidGenerator`、`HeuristicTokenCounter` |
| `modules/agent_loop/pipeline.py` | `HandlerRegistry` + `SitePipeline`：**只做 handler 调度** |
| `tests/` | `fakes/` 假件 + `test_contracts/` + `test_agent_loop/` + `test_infra/` |

**阶段 0 之后追加落地的两件（同样是地基）：**

| 产出 | 内容 |
|---|---|
| `infra/llm/` | OpenAI 兼容的流式客户端：`messages.py`（Message → OpenAI 结构）、`errors.py`（SDK 异常 → `LlmCallError.kind`）、`client.py`（收流 + 工具调用增量拼装 + 重试计数不接管）。**SDK 自带重试被显式关掉**（`max_retries=0`），重试归主循环，免得两边各 retry 一次 |
| `modules/agent_loop/` 主循环 | `TraceRunner`（位点 1 → while{2..9} → 位点 10，异常翻译成收尾状态）、`TurnRunner`（一轮的位点 2..9，含位点 4 收流与工具批次）、`termination.py`（终止判定）、`agent.py`（门面，组装 trace_id / Runtime State / 快照） |
| `modules/runtime_state/bootstrap.py` | 位点 1 的初始化：由内存 Session 与冻结快照建出 Runtime State。纯函数 |
| `scripts/check_llm.py` | 连通性自检：对真实模型发一条最短消息。**不打印密钥** |
| `scripts/dump_handlers.py` | 从装配根打出全部 handler 的位点与优先级，用来刷新 [`HANDLERS.md`](HANDLERS.md) |

> 骨架先行的目的：让后续每个模块写完 handler 就能立刻挂上位点跑集成冒烟，而不是等 D1 完整实现。

> 端口的分批聚合：`Ports` 阶段 0 只装 clock / ids / logger / events / tokens 五个。
> `store`、`approvals`、`tools` 的协议已在 `contracts/ports.py` 定好，但要等
> 实现类型（`ToolExecutionResult`、`SandboxDecision` 等）随模块落地后再加进去，
> 免得提前定死一个会返工的签名。`Ports` 只在 composition 层构造，加字段是兼容改动。

### 4.2 九个模块的开发顺序

| 序 | 模块（维度） | 依赖 | 为什么放在此时 | 单测可用的假件 |
|---|---|---|---|---|
| S1 | **runtime_state**（D9） | contracts | 纯数据结构 + 纯位点操作，零外部依赖，最易做到 100% 覆盖 | 无（不需要假件） |
| S2 | **session**（D4） | contracts | 真相源，投影/游标/草稿槽全是纯逻辑；后续所有模块都要读它 | FakeClock / FakeIds |
| S3 | **checkpoint**（D6） | session、SessionStore 端口 | 依赖 S2 的日志；崩溃注入测试需要稳定的落盘实现 | FileSessionStore（可注入 flush 失败） |
| S4 | **compression**（D5） | session、LlmClient 端口 | 只依赖 S2 + 一个可假的摘要模型，逻辑分支多、适合早测 | FakeLlm（返回固定摘要）、FakeTokenCounter |
| S5 | **hook**（D7） | contracts | 注册/匹配/执行全是无状态机制，随时可写 | FakeSiteHandler |
| S6 | **sandbox**（D8） | contracts | 策略判定是纯函数，审批是纯状态机 | FakeApprover |
| S7 | **tool_calling**（D2） | runtime_state、session、checkpoint、sandbox、hook | 第一次真正集成前六个模块；检查链 + 幂等 + 重试矩阵需要全部前置件 | FakeExecutor、FakeSandbox |
| S8 | **skill**（D3） | session（目录持久消息）、tool_calling（skill 工具） | 目录注入要验证「持久消息 + 整体替换」，需要 S2；正文加载是普通工具调用 | tmp_path 假 skill 目录 |
| S9 | **agent_loop**（D1） | 全部 | 只做编排，此时所有 handler 已就位 | 全部假件 + ASGI 集成 |

### 4.3 每个模块的完成口径（DoD）

1. 该模块 `modules/<name>/` 全部实现，且只 import `contracts`
2. `tests/test_<name>/` 单测通过，分支覆盖 ≥ 90%
3. 该模块声明的位点 handler 能被 `agent_loop/pipeline.py` 单独调度（集成冒烟）
4. 本文档对应模块章节的「正确设计」逐条有对应用例

### 4.4 `api/` 的位置

`api/` 不在 S1–S9 里（它是表面层，不是维度）。它的前置条件已经全部满足（见 §7.10），
可以一次写完，不用返工。

事件通道已经接上：`contracts/events.py` 定义类型，session 与内核在写日志的地方顺手发（§7.5）。
`api/sse` 只订阅与编码，不产生事件。

---

## 5. 模块详设

> 以下 D1~D9 沿用源文档的维度编号；开发顺序见 §4.2 的 S1~S9。

### D9 Runtime State（`modules/runtime_state`，开发序 S1）

**定位**：一次 trace 期间常驻内存的**执行态**，不是真相源。

**边界**
- 做：保存身份游标 / 进度与预算 / 信息 / 模型与权限四组字段；提供十个位点的操作；pending_queue 入队出队；工具成功失败聚合。
- 不做：不持有历史消息、不持有模型输出草稿、不落盘、不参与崩溃恢复。

**核心结构**

| 组 | 字段 |
|---|---|
| 身份游标 | `session_id`、`trace_id`、`parent_trace_id`、`turn_index`、`llm_attempt_number` |
| 进度与预算 | `context_tokens`、`max_tokens`、`max_turns`、`turn_tool_stats`、`compact_tokens`、`started_at` |
| 信息 | `user_message`、`pending_queue`、`is_running`、`pending_tool_calls` |
| 模型与权限 | `model`、`permission` |

**位点参与**：1、2、3、4、5、6、7、8、10 —— 九个位点各一个 handler。

> **位点 6 的职责是"摘掉不执行的调用"**：内核只对判定为 `execute` 的调用往下跑位点 7/8，所以被拒的、判定缺失的、审批没走完的调用**永远到不了位点 8** —— 而位点 8 才是执行路径的摘除点。不在这里摘，它们会一直挂在 `pending_tool_calls` 上，与这个字段"尚未落地"的定义不符，也让 `TurnEndPayload.has_pending_work` 虚高。它读的是 tool_calling 与 sandbox 填好的 `decisions`，所以优先级排在它们之后（450）。
>
> **位点 9 刻意没有 handler**：max_turns 判定归内核（2.3 第 3 条），`TurnEndPayload` 的三个字段也都由内核现算现填，没有留给执行态的东西。
>
> **位点 2 的 `turn_index++` 与位点 10 的 `is_running=false` 也不在这里** —— 那是内核固有职责（I9）。

**关键正确设计**
- **位点 3 算 `context_tokens` 有两种口径，优先用准的那个**：上一次请求发出去的 messages 若正好是这一次的前缀，就直接采信提供方报过的 `input_tokens + output_tokens`，只补算后面新增的几条；前缀对不上（压缩改写过、上下文变短）才退回全量估算。锚点存在 `SessionHandle` 上，**不落盘** —— 它是一次测量的缓存，不是事实。
- 每次成功调用都重新锚定，所以估算误差**不跨轮累积**。`input_tokens` 已经含工具 schema 与系统提示词，走锚点那条路不再另加。
- 每个参与的位点各对应一个**独立操作模块**（`ops/sNN_*.py`），函数为「读入 ctx，原地更新」，无隐藏全局状态 → 可直接逐位点断言。
- **中途消息的交接走 payload**：位点 5/8 由 runtime_state 从 `pending_queue` 整体取出、写进 `LlmAfterPayload.pending_messages` / `ToolAfterPayload.pending_messages`，session 随后落成 user 消息。取过队列即为空，不会被写第二遍。
- `turn_tool_stats` 以 `dict[tool_name, ToolStat]` 保存；**被 rejected 的调用不计入**。
- `llm_attempt_number` 进入新 turn 归零；仅「调用失败且判定可重试」时 +1。
- `RuntimeState` 由 Session 建立，来源是新会话还是刚恢复的会话**完全一样**；trace 结束即销毁。
- `pending_queue` 只在位点 5、8 出队；任何时刻可入队。

**已否决（一句话）**：曾把 `messages`、`llm_buffer` 放进 Runtime State，破坏「Session 唯一真相源」，放弃。

**单测要点**：逐位点操作的前后状态断言；`bootstrap_from_session` 对新建/恢复会话等价；rejected 调用不计数；内核固有职责的字段（`turn_index`、`is_running`）在对应位点上一动都不动。

**测试目录**：`tests/test_runtime_state/`（参与的位点各一个 `test_sNN_*.py`，外加 `test_pending_queue` / `test_tool_stats` / `test_handler`）。

---

### D4 Session（`modules/session`，开发序 S2）

**定位**：跨 trace 长期存活的事实档案，**唯一真相源**。它有两份形态：
- **磁盘形态**：只有 `meta` + `entries`
- **内存形态**：`meta` + `entries` + **上下文投影** + `context_cursor` + **草稿槽**

**边界**
- 做：append-only 日志、上下文投影与增量并入、草稿槽、标题生成、只读配平视图。
- 不做：不做缓冲落盘调度（归 Checkpoint）；不改写、不删除任何条目。

**核心结构**

| 类型 | 要点 |
|---|---|
| `SessionMeta` | `session_id`、**`user_id`**、`created_at`、`updated_at`、`parent_session_id`、`title`、`workspace`、`model`、`permission`、`tool_result_max_chars`、`status`、`context_cursor` |
| `SessionEntry` | `entry_id`、`trace_id`、`seq`（单调递增）、`timestamp`、`type`、`payload` |
| `type` 五种 | `trace_start`、`trace_end`、`message`、`tool_start`、`compaction` |

**没有计数器字段。** 会话级的"跑过几次 trace、一共几轮、上次占了多少上下文"全都在读的时候从日志算
（`api/stats.py`）：`traces` 数 `trace_start` 条目；`turns` 与 `context_tokens` 是 `trace_end` 收尾时记下的两个字段。
计数一旦落盘就成了第二个真相源 —— 它和日志对不上时，没有任何办法判断该信谁。

**文件分工**

| 文件 | 职责 |
|---|---|
| `log.py` | append-only 内存日志；分配 `seq` 与 `entry_id`；按 id / 类型查询 |
| `projection.py` | **全量投影**（三条来源规则）与草稿消息的包装。纯函数 |
| `cursor.py` | 投影起点（`context_cursor`）的推导、按起点取条目、把一批条目并进上下文 |
| `draft_slot.py` | 会话级单槽草稿 |
| `reader.py` | 对一段 entries 的只读视图，复用同一套投影规则，**不写回** |
| `session.py` | 聚合根，实现 `SessionHandle` 端口 |
| `title.py` | 把第一条用户消息压成一行标题。纯函数，不碰日志 |
| `handler.py` | 位点 1 / 3 / 5 / 6 / 8 / 10 的 handler。**所有日志写入都在这里** |

**关键正确设计**
- **投影规则**：从投影起点（最近一次 `compaction`）取起：① 装入其 `summary`；② 装入它 `entry_ids` 指向且仍保留的 `message`；③ 装入它之后所有 `message`。无 `compaction` 则从第一条 `message` 起。`trace_start` / `trace_end` / `tool_start` **不进 messages**。
- **摘要消息的角色是 `user`**，正文前加一句交代来历，保证它能独立阅读。
- 若草稿槽非空，把 `draft.content` 作为一条 **assistant** 消息接在投影末尾（附一句「上一轮输出被打断，以下是已产生的部分」）。草稿不在日志里，所以它是**读时叠加**的：`messages()` 每次现拼，不叠进物化上下文。
- 投影**不每次重算，但结果与全量重算逐条相等**。
- **`context_cursor` 是投影的起点**，不是"每轮前进的读指针"：空串 = 从头；否则 = **最近一次 `compaction` 的 entry_id**，投影从它开始（**含它**）。它只在重建时算一次 —— Session 构造、崩溃恢复、每次压缩之后。它镜像在 `SessionMeta.context_cursor` 上，由屏障随 meta 一起落盘；**恢复时仍按全量重算**，磁盘上那个值只作留痕。
- "已经并到哪了"是另一回事：那是内存里的增量记账，不进 meta、不落盘。重建只在三处发生——Session 构造、压缩之后、崩溃恢复。`append_entry` **不等于**并入，新条目要等下一次 `messages()`。
- **标题只写一次**：素材是会话的第一条用户消息，条件是 `meta.title` 还是 `None` —— 用户显式给过标题（`SessionRegistry.create(title=...)`）或已经写过一次都不再改。压成一行：换行与连续空白并成一个空格，超过 30 字截断并把省略号算在上限内；没有可用文本（空消息、只有图片或工具调用）就保持 `None`。它只是 meta 的一个字段 —— 不新增条目类型，靠屏障随 meta 落盘。
- **会话必须有主**：`SessionMeta.user_id` 是必填的。隔离落在**存储层**：`load(session_id, user_id)` 对不上就返回 `None`，`list_sessions(user_id)` 只列这个用户的 —— 两处都是必填参数，没有"列出所有人的会话"这个用法，省得哪天漏传就漏了别人的数据。`"不存在"` 与 `"不是你的"` 回同一个 `None`，调用方一律按 404 处理。**它不做鉴权** —— "是谁的用户"由 api 层确认，存储只认这个值。
- `trace_start` / `trace_end` 只放**推不出来**的事实（触发者、父子关系、模型、权限、预算、压缩配置、结束状态）；耗时、turn 数、token 量都不写。
- **草稿槽单槽**，两个清空点，缺一不可：
  - **位点 5 定稿** —— 内核收流结束时调 `take_draft()`，取出的内容拼成 assistant 消息（`turn_runner._collect_stream`，发生在位点 5 的管线之前）。
  - **位点 3 取材之后** —— 由 session 的 handler 清空，且排在 compression 之后。
  - 草稿**永不落盘**。
- 多表面（Web / 其他前端）按同一 `session_id` 挂到同一会话，共享同一份 entries 与同一追加通道。
- 只读观察方（历史列表、审计）读日志时**套用同一套投影规则**，但**不写回**（写回只有恢复侧做）。`reader.py` 就是它：没有游标、没有草稿槽、没有任何可变状态。
- 位点 6 只给判定为 `execute` 的调用写 `tool_start`。
- 位点 8：tool_calling 没给 `normalized` 时，由 session 兜底拼一条结果消息并记 warning。

**已否决（一句话）**：曾把「当前上下文」存成字段、或每次取材都从头重算投影，两者都被「投影本该是确定性视图」否定。

**单测要点**：四类投影场景（无 compaction / 有 compaction / compaction 后又追加 / 草稿槽并入）；**增量并入 == 全量重算**；草稿槽两个清空点；`seq` 单调（含续写与手塞）；只读视图不改写；只给要执行的写 `tool_start`；结果消息兜底；标题只写一次、空消息不出标题、截断不超上限。

**测试目录**：`tests/test_session/`；内存存储的契约测试在 `tests/test_infra/test_storage.py`。

---

### D6 Checkpoint（`modules/checkpoint`，开发序 S3）

**定位**：决定 Session 日志**什么时候必须落盘**，并在崩溃后把磁盘日志恢复成内存 Session。它**不产生任何结构**。

**边界**
- 做：五个屏障的调度、语义配平、恢复七步。
- 不做：不定义新条目类型、不参与 Runtime State 重建。

**四个屏障位点**（已落盘是**下界**，屏障之间仍可批量落盘）

| 位点 | 为什么必须落盘 |
|---|---|
| 3 llm:before | 请求发出前已落盘，响应前崩溃不会重放未落盘的请求 |
| 5 llm:after | 本轮 assistant 消息（含它请求的工具调用）先落盘再进工具阶段 |
| 6 tool:before | 外部副作用发生前落盘；并写下 `tool_start` 标记 |
| 8 tool:after | 本批工具结果先落盘再进下一轮 |

**关键正确设计**
- **失败即阻止**：位点 3 落盘失败拦掉模型调用；位点 6 落盘失败拦掉工具正文。
- **可恢复前缀 = 最后一次成功 flush 的前缀**，其后缓冲内容崩溃后视为不存在。
- **落盘是原子的**：文件实现写临时文件后整份 `os.replace`，PG 实现提交事务。盘上要么是上一次完整快照、要么是这一次，不会留下半条条目——恢复侧因此不需要截断。
- **恢复判据**：日志末尾**没有 `trace_end`**，或最后一条 `trace_end` 的 `status == aborted`。
- **恢复七步**：① 装 meta ② 装 entries ③ 补悬空工具调用结果 ④ 补未收尾 trace 的 `trace_end(aborted)` ⑤ 合成事件落盘 ⑥ 重建投影、游标推末尾 ⑦ 草稿槽置空。（③④⑥⑦ 只在「没有 trace_end」时做）
- **悬空调用三情形**（判据 = 有没有 `tool_start` × 副作用等级/是否接受幂等键）：

  | tool_start | 副作用等级 | 补成什么 | 可重试 |
  |---|---|---|---|
  | 无 | 任意 | 开始前被中断 | 可安全重试 |
  | 有 | none / idempotent / 接受幂等键 | 结果未知 | 可安全重试 |
  | 有 | non_idempotent 且不接受幂等键 | 结果未知 | **不自动重试**，先验证或问用户 |

- 恢复时的重试判定**复用**工具声明里那一条，不另立规则。
- 只看 `aborted`，不看 `failed` / `budget_exceeded`（后两者是位点 10 正常收尾，留不下悬空调用）。
- 位点 10 只补 `trace_end`，**不补工具调用**——留待下一次 trace 开始前的恢复处理。

**已否决（一句话）**：曾用「每条目立即 fsync」作为落盘时机，性能不可接受，放弃。

**单测要点**：屏障把整份日志推出去、没有新条目时是空操作；落盘失败时水位不动、重推不产生重复行（连冷启动的存储也算）；尾部三情形；悬空三情形补出来的正文；补出来的条目算在**出事的那次** trace 名下且立刻落盘；恢复之后再重启一次得到 `aborted` 尾部。

**测试目录**：`tests/test_checkpoint/` + `tests/test_recovery/`（崩溃注入独立放）。

---

### D5 压缩（`modules/compression`，开发序 S4）

**定位**：上下文触达阈值时的两级压缩。**先便宜后昂贵**。

**唯一触发点：位点 3 llm:before**

`context_tokens` 触达 `compact_tokens` 就压，压完才发请求。**位点 5 不做压缩**：
压完仍放不下时模型会把这次调用挡回来，本次 trace 按 `budget_exceeded` 收尾——
不重试、不再压。

**两级**

| 级 | 做法 | 代价 | 是否落盘 |
|---|---|---|---|
| 一 | 把较早的长工具结果正文换成占位词（保留工具名+关键参数+原文规模） | 零模型调用，纯字符串替换 | **否**，只改内存上下文 |
| 二 | 保留最后 n 条 message，其余 + 上一条摘要 → 摘要模型产出新 summary → 追加 `compaction` 条目 | 一次模型调用 | **是** |

**关键正确设计**
- 一级做完**只减去它省下的量**，不重数整份上下文 —— 一级换掉的那几条长结果自己知道自己省了多少。仍触达才升二级。重数一遍 900K 字符的上下文要几十毫秒，而且它卡在事件循环上，所有会话一起等。
- **工具 schema 要一直算在里面**：它不是 messages 的一部分，重数 messages 数不到它。压缩只动 messages，schema 那份原样保留，由位点 3 算一次放进 payload，二级重建上下文之后补回去。
- **模型看到压缩后的上下文时，对应的 `compaction` 必须已经在盘上。** 由位点 3 的屏障保证：压缩排在 session(300) 之前把条目写好，屏障(900) 在模型调用之前把它推下去；屏障失败抛 FlushError，那次模型调用根本不会发生。
- **一级只改"这一轮发出去的那份"**：entries 一个字节不动，下一轮投影拿到的还是原文。
- **二级从真投影取素材**，不是从一级的输出取 —— 否则长工具结果已经变成占位词，摘要里就只剩"已省略"。
- **二级之后要再补一次一级**：二级换掉了投影起点，重新取到的上下文里，保留窗口那几条长工具结果是全文；不再补一遍，一级那一趟就等于白做。
- **不构成 turn**：摘要调用不产生 assistant 消息、不进 messages、不加 `turn_index`，可以用比主模型更便宜的模型。
- **压缩是有损的**：被压掉的正文不保留任何副本，摘要取代它们。
- **上一条压缩会被一并压掉，不叠加**：无论压多少次，上下文里永远只有一条摘要。
- 被保留的 n 条**只留 `entry_id` 引用**，不内嵌内容。
- 压缩**不改写、不删除**任何旧条目。
- 摘要调用**不构成 turn**：不产生 assistant 消息、不进 messages、不加 `turn_index`；可用与主模型不同的模型。
- 摘要必须**能独立阅读**；提示词逐条明确保留：任务目标、已拍板决定、已完成工作与结果、当前状态与下一步、失败的尝试与原因、必须逐字保留的内容（路径/标识符/参数/数字/报错原文）。明确要求舍弃：客套话、已被推翻的中间结论、重复的过程叙述。
- 二级重建了上下文，**只有这一步必须重数一次**（重建后的列表只剩摘要 + 保留窗口）。
- 二级做完仍触达阈值 → `guard` 抛 `ContextOverflowError`：请求压根不发出去，`trace_end` 记 `budget_exceeded`。
- **估算偏了、模型真的挡回来**时同样抛 `ContextOverflowError`（`kind` 固定为 `OVERFLOW`），处置一样：不重试、不再压，按预算失败收尾。

**已否决（一句话）**：曾让压缩改写旧条目或做「摘要的摘要」叠加，均因破坏 append-only 与单摘要不变量而放弃。

**单测要点**：一级替换不改变 entries、返回新列表；二级保留的是最后 n 条且只留 `entry_id`；**压第二次时上一条摘要在被压掉的那段里**（摘要永远只有一条）；草稿不参与摘要；模型没吐东西就不写空摘要；压完仍超限时 `guard` 抛 `ContextOverflowError`（budget_exceeded）。

**测试目录**：`tests/test_compression/`。

---

### D7 Hook（`modules/hook`，开发序 S5）

**定位**：在固定位点注入用户逻辑的机制。它自己也走 `HandlerRegistry` 挂上去 ——
只是它注册的 handler 干的事是"替用户跑他们挂上来的那些 hook"。

**边界**
- 做：Hook 注册、匹配、执行；`agent/*` 事件。
- 不做：Hook 不得绕过位点语义（如跳过落盘、改写历史条目）。

**挂法**

| 挂法 | 回调签名 | 返回值 |
|---|---|---|
| `registry.on(event, fn)` | `fn(ctx)` | 位点 1/6 的 `False` 是否决；别处忽略 |

- 事件名**就是十位点**（`events.SITE_EVENT` 是唯一映射处），不新增扩展点。
- 同一事件内按**注册顺序**跑，不另设 per-hook 优先级 —— 要 A 先跑就先注册 A。
- 名字在整张表内唯一；`remove` 只摘得动用户注册的。
- 注册表整个交给容器：`container.hooks.on(...)`，不用碰管线。

**handler 与它的 priority**

| handler | 挂载面 | priority | 为什么在这个位置 |
|---|---|---|---|
| `hook.site` | 十个位点全覆盖 | `500` | hook 段位的下界：晚于维度（100–499）才看得到它们算出来的结果，早于屏障（900）改的数据才落得下去 |

**关键正确设计**
- **否决只在位点 1 与位点 6 有语义**，与内核一致：位点 1 不放这次 trace 开始，位点 6 不放这批工具执行。其余位点返回值一律不看，返回 `False` 记一条 `hook.reject_out_of_contract` 后继续。
- Hook 失败**不静默**：抛 `SiteAbort` 这类领域异常原样上抛（那是 hook 有意的控制流）；抛别的记 `hook.failed`，有否决语义的位点按**拒绝**处理，其余位点跳过它继续。
- **内置与用户的边界**：同表、同排序、同一个命名空间（重名注册直接报错）；差别只有一条 —— 用户注册的摘得掉，内置的摘不掉。

**待做：工具守卫（`tools/pre_execute`）**

原设计里还有一层"工具执行守卫"（`guard.py`），**现在不做** —— 它判的是位点 6 的 `decisions`，
而那个通道要等 S7 tool_calling 落地才有人写 `execute`：今天挂个守卫上去，只能否决一个本来就跑不了的调用，
而且它的 priority（要不要插进维度段、插在谁前面）、它的粒度（跟位点 6 一起逐条，还是保持整批管线里逐条问），
都取决于那时检查链的实际形状。写 S7 时连着一起定。

**已否决（一句话）**：曾用「继承主循环子类、覆写方法」做扩展，耦合过深，放弃。

**单测要点**：注册顺序与事件匹配；位点 1/6 否决短路、别处返回 `False` 被忽略并记 warning；hook 异常的三种口径（`SiteAbort` 直穿 / 能否决位点按拒绝 / 其余跳过）；名字唯一与注销规则；容器接线后端到端能跑到。

**测试目录**：`tests/test_hook/`。

---

### D8 沙箱与权限（`modules/sandbox`，开发序 S6）

**定位**：工具执行的安全边界。**不判「危险」，判「越界」** —— 危险是主观的、永远有绕过；
而「碰的是什么 / 允许碰什么 / 在不在里面」三个问题都答得出来。

**边界**
- 做：`Permission` 三档比较；参数 → 资源的解析；工具级 + 参数级判定 → 三态；把结论写进 `decisions`；人工审批的往返。
- 不做：不执行工具本身；不做存在性与参数 schema 校验（归 tool_calling）；不做落盘调度（归 checkpoint）。

**三态**（复用 `ToolDecision`，不另立枚举）：放行 `EXECUTE` / 用户审批 `AWAIT_APPROVAL` / 拒绝 `REJECT`。

**两层路由：一层静态、一层动态**

| 层 | 看什么 | 谁做 |
|---|---|---|
| 这工具**能**碰什么 | `parameters_schema` 里声明了哪些参数名 | `policies.route(declaration)` |
| 这一次碰**成什么样** | 实际传进来的值 | `policies.resolve_path(arguments, workspace)` |

只看参数值是不行的：`write` 少给一个 `path` 就变成「不碰文件」，整类判定凭空消失。
**参数名是形状，值是内容 —— 判不了内容不等于没有形状。**

路由靠**参数名约定**（`path` → 文件、`url` → 网络、`command` → 命令），不新增声明字段。

**判定分两类资源，处置不同**

| 类 | 例子 | 档位不够时 | 为什么 |
|---|---|---|---|
| **边界类** | 文件 | **拒绝** | workspace 划的线是明确的，不够就是不够 |
| **能力类** | 命令、网络 | **用户审批** | 能力无法静态判定，硬拒绝等于替用户做决定 |

命令类的能力**由策略定死要 `full_access`**，不看工具自己声明什么 —— 一个 command 工具把
`required_permission` 声明低了，不该因此让它在中低档位跑起来。

**参数级只判文件类**：
- 规范化之后在工作目录内 → 放行；
- 越界 → **用户审批**（不是规则明确禁止的事，让用户自己决定这一次）；
- **判不出来 → 用户审批**（缺参数、类型不对、路径非法）。判不了不等于没危险。

命令与网络没有参数级判定：`bash -c "..."` 里是什么，关键词黑名单认不出来（`r''m -rf /`、
base64、`$(...)` 都能绕）。**判不准的东西在安全边界上不能给放行。**

**关键正确设计**
- **规范化必须在判定之前**：`sub/../../x`、软链接、`~` 全靠 `resolve()` 挡；两侧都要规范化，否则大小写差异就会漏。
- 判定是**纯函数** `(permission, declaration, arguments, workspace) -> Verdict`，不碰端口、不碰时钟、不写日志 —— 三档 × 参数组合可以穷举。
- 返回 `Verdict`（结论 + 理由）而不是光秃秃一个枚举：要人审批时得告诉用户**为什么**，理由是判定的副产品。
- **执行边界在执行前生效**：结论写进位点 6 的 `decisions`，内核只对 `execute` 的调用往下跑位点 7/8。
- **不写 = 拒绝**，但沙箱只对**判得了的**调用落笔：查不到声明的留给 tool_calling 的存在性检查，边界不替它发言。
- 审批**无可用应答方时 fail closed**（落成拒绝），不静默放行。
- **没同意就是没同意，等人那一步不设兜底**：`await approvals.request(...)` 一直挂着，直到有人回执。不搞超时，也不跟 `ctx.signal` 竞速 —— 挂着的那次 trace 本来就是个未收尾的执行，交给恢复路径处理（见 §9）。
- **× 的语义不在这一层**：系统层只认「有没有回执、回执是什么」，判不出「×」是什么意思。所以必须由前端明确表态（关框时照样发 `{"decision": "deny"}`），后端不做推断。

**依赖怎么来**

| 端口 | 缺了怎么办 |
|---|---|
| `Ports.tools`（`ToolRegistry`） | 一条都不判。今天就是缺的（tool_calling 未落地），所以本模块现在完全是惰性的 |
| `Ports.approvals`（`ApprovalProvider`） | 把「等审批」落成拒绝 |

**已否决（一句话）**：曾想用「执行后审计」替代执行前拦截，无法阻止副作用，放弃；
也曾想让 `ToolDeclaration` 声明「工具粒度还是参数粒度」，但工具粒度的判定是参数粒度判定的特例
（不看参数就是工具粒度），二选一里只有一端有用，且同一条命令在不同档位下需要不同粒度。

**待做**：位点 7 的运行时边界复核（要等 S7 有执行器）；`url` 的 host 允许列表（见 §9）。

**单测要点**：三档权限 × 参数组合的决策矩阵；`..` 不能绕过边界；参数缺失 → 问人而不是放行；
工具自报低档位不影响命令类的判定；一条调用同时碰文件与网络时两类都判；ask 无应答方 fail closed；
审批通过/拒绝两条回执路径；要审批时广播 `ApprovalRequired`。

**测试目录**：`tests/test_sandbox/`。

---

### D2 Tool Calling（`modules/tool_calling`，开发序 S7）

**定位**：工具注册、检查、执行、结果回填；MCP 是本维度的远程工具来源。

**边界**
- 做：注册表与可见性；位点 6 检查链；幂等键；执行/重试/超时；输出规范化与超限截断；MCP 适配；内置工具。
- 不做：不做审批决策本身（调 sandbox 端口）；不做落盘调度（调 checkpoint 端口）。

**工具声明（静态事实）**：名称、描述、参数 schema、输出契约、**副作用等级**（`none` / `idempotent` / `non_idempotent`）、**是否接受幂等键**、权限、执行逻辑。

**位点 6 检查链（顺序固定）**
1. 存在性检查
2. 补默认参数
3. 参数校验
4. 权限审批（工具级 / 参数级）
5. 重复调用检测（**指纹 = 工具名 + 参数，不含 tool_call_id**，只提醒不拦截）
6. 并行/串行判定（无法判定按串行）
7. 生成幂等键 `hash(session_id + trace_id + tool_call_id + function_name + sorted_arguments)`
8. 追加 `tool_start` 条目并落盘
9. 给每一条**不执行**的调用写结果消息（见下一条）

**关键正确设计**
- **不执行的调用的全部收尾都在位点 6 完成**，内核只对判定为 `execute` 的调用往下跑位点 7/8。在位点 6 的检查链末尾要给每条不执行的调用（`reject`，或审批没走完的 `await_approval`）：① 把 `call.status` 置为 `rejected`；② 追加一条配对的 tool 结果消息；③ 记一条拒绝事件。这些调用随后由 runtime_state 在位点 6 从 `pending_tool_calls` 里摘掉。
- **工具失败也是结果，不是 trace 失败**：检查链判不通过 → `REJECT` + 一条配对的工具结果消息；执行期任何异常（超时、文件不存在，以及工具自己写挂）→ 收成 `ToolAfterPayload.error` + `raw_result`，由 session 落成一条 tool 消息。模型必须拿到「为什么没成」才能改，上抛只会让整次 trace 挂掉。**只有 `SiteAbort`（内核的控制流）与 `CancelledError` 直穿。**
- 被拒调用的结果消息要写**具体原因**（「缺少必填参数：path」），不能只写「被拒绝」—— 笼统的说明只会让模型重复同一个错误。这跟沙箱 `Verdict.reason` 是同一个道理。
- 位点 8 的统计口径：被拒的调用不计入成功也不计入失败。
- 运行时对幂等键只有两件事：**生成并写入日志、传递给工具**。**不做「查它做过没有」的跳过**。
- 键里含 `tool_call_id`，所以标识「这一次具体调用」；重试/重放时 id 不变、键不变；用户想扣两次则是两次调用、键不同。
- 键买到的是**可重试性**，价值在「重试」与「崩溃恢复重放」两处兑现。
- 构造参数可**按业务灵活取用**：只读工具可只保留 `tool_call_id`；业务单号类需求换成业务字段。
- **重试规则**：`none` / `idempotent` 可重试；`non_idempotent` **仅在接受幂等键时**可重试；超时走同一规则。此规则与恢复判定**同一条**。
- 位点 8：规范化输出 → 超过 `tool_result_max_chars` 时按字符截断并在末尾标注原文总长 → 结果写入 Session 与内存消息列表 → 发送完成事件。幂等键不重复写（位点 6 已随 `tool_start` 落过）。
- **超限只截断，不留原文、不留引用**：超过 `tool_result_max_chars` 的部分直接丢弃。要全文的场景由工具自己写进工作区，框架不再存第二份。
- MCP：MCP tool 经 adapter 变成 `ToolDeclaration`，对 kernel 与模型**完全透明**，与本地工具同一条检查链。

**落成了什么样（与计划的差异）**

| 计划里有 | 结果 | 为什么 |
|---|---|---|
| `idempotency.py` | **没做** | 现在没有工具需要幂等键。`ToolDeclaration.accepts_idempotency_key` 留着，`checkpoint.rebalance` 的重试判定在读它 |
| `dedup.py` | **没做** | 「只提醒不拦截」，没有用户之前是空转 |
| `mcp/` | **没做** | 首批 transport 还没定（§9） |
| `builtin/` 只做了 `read` | **七个都做了** | 见下面的「内置工具集合」 |
| 位点 6 一个 handler | **两个** | 检查链（100）与收尾（445）中间隔着 sandbox（435）——「给不执行的调用写结果消息」必须等所有判定落定 |

**内置工具集合**

| 工具 | 权限 | 副作用 | 做什么 |
|---|---|---|---|
| `read` | `read_only` | `none` | 读一个文件，返回带行号的内容（可给起始行与行数） |
| `list_dir` | `read_only` | `none` | 列一个目录的条目，目录在前、带尾斜杠 |
| `glob` | `read_only` | `none` | 按文件名模式递归找文件 |
| `grep` | `read_only` | `none` | 按正则表达式搜文件内容，返回路径:行号:内容 |
| `write` | `editable` | `idempotent` | 整份写一个文件，缺的父目录一并建好 |
| `edit` | `editable` | `idempotent` | 替换文件里唯一命中的一段；零次或多次都不改 |
| `run_command` | `full_access` | `non_idempotent` | 跑一条命令，标准错误并进输出（Windows 用 pwsh，其它平台用 sh） |

- **参数名不是随便起的**：文件类工具的参数必须叫 `path`、命令类必须叫 `command` —— 沙箱按参数名路由（D8），名字换了那一类判定就**整类静默消失**。有一条测试专门钉住这件事。
- **回报的路径一律相对工作目录**，模型可以原样把它传给下一个工具。
- 失败时说**给人听的一句话**（"文件不存在：a.py"、"old_string 出现了 2 次，多带几行上下文再试"），由执行器收成工具结果交给模型。
- `edit` 要求原文唯一：出现多处时改哪一处是猜的，猜错比不改更难收拾。

**并行/串行编排**：`scheduling.plan_for` 把一批调用切成若干组，**组内并发、组间串行**。规则只有一条 —— **相邻的只读调用并成一组，绝不重排**：会改内容的工具前后可能有因果关系，所以 `[read, write, read]` 是 `[[read], [write], [read]]` 而不是 `[[read, read], [write]]`。判定「能不能并行」看的是 `SideEffect.NONE`，不另设字段。

**已否决（一句话）**：曾让运行时用幂等键或指纹做去重/硬拦截，前者越权、后者误杀，均放弃。

**单测要点**：检查链每一步（含中止分支）；幂等键对相同输入稳定、对不同 tool_call_id 不同；重试矩阵（副作用等级 × 接受幂等键 × 超时/失败）；输出规范化与截断标注；MCP 适配与调用。

**测试目录**：`tests/test_tool_calling/`。

---

### D3 Skill（`modules/skill`，开发序 S8）

**定位**：可复用的任务指令包（目录 + 入口文件 + 可选脚本/资源）。

**边界**
- 做：扫描发现、声明解析、来源优先级合并去重、目录注入、正文按需加载。
- 不做：不执行 skill 里的脚本（走普通工具调用）。

**声明项**：名称（kebab-case）、描述、何时使用（可选）、调用策略（模型可调用 / 用户可调用）、正文、资源与脚本。

**关键正确设计**
- **发现阶段只读声明**，正文按需加载（靠 `skill` 工具）。
- **不可模型调用的 skill 不进目录**。
- 目录的注入是**一条持久消息**，不是每轮拼接：内容没变不重复下发；变了**整体替换**（含空目录，用于显式停用旧名称）。这样它落在可复用前缀之后，不破坏已缓存上下文。
- 正文加载与脚本执行都是普通工具调用，走 D2 的位点 6/7/8，本模块不重复。
- 来源优先级合并去重后再登记。

**已否决（一句话）**：曾把目录每轮拼进 system prompt，破坏前缀缓存且产生重复消息，放弃。

**单测要点**：多来源合并去重的优先级；目录内容不变不重复注入、变化整体替换（含空目录）；模型不可调用被排除；正文加载。

**测试目录**：`tests/test_skill/`（用 `tmp_path` 造 skill 目录）。

---

### D1 Agent Loop（`modules/agent_loop`，开发序 S9）

**定位**：主循环与位点管线，唯一认识「trace / turn」的模块。

**边界**
- 做：位点调度、trace/turn 生命周期、终止判定、预算守卫、pending_queue 消费时机、对外门面。
- 不做：不认识任何具体维度；维度逻辑一律通过 handler 与端口。

**主循环形态**
```text
位点1 → while:
  位点2 → 位点3 → 位点4(收流) → 位点5
  对每个 pending_tool_call: 位点6 → 位点7 → 位点8
  位点9 → 终止?
位点10
```

**关键正确设计**
- **turn 定义**：一次大模型调用 + 它触发的工具调用。`turn_index` 从 0 起，位点 2 加一。
- 终止状态四种：`completed` / `failed` / `aborted` / `budget_exceeded`，记在位点 10 的 `trace_end`。
- `pending_queue` 消费只在位点 5、8，不打断当前动作。
- 位点 3 落盘失败 → 拦掉本次模型调用；位点 6 落盘失败 → 拦掉工具正文（Checkpoint 的「失败即阻止」由主循环执行）。
- 管线是**串行**的（按 priority），不并行执行 handler。但**同一个工具组里的调用是并发的** —— `plan.groups` 由 tool_calling 在位点 6 算出来，内核按组 `asyncio.gather`，组间按顺序。编排是批级决策（谁跟谁能同时跑要看整批），执行是逐条的，内核只负责照做。
- **位点 7 → 8 的结果由内核搬运**：位点 8 的 payload 是内核新造的，位点 7 填的 `raw_result` / `error` 得由中间这一层递过去（跟位点 4 的收流结果交给位点 5 同一个形状）。组内并发时每个调用各搬各的，天然隔离。
- **模型调用失败分三种不重试**：种类本身不可重试、尝试次数用光、已经吐出半截输出（I11）。前两种把真实异常原样上抛，让 `trace_end.reason` 留下真正的原因。

**已否决（一句话）**：曾把各维度逻辑硬编码进主循环，导致新增维度必改内核，放弃。

**单测要点**：纯逻辑 turn 循环（全部假 handler）；终止四态；pending_queue 合并时机；位点失败阻断；预算截断。

**测试目录**：`tests/test_agent_loop/`。

---

## 6. 非维度基础设施

### 6.1 `infra/llm`（已完成，OpenAI 兼容）

- 统一**流式**接口：所有厂商差异归一成五种分片——文本增量、思考增量、**完整**工具调用、用量、结束。
- **思考增量**（推理模型在正文之前吐的那段）从 `reasoning_content` 取。开关走 `extra_body`
  （`YZY_THINKING=true`），它不是 OpenAI 标准参数。
- **带 `tools` 的请求必须把上一轮的思考原样传回去**，否则服务端 400 —— 这是 DeepSeek 明文要求的，
  所以 `messages.py` 会把 `ThinkingPart` 拼成 `reasoning_content` 挂回 assistant 消息，token 估算也算它。
- **工具调用增量 JSON 的拼装在实现内部完成**（按 index 归并 id / name / arguments），内核拿到的永远是拼好的一条。
- **错误翻译**：SDK 那一堆类型在 `errors.py` 里收敛成一张表。分类只做一次，主循环只认 `LlmErrorKind`。

| 失败 | 种类 | 处置 |
|---|---|---|
| 密钥不对、没有权限（401 / 403） | `AUTH` | 上抛 |
| 请求不合法、模型或接口不存在（400 / 404 / 其余 4xx） | `REQUEST` | 上抛 |
| 上下文放不下（错误码或错误文本认出来的） | `OVERFLOW` | 上抛，`trace_end` 记 `budget_exceeded` |
| 被限流（429） | `RATE_LIMIT` | 重试 |
| 服务端 5xx | `SERVER` | 重试 |
| 连不上 / 连接中途断掉 | `CONNECTION` | 重试 |
| 超时 | `TIMEOUT` | 重试 |
| 认不出来（含非 SDK 的普通异常） | `UNKNOWN` | 上抛 |

- **超窗是独立类型**：`ContextOverflowError` 是 `LlmCallError` 的子类，`kind` 固定为 `OVERFLOW`。两个来源——位点 3 的 `guard` 拦下的，或模型挡回来的——处置一样。
- **重不重试由种类推出来**：`retryable` 是 `kind` 的派生属性（就是 `RETRYABLE_KINDS` 那张表），不另存一份。`llm.retry` 日志带 `kind`，排错不用猜是哪种失败。
- **不接管重试**：SDK 自带重试关掉（`max_retries=0`），重试由主循环做——它才掌握 `llm_attempt_number` 与"这次尝试有没有产出"（I11）。
- **它在循环里的位置**：装配根把真客户端放进 `ports.llm`，主循环在 `turn_runner._collect_stream` 里**过 `LlmClient` Protocol** 调它的 `stream`。所以直接搜 `OpenAiCompatibleClient` 只会找到装配根与自检脚本 —— 调用点上写的是 `trace.ports.llm.stream(...)`，不点名任何具体类。
- 原先规划成单独文件的 `providers/`、`streaming.py`、`retry.py` 都没有落地：单 provider 时收流并进了 `client.py`，重试归主循环。
- 密钥只在 `build_openai_client(config)` 里读一次，**不进 trace 快照、不落 Session 日志、不打日志**。缺失时启动阶段就抛 `ConfigError`。
- token 计数仍在 `infra/system/tokens.py`（`HeuristicTokenCounter`），不在这里重复一份。
- 待办：主模型与摘要模型的路由（压缩落地时再加）。

### 6.2 `infra/db` 与 `infra/storage`

**两张表**

| 表 | 内容 |
|---|---|
| `sessions` | `SessionMeta`：`session_id` 主键，其余字段一一对应；`user_id` 上建索引（会话列表按它筛） |
| `session_entries` | append-only 事实流：`entry_id` 主键，`session_id` 外键，`seq` 会话内唯一，`payload` 存 JSONB |

**`infra/db`** —— 与"存什么"无关的部分：连接池、表结构、迁移、payload 的 JSONB 编解码。

**`infra/storage`** —— 端口实现：`file_store`（文件版，现在用）与 `postgres_store`（PG 版，待做）。

**磁盘布局**：一个会话一个目录。

| 路径 | 内容 | 什么时候写 |
|---|---|---|
| `<session_dir>/<session_id>/meta.json` | 元信息，可被覆盖 | `save_meta` 整份替换 |
| `<session_dir>/<session_id>/entries.jsonl` | 条目，一行一条 | `flush` 整份替换 |

**关键正确设计**
- **`load` 只读文件，不读内存。** 它服务的场景就是"这个会话已经不在内存里"（重启之后的 `get_or_load`）。
- **落盘是整份替换**：写 `.tmp` → `fsync` → `os.replace`。盘上要么是上一次的完整快照、要么是这一次，不会留下半条条目，所以恢复侧不需要截断。
- **写路径不解码**：`flush` 读回 jsonl 文本、拼上新行、整份替换；只有 `load` 解码。
- **内存里只留增量**：`append` 进来、还没 `flush` 的那一截。它不是会话的第二份，flush 之后立刻清空。
- **meta 跟着条目一起落**：屏障处 `Checkpoint.ensure` 推完条目后调 `save_meta(session.meta)`。游标不单开落盘路径 —— 它是 meta 的一部分，而 `save_meta` 本来就是整份替换。写 meta 失败时落盘水位不推进，下一次屏障重试整段。
- **`codec`** 负责 `SessionEntry` / `SessionMeta` ↔ JSON，带类型白名单。规则：dataclass 编成带 `__type__` 的对象、`StrEnum` 存 `value`、`datetime` 存 ISO 8601。PG 的 `infra/db/codec.py` 复用同一套。
- 端口方法是**异步的**（PG 驱动只有异步 API）。文件实现里的落盘是同步完成、直接阻塞事件循环几毫秒。
- PG 版的 `flush(session_id, up_to_seq)` 就是**提交事务**：append 进来的条目在提交前对别人不可见，提交后才是"已落盘"。
- **单写者假设**：整份替换是"覆盖"而不是"追加交错"，两个写者同时刷同一个会话会互相抹掉。文件实现只在单进程单写者下成立。
- 两个实现**共用同一套契约测试**。

### 6.3 `infra/config`

- 配置项：模型、权限、`max_tokens`、`max_turns`、压缩阈值比例 `compact_ratio`、保留条数 n、长工具结果判定与保留条数、摘要提示词模板、思考开关（`YZY_THINKING`）、沙箱策略、审批策略。
- `compact_ratio`（默认 **0.7**）算出 **`compact_tokens = int(max_tokens × compact_ratio)`**，在冻结时换算一次。**阈值不是独立配置项**：窗口一改，它跟着走，不存在"换了模型忘了改阈值、压缩永远不触发"这种事。
- **哪些是会话级的**：`model`、`permission`、`tool_result_max_chars` 由 `SessionMeta` 携带（建会话时从 `AppConfig` 抄一份初值），位点 1 从会话取；其余项只有全局一份。
- 位点 1 **冻结**本次 trace 快照；之后改配置不影响已开始的 trace。

**取值来源与优先级**（后面覆盖前面）：

1. 项目根目录的 `.env`（路径可用环境变量 `YZY_ENV_FILE` 指定，设成空串则不读）
2. 真实环境变量

真实环境变量优先，是为了让容器 / CI 里的注入能盖掉开发机上的 `.env`。
`uv run` **不会**自动加载 `.env`，所以这一步由 `loader.load_app_config` 自己做——
以后用 uvicorn 起服务也照样生效。

两个文件：`.env`（本机实配，已 gitignore）与 `.env.example`（模板，提交）。
解析器是自带的极简实现（`dotenv.py`），不引第三方依赖，支持注释、空行、
`export` 前缀、成对引号与双引号内的三种转义，**不做变量展开、不支持多行值**。

### 6.4 `infra/observability`

- 一次 trace 一个 span；turn / llm 调用 / 工具调用是子 span。
- EventBus 是内部事件通道，与 SSE 是两层：内部事件 → SSE 帧。

---

## 7. API 层（HTTP + SSE）

> **状态：设计已定，可以开工。** 开工顺序见 §7.10。
> 选型：FastAPI（承载一元调用）+ SSE（承载流与事件）。

**为什么分两条传输**：一元调用都是"问一句答一句"，走 HTTP 就白拿状态码、OpenAPI 与 curl 调试；
只有"服务端主动推"的东西才值得占一条长连接。**一条流多路复用**，同时看着多个会话。

> `user_id` 的传递位置统一在**查询参数**（`?user_id=`），不放进请求体。原因很具体：
> **浏览器的 `EventSource` 不能设请求头**，`/events` 只能用查询参数；HTTP 跟着用同一个位置，
> 免得两处规则不一致。

### 7.1 端点总览

**HTTP —— 一元调用**。全部带 `?user_id=`。

| 方法 | 路径 | 返回 | 用途 |
|---|---|---|---|
| GET | `/health` | JSON | 健康检查 |
| POST | `/sessions` | JSON | 新建会话 |
| GET | `/sessions` | JSON | 会话列表（只列这个用户的） |
| GET | `/sessions/{session_id}?limit=50&before_seq=…&after_seq=…` | JSON | 会话详情 + **一页**消息 + `has_more`。**重连时拉的就是它** |
| DELETE | `/sessions/{session_id}` | JSON | 删除会话，**消息记录一起没，不可恢复** |
| POST | `/sessions/{session_id}/messages` | JSON `202` | 发消息，必要时开启一次 trace。**不返回内容** |
| POST | `/sessions/{session_id}/interrupt` | JSON | 中断当前 trace |
| GET | `/approvals?session_id=…` | JSON | 待审批列表。**重连后拉一次**，见 §7.6 |
| POST | `/approvals/{request_id}?session_id=…` | JSON | 审批回执 |

> `/approvals` 这两个都**要求 `session_id`**：归属校验就是一次 `get_or_load(session_id, user_id)`，
> 和别的端点同一个动作，不另建一张"谁的待审批"的表。客户端手里也一定有它 ——
> 审批只在一个会话的上下文里才有意义。

**SSE —— 流与事件**

| 方法 | 路径 | 返回 | 用途 |
|---|---|---|---|
| GET | `/events?user_id=…&session_id=…` | `text/event-stream` | 一条流多路复用；看着若干会话的事件流。帧格式见 §7.4，事件见 §7.5 |

> `session_id` 可以重复出现（一条流看多个会话）。**集合定在连接时**，改集合就重连一次。
> 会话不是这个用户的 → 握手阶段就 404，不开流。

**消息是分页的**，别一次把整份历史搬给浏览器：

| 参数 | 含义 |
|---|---|
| `limit` | 一页多少条，默认 50，上限 500 |
| 不给游标 | 给**最后** limit 条 —— 打开会话先看最新的一屏 |
| `before_seq` | 往前翻：给已加载的最早那条的 `entry_seq` |
| `after_seq` | 往后补：给最新那条的 `entry_seq`，只回新增的 —— 增量刷新走它 |

`has_more` 说明这个方向上还有没有更多。两个游标同时给 → `400 invalid_cursor`。
统计仍然遍历整份日志（`stats` 一直是全量的），只有 `messages` 分页。

### 7.2 接口层**不收**的字段

这一节是安全边界，不是偏好：

| 字段 | 为什么不能收 |
|---|---|
| `workspace` | 它是沙箱根。让调用方指定等于把整个文件系统交出去。只能来自服务端配置 |
| `permission` | 客户端能指定就等于能自己升到 `full_access`（执行任意命令、访问网络）——这是**权限提升**，不是偏好设置 |
| `model` | v1 由服务端配置决定（`YZY_MODEL`）。以后要做模型切换再加，那时得先有鉴权 |
| 预算（`max_tokens` / `max_turns`，以及由窗口比例算出的 `compact_tokens`） | 服务端配置，位点 1 冻结进本次 trace |

> v1 **不做鉴权**（本机 127.0.0.1）。一旦暴露到网络，鉴权与上面这几项要一起做——
> 它们本质是同一个问题：**你凭什么代表这个用户做这个决定。**

**反过来，`user_id` 是收的**：接口层原样透传给存储层，只用来给会话分类。它**不是权限声明** ——
没有凭证校验，谁都能填别人的 id。存储层的 `load` / `list_sessions` 按它拒绝跨用户的读，
挡的是"手滑传错了"，不是"故意冒充"。这是**分类**不是**隔离**；隔离要等鉴权（§9）。

### 7.3 HTTP 请求与响应

> `user_id` 统一放查询参数，**POST 也不放进请求体** —— 和 `/events` 保持同一个位置。

#### `POST /sessions?user_id=user_7f3a91`

```json
// 请求（可选，可以直接 {}）
{ "title": "重构 session 模块" }

// 201
{
  "session_id": "sess_9c8b7a1d2e3f",
  "user_id": "user_7f3a91",
  "created_at": "2025-01-01T12:00:00Z",
  "updated_at": "2025-01-01T12:00:00Z",
  "title": null,
  "model": "deepseek-chat",
  "permission": "editable",
  "status": "active"
}
```

`title` 不填就是 `null`，之后由**首条用户消息截断**补上（见 §5 D4）。

#### `GET /sessions?user_id=user_7f3a91`

```json
{ "sessions": [ { "...": "SessionMeta 的字段" } ] }
```

只列这个用户的。只读视图。**读之前要在内存里套用配平规则**（补齐悬空工具调用、补上缺失的 `trace_end`），
但**不写回**——写回只有恢复侧做。

#### `GET /sessions/{session_id}?user_id=user_7f3a91`

```json
{
  "session_id": "sess_...",
  "user_id": "user_7f3a91",
  "created_at": "...", "updated_at": "...",
  "title": "重构 session 模块",
  "model": "deepseek-chat",
  "permission": "editable",
  "status": "active",
  "is_running": false,
  "messages": [
    {
      "entry_id": "e_000001", "entry_seq": 1, "timestamp": "...",
      "role": "user",
      "content": [ { "type": "text", "text": "帮我读一下 a.py" } ]
    }
  ],
  "stats": { "traces": 3, "turns": 11, "context_tokens": 42815, "max_tokens": 1000000 }
}
```

前端渲染历史**只用这一个接口**，数据源就是 Session 日志（唯一真相源），不要另建一套。
**重连时拉的就是它** —— 重连不做增量续传，直接重拉（见 §7.6）。

`stats` 是**现数**的，没有计数器字段（见 §5 D4）：`traces` 数 `trace_start` 条目，
`turns` 把各次 `trace_end` 里记的轮数加起来，`context_tokens` 取最后一次有记录的值。
正在跑的那次 trace 还没收尾，它的轮数**不在** `turns` 里 —— 客户端要显示就自己加上事件带来的 `turn_index`。

#### `DELETE /sessions/{session_id}?user_id=user_7f3a91`

```json
// 200
{ "session_id": "sess_...", "deleted": true }
```

- **不可恢复**：`meta.json` 与 `entries.jsonl` 整个目录一起删，不是打个标记。
- **正在跑的时候删不了**，回 `409 session_busy`：那一刻 checkpoint 正在往它的目录里推条目，
  删了目录，下一次屏障会把它写回来一半。要删就先把 trace 停下来。
- 不存在、或者不是你的 → `404 session_not_found`，与别的端点同一条规则。

#### `POST /sessions/{session_id}/messages?user_id=user_7f3a91`

```json
// 请求
{
  "content": [
    { "type": "text", "text": "帮我读一下 a.py" }
  ],
  "client_message_id": "web-7f3a91"
}

// 202
{ "session_id": "sess_...", "accepted": true, "queued": false }
```

**这里回不出 `entry_id`**：用户消息的条目是位点 1 的 handler 写的，而那是随后台任务跑的，
受理这一刻还不存在。条目的身份从事件流拿 —— `TraceStart` 带着 `user_message_entry_id`。

**这个端点只"收下活"，不回内容。** 模型说了什么、工具跑了什么、什么时候结束，
全部从 `/events` 的事件流拿（§7.5）。

**为什么把"受理"和"结果"分开**：发消息的成功与否就不受事件流状态影响了 ——
断着线也能发，重连之后照样看得到结果（§7.6）。把"受理"和"结果"绑在一起
（发消息顺便开一条流）就等于"连接断了 = 消息没发出去"，而服务端其实早就收下了。

**会话正在跑时回 409 `session_busy`** —— 运行中入队（`pending_queue`）还没接，见 §9。

| 字段 | 必填 | 说明 |
|---|---|---|
| `content` | ✅ | **只收数组**，纯文字也要包一层。图片块为 `{"type":"image","media_type":"image/png","data_base64":"..."}` 或 `{"type":"image","url":"https://..."}` |
| `client_message_id` | ⭕ | 前端自己生成，用于**去重**与**对账** |

**为什么要 `client_message_id`**：`session_id` 回答"发到哪个会话"，回答不了"这是不是同一条消息"。
前端重发一次（连接在响应头回来前断掉、用户双击、超时重试），服务端就会再写一条用户消息进 Session——
而 Session 日志**只追加，写进去删不掉**，对话记录被永久污染。带上它，服务端收消息时扫一遍即可认出重复。

同一条消息在 trace 运行中到达 → **不开新 trace，入队**（`pending_queue`），
等当前模型调用或工具调用返回后再注入。前端靠 202 里的 `queued` 知道这次是入队了。

#### `POST /sessions/{session_id}/interrupt?user_id=user_7f3a91`

```json
// 请求：空
// 202
{ "session_id": "sess_...", "interrupted": true, "trace_id": "trace_x" }
```

- 没有正在跑的 trace 时返回 `200 { "interrupted": false }`——**幂等，不报错**。
- 取消是异步的：这里只表示"已请求中断"，真正的结束状态由流上的 `trace_end` 通知。
- 不需要传 `trace_id`：一个会话同时只有一个 trace 在跑。

#### `GET /approvals?user_id=user_7f3a91&session_id=sess_...`

```json
// 200
{ "approvals": [
    { "request_id": "appr_7f3a91", "session_id": "sess_...",
      "trace_id": "trace_x", "tool_call_id": "call_1",
      "tool_name": "write", "arguments": { "path": "a.py", "content": "..." },
      "reason": "路径在工作目录之外：D:\\etc\\passwd" }
] }
```

- 按**发出的先后**排；一个都没在等就是 `{"approvals": []}`。
- `arguments` 是**补齐默认值、校验之后的最终参数** —— 和沙箱判定、工具执行用的是同一份。
- `session_id` 必填。

**为什么有这条**：审批请求**不落盘** —— 它不在 Session 日志里。所以流断开期间到达的审批，
客户端补不回来（界面上没有确认框，而后端一直在等）。重连后拉一次这个接口就补齐了。
见 §7.5 那张表的最后三行。

#### `POST /approvals/{request_id}?user_id=user_7f3a91&session_id=sess_...`

```json
// 请求
{ "decision": "approve", "comment": "这个文件本来就该改" }
// 200
{ "request_id": "appr_7f3a91", "decision": "approve", "accepted": true }
```

- `decision` 取值：`approve` / `deny`。**没有"还没想好"** —— 关掉弹窗也得明确表态（§9）。
- 回执一到，那条挂着的工具调用立刻往下走，同一次 trace 不用重发。
- 已经处理过、或者不属于这个会话 → `404 approval_not_found`。

### 7.4 SSE 帧

一条 `/events` 流同时看着若干会话。**帧是 JSON 对象**，由 `type` 决定形状，一律写成
`data: {json}` 加一个空行。**没有上行帧** —— 这条流只往下推，上行交互全走普通 POST。

| type | 字段 | 说明 |
|---|---|---|
| `ready` | `client_id`, `server_time`, `heartbeat_ms` | **连上之后第一条，恒发**。在它到达之前客户端不算连上 |
| `subscribed` | `session_id`, `is_running`, `draft_text`, `latest_entry_seq` | 这条流已经看着这个会话了。**先发这一条，再推事件**；每个会话一条，按请求里的先后 |
| `event` | `session_id`, `name`, `data` | 会话事件，`name` 见 §7.5 |
| `error` | `code`, `message`, `session_id?` | 这条流要收尾了，带上原因。`session_id` 缺省表示是整条流的错 |

四条约定：

1. **响应头固定**：`text/event-stream`、`Cache-Control: no-cache`、`X-Accel-Buffering: no`。
   最后一条是给反向代理的 —— 不关掉它，代理会把整条流攒着不发。
2. **心跳是注释行**：空闲超过 `heartbeat_ms`（默认 2000）就写一行 `: ping`。它不产生帧，
   中间设备靠它知道这条连接还活着；`heartbeat_ms` 报的就是这个间隔，只有一个来源。
3. **连接是长活的，不随 trace 结束而关闭。** 会话没有新事件时就只剩心跳，什么时候断开由客户端
   决定。别照搬"一次请求一条流、流完就关"的直觉：这条流的寿命是整个客户端会话，不是一次 trace。
4. **`ready` 必须在任何 `event` 之前到。** 基线读取不能跑在增量观察前面。

```text
GET /events?user_id=user_7f3a91&session_id=sess_ab12

data: {"type": "ready", "client_id": "client_3f9a", "server_time": "...", "heartbeat_ms": 2000}

data: {"type": "subscribed", "session_id": "sess_ab12", "is_running": true,
       "draft_text": "好的，我看一下", "latest_entry_seq": 4}

data: {"type": "event", "session_id": "sess_ab12", "name": "assistant_message",
       "data": {"trace_id": "trace_x", "turn_index": 1, "entry_id": "e_000003",
                "entry_seq": 3, "content": [{"type": "text", "text": "好的，我看一下"}]}}
```

**`subscribed` 里的 `draft_text` 是关键**：草稿槽**永不落盘**，所以模型正在生成的那半截
不在日志里、补拉补不到。连上时把槽里的当前全文整段带过去，客户端立刻就能显示出来。

**一条流看着哪些会话是连接时定死的**，改集合就重连一次。客户端一次只看一个会话，所以
"切对话"就是重开一条流 —— 那正好也是它的重连路径。

**断开怎么发现**：生成器每轮先问一次 `request.is_disconnected()`，空闲时靠一次心跳把发现
时间限制在 `heartbeat_ms` 以内。发现之后收尾，把这条流从 `Hub` 上摘掉，注册表里不留死听众。

### 7.5 事件表

**每个事件都是一条 Session 条目的投影**，加上少量只有内存里才知道的字段。

| event | 从哪条条目派生 | 内存态字段 |
|---|---|---|
| `message_accepted` | `MESSAGE`(user) | `client_message_id`、`queued` |
| `trace_start` | `TRACE_START` | ——（`user_message` 取自紧随其后的那条 `MESSAGE`） |
| `assistant_message` | `MESSAGE`(assistant) | `turn_index` |
| `tool_call_start` | `TOOL_START` | ——（`arguments` 取自 assistant 消息里的 `ToolCallPart`） |
| `tool_result` | `MESSAGE`(tool) | `ok`、`name` |
| `compaction` | `COMPACTION` | —— |
| `trace_end` | `TRACE_END` | `turns`、`duration_ms` |
| — | — | — |
| `turn_start` | **没有条目** | 全部（turn 不落盘） |
| `assistant_delta` | **没有条目** | 全部（草稿槽永不落盘） |
| `assistant_thinking` | **没有条目** | 全部（思考也在草稿槽里过一趟） |
| `approval_required` | **没有条目** | 全部（审批不进日志，用 `ApprovalResult` 回执） |

各事件的 `data`：

| event | data |
|---|---|
| `message_accepted` | `entry_id, entry_seq, content, client_message_id?, queued` |
| `trace_start` | `trace_id, model, permission, max_turns, user_message{entry_id, entry_seq, content}` |
| `turn_start` | `trace_id, turn_index` |
| `assistant_delta` | `trace_id, turn_index, text` |
| `assistant_thinking` | `trace_id, turn_index, text` |
| `assistant_message` | `trace_id, turn_index, entry_id, entry_seq, content` |
| `tool_call_start` | `trace_id, tool_call_id, name, arguments` |
| `tool_result` | `trace_id, tool_call_id, name, ok, preview` |
| `compaction` | `trace_id, summary_preview, kept_entries` |
| `trace_end` | `trace_id, status, reason, turns, duration_ms`。`status` 取 `completed` / `failed` / `aborted` / `budget_exceeded` |
| `approval_required` | `request_id, tool_name, arguments, reason` |

**上面第一张表的最后四行是它的全部价值**：它逼着承认有四个事件**补不回来**。逐个说后果：

- **`turn_start` 补不回来，但无害。** 它对客户端只是"第几轮"的分隔标记，而 `assistant_message`
  带着 `turn_index`，下一个事件就把轮次对齐了。
- **`assistant_delta` 补不回来，用 `subscribed.draft_text` 顶上。** 见 §7.4。
- **`assistant_thinking` 同理**，而且更没有兜底：`subscribed` 只带正文草稿。断线期间的思考过程
  补不回来，但**定稿之后它就在那条 assistant 消息的 `thinking` 块里**，所以只是丢了"过程"，不丢内容。
- **`approval_required` 补不回来，而且会卡死。** 断线期间到达的审批，客户端不知道它存在 ——
  界面上没有确认框，后端一直等。所以有了 `GET /approvals`（§7.3）。

**事件从哪来**：走 `EventBus`（`ctx.ports.events`）。`api/sse` 只订阅与编码，不产生事件。

**谁写日志谁顺手发。** session 是所有日志写入的唯一场所，所以条目派生的事件都在那里发；
没有对应条目的两个由内核发：`TurnStart` 在 `turn_runner` 推进 `turn_index` 之后，
`AssistantDelta` 在收流写草稿槽的同一个循环里。

**已经接上的**：`TraceStart` / `TurnStart` / `AssistantDelta` / `AssistantMessage` / `TraceEnd`
—— 一次最简单的对话正好按这个顺序发一遍；`Compaction` 由 compression 在位点 3 发；
`ApprovalRequired` 由 sandbox 在位点 6 要人确认时发。
**还没接的**：`MessageAccepted`（api 层）、`ToolCallStart` / `ToolResult`（S7）。
类型已经在 `contracts/events.py` 定好了。

### 7.6 断线与重连

**断线不中止 trace。** 理由三条：

1. **切对话** —— 用户在对话 A 里跑着任务，切到 B 看，前端会退订 A。断线即中止的话，A 里正在跑的活就白干了。
2. **刷新页面 / 网络抖动** —— 同理。
3. **多表面共享** —— 两个浏览器挂着同一个会话，关掉一个不该把另一个的活掐了。

所以：**"没人看"从来不是"不要了"的证据。** 停止只有一条路——显式 `POST /interrupt`。

代价（用户关掉页面走了，后端还在跑）用**预算**兜，不用"有没有人看"来判：

| 兜底 | 说明 |
|---|---|
| `max_turns` | 已有 |
| token 预算 | 已有（`max_tokens` / `compact_tokens`） |
| 前端主动取消 | 关页面前用 `navigator.sendBeacon` 调 `/interrupt` |
| 墙钟超时 | 可选，以后再加。注意它是**预算**的一种，**与连接无关** |

#### 重连 = 全量重拉，不做增量续传

```text
t0  客户端 POST /messages              202（受理）
t1  服务端 create_task(跑 trace)        <- 与连接解耦
    └─ /events 这条流只是该会话事件的一个订阅者
t2  流式输出中，delta 不断从流上推
    └─ 注意：磁盘上还没有这一段（草稿槽永不落盘，见 I7）
t3  流断了（切对话 / 刷新 / 抖动）
    └─ trace 继续跑，后台任务不受影响
t4  位点 5：assistant 消息定稿 -> 写 Session 日志 -> 屏障落盘
t5  客户端重连：
    ├─ GET /sessions/{id}?user_id=...     重拉全量历史
    ├─ GET /approvals?session_id=...      补等待中的审批
    └─ GET /events?...&session_id=...
        └─ subscribed 带 is_running 与 draft_text，半截消息立刻能显示
```

**游标续传是刻意不做的。** 三条理由：

1. **正确性不靠它** —— 全量重拉永远对：`assistant_message` 本来就是全量，历史本来就来自日志（唯一真相源）。
2. **它解决的场景比看上去窄** —— 只有"进程还活着、trace 还在跑"的断线用得上；服务端重启时那次 trace
   已经死了（`is_running=false`），客户端反正要重拉一次状态，增量补拉和全量拉是同一个动作。
3. **现在的前端（`src/web/`）也是全量重拉** —— 只在 `subscribed` / `assistant_message` /
   `turn_start` / `trace_end` 四个时刻各拉一次，协议形状没为它改变过。

> 将来真要补拉，游标用 `SessionEntry.seq`：它落盘、跨重启有效，而服务端的补拉就是
> "读日志 → 按 §7.5 那张表派生成事件"，不需要环形缓冲。**协议里现在不留这个字段。**

### 7.7 多表面共享

所有前端按 `session_id` 挂到同一会话，读同一份 entries，写走同一追加通道；表面之间不互相同步状态。

因此服务端必须持有**跨请求存活的内存会话状态**：内存 Session（投影 + 草稿槽）、该会话的事件订阅者、
以及正在跑的那个 task。这就是"会话注册表"（`composition/registry.py`）——它从"可选"变成了**必需**。

**同一个会话被两条流看着就是两个表面**：两边收到同样的事件，`draft_text` 各拿一份，
互不干扰。一条流也可以同时看着多个会话，事件按帧里的 `session_id` 分派。

### 7.8 错误响应

HTTP 统一形状：

```json
{ "error": { "code": "session_not_found", "message": "会话不存在：sess_xxx" } }
```

| HTTP | code | 场景 |
|---|---|---|
| 404 | `session_not_found` | 会话不存在，**或不属于这个 `user_id`** |
| 404 | `approval_not_found` | 审批请求不存在或已处理 |
| 409 | `session_busy` | 会话正在跑一次 trace，暂时不收新消息 |
| 409 | `session_archived` | 会话已归档，不再接受新消息 |
| 422 | —— | 请求体校验失败（FastAPI 默认结构） |
| 500 | `internal_error` | 未预期异常 |

**握手阶段**的失败走上面的 HTTP 错误（`session_not_found` 在这里也是 404），不开流。
**流开了之后**只剩一种收尾方式：发一帧 `error` 就断开，客户端按 §7.6 重连并全量重拉。

**"不存在"与"不是你的"回同一个 404** —— 分开回话等于告诉别人"这个 id 确实存在"。

### 7.9 实现分层

```text
api/app.py            FastAPI 实例、异常处理器、生命周期；建 Hub
api/deps.py           依赖注入：拿 Container
api/errors.py         ApiError：状态码 + 错误码
api/schemas.py        请求与响应 DTO（pydantic）
api/routes/*.py       各 HTTP 端点
api/sse/frames.py     帧类型与序列化；事件 -> event 帧的一张表；data: 行
api/sse/hub.py        订阅登记表：按会话找订阅者
api/sse/endpoint.py   /events 握手、断开探测、写流
```

**`api/sse/hub.py` 是唯一持有"谁在听哪个会话"的地方**：会话模块与内核把事件发到 `EventBus`，
hub 订阅一次，按 `session_id` 分发给看着该会话的流。别的层不需要知道有没有人在听。

### 7.10 开工顺序

前置条件**已经全部满足**：Session 的内存实现、`SessionStore`（文件版）、跨请求存活的会话注册表、
位点 1/5/10 的 handler、内核取消处理，都在。

| 步 | 做什么 | 依赖 |
|---|---|---|
| 1 | **定事件类型 + 发事件** ✅ `contracts/events.py`；session 发三个（位点 1/5/10），内核发两个 | 已完成 |
| 2 | **HTTP 一元端点** ✅ `/health`、`/sessions` 那组、`messages`、`interrupt` | 已完成 |
| 3 | **SSE 流与事件** ✅ `/events` 握手、`ready` / `subscribed` / `event` / `error`、写队列、心跳、断开摘听众 | 已完成 |
| 4 | **审批端点** ✅ `GET /approvals` + 回执；`infra/approvals/gate.py` 提供 `ApprovalProvider` 的进程内实现 | 四步全完成 |

**入口**：`api/main.py` 是全项目唯一在模块级建 `app` 的地方（`app = create_app(build_container())`），
启动命令 `uv run uvicorn agent_architecture.api.main:app`。装配根只在这里调一次 —— 配置错（缺密钥、阈值矛盾）当场就炸。

---

## 8. 测试体系

### 8.1 目录约定

- **一个模块一个测试目录**：`tests/test_<module>/`，与 `src/agent_architecture/modules/<module>/` 一一对应；非维度层也各有目录（`test_contracts/`、`test_infra/`、`test_api/`）。
- `tests/fakes/`：所有假实现与工厂，跨模块复用，行为完全确定（无真实时钟、无随机、无网络）。已落地 `FakeClock`、`SequentialIds`、`StubSession`、`RecordingHandler`/`HandlerSpy`、`make_ports`/`make_frozen_config`/`make_runtime_state`/`make_trace_context`、`ScriptedLlm`（按脚本吐契约分片）、`FakeOpenAiSdk`（假 SDK 传输层，配真客户端用）；后续模块按需补 `FakeApprover`、`FakeExecutor`。
- `tests/conftest.py`：**全项目只有这一个 conftest**，只放全局 fixture；各模块目录不再放 conftest，避免 fixture 来源分散。
- `tests/test_integration/`：跨模块集成。现在跑的是「**真客户端接进循环**」：整条链路用 `OpenAiCompatibleClient`，只把网络那层换成 `fakes.FakeOpenAiSdk`，验证内核真的调到了它、收流真的落进日志、每种失败真的按种类收尾。
- `tests/test_recovery/`：崩溃恢复，独立于 `test_checkpoint`。走的是真实落盘：写进文件 → 换一个 store 实例装回来 → 配平 → 再装回来。

pytest 配置写在 `pyproject.toml`：

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"             # 异步测试不用逐个加 @pytest.mark.asyncio
testpaths = ["tests"]
pythonpath = ["tests"]            # 让测试文件能 import fakes
addopts = "-p no:cacheprovider"   # 见下方说明
```

运行：`uv run pytest -q`，不带任何额外参数。

关掉缓存插件是因为 cacheprovider 会在项目根目录落下 `pytest-cache-files-XXXX` 临时目录；
`.gitignore` 里另加了 `pytest-cache-files-*/` 与 `.pytest_cache/` 兜底。

**测试一律不写磁盘**：受限环境里，由子进程创建的目录当前进程**删不掉**（实测 `Remove-Item`
与 `.NET Directory.Delete` 都是 Access denied，只有提权才能清），会变成永久垃圾。
所以：

- 需要临时文件的测试，一律改成**纯函数 + 固定样例文件**（如 `tests/fixtures/sample.env`）；
- 不使用 pytest 的 `tmp_path` fixture；
- 需要注入文件内容的场景，用 `monkeypatch` 替换读取函数，而不是真去造一个文件。

### 8.2 分层测试口径

| 层 | 手段 | 关注 |
|---|---|---|
| 纯逻辑（runtime_state / session / compression 判定 / sandbox 策略） | 直接函数调用 | 状态迁移、边界条件、确定性 |
| 契约（storage 的 PG 版 vs 文件版） | 同一套 `SessionStore` 用例参数化两种实现 | 行为一致 |
| 位点（各模块 handler） | 用最小 `agent_loop/pipeline.py` 单独调度 | 位点前后副作用 |
| 集成（agent_loop） | 全部假件驱动 | trace/turn 顺序、终止、队列消费 |
| 恢复 | 在指定 seq 注入 flush 失败 / 在 `os.replace` 前后各崩一次 / 悬空调用 | 前缀正确、配平正确 |
| API（HTTP） | `httpx.ASGITransport` | 状态码、错误形状、`user_id` 隔离 |
| API（流） | 自己驱动 ASGI 读流（TestClient 会整体缓冲） | 帧序、`ready` 先于事件、断开摘听众、心跳、积压收尾 |

### 8.3 覆盖目标

- 九个维度模块：分支覆盖 ≥ 90%
- `checkpoint` 与 `compression`：分支覆盖 ≥ 95%（正确性风险最高）
- `contracts`：不测（纯类型）

---

## 9. 待讨论项（开发对应模块时再定）

| 模块 | 待定问题 |
|---|---|
| runtime_state | `pending_tool_calls` 是否需要在状态内保存顺序保证（当前按模型输出顺序） |
| session | 分叉（fork）的语义：分叉点怎么记、entries 是复制到分叉点还是共享前缀。`SessionMeta.parent_session_id` 已预留但全项目未接线；S2 只需保证 `entry_id` 稳定可寻址、子会话 `seq` 从 1 重开，分叉本身以后再做 |
| checkpoint | 批量/定时刷盘：现在日志的写入点（位点 1/5/6/8）全都落在屏障上，屏障之间没有条目，阈值与定时都是空转。等压缩、并行工具这类"屏障之间大量写条目"的模块落地再加 |
| storage | 崩溃在 `os.replace` 之前会留下孤儿 `.tmp`；恢复侧要不要顺手清理 |
| storage | 契约测试目前只跑文件版；PG 版落地后参数化跑两遍，PG 用跳过标记 |
| storage | `seq` 的职责收缩为**排序键**：`projection._kept_messages` 靠它排 `entry_id` 引用回来的乱序条目，PG 表靠它 `ORDER BY`；它不再承担"划可恢复前缀" |
| storage | `seq` 由单进程内存自增分配，靠 `UNIQUE(session_id, seq)` 兜底；多实例同时写同一会话会冲突 |
| storage | `load(session_id, user_id)` 一次读回全量条目，恢复与前端拉历史都走它；长会话的读放大要不要加范围参数 |
| compression | 摘要模型选型与提示词模板的最终措辞 |
| hook | **事件名这一层要不要留**：守卫删掉后 `SITE_EVENT` 成了严格的一一映射（10 位点 ↔ 10 事件名），表达力上不再有收益。留着 = 位点枚举改名/调序时字符串不变、给以后非位点事件留位置；去掉 = 直接用 `Site` 当键、少一套词汇与拼写错误的可能。当初引入它是为了守卫（`tools/pre_execute` 不占独立位点），那个理由已经不在了 |
| sandbox | 命令白名单/黑名单策略的初始形态 |
| tool_calling | MCP transport 的首批支持范围 |
| infra/llm | **思考通道只认 DeepSeek 那一套**：开关是 `extra_body={"thinking": {...}}`、内容是 `reasoning_content`。Anthropic 用 `output_config`、Responses API 用 `reasoning.effort`，接别家时这里要加一层厂商适配 |
| tool_calling | 内置工具里**没有网络类工具**（`fetch` 之类）：主依赖里没有 HTTP 客户端，而且 host 允许列表还没落地（见下面 sandbox 那行）。`url` 参数的沙箱路由目前只有假件在测 |
| tool_calling | **搜索工具没有忽略清单**：`glob` / `grep` 会走遍整个工作区（含 `.venv`、`node_modules` 这类依赖目录），只靠「结果上限 + 单文件大小上限」兜底，走路本身的耗时没有上限。是按名字固定跳过几种目录，还是给工具加一个忽略参数，没定 |
| tool_calling | **工具超时还没有配置项**：现在是模块常量 `DEFAULT_TIMEOUT_SECONDS = 60`，而 `ToolDuringPayload.timeout_seconds` 已经预留了「None 表示用工具声明的默认值」—— 但 `ToolDeclaration` 没有这个字段。等真有需要长跑的工具再定是加到声明上还是加到 `AppConfig` |
| tool_calling | **参数校验只认 `required` / `type` / `enum`**：够内置工具用。完整 JSON Schema（`oneOf` / `pattern` / 嵌套）要等真有复杂的工具 schema 时再说，别提前引依赖 |
| skill | skill 根目录的默认位置与来源优先级顺序 |
| agent_loop | 中断信号在 turn 边界的检查点位置 |
| infra/llm | 首批 provider；token 计数是精确分词还是估算 |
| sandbox | **网络只有能力档位判定，没有 host 允许列表**：`url` 参数现在只用来把工具归到「能力类」（要 `full_access`），host 本身没比对。允许列表落地时，不在列表里的应回到 `AWAIT_APPROVAL` |
| sandbox | **MCP 工具的参数名不可控**：路由靠参数名约定（`path` / `url` / `command`），远程工具不一定遵守。到时要么要求适配层做映射，要么给 `ToolDeclaration` 加一个声明字段 |
| sandbox | 位点 7 的运行时边界复核没做（要等 S7 有执行器）—— 现在「强制」只体现在位点 6 的 `decisions` 上 |
| sandbox | **审批弹窗上的 × 由谁定义**：系统层只认「有没有回执、回执是什么」，判不出「×」是什么意思。所以必须由前端明确表态（关框时照样发 `{"decision": "deny"}`），后端不做推断 |
| sandbox | **挂起的审批没人答就卡住整个会话**（已决定不加兜底，但后果要记住）：人一直不点 → `await approvals.request(...)` 永远不返回 → 那次 trace 不收尾 → `is_running` 保持 true → 这个会话发新消息一律 409。`GET /approvals` + 回执现在给了**重连之后把它找出来答掉**的路，但"这次根本没人来"仍然无解。而且 **`/interrupt` 也解不开**：内核的三个安全点（每轮开头、收流分片、每条工具调用之前）都在这个 await 之外，`signal.set()` 没有人在听。唯一出路是进程重启 → 落盘日志没有 `trace_end` → 走 checkpoint 恢复。真要治，兜底该放在**连接层**（流断开时取消那次请求挂起的审批），不是在沙箱里加超时 |
| infra/llm | **图片不计入 token 估算**：`HeuristicTokenCounter` 只算文本与工具调用的参数，`ImagePart` 一个 token 都不算，带图的会话会低估到被模型挡回来。要么按 base64 长度折算，要么每张图记一个固定值 |
| api | **装成 wheel 时没有前端**：`src/web/` 在包外面，`uv build` 不会带它，`_mount_web` 会静默跳过 —— 那种部署只提供 API。要带上，得把它挪进 `agent_architecture/web/`，或者单独发一份静态产物 |
| api | 鉴权：`user_id` 目前是**调用方自报的**，只做分类不做隔离 —— 谁都能填别人的 id。对外服务前要有服务端推导 user_id 的路径（凭证/网关头）
| api | 会话列表的默认排序与分页 |
| api | **慢客户端**：每条流的写队列上限 512 帧，满了发一帧 `stream_overflowed` 收尾，客户端重连全量重拉（§7.6）。还没在真实网络下验证过这个阈值 |
| api | **运行中入队没接**：会话正在跑时 `POST /messages` 回 409。打通要让 API 层拿得到正在跑那次 trace 的 `RuntimeState`（`pending_queue` 在它身上），而它是 `Agent.run` 内部建的 —— 要么把队列挪到会话级（像草稿槽那样挂 `SessionHandle`），要么把门面拆成 `prepare` / `run` 两步 |
| api | **`client_message_id` 收下了但还没做去重**：它防的是"前端重发一次，日志里就多一条一模一样的用户消息"，而日志只追加、写进去删不掉。去重表挂在会话活状态上即可（进程内） |
| 全局 | 部署为后端服务后不再有"工作目录"与"沙箱"：`SessionMeta.workspace`、D8 沙箱模块、工具的文件边界都要按这个前提重新界定 |

---

## 附：模块 ↔ 目录 ↔ 测试 对照

| 维度 | 模块目录 | 测试目录 | 开发序 |
|---|---|---|---|
| D9 Runtime State | `modules/runtime_state/` | `tests/test_runtime_state/` | S1 |
| D4 Session | `modules/session/` | `tests/test_session/` | S2 |
| D6 Checkpoint | `modules/checkpoint/` | `tests/test_checkpoint/` | S3 |
| D5 压缩 | `modules/compression/` | `tests/test_compression/` | S4【已完成】 |
| D7 Hook | `modules/hook/` | `tests/test_hook/` | S5【已完成】 |
| D8 沙箱与权限 | `modules/sandbox/` | `tests/test_sandbox/` | S6【已完成】 |
| D2 Tool Calling | `modules/tool_calling/` | `tests/test_tool_calling/` | S7【已完成】 |
| D3 Skill | `modules/skill/` | `tests/test_skill/` | S8 |
| D1 Agent Loop | `modules/agent_loop/` | `tests/test_agent_loop/` | S9 |
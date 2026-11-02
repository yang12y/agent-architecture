# agent-frame 架构设计

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

**import 约定**：模块内部文件用相对 import；跨层只允许 `from agent_frame.contracts import ...`，不深层导入。

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

两层扩展点，各管一件事：**维度模块管「agent 怎么跑」，业务工具管「agent 能做什么」**。两者互不认识，都只依赖契约层。

| 层 | 目录 | 内容 | 能否依赖别的层 |
|---|---|---|---|
| L0 契约 | `contracts/` | 十位点、共享模型、端口 Protocol、错误、工具执行现场 | 零依赖，不 import 其他层 |
| L1 维度 | `modules/*`（9 + 1） | 九个维度 + 位点 3 的提示词组装，各自实现 `SiteHandler` | 只允许依赖 L0 |
| L1.5 工具 | `tools/*`【待做】 | 业务工具：声明（数据）+ 通用取数 + 输出投影，不参与任何位点 | 只允许依赖 L0 |
| L2 基础设施 | `infra/*` | llm / http / storage / config / observability | 只允许依赖 L0 |
| L3 装配 | `composition/` | 唯一装配根，实例化并注入 | 可依赖全部 |
| L4 接入 | `api/` | FastAPI 路由、SSE、DTO | 只依赖 L3 + L0 |

### 3.1 依赖规则（用 import-linter 强制）

| 规则 | 内容 |
|---|---|
| R1 | `contracts` 不得 import `modules` / `infra` / `composition` / `api` |
| R2 | 九个维度模块与 `prompts` 之间**禁止互相 import**；需要时走 `contracts` 的端口 |
| R3 | 维度模块与 infra 之间**禁止互相 import**；`infra` 实现 `contracts` 端口，由 composition 注入 |
| R4 | `agent_loop` 只依赖 `contracts`，不认识任何具体维度 |
| R5 | `composition` 之外任何地方不得出现跨层 import |
| R6 | 对外只暴露包根 `__init__.py`；禁止深层 import（`from agent_frame.modules.session.log import ...`） |
| R7 | `tools/*` 只允许依赖 `contracts`；`modules` 与 `tools` 互不 import，`tools` 也不 import `infra` / `composition` / `api` |

### 3.2 目录架构

```text
agent-frame/
├── pyproject.toml
├── README.md
├── .env.example
├── docs/
│   ├── ARCHITECTURE.md                     # 本文档
│   ├── HANDLERS.md                         # 全部 handler 的位点 × 优先级一览
│   ├── TOOLS.md                            # 业务工具层设计（tools/）
│   └── interesting.md                      # 点子池：能做、想做、还没排期的功能
├── src/
│   └── agent_frame/
│       ├── __init__.py
│       │
│       ├── contracts/                      # L0 共享契约【已完成】
│       │   ├── __init__.py                 # 统一出口，其他层只从这里 import
│       │   ├── sites.py                    # Site 十位点 / 十个位点 payload /
│       │   │                               #   TraceContext / SiteContext / Handler / CancelToken
│       │   ├── models.py                   # 全部共享枚举与数据类：消息、工具、Runtime State、
│       │   │                               #   Session 条目、草稿槽、审批
│       │   ├── config.py                   # AppConfig（进程级）/ FrozenConfig（trace 快照）
│       │   ├── ports.py                    # Protocol 端口 + Ports 聚合（含 HttpTransport）
│       │   ├── tools.py                    # 工具执行现场 ToolContext（含 trace 变量）+ 实现签名 ToolRunner
│       │   ├── charts.py                   # 图表参数 → Chart 的解析规则（工具与位点 8 共用）
│       │   ├── stats.py                    # 分位数 / 箱线统计量（图表与统计工具共用同一份口径）
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
│       │   ├── skill/                      # D3【已完成】
│       │   │   ├── __init__.py
│       │   │   ├── manifest.py             # 声明解析（front-matter）
│       │   │   ├── scanner.py              # 扫描各 skill 根目录
│       │   │   ├── registry.py             # 来源优先级合并去重
│       │   │   ├── catalog.py              # 目录渲染（位点 3 追加进注入消息）
│       │   │   ├── loader.py               # 正文按需加载 + skill 工具的声明与实现
│       │   │   ├── handler.py              # 位点 3：把目录写进 payload.injected_messages
│       │   │   └── skills/                 # 随包发布的技能：一个目录一个技能
│       │   │       ├── add-handler/SKILL.md
│       │   │       └── run-tests/SKILL.md
│       │   │
│       │   ├── todo/                       # 非维度：待办清单【已完成】
│       │   │   ├── tool.py                 # todo_write 的声明与校验（工具不写日志）
│       │   │   └── handler.py              # 位点 8：写 todo 条目 + 广播 TodoUpdated
│       │   │
│       │   ├── charts/                     # 非维度：图表【已完成】
│       │   │   └── handler.py              # 位点 8（405）：写 chart 条目 + 广播 ChartRendered
│       │   │
│       │   ├── ask_user/                   # 非维度：向用户提问【已完成】
│       │   │   └── tool.py                 # ask_user 的声明、参数校验、答案编码；挂起在提问端口里
│       │   │
│       │   ├── prompts/                    # 非维度：位点 3 拼系统提示词【已完成】
│       │   │   ├── prompt.py               # 静态段落表 + 排序 + {{变量}} 插值（静态在前、环境在后）
│       │   │   └── handler.py              # 位点 3：写进 payload.system_prompt，**不进 messages 也不写 Session**
│       │   │
│       │   └── agent_loop/                 # D1【pipeline 已完成】
│       │       ├── __init__.py
│       │       ├── pipeline.py             # HandlerRegistry + SitePipeline（位点内按 priority 调度）
│       │       ├── trace_runner.py         # 一次 trace 的生命周期
│       │       ├── turn_runner.py          # 一次 turn 的生命周期
│       │       ├── termination.py          # completed / failed / aborted / budget_exceeded
│       │       └── agent.py                # 对外门面：run / resume / enqueue
│       │
│       ├── tools/                          # L1.5 业务工具【进行中】：只 import contracts；设计见 docs/TOOLS.md
│       │   ├── __init__.py
│       │   ├── spec.py                     # HttpTool：接口 + 取数策略 + 输出投影（声明即数据）
│       │   ├── params.py                   # 参数 schema 小 DSL：文本 / 数字 / 枚举 / 数组
│       │   ├── paging.py                   # 五种取数：首屏 / 翻页取全 / 逐页到不满 / 单次 / 只取总数；上限常量只在这里
│       │   ├── envelope.py                 # 业务报文容器 → Page(records, count, truncated)
│       │   ├── records.py                  # 下游行 → 模型看到的行（字段映射 + 裁剪）
│       │   ├── aggregate.py                # 分组计数 / 比率（统计与指标共用的纯函数）
│       │   ├── errors.py                   # 下游失败：接口报错 / 非 JSON / 超时
│       │   ├── catalog.py                  # 声明 → ToolDeclaration + runner；工具名唯一性在这里校验
│       │   └── chat_tools/                 # 聊天类工具：直接往对话里画东西（4 个绘图工具）
│       │
│       ├── infra/                          # L2 基础设施（config / observability / system 已完成）
│       │   ├── llm/                        # 【已完成】OpenAI 兼容
│       │   │   ├── client.py               # 收流 + 工具调用增量拼装
│       │   │   ├── messages.py             # Message（契约）→ OpenAI 结构
│       │   │   └── errors.py               # SDK 异常 → LlmErrorKind（分类的唯一入口）
│       │   ├── pending.py                  # 待办表：审批与提问共用的挂起表（不落盘）
│       │   ├── presence.py                 # 谁在看哪个会话：SSE 流连上/断开时改它
│       │   ├── approvals/                  # 【已完成】ApprovalProvider 的进程内实现
│       │   │   └── gate.py                 # 待审批表：挂起 future、按会话列、交回执（不落盘）
│       │   ├── questions/                  # 【已完成】QuestionProvider 的进程内实现
│       │   │   └── gate.py                 # 提问表：没人连着当场失败，有人连着挂起等回答
│       │   ├── http/                       # 【已完成】HttpTransport 的实现
│       │   │   ├── __init__.py
│       │   │   └── client.py               # 一个进程一条连接池：超时 / JSON / 状态码 / 错误分类
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
│       │   │   ├── loader.py               # 环境变量 AGENT_* → AppConfig
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
│           ├── docx.py                     # 【已完成】「块」→ .docx 的排版（python-docx，全程内存）
│           ├── routes/
│           │   ├── health.py               # 【已完成】
│           │   ├── sessions.py             # 【已完成】建会话 / 列表 / 详情
│           │   ├── chat.py                 # 【已完成】发消息 / 中断：只收下活，不回内容
│           │   ├── approvals.py            # 【已完成】待审批列表 + 回执
│           │   ├── questions.py            # 【已完成】待回答列表 + 交回答
│           │   └── exports.py              # 【已完成】回答 → .docx（不落盘）
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
    ├── test_tools/                         # 业务工具：取数策略 / 报文容器 / 字段映射 / 装配
    ├── test_skill/                         # S8
    ├── test_api/                           # FastAPI(HTTP) + SSE 流
    ├── test_integration/                   # 跨模块集成
    └── test_recovery/                      # 崩溃注入，独立目录（需要"可失败 store"这类特殊桩）
```

> 前端静态页在 `src/web/`，不在上面的树里（它不是 Python 包）：HTML + CSS + 原生 ES 模块，
> 由 `api/app.py` 的 `_mount_web` 挂在根路径。

---

## 4. 模块开发顺序

原则：**先纯函数、后集成**；每个模块只依赖已完成的模块 + `contracts` 端口，因此每个阶段都能独立跑单元测试。

### 4.1 阶段 0：地基（非维度）

**状态：已完成**（110 个用例全绿）。阶段 0 之后又补上了 `infra/llm` 与主循环，见下表最后三行。

| 产出 | 内容 |
|---|---|
| `contracts/charts.py` | 图表参数 → `Chart` 的解析规则与上限；工具与位点 8 的 handler 共用 |
| `contracts/stats.py` | `quantile` / `box_stats`：分位数与箱线统计量，图表工具与统计工具共用同一份口径 |
| `contracts/errors.py` | `AgentError` 及 `ConfigError` / `SiteAbort` / `FlushError` / `ContextOverflowError` |
| `contracts/models.py` | 共享枚举与数据类：消息、工具、Runtime State、Session 日志、草稿槽、审批 |
| `contracts/config.py` | `AppConfig`（进程级）与 `FrozenConfig`（trace 级快照） |
| `contracts/ports.py` | Protocol 端口 + 聚合用的 `Ports`（阶段 0 聚合五个基础端口） |
| `contracts/sites.py` | 十位点枚举、十个位点专属 payload、`TraceContext` / `SiteContext` / `Handler`、`CancelToken` |
| `infra/config/` | 环境变量加载（`AGENT_*`）、默认值、`freeze_config` |
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

`api/` 不在 S1–S9 里（它是表面层，不是维度）。它的前置条件已经全部满足，
可以一次写完，不用返工。

事件通道已经接上：`contracts/events.py` 定义类型，session 与内核在写日志的地方顺手发。
`api/sse` 只订阅与编码，不产生事件。

---

## 5. 模块详设

> D1~D9 沿用源文档的维度编号；开发顺序见 §4.2 的 S1~S9。每条都是**规则**，为什么在代码注释里。

### D9 Runtime State（`modules/runtime_state`，开发序 S1）

**定位**：一次 trace 期间常驻内存的执行态，不是真相源。

**边界**
- 做：身份游标 / 进度与预算 / 信息 / 模型与权限四组字段；十个位点的操作；`pending_queue` 入队出队；工具成功失败聚合。
- 不做：不持有历史消息与模型输出草稿、不落盘、不参与崩溃恢复。

**位点参与**：1、2、3、4、5、6、7、8、10 —— 九个位点各一个 handler。

> 位点 6 摘掉不执行的调用：位点 8 才是执行路径的摘除点，被拒的、判定缺失的、审批没走完的到不了那里。优先级排在 tool_calling/sandbox 之后（450）。
> 位点 9 没有 handler：max_turns 判定归内核。位点 2 的 `turn_index++` 与位点 10 的 `is_running=false` 也归内核（I9）。

**关键正确设计**
- 位点 3 算 `context_tokens`：上次请求正好是这次的前缀，就采信提供方报的 `input_tokens + output_tokens`、只补算新增；对不上才全量估算。锚点在会话上、不落盘。
- 每次成功调用重新锚定，估算误差不跨轮累积；`input_tokens` 已含工具 schema 与系统提示词。
- 每个参与的位点一个独立操作模块（`ops/sNN_*.py`），函数只读 ctx、原地更新，无隐藏全局状态。
- 中途消息走 payload：位点 5/8 整体取出 `pending_queue` 写进 payload，session 随后落成 user 消息；取过即空。
- `turn_tool_stats` 按工具名聚合，**被 rejected 的调用不计入**。
- `llm_attempt_number` 进入新 turn 归零，仅「失败且可重试」时 +1。
- `RuntimeState` 由 Session 建立，新会话与恢复会话完全一样；trace 结束即销毁。
- `pending_queue` 只在位点 5、8 出队，任何时刻可入队。

**已否决（一句话）**：曾把 `messages`、`llm_buffer` 放进 Runtime State，破坏「Session 唯一真相源」，放弃。

---

### D4 Session（`modules/session`，开发序 S2）

**定位**：跨 trace 长期存活的事实档案，**唯一真相源**。磁盘形态只有 `meta` + `entries`；内存形态再加投影、`context_cursor`、草稿槽。

**边界**
- 做：append-only 日志、上下文投影与增量并入、草稿槽、标题生成、只读配平视图。
- 不做：不做缓冲落盘调度（归 Checkpoint）；不改写、不删除任何条目。

**没有计数器字段**：会话级统计（跑过几次 trace、几轮、上次占多少上下文）全在读取时从日志算（`api/stats.py`）。

**文件分工**

| 文件 | 职责 |
|---|---|
| `log.py` | append-only 内存日志；分配 `seq` 与 `entry_id`；按 id / 类型查询；分叉点的切法 |
| `projection.py` | 全量投影（三条来源规则）与草稿消息包装。纯函数 |
| `cursor.py` | 投影起点推导、按起点取条目、把一批条目并进上下文 |
| `draft_slot.py` | 会话级单槽草稿 |
| `reader.py` | 对一段 entries 的只读视图，复用同一套投影规则，不写回 |
| `session.py` | 聚合根，实现 `SessionHandle` 端口 |
| `title.py` | 第一条用户消息压成一行标题。纯函数 |
| `handler.py` | 位点 1/3/5/6/8/10；**所有日志写入都在这里** |

**关键正确设计**
- **投影规则**：从投影起点（最近一次 `compaction`）取起 ① 它的 `summary`；② 它 `entry_ids` 指向且仍保留的 `message`；③ 它之后所有 `message`。无 `compaction` 从第一条 `message` 起；`trace_start`/`trace_end`/`tool_start` 不进 messages。
- 摘要消息的角色是 `user`，正文前加一句交代来历，保证能独立阅读。
- 草稿槽非空时把 `draft.content` 作为一条 assistant 消息接在投影末尾。草稿不在日志里，**读时叠加**。
- 投影不每次重算，但结果与全量重算逐条相等。
- **`context_cursor` 是投影起点**，不是每轮前进的读指针：空串=从头，否则=最近一次 `compaction` 的 entry_id（含它）。只在重建时算一次，镜像进 meta 随屏障落盘；恢复时仍按全量重算。
- 「已经并到哪了」是内存里的增量记账，不进 meta；`append_entry` 不等于并入。
- **标题只写一次**：素材是第一条用户消息，条件是 `meta.title` 还是 `None`。压成一行、超 30 字截断；没有可用文本就保持 `None`。它只是 meta 字段，不新增条目类型。
- **会话必须有主**：`user_id` 必填，隔离落在存储层（`load`/`list_sessions` 都按它过滤，对不上一律 404）。它不做鉴权。
- `trace_start`/`trace_end` 只放推不出来的事实（触发者、父子关系、模型、权限、预算、压缩配置、结束状态）；耗时、turn 数、token 量都不写。
- **草稿槽单槽、两个清空点**：位点 5 定稿时内核 `take_draft()` 拼成 assistant 消息；位点 3 取材之后由 handler 清空（排在 compression 之后）。草稿永不落盘。
- 多表面按同一 `session_id` 挂同一会话，共享同一份 entries 与同一追加通道。
- 只读观察方（历史列表、审计）套用同一套投影规则但**不写回**（`reader.py`：没有游标、没有草稿槽）。
- 位点 6 只给判定为 `execute` 的调用写 `tool_start`；位点 8 在 tool_calling 没给 `normalized` 时兜底拼结果消息并记 warning。
- **`trace_end` 还带 `duration_ms` 与 `spans`**（耗时明细，见 D1）：耗时是"推不出来"的事实，只能收尾时记一次。它们**只进日志不进上下文** —— 投影、压缩、下一轮请求都碰不到，与 `todo` / `chart` 同一个套路。
- **分叉的切法**：`fork_prefix(entries, turn_seq)` 截到该轮 `trace_end`（含）。下一轮的 `trace_start` 与它的用户消息同一位点连写，截在 `trace_end` 上才不会留下孤悬的 `trace_start`（会被恢复侧当成崩在 trace 中途）。
- **分叉出来的会话独立**：除 `parent_session_id` 一条血缘指针外不共享任何东西 —— 不级联删除、父会话删了照常打开。配置（workspace / model / permission / 工具结果截断尺度）**逐字抄源会话**，不抄进程级配置。
- **分叉当场落盘**，顺序先 entries 后 meta：中途失败留下的是"没有 meta 的目录"（列表里看不见），而不是一个看得见但空的会话。

**已否决（一句话）**：曾把「当前上下文」存成字段、或每次取材都从头重算投影，两者都被「投影本该是确定性视图」否定。

---

### D6 Checkpoint（`modules/checkpoint`，开发序 S3）

**定位**：决定日志什么时候必须落盘，并在崩溃后把磁盘日志恢复成内存 Session。它不产生任何结构。

**边界**
- 做：五个屏障的调度、语义配平、恢复七步。
- 不做：不定义新条目类型、不参与 Runtime State 重建。

**五个屏障位点**（已落盘是下界，屏障之间仍可批量落盘）

| 位点 | 为什么必须落盘 |
|---|---|
| 3 llm:before | 请求发出前已落盘，响应前崩溃不会重放 |
| 5 llm:after | 本轮 assistant 消息（含它请求的工具调用）先落盘再进工具阶段 |
| 6 tool:before | 外部副作用发生前落盘，并写下 `tool_start` 标记 |
| 8 tool:after | 本批工具结果先落盘再进下一轮 |
| 10 trace:end | 收尾事实落盘，下次启动才判得出"上次是正常结束的" |

**关键正确设计**
- **失败即阻止**：位点 3 落盘失败拦掉模型调用；位点 6 落盘失败拦掉工具正文。
- 可恢复前缀 = 最后一次成功 flush 的前缀，其后缓冲内容崩溃后视为不存在。
- 落盘原子：写临时文件后整份 `os.replace`（PG 是提交事务），盘上不会留半条条目。
- **恢复判据**：日志末尾没有 `trace_end`，或最后一条 `trace_end` 的 `status == aborted`。
- **恢复七步**：① 装 meta ② 装 entries ③ 补悬空工具调用结果 ④ 补 `trace_end(aborted)` ⑤ 合成事件落盘 ⑥ 重建投影、游标推末尾 ⑦ 草稿槽置空。（③④⑥⑦ 只在「没有 trace_end」时做）
- **悬空调用三情形**（判据 = 有没有 `tool_start` × 副作用等级/是否接受幂等键）：

  | tool_start | 副作用等级 | 补成什么 | 可重试 |
  |---|---|---|---|
  | 无 | 任意 | 开始前被中断 | 可安全重试 |
  | 有 | none / idempotent / 接受幂等键 | 结果未知 | 可安全重试 |
  | 有 | non_idempotent 且不接受幂等键 | 结果未知 | **不自动重试**，先验证或问用户 |

- 重试判定复用工具声明里那条规则，不另立。
- 只看 `aborted`，不看 `failed` / `budget_exceeded`（后两者是正常收尾，留不下悬空调用）。
- 位点 10 只补 `trace_end`，不补工具调用。

**已否决（一句话）**：曾用「每条目立即 fsync」作为落盘时机，性能不可接受，放弃。

---

### D5 压缩（`modules/compression`，开发序 S4）

**定位**：上下文触达阈值时的两级压缩，先便宜后昂贵。

**唯一触发点：位点 3**。`context_tokens` 触达 `compact_tokens` 就压，压完才发请求；位点 5 不压缩。压完仍放不下 → 本次 trace 按 `budget_exceeded` 收尾，不重试、不再压。

**两级**

| 级 | 做法 | 代价 | 落盘 |
|---|---|---|---|
| 一 | 较早的长工具结果正文换成占位词（保留工具名+关键参数+原文规模） | 零模型调用 | 否，只改内存上下文 |
| 二 | 保留最后 n 条 message，其余 + 上一条摘要 → 摘要模型产出新 summary → 追加 `compaction` 条目 | 一次模型调用 | 是 |

**关键正确设计**
- 一级只减去它省下的量，不重数整份上下文；仍触达才升二级。
- 工具 schema 一直算在里面：压缩只动 messages，schema 由位点 3 单独补回。
- 模型看到压缩后的上下文时，对应的 `compaction` 必须已在盘上（位点 3 屏障保证）。
- 一级只改「这一轮发出去的那份」，entries 一个字节不动。
- 二级从真投影取素材，不从一级的输出取。
- 二级之后要再补一次一级（保留窗口那几条长结果是全文）。
- **不构成 turn**：摘要调用不产生 assistant 消息、不进 messages、不加 `turn_index`，可用更便宜的模型。
- 压缩有损：被压掉的正文不留副本；压缩不改写、不删除任何旧条目。
- 上一条压缩会被一并压掉、不叠加 —— 上下文里永远只有一条摘要。
- 被保留的 n 条只留 `entry_id` 引用，不内嵌内容。
- 摘要必须能独立阅读：保留任务目标、已拍板决定、已完成工作与结果、当前状态与下一步、失败尝试与原因、必须逐字保留的内容；舍弃客套话、被推翻的中间结论、重复的过程叙述。
- 二级重建了上下文，只有这一步必须重数一次。
- 二级做完仍触达阈值 → `guard` 抛 `ContextOverflowError`，请求不发，`trace_end` 记 `budget_exceeded`。
- 估算偏了、模型真挡回来时同样抛 `ContextOverflowError`，处置一样：不重试、不再压。

**已否决（一句话）**：曾让压缩改写旧条目或做「摘要的摘要」叠加，破坏 append-only 与单摘要不变量，放弃。

---

### D7 Hook（`modules/hook`，开发序 S5）

**定位**：在固定位点注入用户逻辑的机制。它自己也走 `HandlerRegistry` 挂上去。

**边界**
- 做：Hook 注册、匹配、执行；`agent/*` 事件。
- 不做：Hook 不得绕过位点语义（跳过落盘、改写历史条目）。

**挂法**：`registry.on(event, fn)`，回调 `fn(ctx)`；位点 1/6 返回 `False` 是否决，别处忽略。

- 事件名就是十位点（`events.SITE_EVENT` 是唯一映射处），不新增扩展点。
- 同一事件内按注册顺序跑，不另设 per-hook 优先级。
- 名字在整张表内唯一；`remove` 只摘得动用户注册的。
- 注册表整个交给容器（`container.hooks.on(...)`），不用碰管线。

**handler**：`hook.site` 覆盖十个位点，priority `500` —— hook 段位下界，晚于维度、早于屏障。

**关键正确设计**
- **否决只在位点 1 与 6 有语义**（与内核一致）：位点 1 不放 trace 开始，位点 6 不放这批工具执行。其余位点返回 `False` 记 `hook.reject_out_of_contract` 后继续。
- Hook 失败不静默：`SiteAbort` 原样上抛；抛别的记 `hook.failed`，有否决语义的位点按拒绝处理，其余跳过它继续。
- 内置与用户同表、同排序、同一个命名空间（重名直接报错）；差别只有「用户注册的摘得掉」。

**待做**：工具守卫（`tools/pre_execute`）现在不做 —— 它判的是位点 6 的 `decisions`，等 S7 的检查链成型时连着一起定。

**已否决（一句话）**：曾用「继承主循环子类、覆写方法」做扩展，耦合过深，放弃。

---

### D8 沙箱与权限（`modules/sandbox`，开发序 S6）

**定位**：工具执行的安全边界。**不判「危险」，判「越界」**。

**边界**
- 做：`Permission` 三档比较；参数 → 资源解析；工具级 + 参数级判定 → 三态；把结论写进 `decisions`；人工审批往返。
- 不做：不执行工具本身；不做存在性与参数 schema 校验（归 tool_calling）；不做落盘调度（归 checkpoint）。

**三态**（复用 `ToolDecision`）：放行 `EXECUTE` / 用户审批 `AWAIT_APPROVAL` / 拒绝 `REJECT`。

**两层路由**

| 层 | 看什么 | 谁做 |
|---|---|---|
| 这工具**能**碰什么 | `parameters_schema` 声明了哪些参数名 | `policies.route(declaration)` |
| 这一次碰**成什么样** | 实际传进来的值 | `policies.resolve_path(arguments, workspace)` |

只看参数值不行：`write` 少给一个 `path` 就变成「不碰文件」，整类判定凭空消失。路由靠**参数名约定**（`path` → 文件、`url` → 网络、`command` → 命令），不新增声明字段。

**判定分两类资源**

| 类 | 例子 | 档位不够时 |
|---|---|---|
| **边界类** | 文件 | **拒绝** |
| **能力类** | 命令、网络 | **用户审批** |

命令类的能力由策略定死要 `full_access`，不看工具自己声明什么。

**参数级只判文件类**：规范化后在目录内 → 放行；越界 → 用户审批；判不出来（缺参数、类型不对、路径非法）→ 用户审批。命令与网络没有参数级判定（`bash -c` 里的东西关键词认不出来）。

**关键正确设计**
- **规范化必须在判定之前**，两侧都规范化（大小写差异会漏）。
- 判定是纯函数 `(permission, declaration, arguments, workspace) -> Verdict`，不碰端口与时钟，三档 × 参数组合可穷举。
- 返回 `Verdict`（结论 + 理由）：要人审批时得告诉用户为什么。
- 结论写进位点 6 的 `decisions`，内核只对 `execute` 的调用往下跑位点 7/8。
- **不写 = 拒绝**，但只对判得了的调用落笔；查不到声明的留给 tool_calling 的存在性检查。
- 审批无可用应答方时 fail closed（落成拒绝），不静默放行。
- **没同意就是没同意**：`await approvals.request(...)` 一直挂着直到有人回执，不设超时、不与取消信号竞速；挂着的那次 trace 交给恢复路径。
- 「×」的语义不在这一层：前端关框时照样发 `{"decision": "deny"}`，后端不做推断。
- 端口缺失时：没有 `Ports.tools` 就一条都不判（模块惰性）；没有 `Ports.approvals` 就把「等审批」落成拒绝。

**已否决（一句话）**：曾用「执行后审计」替代执行前拦截（无法阻止副作用），也曾让声明里选工具粒度或参数粒度（前者是后者的特例），均放弃。

**待做**：位点 7 的运行时边界复核（等 S7 有执行器）；`url` 的 host 允许列表。

---

### D2 Tool Calling（`modules/tool_calling`，开发序 S7）

**定位**：工具注册、检查、执行、结果回填；MCP 是本维度的远程工具来源。

**边界**
- 做：注册表与可见性；位点 6 检查链；幂等键；执行/重试/超时；输出规范化与超限截断；MCP 适配；内置工具。
- 不做：不做审批决策本身（调 sandbox 端口）；不做落盘调度（调 checkpoint 端口）；不实现业务工具（在 `tools/`，见 §3.2）。

**工具声明**：名称、描述、参数 schema、输出契约、副作用等级（`none`/`idempotent`/`non_idempotent`）、是否接受幂等键、权限、执行逻辑。

**位点 6 检查链（顺序固定）**：① 存在性 ② 补默认参数 ③ 参数校验 ④ 权限审批（工具级/参数级）⑤ 重复调用检测（指纹 = 工具名 + 参数，只提醒不拦截）⑥ 并行/串行判定（判不了按串行）⑦ 生成幂等键 ⑧ 追加 `tool_start` 并落盘 ⑨ 给每条不执行的调用写结果消息。

**关键正确设计**
- **不执行的调用全部在位点 6 收尾**：置 `call.status = rejected`、追加一条配对的 tool 结果消息、记一条拒绝事件；随后由 runtime_state 从 `pending_tool_calls` 摘掉。
- **工具失败也是结果，不是 trace 失败**：检查链不过 → `REJECT` + 配对结果；执行期异常 → 收成 `error` + `raw_result` 落成 tool 消息。只有 `SiteAbort` 与 `CancelledError` 直穿。
- 被拒调用的结果消息要写具体原因（「缺少必填参数：path」），不能只写「被拒绝」。
- 位点 8 统计口径：被拒的调用不计成功也不计失败。
- 幂等键：运行时只**生成并写入日志、传给工具**，不做「查它做过没有」的跳过。键含 `tool_call_id`，重试/重放时 id 与键都不变。
- 键买到的是可重试性，价值在重试与崩溃恢复重放两处兑现；构造参数可按业务灵活取用。
- **重试规则**：`none`/`idempotent` 可重试；`non_idempotent` 仅在接受幂等键时可重试；超时同一规则。此规则与恢复判定同一条。
- 位点 8：规范化输出 → 超 `tool_result_max_chars` 按字符截断并在末尾标注原文总长 → 写 Session 与内存 messages → 发完成事件。幂等键不重复写。
- **超限只截断，不留原文、不留引用**；要全文由工具自己写进工作区。
- MCP tool 经 adapter 变成 `ToolDeclaration`，对内核与模型完全透明，与本地工具同一条检查链。

**落成了什么样（与计划的差异）**

| 计划里有 | 结果 | 说明 |
|---|---|---|
| `idempotency.py` | 没做 | 现在没有工具需要幂等键；`accepts_idempotency_key` 留着，恢复判定在读它 |
| `dedup.py` | 没做 | 「只提醒不拦截」，没有用户之前是空转 |
| `mcp/` | 没做 | 首批 transport 还没定 |
| `builtin/` 只做 `read` | 七个都做了 | 见下表 |
| 位点 6 一个 handler | 两个 | 检查链（100）与收尾（445）中间隔着 sandbox（435） |

**内置工具集合**

| 工具 | 权限 | 副作用 | 做什么 |
|---|---|---|---|
| `read` | `read_only` | `none` | 读一个文件，返回带行号的内容 |
| `list_dir` | `read_only` | `none` | 列目录，目录在前、带尾斜杠 |
| `glob` | `read_only` | `none` | 按文件名模式递归找文件 |
| `grep` | `read_only` | `none` | 按正则搜内容，返回路径:行号:内容 |
| `write` | `editable` | `idempotent` | 整份写文件，父目录一并建好 |
| `edit` | `editable` | `idempotent` | 替换唯一命中的一段；零次或多次都不改 |
| `run_command` | `full_access` | `non_idempotent` | 跑一条命令，stderr 并进输出 |
| `todo_write` | `read_only` | `idempotent` | 整份替换待办（见 `modules/todo`） |
| `skill` | `read_only` | `none` | 按名字取技能正文 |
| `ask_user` | `read_only` | `non_idempotent` | 向用户提问并等回答（见 `modules/ask_user`） |
| `chart_line` / `chart_bar` / `chart_pie` / `chart_box` | `read_only` | `none` | 画折线/柱状/饼图/箱线图（见 `tools/chat_tools`） |

- **参数名不是随便起的**：文件类必须叫 `path`、命令类必须叫 `command`，沙箱按参数名路由（D8），换了名字那类判定整类消失。
- 回报的路径一律相对工作目录。失败时回**给人听的一句话**。
- `edit` 要求原文唯一（多处等于猜）。
- **等人工的工具自己声明超时**：`ToolDeclaration.timeout` 覆盖内核默认 60 秒；`ask_user` 声明 300 秒。
- **编排**：`scheduling.plan_for` 组内并发、组间串行；相邻的只读调用并成一组、绝不重排（`SideEffect.NONE` 是判据，不另设字段）。

**已否决（一句话）**：曾让运行时用幂等键或指纹做去重/硬拦截，前者越权、后者误杀，均放弃。

---

### D3 Skill（`modules/skill`，开发序 S8）

**定位**：可复用的任务指令包（目录 + 入口文件 + 可选脚本/资源）。

**边界**
- 做：扫描发现、声明解析、来源优先级合并去重、目录注入、正文按需加载。
- 不做：不执行 skill 里的脚本（走普通工具调用）。

**声明项**：名称（kebab-case）、描述、何时使用（可选）、调用策略（模型可调用/用户可调用）、正文、资源与脚本。

**关键正确设计**
- 发现阶段只读声明，正文按需加载（靠 `skill` 工具）。
- 不可模型调用的 skill 不进目录。
- 目录与系统提示词走同一条注入链（位点 3 追加进 `payload.injected_messages`），**不写 Session、不改日志**。
- 注入内容逐字稳定，落在可复用前缀里，不破坏缓存。
- 正文加载与脚本执行都是普通工具调用，走 D2 的位点 6/7/8。
- 来源优先级合并去重后再登记：后面的根覆盖前面的同名技能。

**已否决（一句话）**：曾把目录写成一条持久消息进 Session，要多一套条目类型与投影规则，放弃。

---

### D1 Agent Loop（`modules/agent_loop`，开发序 S9）

**定位**：主循环与位点管线，唯一认识「trace / turn」的模块。

**边界**
- 做：位点调度、trace/turn 生命周期、终止判定、预算守卫、`pending_queue` 消费时机、对外门面。
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
- **turn 定义**：一次模型调用 + 它触发的工具调用；`turn_index` 从 0 起，位点 2 加一。
- 终止状态四种（`completed`/`failed`/`aborted`/`budget_exceeded`），记在位点 10 的 `trace_end`。
- `pending_queue` 只在位点 5、8 消费，不打断当前动作。
- 位点 3 落盘失败 → 拦掉模型调用；位点 6 落盘失败 → 拦掉工具正文（「失败即阻止」由主循环执行）。
- 管线按 priority **串行**；但同一工具组的调用**并发**（`plan.groups` 由 tool_calling 算，内核按组 `asyncio.gather`）。
- **位点 7 → 8 的结果由内核搬运**（位点 8 的 payload 是新造的），组内并发时各搬各的。
- **模型调用失败分三种不重试**：种类不可重试、尝试次数用光、已吐出半截输出（I11）；前两种原样上抛，让 `trace_end.reason` 留下真实原因。
- **时间轴由内核记，不由维度记**：位点管线记一格一位点（含被否决、抛异常的那次），模型调用记一格一次调用（含重试）；起点与时长都是**相对 trace 起点**的毫秒，基准是 `RuntimeState.started_monotonic`（不落盘）。收尾时整份交给位点 10，由 session 写进 `trace_end` 条目。`trace:end` 自己那一格不在里面 —— 它记的是收尾本身。

**已否决（一句话）**：曾把各维度逻辑硬编码进主循环，新增维度必改内核，放弃。

---

## 6. 非维度基础设施

### 6.1 `infra/llm`（已完成，OpenAI 兼容）

- 统一**流式**接口：所有厂商差异归一成五种分片——文本增量、思考增量、**完整**工具调用、用量、结束。
- **思考增量**（推理模型在正文之前吐的那段）从 `reasoning_content` 取。开关走 `extra_body`
  （`AGENT_THINKING=true`），它不是 OpenAI 标准参数。
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
| 连不上 / 连接中途断掉 | `CONNECTION` | 重试（**退避** 0.3s / 0.8s / 2s） |
| 超时 | `TIMEOUT` | 重试（**退避**同上） |
| 认不出来（含非 SDK 的普通异常） | `UNKNOWN` | 上抛 |

- **超窗是独立类型**：`ContextOverflowError` 是 `LlmCallError` 的子类，`kind` 固定为 `OVERFLOW`。两个来源——位点 3 的 `guard` 拦下的，或模型挡回来的——处置一样。
- **重不重试由种类推出来**：`retryable` 是 `kind` 的派生属性（就是 `RETRYABLE_KINDS` 那张表），不另存一份。`llm.retry` 日志带 `kind`，排错不用猜是哪种失败。
- **不接管重试**：SDK 自带重试关掉（`max_retries=0`），重试由主循环做——它才掌握 `llm_attempt_number` 与"这次尝试有没有产出"（I11）。
- **网络类才退避**：`CONNECTION` / `TIMEOUT` 两次尝试之间等 0.3s → 0.8s → 2s（`turn_runner.retry_delay`）。网络抖动多在几百毫秒内恢复，三次只隔十几毫秒等于没重试；退避发生在 `llm` 那格 span 记完之后，等待不算进这次调用的耗时。鉴权、参数、超窗这些等多久都一样，立刻重试。
- **网络类失败要带上 `__cause__`**：SDK 的 `APIConnectionError` 默认文案就是 `Connection error.`，DNS 失败、连接被拒、连接被重置全在 `__cause__` 里；`errors.with_reason` 把它拼进 `LlmCallError.message`，否则 `trace_end.reason` 只剩六个字，查不出是网络还是地址写错。
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

- 配置项：模型、权限、`max_tokens`、`max_turns`、压缩阈值比例 `compact_ratio`、保留条数 n、长工具结果判定与保留条数、摘要提示词模板、思考开关（`AGENT_THINKING`）、沙箱策略、审批策略。
- `compact_ratio`（默认 **0.7**）算出 **`compact_tokens = int(max_tokens × compact_ratio)`**，在冻结时换算一次。**阈值不是独立配置项**：窗口一改，它跟着走，不存在"换了模型忘了改阈值、压缩永远不触发"这种事。
- **哪些是会话级的**：`model`、`permission`、`tool_result_max_chars` 由 `SessionMeta` 携带（建会话时从 `AppConfig` 抄一份初值），位点 1 从会话取；其余项只有全局一份。
- 位点 1 **冻结**本次 trace 快照；之后改配置不影响已开始的 trace。

**取值来源与优先级**（后面覆盖前面）：

1. 项目根目录的 `.env`（路径可用环境变量 `AGENT_ENV_FILE` 指定，设成空串则不读）
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

## 7. 测试体系

### 7.1 目录约定

- **一个模块一个测试目录**：`tests/test_<module>/`，与 `src/agent_frame/modules/<module>/` 一一对应；非维度层也各有目录（`test_contracts/`、`test_infra/`、`test_api/`）。
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

### 7.2 分层测试口径

| 层 | 手段 | 关注 |
|---|---|---|
| 纯逻辑（runtime_state / session / compression 判定 / sandbox 策略） | 直接函数调用 | 状态迁移、边界条件、确定性 |
| 契约（storage 的 PG 版 vs 文件版） | 同一套 `SessionStore` 用例参数化两种实现 | 行为一致 |
| 位点（各模块 handler） | 用最小 `agent_loop/pipeline.py` 单独调度 | 位点前后副作用 |
| 集成（agent_loop） | 全部假件驱动 | trace/turn 顺序、终止、队列消费 |
| 恢复 | 在指定 seq 注入 flush 失败 / 在 `os.replace` 前后各崩一次 / 悬空调用 | 前缀正确、配平正确 |
| API（HTTP） | `httpx.ASGITransport` | 状态码、错误形状、`user_id` 隔离 |
| API（流） | 自己驱动 ASGI 读流（TestClient 会整体缓冲） | 帧序、`ready` 先于事件、断开摘听众、心跳、积压收尾 |

### 7.3 覆盖目标

- 九个维度模块：分支覆盖 ≥ 90%
- `checkpoint` 与 `compression`：分支覆盖 ≥ 95%（正确性风险最高）
- `contracts`：不测（纯类型）

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
| D3 Skill | `modules/skill/` | `tests/test_skill/` | S8【已完成】 |
| D1 Agent Loop | `modules/agent_loop/` | `tests/test_agent_loop/` | S9 |
| —（非维度） | `modules/prompts/` | `tests/test_prompts/` | 【已完成】 |
| —（非维度） | `modules/todo/` | `tests/test_todo/` | 【已完成】 |
| —（非维度） | `modules/charts/` | `tests/test_charts/` | 【已完成】 |
| —（非维度） | `modules/ask_user/` | `tests/test_ask_user/` | 【已完成】 |
"""契约层共享数据模型。

设计原则：**结构放契约，算法放模块**。
这里只有数据，没有行为——不读文件、不调模型、不碰磁盘。
九个维度模块互不 import，它们交换的数据全部来自这个文件。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

# JSON 可序列化值的类型别名，工具的 arguments 用它。
#
# 举例：{"path": "a.py", "limit": 10}
JsonValue = Any

# 时间戳类型别名。统一使用带时区的 datetime（UTC）。
#
# 举例：datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
Timestamp = datetime


# --------------------------------------------------------------------------
# 枚举：所有"固定几个值"的字段都在这里，每个取值配中文解释
# --------------------------------------------------------------------------


class Permission(StrEnum):
    """权限档位。决定沙箱放行哪些操作。"""

    # 只读 —— 只能读工作目录内的文件；写文件一律拒绝（边界是明确的）。
    # 碰命令、网络这类**无法静态判定的能力**时走人工审批，不硬拒 —— 判不准就不替用户做决定。
    READ_ONLY = "read_only"

    # 可编辑 —— 允许在工作目录内读写文件；碰命令、网络仍走人工审批。
    EDITABLE = "editable"

    # 完全访问 —— 允许执行命令、访问网络等全部能力，风险最高。
    FULL_ACCESS = "full_access"


class MessageRole(StrEnum):
    """一条消息的发言方。"""

    # 系统 —— 每次模型调用前现拼的提示词。**只出现在请求里，不写进 Session 日志。**
    #
    # 举例：Message(role=MessageRole.SYSTEM, content=[TextPart(text="你是 agent-architecture……")])
    SYSTEM = "system"

    # 用户 —— 用户输入的消息，也可能是用户中途插话（pending_queue）。
    USER = "user"

    # 模型 —— 模型的输出，可能同时包含文本和它请求的工具调用。
    ASSISTANT = "assistant"

    # 工具 —— 工具执行结果回填的消息，metadata 里带 tool_call_id。
    TOOL = "tool"


class ToolCallStatus(StrEnum):
    """一条工具调用的生命周期状态。"""

    # 待执行 —— 已通过前置检查，排队等待执行。
    PENDING = "pending"

    # 待审批 —— 需要人工放行，正在等审批回执，尚未产生任何副作用。
    AWAITING_APPROVAL = "awaiting_approval"

    # 执行中 —— 已派发；从这一刻起副作用可能已经发生，结果未知。
    RUNNING = "running"

    # 未执行即结束 —— 工具不存在、参数非法，或被审批拒绝。它不计入工具统计。
    REJECTED = "rejected"


class SideEffect(StrEnum):
    """工具的副作用等级。决定失败/超时/崩溃恢复时能不能自动重试。"""

    # 无副作用 —— 纯读取，比如 read、grep。重试永远安全。
    NONE = "none"

    # 幂等 —— 重复执行结果相同，比如"把文件内容设为 X"。重试安全。
    IDEMPOTENT = "idempotent"

    # 非幂等 —— 重复执行会产生额外影响，比如"发送邮件"。
    # 只有在该工具接受幂等键时才允许重试，否则一律不重试。
    NON_IDEMPOTENT = "non_idempotent"


class SessionEntryType(StrEnum):
    """Session 日志里一条条目的类型。"""

    # trace 开始 —— 记录本次执行的初始快照（触发者、父子关系、模型、权限、预算）。
    # 它只标记边界，不进上下文 messages。
    TRACE_START = "trace_start"

    # trace 结束 —— 记录结束状态与原因。恢复时靠"末尾有没有它"判断上次是否正常收尾。
    # 它只标记边界，不进上下文 messages。
    TRACE_END = "trace_end"

    # 会话消息 —— 用户消息、模型输出、工具结果。只有它进上下文 messages。
    MESSAGE = "message"

    # 工具调用已派发 —— 位点 6 在副作用发生前写下它，证明这次调用已经发出去过。
    # 它只标记边界，不进上下文 messages。
    TOOL_START = "tool_start"

    # 压缩 —— 一段旧消息被一条摘要取代。摘要本身进上下文 messages。
    COMPACTION = "compaction"


class SessionStatus(StrEnum):
    """会话的生命周期状态。"""

    # 活跃 —— 可以继续发消息、继续执行。
    ACTIVE = "active"

    # 已归档 —— 只读，不再接受新消息，但历史仍可查询与恢复。
    ARCHIVED = "archived"


class TraceTrigger(StrEnum):
    """谁发起了这次 trace。"""

    # 用户 —— 用户在界面上发了一条消息。
    USER = "user"

    # 定时 —— 定时任务按计划自动发起。
    SCHEDULE = "schedule"

    # 上游 agent —— 由另一个 agent（父 trace）派生出来的子任务。
    AGENT = "agent"

    # 外部 API —— 第三方系统通过接口直接发起。
    API = "api"


class TraceStatus(StrEnum):
    """一次 trace 的收尾状态，写进 trace_end 条目。"""

    # 正常完成 —— 模型给出了最终答复，或工具执行完毕且无后续动作。
    COMPLETED = "completed"

    # 失败 —— 模型调用失败，或处理过程中抛出未预期的异常。
    FAILED = "failed"

    # 被中止 —— 用户手动中断，或输出意外断流。恢复时看的就是它。
    ABORTED = "aborted"

    # 超预算 —— 撞到 max_turns，或压缩后仍超出上下文窗口。
    BUDGET_EXCEEDED = "budget_exceeded"


class StopReason(StrEnum):
    """模型这次输出为什么停下来。"""

    # 自然结束 —— 模型说完了，没有请求工具调用。
    END_TURN = "end_turn"

    # 请求工具 —— 模型要求执行工具，本轮结束但 trace 还要继续。
    TOOL_CALLS = "tool_calls"

    # 输出被截断 —— 达到单次输出上限，内容可能不完整。
    MAX_TOKENS = "max_tokens"

    # 被中止 —— 用户中断或网络断流。
    ABORTED = "aborted"


class ApprovalDecision(StrEnum):
    """人工审批的回执结果。"""

    # 同意 —— 该工具调用放行执行。
    APPROVE = "approve"

    # 拒绝 —— 该工具调用不执行，标记为 rejected。
    DENY = "deny"


# --------------------------------------------------------------------------
# 消息与内容块
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TextPart:
    """文本内容块。"""

    # 这一块的文本正文。
    #
    # 举例："帮我读一下 src/main.py"
    text: str


@dataclass(frozen=True, slots=True)
class ThinkingPart:
    """模型的思考过程。**只给人看，不回传给模型。**

    推理模型（DeepSeek-R1 这类）在正文之前会先吐一段推理，它进日志、进界面，
    但下一轮请求里不带它 —— 省 token，而多数服务端也不接受回传。
    """

    # 这一轮的完整思考内容。
    #
    # 举例："用户要读文件，先确认路径在不在工作目录里"
    text: str


@dataclass(frozen=True, slots=True)
class ImagePart:
    """图片内容块。"""

    # 图片的 MIME 类型。
    #
    # 取值（常见三种）：
    #     - "image/png"   PNG 位图，截图默认格式
    #     - "image/jpeg"  JPEG 照片
    #     - "image/webp"  WebP，体积更小
    # 举例："image/png"
    media_type: str

    # 图片的 base64 原文；与 url 二选一。
    #
    # 举例："iVBORw0KGgoAAAANSUhEUg..."
    data_base64: str | None

    # 图片的可访问地址；与 data_base64 二选一。
    #
    # 举例："https://example.com/chart.png"
    url: str | None


@dataclass(frozen=True, slots=True)
class ToolCallPart:
    """模型请求调用某个工具的内容块（挂在 assistant 消息上）。"""

    # 本次调用的唯一标识，由模型或适配层生成。
    #
    # 作用：工具结果靠它回填；幂等键的构成要素之一。
    # 举例："call_7f3a91"
    tool_call_id: str

    # 要调用的工具名，必须已在工具注册表里。
    #
    # 举例："read"
    name: str

    # 模型给出的原始参数，尚未补默认值、尚未校验。
    #
    # 举例：{"path": "src/main.py"}
    arguments: JsonValue


# 一条消息里的内容块。一条消息可以有多块，比如"思考 + 文字 + 工具调用"。
ContentPart = TextPart | ThinkingPart | ImagePart | ToolCallPart


@dataclass(slots=True)
class Message:
    """会话中的一条消息，是上下文投影的最小单位。"""

    # 发言方。
    #
    # 举例：MessageRole.USER
    role: MessageRole

    # 内容块列表，按顺序拼接成这条消息。
    #
    # 举例：[TextPart(text="帮我读一下 src/main.py")]
    content: list[ContentPart]

    # 附加信息。工具结果消息在这里带 tool_call_id，用来和请求配对。
    #
    # 举例：{"tool_call_id": "call_7f3a91"}
    metadata: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Usage:
    """一次模型调用的用量统计。"""

    # 本次请求占用的输入 token 数。
    #
    # 举例：15230
    input_tokens: int

    # 本次输出产生的 token 数。
    #
    # 举例：418
    output_tokens: int


@dataclass(frozen=True, slots=True)
class TokenAnchor:
    """上一次请求的真实用量，下一次估算从它往后加。

    它是**可复用的测量**，不是事实：只有"这次要发的 messages 是它的严格扩展"时才认它，
    否则作废、退回全量估算。

    举例：TokenAnchor(messages=(...), input_tokens=42815, output_tokens=312)
    """

    # 上次真正发出去的那份 messages。
    #
    # 举例：(Message(role=MessageRole.USER, ...), Message(role=MessageRole.ASSISTANT, ...))
    messages: tuple[Message, ...]

    # 提供方报的提示词规模。**已经含工具 schema 与系统提示词**，别再往上加。
    #
    # 举例：42815
    input_tokens: int

    # 提供方报的生成规模。它下一次也进上下文（思考也在里面）。
    #
    # 举例：312
    output_tokens: int


# --------------------------------------------------------------------------
# 工具声明
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ToolDeclaration:
    """工具的静态声明。注册时一次性写死，运行时按它决定怎么呈现给模型、能不能重试。"""

    # 模型看到的工具名，全局唯一。
    #
    # 举例："read"
    name: str

    # 告诉模型这个工具做什么、什么时候该用。这句话会进上下文，直接影响模型选不选它。
    #
    # 举例："读取文件内容。当需要查看某个文件的现有实现时使用。"
    description: str

    # 参数的 JSON Schema，描述类型、约束与默认值。缺省参数由运行时的补默认值步骤补齐。
    #
    # 举例：{"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}
    parameters_schema: dict[str, JsonValue]

    # 输出契约的文字说明：这个工具产出什么结构、怎么投影成模型可读内容。
    #
    # 举例："返回文本；超过 token 上限时原文落盘，消息里只留路径与省略元数据。"
    output_contract: str

    # 副作用等级，决定失败与超时能不能自动重试。
    #
    # 举例：SideEffect.NONE
    side_effect: SideEffect

    # 是否接受幂等键并透传给下游做去重。
    #
    # 作用：为 True 时，即使是 non_idempotent 的调用也可以安全重试。
    # 举例：True
    accepts_idempotency_key: bool

    # 执行本工具所需的最低权限档位。低于它就进入审批或被拒绝。
    #
    # 举例：Permission.EDITABLE
    required_permission: Permission

    # 工具可见性作用域：只有作用域匹配的 agent 才看得到这个工具的 schema。
    #
    # 举例："coding"；None 表示对所有 agent 可见。
    scope: str | None


# --------------------------------------------------------------------------
# Runtime State 的零件
# --------------------------------------------------------------------------


@dataclass(slots=True)
class ToolCall:
    """一条工具调用的执行态记录，在落地前一直挂在 Runtime State 上。"""

    # 本次调用的唯一标识（来自模型的 tool_call_id）。
    #
    # 作用：幂等键的构成要素；工具结果靠它回填到对应调用。
    # 举例："call_7f3a91"
    id: str

    # 工具名，必须已在注册表中。
    #
    # 举例："read"
    name: str

    # 补齐默认值、并通过校验之后的最终参数。
    #
    # 举例：{"path": "src/main.py", "limit": 2000}
    arguments: JsonValue

    # 这条调用当前走到生命周期哪一步。
    #
    # 举例：ToolCallStatus.PENDING
    status: ToolCallStatus


@dataclass(slots=True)
class ToolStat:
    """本轮某个工具的成功/失败计数，用来发现"某个工具是不是在反复失败"。"""

    # 工具名，聚合的键。
    #
    # 举例："grep"
    name: str

    # 本轮成功次数。被拒绝（rejected）的调用不计入这里。
    #
    # 举例：3
    succeeded: int

    # 本轮失败次数。被拒绝（rejected）的调用同样不计入。
    #
    # 举例：1
    failed: int


@dataclass(frozen=True, slots=True)
class BudgetSnapshot:
    """写进 trace_start 条目的预算快照。"""

    # 本次 trace 允许的最大 turn 数。
    #
    # 举例：50；None 表示不限。
    max_turns: int | None

    # 本次 trace 的上下文窗口上限。
    #
    # 举例：128000
    max_tokens: int


@dataclass(frozen=True, slots=True)
class CompactionSnapshot:
    """写进 trace_start 条目的压缩配置快照。"""

    # 上下文触达该值即开始压缩。
    #
    # 举例：100000
    compact_tokens: int

    # 二级压缩保留的最后 n 条 message 条目。
    #
    # 举例：20
    keep_recent: int

    # 单条工具结果超过多少 token 算"长"。
    #
    # 举例：2000
    long_tool_result_max_tokens: int

    # 长工具结果保留最近几条不替换。
    #
    # 举例：3
    long_tool_result_keep_recent: int


@dataclass(slots=True)
class RuntimeState:
    """一次 trace 期间常驻内存的运行态。

    它不是真相源：历史消息、工具结果都能从 Session 日志重放出来，所以这里不存。
    凡是能由 Session 重建的，都放这里；不能重建的，才落 Session。
    trace 结束即销毁，不落盘、不参与崩溃恢复。
    """

    # —— 身份游标：我是谁，跑到哪了 ——

    # 会话标识，跨 trace、跨进程稳定。位点 1 绑定。
    #
    # 举例："sess_20250101_ab12"
    session_id: str

    # 本次 trace 的唯一标识（一次用户请求的执行）。位点 1 生成后不再变。
    # 举例："trace_9c8b7a"
    trace_id: str

    # 父 trace 标识，用于 subagent / 并行子任务；顶层为 None。
    #
    # 举例："trace_111111"；顶层为 None
    parent_trace_id: str | None

    # 当前是第几轮（turn = 一次大模型调用 + 它触发的工具调用），从 0 开始。
    # 每个 turn 开始时（位点 2）加一。
    #
    # 举例：3
    turn_index: int

    # 本 turn 内模型调用的重试计数，首次调用为 0。
    # 调用失败且判定可重试时加一，进入新 turn 时归零。
    #
    # 举例：1（表示这是第 2 次尝试）
    llm_attempt_number: int

    # —— 进度与预算：花了多少，还能花多少 ——

    # 当前这次请求实际占用的上下文量 = system prompt + 历史消息 + 工具 schema。
    # 这是本架构唯一的 token 指标：位点 3 模型调用前计算、调用后重算，
    # 且总在 pending_queue 注入之后。
    #
    # 举例：42815
    context_tokens: int

    # 上下文窗口上限。位点 1 由模型配置写入。
    #
    # 举例：128000
    max_tokens: int

    # turn 数上限，None 表示不限。位点 1 由配置写入；位点 9 用它判断是否该收尾。
    #
    # 举例：50
    max_turns: int | None

    # 本轮各工具的成功与失败次数，按工具名聚合，用来判断某个工具是否反复失败。
    # turn 开始时（位点 2）清空，每条工具结果返回时（位点 8）加一，被拒的调用不计入。
    # 举例：{"grep": ToolStat(name="grep", succeeded=3, failed=1)}
    turn_tool_stats: dict[str, ToolStat]

    # 压缩阈值：context_tokens 触达该值即触发压缩。位点 1 写入。
    compact_tokens: int

    # 本次 trace 开始的时刻（位点 1 写入）。
    # 耗时不在状态里累计，由"当前时间 - 它"得出。
    #
    # 举例：datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
    started_at: Timestamp

    # —— 信息：本次执行正在搬动什么数据 ——

    # 当前正在处理的用户消息原文。位点 1 写入。
    #
    # 举例：Message(role=MessageRole.USER, content=[TextPart(text="帮我读 a.py")])
    user_message: Message | None

    # 用户中途发来的消息队列。不打断当前动作，等模型调用或工具调用返回后再注入。
    # 任何时刻可入队；只在位点 5 与位点 8 出队。
    #
    # 举例：deque([Message(role=MessageRole.USER, content=[TextPart(text="顺便看下 b.py")])])
    pending_queue: deque[Message]

    # agent 是否正在执行。
    # 为 False 时新消息直接开启新 trace；为 True 时入队。trace 开始置 True，结束置 False。
    #
    # 举例：True
    is_running: bool

    # 本轮模型要求执行、尚未落地的工具调用。
    # 位点 5 写入，位点 6/7 逐条改状态，位点 8 逐条移除。
    #
    # 举例：[ToolCall(id="call_7f3a91", name="read", arguments={"path": "a.py"},
    #                 status=ToolCallStatus.PENDING)]
    pending_tool_calls: list[ToolCall]

    # —— 模型与权限：用什么模型，以什么权限执行 ——

    # 当前 turn 使用的大模型标识。位点 1 写入；运行中切换模型时更新。
    #
    # 举例："deepseek-chat"
    model: str

    # 当前 agent 的权限档位。位点 1 由配置写入；运行中可被策略提升或降级。
    #
    # 举例：Permission.EDITABLE
    permission: Permission


# --------------------------------------------------------------------------
# Session 日志
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TraceStartEntryPayload:
    """trace_start 条目的内容：本次 trace 的初始快照。

    只放"推不出来"的东西——耗时能由时间戳相减得出，turn 数与 token 量能重算，都不写。
    """

    # 谁发起了这次 trace。
    #
    # 举例：TraceTrigger.USER
    trigger: TraceTrigger

    # 父 trace 标识，顶层为 None。
    #
    # 举例：None
    parent_trace_id: str | None

    # 本次生效的模型。
    #
    # 举例："deepseek-chat"
    model: str

    # 本次生效的权限档位。
    #
    # 举例：Permission.EDITABLE
    permission: Permission

    # 本次生效的预算快照。
    #
    # 举例：BudgetSnapshot(max_turns=50, max_tokens=128000)
    budget: BudgetSnapshot

    # 本次生效的压缩配置快照。
    #
    # 举例：CompactionSnapshot(compact_tokens=100000, keep_recent=20,
    #                         long_tool_result_max_tokens=2000,
    #                         long_tool_result_keep_recent=3)
    compaction: CompactionSnapshot


@dataclass(frozen=True, slots=True)
class TraceEndEntryPayload:
    """trace_end 条目的内容：本次 trace 的收尾事实。"""

    # 结束状态，决定恢复时怎么处理。
    #
    # 举例：TraceStatus.COMPLETED
    status: TraceStatus

    # 失败原因、被谁中断等；正常结束为 None。
    #
    # 举例："用户中断"；正常结束为 None
    reason: str | None

    # 这次 trace 一共跑了几轮。**会话级的总轮数靠它求和** —— 轮次没有日志条目，数不出来。
    #
    # 0 表示没有记录（进程中断后由恢复侧补出来的那条 trace_end）。
    #
    # 举例：3
    turns: int = 0

    # 收尾时最后一次模型请求占用的上下文量（历史消息 + 工具 schema）。
    #
    # 0 表示没有记录；界面上"上下文占用"那个圈读的就是它。
    #
    # 举例：42815
    context_tokens: int = 0


@dataclass(frozen=True, slots=True)
class ToolStartEntryPayload:
    """tool_start 条目的内容：一次工具调用已通过前置检查、即将执行。

    这条日志一旦写下，这次调用就算已经派发——恢复时靠它区分
    "调用在开始前就被打断"（没做过，可安全重试）和"结果未知"。
    """

    # 对应 assistant 消息里的那次调用。
    #
    # 举例："call_7f3a91"
    tool_call_id: str

    # 工具名。
    #
    # 举例："write"
    name: str

    # 本次调用的幂等键，位点 6 算出。重试与重放时它不变，保证命中的是同一次副作用。
    #
    # 举例："a3f1c9e2b7d4..."
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class CompactionEntryPayload:
    """compaction 条目的内容：一段旧消息被这条摘要取代。"""

    # 压缩产生的摘要。进上下文时它作为一条消息出现，且必须能独立阅读。
    #
    # 举例："用户要求重构 session 模块。已确定投影规则为……下一步是实现游标增量并入。"
    summary: str

    # 未被压缩、仍然保留的 message 条目的 entry_id。
    # 只引用、不内嵌完整条目——它们本来就在 entries 里。
    #
    # 举例：["e_0042", "e_0043", "e_0044"]
    entry_ids: list[str]


# 五种条目的 payload。注意 message 类型的 payload 就是 Message 本身。
SessionEntryPayload = (
    TraceStartEntryPayload
    | TraceEndEntryPayload
    | ToolStartEntryPayload
    | CompactionEntryPayload
    | Message
)


@dataclass(slots=True)
class SessionEntry:
    """Session 日志里的一条条目。日志只追加，不改写、不删除。"""

    # 条目自身的唯一标识。compaction 的 entry_ids 引用的就是它。
    #
    # 举例："e_0042"
    entry_id: str

    # 属于哪一次 trace。trace 划的是"执行"的边界，不是"上下文"的边界。
    #
    # 举例："trace_9c8b7a"
    trace_id: str

    # 单调递增的序号，决定重放顺序。同一个会话内全局递增。
    #
    # 举例：42
    seq: int

    # 这条条目产生的时刻。
    #
    # 举例：datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
    timestamp: Timestamp

    # 条目类型，决定 payload 按哪种结构解析。
    #
    # 举例：SessionEntryType.MESSAGE
    type: SessionEntryType

    # 条目内容，结构由 type 决定。
    #
    # 举例：Message(role=MessageRole.USER, content=[TextPart(text="帮我读 a.py")])
    payload: SessionEntryPayload


@dataclass(slots=True)
class SessionMeta:
    """会话的元信息。它和 entries 一起构成磁盘上的 Session。"""

    # 会话标识，可被寻址、恢复、分叉。
    #
    # 举例："sess_20250101_ab12"
    session_id: str

    # 会话属于哪个用户。**隔离的判据**：存储层按它拒绝跨用户的读。
    # 它不做鉴权 —— "是谁的用户"由调用方（api 层）负责确认，存储只认这个值。
    #
    # 举例："user_7f3a91"
    user_id: str

    # 会话创建时刻。
    #
    # 举例：datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
    created_at: Timestamp

    # 最近一次更新时刻。位点 10 收尾时刷新。
    #
    # 举例：datetime(2025, 1, 1, 12, 5, tzinfo=UTC)
    updated_at: Timestamp

    # 由哪个会话分叉而来；不是分叉出来的就是 None。
    #
    # 举例："sess_20241231_zz99"；顶层为 None
    parent_session_id: str | None

    # 会话标题，由首条用户消息或模型归纳生成，用于列表展示。
    #
    # 举例："重构 session 模块"
    title: str | None

    # 工作目录，同时也是沙箱根。
    #
    # 举例："D:/CodeProjects/agent_architecture"
    workspace: str

    # 新 trace 默认使用的模型。
    #
    # 举例："deepseek-chat"
    model: str

    # 新 trace 的默认权限档位。
    #
    # 举例：Permission.EDITABLE
    permission: Permission

    # 本会话单条工具结果最多留多少个字符。建会话时从全局配置抄一份初值，之后随会话落盘 ——
    # 同一条会话日志里的截断尺度因此始终一致，改全局设置不会让旧会话前后两半对不上。
    #
    # 举例：20000
    tool_result_max_chars: int

    # 会话状态。
    #
    # 举例：SessionStatus.ACTIVE
    status: SessionStatus

    # **投影的起点**：最近一次 compaction 的 entry_id；没有压缩就是空串（从头开始）。
    # 它只在重建时算一次 —— Session 构造、崩溃恢复、每次压缩之后 —— 不随每轮并入前进。
    #
    # 举例："e_0045"
    context_cursor: str


# --------------------------------------------------------------------------
# 模型输出草稿
# --------------------------------------------------------------------------


@dataclass(slots=True)
class Draft:
    """一段模型输出：从开始产生，到被并入上下文或定稿为止。

    它既不属于 Runtime State（那是"一次 trace 一组"，而它要活过 trace 边界），
    也不属于磁盘上的 Session（它永不落盘），而是内存 Session 的一部分。
    """

    # 在哪次 trace 里产生。
    #
    # 举例："trace_9c8b7a"
    trace_id: str

    # 第几轮产生。
    #
    # 举例：3
    turn_index: int

    # 开始产生的时刻。
    #
    # 举例：datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
    created_at: Timestamp

    # 已经收到的正文，可能为空串。
    #
    # 举例："我来帮你读一下这个文件"（可能只收到一半："我来帮你读一"）
    content: str

    # 已经收到的思考过程，可能为空串。**和正文分开存** —— 它不进下一轮请求。
    #
    # 举例："用户要读文件，先确认路径在不在工作目录里"
    reasoning: str = ""


@dataclass(slots=True)
class DraftSlot:
    """会话级的草稿槽：进程内、跨 trace、永不落盘。

    单槽是自然结果而非取舍——一段输出被并入上下文之后就腾空了，
    同一时刻槽里只会有一段。
    """

    # 这个槽属于哪个会话。
    #
    # 举例："sess_20250101_ab12"
    session_id: str

    # 当前草稿；None 表示槽是空的。
    #
    # 举例：Draft(trace_id="trace_9c8b7a", turn_index=3,
    #              created_at=..., content="我来帮你读一下")
    draft: Draft | None


# --------------------------------------------------------------------------
# 审批
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    """一次待人工确认的审批请求。"""

    # 审批请求的唯一标识，回执时靠它配对。
    #
    # 举例："appr_5f2c"
    request_id: str

    # 这次调用属于哪个会话。**路由与归属的键**：事件按它分发，回执按它核对是不是这个人的。
    #
    # 举例："sess_20250101_ab12"
    session_id: str

    # 发起审批的 trace。
    #
    # 举例："trace_9c8b7a"
    trace_id: str

    # 触发审批的那次工具调用。
    #
    # 举例："call_7f3a91"
    tool_call_id: str

    # 工具名。
    #
    # 举例："write"
    tool_name: str

    # 已经补齐默认值、通过校验的最终参数。审批按这些参数判定，而不是模型的原始输入。
    #
    # 举例：{"path": "src/main.py", "content": "..."}
    arguments: JsonValue

    # 为什么需要审批，会展示给用户。
    #
    # 举例："write 会修改文件，当前权限 editable 且策略要求人工确认"
    reason: str


@dataclass(frozen=True, slots=True)
class ApprovalResult:
    """审批回执。"""

    # 对应哪次审批请求。
    #
    # 举例："appr_5f2c"
    request_id: str

    # 审批结论。
    #
    # 举例：ApprovalDecision.APPROVE
    decision: ApprovalDecision

    # 审批人的备注，会写进日志便于追溯。
    #
    # 举例："同意，这个文件本来就该改"；没备注为 None
    comment: str | None

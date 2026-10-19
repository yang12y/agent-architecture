"""十位点的契约：位点枚举、位点专属数据、位点上下文、handler 接口。

位点是全系统唯一的扩展点，一共十个，顺序固定，不可增删。

**位点之间的先后由主循环的代码决定，不由这里的数据决定**：
主循环写的是 `位点1 -> while{位点2..9} -> 位点10`。
本文件里的管线相关部分只负责一件事——在某个位点上，把注册在这一刻的 handler
按优先级顺序跑完。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from .config import FrozenConfig
from .errors import AgentError
from .models import (
    JsonValue,
    Message,
    Permission,
    RuntimeState,
    StopReason,
    ToolCall,
    ToolCallPart,
    ToolStat,
    TraceStatus,
    TraceTrigger,
    Usage,
)
from .ports import Ports, SessionHandle

# --------------------------------------------------------------------------
# 位点枚举
# --------------------------------------------------------------------------


class Site(StrEnum):
    """十个注入位点。取值是"时间点"，不是"动作"。"""

    # ① trace 开始前 —— 收到用户消息、还没建 Runtime State。
    # 谁在哪：checkpoint 先查要不要恢复，session 写 trace_start 与用户消息，
    # skill 发现目录，tool_calling 注册工具，runtime_state 绑定游标与上限。
    # 是否决位点：任何 handler 返回 False，这次 trace 就不开始——位点 2-9 一次都不跑，
    # 位点 10 照常收尾为 failed，Runtime State 照样被销毁。
    TRACE_BEFORE = "trace:before"

    # ② turn 开始前 —— 每轮循环的开头。
    # 谁在哪：runtime_state 把 turn_index 加一、重试计数归零、清空本轮工具统计。
    TURN_BEFORE = "turn:before"

    # ③ 模型调用前 —— 请求还没发出去。
    # 谁在哪：runtime_state 算 context_tokens，compression 超阈值就压缩，
    # tool_calling 填工具 schema，skill 注入目录，checkpoint 落盘（屏障）。
    LLM_BEFORE = "llm:before"

    # ④ 模型调用中 —— 每次模型调用触发一次（进入收流的那一刻），不是每个 token 一次。
    # 谁在哪：hook 可在此设置超时、挂上取消条件。流式增量不走这里，
    # 由内核直接写草稿槽并推 EventBus。
    LLM_DURING = "llm:during"

    # ⑤ 模型调用后 —— 流已经结束。
    # 谁在哪：runtime_state 解析出本轮的 pending_tool_calls、消费中途消息，
    # session 定稿 assistant 消息，compression 处理超窗兜底，checkpoint 落盘（屏障）。
    LLM_AFTER = "llm:after"

    # ⑥ 工具调用前 —— 整批待执行调用，一次管线运行覆盖整批。
    # 谁在哪：tool_calling 走完整检查链并算出幂等键，sandbox 判定审批，
    # session 写 tool_start，checkpoint 落盘（屏障）。
    # 这是两个否决位点之一（另一个是位点 1）：返回 False 表示这一批一条都不执行。
    # 不执行的调用也在这里收尾：内核只对判定为 execute 的调用往下跑位点 7/8，
    # 所以被拒（reject）与审批没走完（await_approval）的调用，结果消息必须在这一位点写完 ——
    # 少了它，assistant 里的 tool_call 没有配对结果，下一轮请求直接不合法（不变量 I10）。
    TOOL_BEFORE = "tool:before"

    # ⑦ 工具调用中 —— 逐条执行，一条一次。
    # 谁在哪：tool_calling 真正执行（含重试与超时），sandbox 强制边界。
    TOOL_DURING = "tool:during"

    # ⑧ 工具调用后 —— 逐条结果回来，一条一次。
    # 谁在哪：tool_calling 规范化输出（超长则落盘），session 写结果消息，
    # runtime_state 累加统计并移除该条，checkpoint 落盘（屏障）。
    TOOL_AFTER = "tool:after"

    # ⑨ turn 结束 —— 本轮收尾。
    # 谁在哪：runtime_state 检查 turn_index 是否触达 max_turns，内核据此决定是否收尾。
    TURN_END = "turn:end"

    # ⑩ trace 结束 —— 无论正常还是异常都要走到这里。
    # 谁在哪：session 写 trace_end 并刷新 updated_at，runtime_state 置 is_running=False，
    # checkpoint 落盘（屏障），随后 Runtime State 被销毁。
    TRACE_END = "trace:end"


# 内核会解释"否决"语义的位点。只有这两处读管线的返回值并据此跳过下游动作，
# 其余 8 个位点的返回值一律丢弃（在那里返回 False 是 handler 的 bug）。
#
# 位点 1：否决 = 这次 trace 不开始 —— 位点 2-9 一次都不跑，位点 10 照常收尾为 failed
# 位点 6：否决 = 这一批工具一条都不执行 —— 跳过位点 7/8，turn 循环继续
#
# 举例：Site.TOOL_BEFORE in REJECTION_SITES -> True
REJECTION_SITES: frozenset[Site] = frozenset({Site.TRACE_BEFORE, Site.TOOL_BEFORE})


# priority 的合法段位，每项是 (段位名, 下界, 上界)。数值越小越先跑；同段位内按注册顺序。
#
# 这是分段的**唯一事实来源**：装配期 HandlerRegistry 逐个校验，不落在任何段位里的
# priority 直接抛 ValueError。校验放在装配期而不是运行期，是因为排序结果一旦不对，
# "落盘屏障必须最后跑"这类依赖只会静默失效，事后很难查。
#
# 举例：PRIORITY_BANDS[1] -> ("dimension", 100, 499)
PRIORITY_BANDS: tuple[tuple[str, int, int], ...] = (
    # 恢复判据 —— 只有 checkpoint 在位点 1 用。必须抢在所有维度之前拿到恢复判据，
    # 晚一步 session 就已经把新 trace 写下去了。
    ("recovery", 50, 50),
    # 维度内置逻辑 —— 九个维度模块的默认区间。
    ("dimension", 100, 499),
    # 用户 Hook —— 排在维度之后，能看到维度的结果；又必须早于落盘屏障，
    # 否则它改的数据落不下去。
    ("hook", 500, 899),
    # 落盘屏障 —— 只有 checkpoint 用。必须最后跑。
    ("barrier", 900, 900),
)


# --------------------------------------------------------------------------
# 取消信号
# --------------------------------------------------------------------------


class CancelToken:
    """协作式取消信号。

    作用：用户中断、超时等外部原因要停下整次 trace 时，调用 set()；
    内核与长耗时 handler 在安全点检查 is_set() 并自己退出。
    举例：token = CancelToken(); token.set(); token.is_set() -> True
    """

    def __init__(self) -> None:
        self._event = asyncio.Event()

    def set(self) -> None:
        """触发取消。可以重复调用。

        举例：token.set()
        """
        self._event.set()

    def is_set(self) -> bool:
        """是否已被取消。非阻塞检查。

        举例：if token.is_set(): return
        """
        return self._event.is_set()

    async def wait(self) -> None:
        """挂起直到被取消。适合和别的 await 一起 race。

        举例：await asyncio.wait([token.wait(), llm_task], return_when=FIRST_COMPLETED)
        """
        await self._event.wait()


# --------------------------------------------------------------------------
# 十个位点的专属数据
# --------------------------------------------------------------------------


@dataclass(slots=True)
class TraceBeforePayload:
    """位点 1（trace:before）的专属数据。此时 Runtime State 还没建好。"""

    # 这次执行挂在哪个会话上。已存在的会话沿用，不存在就新建。
    #
    # 举例："sess_20250101_ab12"
    session_id: str

    # 谁发起了这次 trace。
    #
    # 举例：TraceTrigger.USER
    trigger: TraceTrigger

    # 父 trace 标识，subagent 派生时才有；顶层为 None。
    #
    # 举例：None
    parent_trace_id: str | None

    # 触发这次执行的用户消息原文。
    #
    # 举例：Message(role=MessageRole.USER, content=[TextPart(text="帮我读 a.py")])
    user_message: Message

    # 本次 trace 打算使用的模型。位点 1 之后会冻结进 Runtime State。
    #
    # 举例："deepseek-chat"
    model: str

    # 本次 trace 打算使用的权限档位。
    #
    # 举例：Permission.EDITABLE
    permission: Permission


@dataclass(slots=True)
class TurnBeforePayload:
    """位点 2（turn:before）的专属数据。"""

    # 即将开始的这一轮的序号。Runtime State 会随即把它加一。
    #
    # 举例：3
    turn_index: int


@dataclass(slots=True)
class LlmBeforePayload:
    """位点 3（llm:before）的专属数据。

    这个 payload 是**可写的**：压缩改的就是这里的 messages，tool_calling 填的是
    这里的 tools_schema，prompts 填的是这里的 system_prompt。改完之后内核才把这份内容
    组装成请求发出去 —— 系统提示词在最前，其余按 messages 原样。
    """

    # 本次请求使用的模型。
    #
    # 举例："deepseek-chat"
    model: str

    # 本次要发给模型的完整上下文。压缩的产物就体现在这个列表上。
    #
    # 举例：[Message(role=MessageRole.USER, ...), Message(role=MessageRole.TOOL, ...)]
    messages: list[Message]

    # 本次要暴露给模型的工具 schema 列表。由 tool_calling 在位点 3 填。
    #
    # 举例：[{"name": "read", "description": "...", "parameters": {...}}]
    tools_schema: list[dict[str, JsonValue]] = field(default_factory=list)

    # 这次请求的系统提示词。由 prompts 在位点 3 现拼。
    #
    # **它不在 messages 里，也不进 Session 日志**：内核发请求时才把它放到最前面。
    # 所以压缩（它只改 messages）碰不到它，落盘也碰不到它。
    #
    # 举例："你是 agent-architecture，一个用工具把任务做完的通用智能体。……"
    system_prompt: str = ""

    # 这次请求要算的 token 总数：messages + 工具 schema + 系统提示词。
    #
    # 举例：42815
    context_tokens: int = 0

    # 其中工具 schema 占的那一份。压缩重建了 messages 之后要靠它把总数补回去 ——
    # schema 不是 messages 的一部分，重数 messages 数不到它。
    #
    # 举例：950
    schema_tokens: int = 0

    # 系统提示词占的那一份，由填它的 handler 一并算好。压缩重建后同样要补回去。
    #
    # 举例：798
    system_tokens: int = 0

    # 压缩阈值。context_tokens 达到它就触发压缩。
    #
    # 举例：100000
    compact_tokens: int = 0


@dataclass(slots=True)
class LlmDuringPayload:
    """位点 4（llm:during）的专属数据。每次模型调用触发一次。"""

    # 本次调用的模型。
    #
    # 举例："deepseek-chat"
    model: str

    # 这是本 turn 内第几次尝试，从 0 开始。大于 0 说明前几次失败了。
    #
    # 举例：1
    attempt: int


@dataclass(slots=True)
class LlmAfterPayload:
    """位点 5（llm:after）的专属数据。"""

    # 模型这次输出的定稿消息，马上会被写进 Session 日志。
    #
    # 举例：Message(role=MessageRole.ASSISTANT, content=[TextPart(text="好的")])
    assistant_message: Message

    # 模型请求的工具调用原始列表，会被解析成 pending_tool_calls。
    #
    # 举例：[ToolCallPart(tool_call_id="call_7f3a91", name="read", arguments={"path": "a.py"})]
    tool_calls: list[ToolCallPart] = field(default_factory=list)

    # 本次要并入上下文的中途消息。runtime_state 从 pending_queue 取出后填在这里，
    # session 随后把它们追加成 user 消息。取过队列即为空，不会被写第二遍。
    #
    # 举例：[Message(role=MessageRole.USER, content=[TextPart(text="顺便看下 b.py")])]
    pending_messages: list[Message] = field(default_factory=list)

    # 模型这次为什么停下来。取值见 models.StopReason。
    #
    # 举例：StopReason.TOOL_CALLS
    stop_reason: StopReason = StopReason.END_TURN

    # 本次调用的用量统计；模型没返回时为 None。
    #
    # 举例：Usage(input_tokens=15230, output_tokens=418)
    usage: Usage | None = None


class ToolDecision(StrEnum):
    """位点 6 对单条工具调用的判定结果。"""

    # 放行 —— 检查都过了，可以执行。
    EXECUTE = "execute"

    # 待审批 —— 需要人工放行，审批通过前不产生任何副作用。
    AWAIT_APPROVAL = "await_approval"

    # 拒绝 —— 工具不存在、参数非法，或被策略/审批拒绝，标记为 rejected。
    REJECT = "reject"


@dataclass(slots=True)
class ExecutionPlan:
    """位点 6 得出的执行编排：哪些调用可以并行、哪些必须串行。"""

    # 分组结果。同一组内的 tool_call_id 可以并发执行，组与组之间必须按顺序串行。
    # 无法判定时按串行处理，也就是每组只有一个元素。
    #
    # 举例：[["call_a", "call_b"], ["call_c"]]（a、b 并行，然后才是 c）
    groups: list[list[str]]


@dataclass(slots=True)
class ToolBeforePayload:
    """位点 6（tool:before）的专属数据。整批一次，覆盖本轮所有待执行调用。"""

    # 本轮模型要求执行的整批工具调用。
    # handler 会在这里补齐默认值、校验并改写 arguments 与 status。
    #
    # 举例：[ToolCall(id="call_7f3a91", name="read", arguments={"path": "a.py"},
    #                 status=ToolCallStatus.PENDING)]
    calls: list[ToolCall] = field(default_factory=list)

    # 每条调用的判定结果，键是 tool_call_id。由 handler 写入，内核读取。
    # 缺失的键按 REJECT 处理。
    #
    # 举例：{"call_7f3a91": ToolDecision.EXECUTE}
    decisions: dict[str, ToolDecision] = field(default_factory=dict)

    # 每条调用的判定理由，键是 tool_call_id。**谁判的谁写，内核不读它** ——
    # tool_calling 在收尾（priority 445）拿它拼给模型看的结果消息。
    # 少了它，被拒的调用只能得到一句「被拒绝」，模型只会重复同一个错误。
    #
    # 举例：{"call_7f3a91": "路径在工作目录之外：D:/etc/passwd"}
    reasons: dict[str, str] = field(default_factory=dict)

    # 并行/串行编排；handler 没写就是 None，内核按"全部串行"处理。
    #
    # 举例：ExecutionPlan(groups=[["call_7f3a91"]])
    plan: ExecutionPlan | None = None

    # 每条调用算出的幂等键，键是 tool_call_id。由 tool_calling 在位点 6 写入，
    # 内核在位点 7 原样透传给工具。重试与重放时它不变，保证命中的是同一次副作用。
    #
    # 举例：{"call_7f3a91": "a3f1c9e2b7d4..."}
    idempotency_keys: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class ToolDuringPayload:
    """位点 7（tool:during）的专属数据。逐条执行，一条一次。"""

    # 正在执行的这条工具调用。
    #
    # 举例：ToolCall(id="call_7f3a91", name="read", arguments={"path": "a.py"},
    #                 status=ToolCallStatus.RUNNING)
    call: ToolCall | None = None

    # 这是该次调用的第几次尝试，从 0 开始。大于 0 说明前面失败过。
    #
    # 举例：1
    attempt: int = 0

    # 本次调用的幂等键，由位点 6 算出并随 tool_start 落过盘。
    # 重试时它不变，保证下游去重命中的是同一次副作用。
    #
    # 举例："a3f1c9e2b7d4..."
    idempotency_key: str = ""

    # 本次调用的超时秒数；None 表示用工具声明的默认值。
    #
    # 举例：30.0
    timeout_seconds: float | None = None

    # 工具返回的原始正文，尚未规范化、可能非常长。由 tool_calling 在位点 7 填，
    # **内核负责把它搬到 ToolAfterPayload** —— 位点 8 拿到的 payload 是内核新造的，
    # 结果只能由内核搬（跟位点 4 的收流结果交给位点 5 同一个形状）。
    #
    # 举例："import os\n\ndef main(): ..."
    raw_result: str = ""

    # 执行失败时的领域异常；成功为 None。同样由内核搬到 ToolAfterPayload。
    #
    # 举例：ToolTimeoutError("read 超过 30 秒没返回")
    error: AgentError | None = None


@dataclass(slots=True)
class ToolAfterPayload:
    """位点 8（tool:after）的专属数据。逐条结果回来，一条一次。"""

    # 刚刚执行完的那条工具调用。
    #
    # 举例：ToolCall(id="call_7f3a91", name="read", arguments={"path": "a.py"},
    #                 status=ToolCallStatus.RUNNING)
    call: ToolCall | None = None

    # 工具返回的原始正文，尚未规范化、可能非常长。
    #
    # 举例："import os
    #
    # def main(): ..."
    raw_result: str = ""

    # 规范化之后要写进 Session 的工具结果消息。由 tool_calling 写入。
    # 超长时正文已经落盘，这里只留路径与省略元数据。
    #
    # 举例：Message(role=MessageRole.TOOL, content=[TextPart(text="...")],
    #               metadata={"tool_call_id": "call_7f3a91"})
    normalized: Message | None = None

    # 执行失败时的领域异常；成功为 None。
    #
    # 举例：ToolValidationError("path 必须是字符串")
    error: AgentError | None = None

    # 本次要并入上下文的中途消息。同 LlmAfterPayload，由 runtime_state 从 pending_queue
    # 取出后填在这里，session 随后把它们追加成 user 消息。
    #
    # 举例：[Message(role=MessageRole.USER, content=[TextPart(text="顺便看下 b.py")])]
    pending_messages: list[Message] = field(default_factory=list)


@dataclass(slots=True)
class TurnEndPayload:
    """位点 9（turn:end）的专属数据。"""

    # 刚刚结束的这一轮的序号。
    #
    # 举例：3
    turn_index: int

    # 本轮各工具的成功/失败统计快照，键是工具名。
    #
    # 举例：{"grep": ToolStat(name="grep", succeeded=3, failed=1)}
    tool_stats: dict[str, ToolStat] = field(default_factory=dict)

    # 本轮结束后是否还有活要干（还有未落地的工具调用，或 pending_queue 里有消息）。
    #
    # 举例：True
    has_pending_work: bool = False


@dataclass(slots=True)
class TraceEndPayload:
    """位点 10（trace:end）的专属数据。无论正常还是异常都会走到这里。"""

    # 结束状态。
    #
    # 举例：TraceStatus.COMPLETED
    status: TraceStatus = TraceStatus.COMPLETED

    # 失败原因、被谁中断等；正常结束为 None。
    #
    # 举例："用户中断"
    reason: str | None = None

    # 本次 trace 的总耗时（毫秒），由 started_at 与当前时间相减得出。
    #
    # 举例：8421.5
    duration_ms: float = 0.0


# 十个位点的专属数据联合类型。handler 通过 ctx.site 判断该按哪种结构读。
SitePayload = (
    TraceBeforePayload
    | TurnBeforePayload
    | LlmBeforePayload
    | LlmDuringPayload
    | LlmAfterPayload
    | ToolBeforePayload
    | ToolDuringPayload
    | ToolAfterPayload
    | TurnEndPayload
    | TraceEndPayload
)


# --------------------------------------------------------------------------
# 位点上下文与 handler
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TraceContext:
    """一次 trace 的执行现场。位点 1 创建，位点 10 之后销毁。

    它跨位点复用：十个位点共享同一份 state / session / config / ports / signal，
    每个位点不同的只是"这一次调用携带什么数据"（SiteContext.payload）。
    """

    # 运行态。十个位点里会被反复覆盖改写。
    #
    # 举例：RuntimeState(trace_id="trace_9c8b7a", turn_index=3, ...)
    state: RuntimeState

    # 内存 Session 的读写口：追加条目、读上下文投影、访问草稿槽。
    #
    # 举例：SessionHandle(meta=SessionMeta(session_id="sess_ab12", ...))
    session: SessionHandle

    # 位点 1 冻结的配置快照，整个 trace 只读。之后改全局配置不影响它。
    #
    # 举例：FrozenConfig(model="deepseek-chat", max_turns=50, ...)
    config: FrozenConfig

    # 基础端口集合：clock / ids / logger / events / tokens。
    #
    # 举例：Ports(clock=SystemClock(), logger=StdLogger("INFO"), ...)
    ports: Ports

    # 取消信号。用户中断时被 set，内核与长耗时 handler 在安全点检查它。
    #
    # 举例：CancelToken()
    signal: CancelToken


@dataclass(slots=True)
class SiteContext:
    """位点上下文：handler 的唯一入参。

    它是"视图"，不是长命对象：内核每次调管线时组装一个，handler 用完即弃，
    不允许跨调用保存。这样 handler 永远不会读到过期的 payload。
    """

    # 本次 trace 的执行现场（跨十个位点共享）。
    #
    # 举例：TraceContext(state=..., session=..., config=..., ports=..., signal=...)
    trace: TraceContext

    # 现在是哪个位点在跑。
    #
    # 举例：Site.TOOL_BEFORE
    site: Site

    # 这个位点的专属数据。按 site 取对应类型，且是**原地改**的数据通道。
    #
    # 举例：ToolBeforePayload(calls=[...], decisions={}, plan=None)
    payload: SitePayload

    @property
    def state(self) -> RuntimeState:
        """快写糖：ctx.state 等价于 ctx.trace.state。

        举例：ctx.state.turn_index
        """
        return self.trace.state

    @property
    def session(self) -> SessionHandle:
        """快写糖：ctx.session 等价于 ctx.trace.session。

        举例：ctx.session.append(entry)
        """
        return self.trace.session

    @property
    def config(self) -> FrozenConfig:
        """快写糖：ctx.config 等价于 ctx.trace.config。

        举例：ctx.config.compact_tokens
        """
        return self.trace.config

    @property
    def ports(self) -> Ports:
        """快写糖：ctx.ports 等价于 ctx.trace.ports。

        举例：ctx.ports.logger.info("tool.start", name="read")
        """
        return self.trace.ports

    @property
    def signal(self) -> CancelToken:
        """快写糖：ctx.signal 等价于 ctx.trace.signal。

        举例：if ctx.signal.is_set(): return False
        """
        return self.trace.signal


class Handler(Protocol):
    """位点处理器：某个模块在某个位点上要执行的一段逻辑。

    注意它是"模块 × 位点"的组合，不是"模块"：同一个模块可以在多个位点注册多个
    handler，各自带不同的 priority。
    """

    # handler 的唯一标识，形如"模块名.位点名.序号"，用于日志与排错。
    #
    # 举例："checkpoint.trace_before"、"session.llm_after"
    id: str

    # 这个 handler 挂在哪些位点上。一个 handler 可以挂多个位点。
    #
    # 举例：frozenset({Site.LLM_AFTER, Site.TOOL_AFTER})
    sites: frozenset[Site]

    # 执行优先级，数值越小越先执行。同一 priority 内按注册顺序执行。
    #
    # 分段（PRIORITY_BANDS 是唯一事实来源，装配期强校验，越界抛 ValueError）：
    #     - 50       恢复判据（checkpoint 在位点 1 用）
    #     - 100-499  维度内置逻辑
    #     - 500-899  用户 Hook
    #     - 900      落盘屏障（checkpoint），必须最后跑，否则别人改的数据落不下去
    # 举例：200
    priority: int

    async def run(self, ctx: SiteContext) -> bool | None:
        """执行这段逻辑。

        返回值只表达**控制**，不表达数据——数据一律原地改 ctx.payload。

        返回值的含义：
            - None 或 True   继续跑下一个 handler
            - False          否决：立刻停止这个位点的后续 handler。
                             只有位点 1（trace:before）与位点 6（tool:before）有否决
                             语义——内核只在这两处读返回值并据此跳过下游动作。
                             其余位点返回 False 内核不解释它，那是 handler 的 bug；
                             要中止整次 trace 请改抛 SiteAbort。

        出错怎么办：
            - 想中止整次 trace，抛 models 之外的 SiteAbort
            - 落盘失败，抛 FlushError（内核按"失败即阻止"处理）

        举例：async def run(self, ctx): ctx.payload.decisions[call_id] = EXECUTE
        """
        ...

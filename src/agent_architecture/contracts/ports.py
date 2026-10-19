"""契约层端口定义。

端口 = 一个 Protocol，只描述"我需要什么能力"，不关心谁实现、怎么实现。
九个维度模块之间禁止互相 import：需要别的模块的能力时，从这里取一个端口，
由 composition 层在装配时把实现注入进来。

阶段 0 只把 `Ports` 聚合到"地基必备"的五个端口；`SessionStore`、
`ApprovalProvider`、`ToolRegistry` 等已在这里定好协议，但要等对应模块落地时才
加进 `Ports`——它们的实现类型（ToolExecutionResult、SandboxDecision 等）还没定，
提前塞进来只会制造返工。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from .llm import LlmClient
from .models import (
    ApprovalRequest,
    ApprovalResult,
    Draft,
    Message,
    SessionEntry,
    SessionEntryPayload,
    SessionEntryType,
    SessionMeta,
    Timestamp,
    TokenAnchor,
    ToolDeclaration,
)

# --------------------------------------------------------------------------
# 基础端口：`Ports` 聚合的就是这五个
# --------------------------------------------------------------------------


@runtime_checkable
class Clock(Protocol):
    """时间来源。所有取时间的地方都走它，测试时才能把时间钉死。"""

    def now(self) -> datetime:
        """当前时刻（带时区）。

        举例：datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
        """
        ...

    def monotonic(self) -> float:
        """单调递增的秒数，用来算耗时（不受系统时钟回拨影响）。

        举例：1735689600.123
        """
        ...


@runtime_checkable
class IdGenerator(Protocol):
    """唯一标识生成器。所有 entry_id、trace_id、tool_call_id 都从这里出。"""

    def new_id(self, prefix: str) -> str:
        """生成一个带前缀的唯一标识。

        作用：前缀让人一眼看出这是什么 id；测试里换成顺序实现就能得到可断言的固定值。
        举例：new_id("e") -> "e_000042"；new_id("trace") -> "trace_9c8b7a"
        """
        ...


@runtime_checkable
class Logger(Protocol):
    """结构化日志。字段进日志而不是拼进字符串，方便按 trace_id 检索。"""

    def debug(self, event: str, **fields: object) -> None:
        """调试级：每次位点调度、每条 handler 的进出。

        举例：logger.debug("site.start", site="tool:before", trace_id="trace_9c8b7a")
        """
        ...

    def info(self, event: str, **fields: object) -> None:
        """常规级：trace/turn 的开始结束、工具调用摘要。

        举例：logger.info("trace.start", session_id="sess_ab12", model="deepseek-chat")
        """
        ...

    def warning(self, event: str, **fields: object) -> None:
        """警告级：可自愈的异常，比如重试、兜底压缩被触发。

        举例：logger.warning("compaction.fallback", reason="context_overflow")
        """
        ...

    def error(self, event: str, **fields: object) -> None:
        """错误级：trace failed、落盘失败。

        举例：logger.error("checkpoint.flush_failed", seq=42, error="磁盘写满")
        """
        ...


@runtime_checkable
class EventBus(Protocol):
    """进程内事件通道。它承载"给别人看"的事件（比如 SSE 推送），与位点无关。"""

    def publish(self, event: object) -> None:
        """发布一个事件，同步分发给所有订阅者。

        举例：bus.publish(AssistantDelta(trace_id="trace_9c8b7a", text="我"))
        """
        ...

    def subscribe(self, event_type: type, handler: Callable[[object], None]) -> Callable[[], None]:
        """订阅某类事件，返回一个"退订"函数。

        举例：unsubscribe = bus.subscribe(AssistantDelta, push_to_sse)
        """
        ...


@runtime_checkable
class TokenCounter(Protocol):
    """token 计数。位点 3 算 context_tokens、压缩判定阈值都靠它。"""

    def count_text(self, text: str) -> int:
        """算一段文本的 token 数。

        举例：count_text("你好") -> 2
        """
        ...

    def count_messages(self, messages: Sequence[Message]) -> int:
        """算一串消息的 token 数（含角色标记等固定开销）。

        举例：count_messages([...]) -> 42815
        """
        ...


# --------------------------------------------------------------------------
# 其余端口：协议先定好，等对应模块落地时再聚合进 `Ports`
# --------------------------------------------------------------------------


@runtime_checkable
class SessionStore(Protocol):
    """Session 日志的持久化端口。文件实现与内存实现必须行为一致。

    方法是异步的：生产实现走 PG，而 PG 驱动只有异步 API。
    """

    async def load(
        self, session_id: str, user_id: str
    ) -> tuple[SessionMeta, list[SessionEntry]] | None:
        """装入某个用户的会话。

        会话不存在、**或者不属于这个用户**，都返回 None —— 存储层不做跨用户的读。
        调用方拿到的 None 一律按"没有这个会话"处理，不区分"不存在"与"不是你的"。

        举例：meta, entries = await store.load("sess_ab12", "user_7f3a91")
        """
        ...

    async def save_meta(self, meta: SessionMeta) -> None:
        """写入或覆盖一个会话的元信息。

        作用：新建会话、刷新 updated_at、改 title / status / context_cursor 都要走它。
        meta 不是 append-only 的事实流，是可以被覆盖的当前值，所以单独一个口子。
        屏障位点上它跟着条目一起落，由 Checkpoint 调用。
        失败时抛 FlushError，与 flush 一致。

        举例：store.save_meta(SessionMeta(session_id="sess_ab12", ...))
        """
        ...

    async def append(self, session_id: str, entries: Sequence[SessionEntry]) -> None:
        """把条目追加进缓冲区（此时还没落盘）。

        举例：store.append("sess_ab12", [entry])
        """
        ...

    async def flush(self, session_id: str, up_to_seq: int) -> None:
        """把序号不超过 up_to_seq 的缓冲条目真正落盘。失败时抛 FlushError。

        作用：检查点屏障的实现手段；落盘失败必须阻止下游动作。
        举例：store.flush("sess_ab12", up_to_seq=42)
        """
        ...

    async def list_sessions(self, user_id: str) -> list[SessionMeta]:
        """列出**某个用户的**全部会话元信息，用于会话列表。

        user_id 必填：不存在"列出所有人的会话"这个用法，省得哪天漏传就漏了别人的数据。
        真要管理员视角，再单开一个方法，不要把这个参数变成可选。

        举例：await store.list_sessions("user_7f3a91")
        """
        ...

    async def delete(self, session_id: str, user_id: str) -> bool:
        """把一个会话连同日志一起删掉，返回有没有删到东西。

        作用：**归属判断和 load 同一条规则** —— 对不上就当它不存在，返回 False。
        删不掉（磁盘不让删）时抛 FlushError：删了一半却回成功，界面上会留下点不开的会话。

        举例：await store.delete("sess_ab12", "user_7f3a91") -> True
        """
        ...


@runtime_checkable
class ApprovalProvider(Protocol):
    """人工审批端口：挂起等回执、列还没回执的、交回执。

    **三个动作都在这儿**，因为有两个消费方：沙箱在位点 6 里等回执，api 层列待审批与收回执。
    审批不进 Session 日志，所以"现在有哪些在等"只能问实现本身（§7.6）。
    """

    async def request(self, request: ApprovalRequest) -> ApprovalResult:
        """发出审批请求并等待回执。

        作用：等待期间该次工具调用状态是 awaiting_approval，不产生任何副作用。
        举例：result = await approvals.request(ApprovalRequest(...))
        """
        ...

    async def pending(self, session_id: str) -> Sequence[ApprovalRequest]:
        """这个会话里还没回执的审批，按发出的先后排。

        作用：`approval_required` 事件补不回来，重连时靠它把等待中的审批捞出来。
        举例：await approvals.pending("sess_ab12") -> [ApprovalRequest(...)]
        """
        ...

    async def respond(self, session_id: str, result: ApprovalResult) -> bool:
        """交一份回执，返回有没有人正等着它。

        作用：不存在、已处理、不属于这个会话，三种情况都返回 False（api 层据此回 404）。
        举例：await approvals.respond("sess_ab12", ApprovalResult(...))
        """
        ...


@runtime_checkable
class ToolRegistry(Protocol):
    """工具注册表（只读视角）。写入与注册由 tool_calling 模块负责。"""

    def get(self, name: str) -> ToolDeclaration | None:
        """按名字取工具声明；不存在返回 None（位点 6 的存在性检查用它）。

        举例：registry.get("read") -> ToolDeclaration(name="read", ...)
        """
        ...

    def declarations(self, scope: str | None = None) -> Sequence[ToolDeclaration]:
        """列出对某个作用域可见的全部工具声明（位点 3 加载 schema 用它）。

        举例：registry.declarations(scope="coding")
        """
        ...


@runtime_checkable
class SessionHandle(Protocol):
    """内存 Session 的读写口。它是 SiteContext 的一部分，不是 `Ports` 的一部分。

    磁盘形态的 Session 只有 meta + entries；这里的句柄指向的是内存形态：
    同一个 meta + entries，外加上下文投影、游标、草稿槽。
    """

    @property
    def meta(self) -> SessionMeta:
        """当前会话的元信息。

        举例：handle.meta.session_id -> "sess_ab12"
        """
        ...

    def append(self, entry: SessionEntry) -> None:
        """追加一条**已经造好**的条目到内存日志。

        正常路径是 append_entry —— 它负责分配 seq 与 entry_id；这个方法留给手里已经有
        完整条目的场景（恢复、分叉）。
        注意**追加不等于并入上下文**：新条目要等下一次 messages() 才进上下文。

        举例：handle.append(SessionEntry(entry_id="e_1", trace_id="t", seq=1, ...))
        """
        ...

    def append_entry(
        self,
        *,
        trace_id: str,
        type: SessionEntryType,
        payload: SessionEntryPayload,
        now: Timestamp,
    ) -> SessionEntry:
        """造一条条目并追加到内存日志；seq 与 entry_id 由实现分配。

        作用：位点上的写入方都用它。没有它，每个要写日志的模块都得自己知道 seq 怎么递增、
        entry_id 从哪来 —— 那是会话内部的记账规则，不该外泄。

        举例：entry = handle.append_entry(trace_id="t", type=SessionEntryType.MESSAGE,
                                          payload=msg, now=now)
        """
        ...

    def entries(self) -> list[SessionEntry]:
        """全部条目的快照，按 seq 升序。

        作用：Checkpoint 靠它知道"日志推到第几号了"，以及尾部干不干净。
        给的是新列表，调用方改它不影响日志本身。

        举例：handle.entries() -> [SessionEntry(seq=1, ...), SessionEntry(seq=2, ...)]
        """
        ...

    def messages(self) -> list[Message]:
        """当前上下文投影：从投影起点（最近一次 compaction）起需要进 messages 的那些条目。

        举例：[Message(role=MessageRole.USER, ...), ...]
        """
        ...

    def token_anchor(self) -> TokenAnchor | None:
        """上一次请求的真实用量；还没打过模型就是 None。

        作用：它是位点 3 估算的锚点 —— 只有这次要发的东西是它的严格扩展时才认。

        举例：handle.token_anchor() -> TokenAnchor(input_tokens=42815, ...)
        """
        ...

    def set_token_anchor(self, anchor: TokenAnchor) -> None:
        """记下这一次请求的真实用量，供下一次估算复用。

        作用：每次成功调用都重新锚定，误差因此不会跨轮累积。
        举例：handle.set_token_anchor(TokenAnchor(messages=(...), input_tokens=1, output_tokens=1))
        """
        ...

    def context_entries(self) -> list[SessionEntry]:
        """当前上下文投影里那些**有条目**的消息，按出现顺序。

        和 messages() 是同一套投影规则的两个视图：那个给模型看的 payload，
        这个给"要引用哪几条"用的 entry_id。压缩靠它决定新的 compaction 保留谁。
        摘要消息是合成的、没有条目，所以它不在结果里 —— 它会被下一次压缩一并压掉。

        举例：[SessionEntry(type=MESSAGE, ...), ...]
        """
        ...

    def append_draft(self, text: str, *, trace_id: str, turn_index: int, now: Timestamp) -> None:
        """往草稿槽追加一段模型输出增量；槽是空的就新建一条草稿。

        作用：模型输出只存这一处，不另开第二份缓冲。
        举例：handle.append_draft("我来帮你读", trace_id="trace_x", turn_index=1, now=now)
        """
        ...

    def append_reasoning(
        self, text: str, *, trace_id: str, turn_index: int, now: Timestamp
    ) -> None:
        """往草稿槽追加一段思考增量。**和正文分开记**，它不会被发回给模型。

        作用：推理模型在正文之前吐的那一段，进日志、进界面，但不占下一轮的上下文。
        举例：handle.append_reasoning("先看看", trace_id="trace_x", turn_index=1, now=now)
        """
        ...

    def draft_text(self) -> str:
        """当前草稿槽里的正文；槽是空的就返回空串。

        作用：给"这次尝试有没有产出"这类判断用。**只读**，不动槽里的内容。
        举例：handle.draft_text() -> "我来帮你读"
        """
        ...

    def draft_reasoning(self) -> str:
        """当前草稿槽里的思考；槽是空的就返回空串。**只读**。

        作用：和 draft_text 一起判断"这次尝试有没有产出过东西"——只吐了思考也算。
        举例：handle.draft_reasoning() -> "先看看有没有这个文件"
        """
        ...

    def take_draft(self) -> Draft | None:
        """取出草稿并**清空**槽；槽本来就是空的就返回 None。

        作用：位点 5 定稿时用。取完即清，避免同一段输出被提交第二遍。
        举例：handle.take_draft().content -> "我来帮你读 a.py"
        """
        ...


# --------------------------------------------------------------------------
# 端口集合
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Ports:
    """一次 trace 内可用的基础端口集合。由 composition 层在装配时构造。

    它是 frozen 的：运行中不替换端口，换实现只能靠重新装配。
    """

    # 时间来源。
    #
    # 举例：SystemClock()
    clock: Clock

    # 唯一标识生成器。
    #
    # 举例：UuidGenerator()
    ids: IdGenerator

    # 结构化日志。
    #
    # 举例：StdLogger(level="INFO")
    logger: Logger

    # 进程内事件通道，SSE 推送从它取数。
    #
    # 举例：InProcessEventBus()
    events: EventBus

    # token 计数器。
    #
    # 举例：HeuristicTokenCounter()
    tokens: TokenCounter

    # 模型接入端口。内核在位点 4 直接用它收流——这是内核固有职责，
    # 不交给 handler，因为主循环要拿到收流结果才能继续。
    #
    # 举例：OpenAiCompatibleClient(api_key="...", base_url="...")
    llm: LlmClient

    # 工具注册表（只读视角），沙箱按它查工具声明。
    #
    # 为 None 表示还没有工具来源（tool_calling 尚未落地）：沙箱这时**一条都不判** ——
    # 存在性归 tool_calling，边界不该替它发言。
    #
    # 举例：ToolRegistry()
    tools: ToolRegistry | None = None

    # 人工审批端口，沙箱判定需要放行时由它去要一个回执。
    #
    # 为 None 表示**没有可用应答方**：这时按 fail closed 处理，把"等审批"落成拒绝。
    # 没有应答方时静默放行是最坏的收场。
    #
    # 举例：None
    approvals: ApprovalProvider | None = None

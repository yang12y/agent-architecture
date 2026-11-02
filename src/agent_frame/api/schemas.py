"""对外 DTO：请求与响应的形状。内部一律用 contracts 里的类型。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Literal, cast

from pydantic import BaseModel, Field

from agent_frame.composition import LiveSession
from agent_frame.contracts import (
    ApprovalDecision,
    ApprovalRequest,
    Chart,
    ChartEntryPayload,
    ChartKind,
    ContentPart,
    ImagePart,
    JsonValue,
    Message,
    MessageRole,
    Permission,
    Question,
    QuestionAnswer,
    QuestionRequest,
    SessionEntry,
    SessionEntryType,
    SessionMeta,
    SessionStatus,
    Signal,
    Span,
    SpanKind,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    TraceEndEntryPayload,
    TraceStatus,
)

# --------------------------------------------------------------------------
# 内容块
# --------------------------------------------------------------------------


class TextPartIn(BaseModel):
    """一段文本。"""

    type: Literal["text"] = "text"
    text: str


class ImagePartIn(BaseModel):
    """一张图片，data_base64 与 url 二选一。"""

    type: Literal["image"] = "image"
    media_type: str
    data_base64: str | None = None
    url: str | None = None


# 请求里能出现的内容块。**故意没有 tool_call** —— 客户端不能伪造一次工具调用，
# 那等于绕过位点 6 的整条检查链。类型上就收不进来。
PartIn = Annotated[TextPartIn | ImagePartIn, Field(discriminator="type")]


class TextPartOut(BaseModel):
    """一段文本。"""

    type: Literal["text"] = "text"
    text: str


class ThinkingPartOut(BaseModel):
    """模型的思考过程。**只出现在响应里** —— 请求不收它（客户端不能伪造推理）。"""

    type: Literal["thinking"] = "thinking"
    text: str


class ImagePartOut(BaseModel):
    """一张图片。"""

    type: Literal["image"] = "image"
    media_type: str
    data_base64: str | None = None
    url: str | None = None


class ToolCallPartOut(BaseModel):
    """模型请求的一次工具调用。"""

    type: Literal["tool_call"] = "tool_call"
    tool_call_id: str
    name: str
    arguments: JsonValue


# 响应里可能出现的内容块：比请求多一个 tool_call（模型请求的那些）。
PartOut = Annotated[
    TextPartOut | ThinkingPartOut | ImagePartOut | ToolCallPartOut, Field(discriminator="type")
]


def to_parts(parts: Sequence[TextPartIn | ImagePartIn]) -> list[ContentPart]:
    """请求里的内容块转成契约类型。

    举例：to_parts([TextPartIn(text="在吗")]) -> [TextPart(text="在吗")]
    """
    converted: list[ContentPart] = []
    for part in parts:
        if isinstance(part, TextPartIn):
            converted.append(TextPart(text=part.text))
        else:
            converted.append(
                ImagePart(
                    media_type=part.media_type,
                    data_base64=part.data_base64,
                    url=part.url,
                )
            )
    return converted


def from_parts(parts: Sequence[ContentPart]) -> list[dict]:
    """契约类型转成对外形状。日志里有什么就回什么，不做删减。

    举例：from_parts([TextPart(text="在吗")]) -> [{"type": "text", "text": "在吗"}]
    """
    converted: list[dict] = []
    for part in parts:
        if isinstance(part, TextPart):
            converted.append({"type": "text", "text": part.text})
        elif isinstance(part, ThinkingPart):
            converted.append({"type": "thinking", "text": part.text})
        elif isinstance(part, ImagePart):
            converted.append(
                {
                    "type": "image",
                    "media_type": part.media_type,
                    "data_base64": part.data_base64,
                    "url": part.url,
                }
            )
        elif isinstance(part, ToolCallPart):
            converted.append(
                {
                    "type": "tool_call",
                    "tool_call_id": part.tool_call_id,
                    "name": part.name,
                    "arguments": part.arguments,
                }
            )
    return converted


# --------------------------------------------------------------------------
# 会话
# --------------------------------------------------------------------------


class HealthOut(BaseModel):
    """健康检查。"""

    status: str


class ErrorBody(BaseModel):
    """统一错误体。"""

    code: str
    message: str


class ErrorOut(BaseModel):
    """错误响应。"""

    error: ErrorBody


class CreateSessionIn(BaseModel):
    """新建会话的请求体，可以整个省略。"""

    title: str | None = None


class MessageIn(BaseModel):
    """发消息的请求体。"""

    content: list[PartIn] = Field(min_length=1)
    client_message_id: str | None = None


def session_fields(meta: SessionMeta) -> dict:
    """SessionMeta 里对外可见的那几个字段。

    举例：session_fields(make_session_meta())["session_id"] -> "sess_test"
    """
    return {
        "session_id": meta.session_id,
        "user_id": meta.user_id,
        "created_at": meta.created_at,
        "updated_at": meta.updated_at,
        "title": meta.title,
        # 分叉来源。侧栏靠它在标题下面标一句「分叉自 X」。
        #
        # 举例："sess_20241231_zz99"；不是分叉出来的就是 None
        "parent_session_id": meta.parent_session_id,
        "model": meta.model,
        "permission": meta.permission,
        "status": meta.status,
    }


class SessionOut(BaseModel):
    """会话元信息。"""

    session_id: str
    user_id: str
    created_at: datetime
    updated_at: datetime
    title: str | None

    # 从哪个会话分叉来的；不是分叉出来的就是 None。
    #
    # 举例："sess_20241231_zz99" / None
    parent_session_id: str | None = None

    model: str
    permission: Permission
    status: SessionStatus

    @classmethod
    def from_meta(cls, meta: SessionMeta) -> SessionOut:
        """按 SessionMeta 填。

        举例：SessionOut.from_meta(live.session.meta)
        """
        return cls(**session_fields(meta))


class SessionListOut(BaseModel):
    """会话列表。"""

    sessions: list[SessionOut]


class MessageOut(BaseModel):
    """历史里的一条消息，对应日志里的一条 message 条目。"""

    entry_id: str
    entry_seq: int
    timestamp: datetime
    role: MessageRole
    content: list[PartOut]

    # 工具结果靠它和那次调用配对；其它消息是 None。正文本身没有工具名，
    # 想显示「工具结果 · read」就得回头查 assistant 消息里的同名 id。
    tool_call_id: str | None = None

    @classmethod
    def from_entry(cls, entry: SessionEntry) -> MessageOut:
        """按条目填。传进来的必须真是 message 条目，message_entries 已经筛过。

        举例：MessageOut.from_entry(session.entries()[0])
        """
        message = cast(Message, entry.payload)
        tool_call_id = message.metadata.get("tool_call_id")
        return cls(
            entry_id=entry.entry_id,
            entry_seq=entry.seq,
            timestamp=entry.timestamp,
            role=message.role,
            content=from_parts(message.content),
            tool_call_id=tool_call_id if isinstance(tool_call_id, str) else None,
        )


def message_entries(entries: Sequence[SessionEntry]) -> list[SessionEntry]:
    """历史只取 message 条目 —— 边界标记不是给人看的消息。

    举例：message_entries(session.entries()) -> [SessionEntry(type=MESSAGE, ...)]
    """
    return [
        entry
        for entry in entries
        if entry.type is SessionEntryType.MESSAGE and isinstance(entry.payload, Message)
    ]


# 历史默认一页多少条。客户端首屏看到的就是这一屏，往上滚再要更早的。
#
# 举例：50
DEFAULT_PAGE_SIZE = 50


def page_messages(
    entries: Sequence[SessionEntry],
    *,
    limit: int,
    before_seq: int | None = None,
    after_seq: int | None = None,
) -> tuple[list[SessionEntry], bool]:
    """从一串条目里切一页 message 条目，返回（这一页, 还有没有更多）。

    两个游标方向相反，都按条目号排他：

    - 不给游标：给**最后** limit 条 —— 打开会话先看最新的一屏，has_more 说明还有更早的。
    - before_seq：往前翻，给 seq 小于它的最后 limit 条。
    - after_seq：往后补，给 seq 大于它的前 limit 条 —— 增量刷新只回新增的那几条。

    举例：page_messages(entries, limit=50) -> (最后 50 条, True)
    """
    kept = message_entries(entries)
    if after_seq is not None:
        rest = [entry for entry in kept if entry.seq > after_seq]
        return rest[:limit], len(rest) > limit
    if before_seq is not None:
        rest = [entry for entry in kept if entry.seq < before_seq]
        return rest[-limit:], len(rest) > limit
    return kept[-limit:], len(kept) > limit


class ChartSeriesOut(BaseModel):
    """一条系列：图例名 + 一串数值，与 ChartOut.x 一一对应。"""

    name: str
    values: list[float]


def signal_fields(signal: Signal) -> dict:
    """把一段信号摊成前端要的字段：采样整段原样带走，前端自己解码。

    举例：signal_fields(signal) -> {"encoding": "base64-float32", "data": "…", "peaks": [...]}
    """
    return {
        "encoding": signal.encoding,
        "unit": signal.unit,
        "point_name": signal.point_name,
        "sample_rate": signal.sample_rate,
        "sample_point": signal.sample_point,
        "data": signal.data,
        "axis_step": signal.axis_step,
        "rpm": signal.rpm,
        "peak_time": signal.peak_time,
        "peaks": [
            {"frequency": peak.frequency, "amplitude": peak.amplitude} for peak in signal.peaks
        ],
    }


def chart_fields(chart: Chart) -> dict:
    """把一幅图摊成前端要的字段。**事件帧与历史 DTO 共用这一份形状**，不写两遍。

    signal 恒在场（模型画的图是 None）：前端只看有没有它决定画哪套轴，
    少一个键会让"有没有"变成"键在不在"，两处判断早晚写岔。

    举例：chart_fields(chart) -> {"chart_id": "c_call_1", "kind": "line", "x": [...], ...}
    """
    return {
        "chart_id": chart.chart_id,
        "tool_call_id": chart.tool_call_id,
        "kind": chart.kind.value,
        "title": chart.title,
        "x": list(chart.x),
        "series": [{"name": series.name, "values": list(series.values)} for series in chart.series],
        "x_title": chart.x_title,
        "y_title": chart.y_title,
        "signal": signal_fields(chart.signal) if chart.signal is not None else None,
    }


class SignalPeakOut(BaseModel):
    """一个谱峰。"""

    # 频率（Hz）与幅值。
    #
    # 举例：24.7 / 1.83
    frequency: float
    amplitude: float


class SignalOut(BaseModel):
    """信号图的采样：base64 float32 原样转给前端，解码在前端做。"""

    # 编码方式与单位。
    #
    # 举例："base64-float32" / "mm/s"
    encoding: str
    unit: str

    # 测点、采样率、点数。
    #
    # 举例："驱动端" / 2560.0 / 1024
    point_name: str
    sample_rate: float
    sample_point: int

    # 打包好的采样点（base64），以及相邻两点的间隔（秒或赫兹）。
    #
    # 举例："AAAAAAAAgD8AAABAAABAQA==" / 0.625
    data: str
    axis_step: float = 0.0

    # 转速与采集时刻，没有就是 None。
    #
    # 举例：1480.0 / "2025-06-01 09:20:00"
    rpm: float | None = None
    peak_time: str | None = None

    # 显著谱峰；时域图是空的。
    #
    # 举例：[SignalPeakOut(frequency=24.7, amplitude=1.83)]
    peaks: list[SignalPeakOut] = []


class ChartOut(BaseModel):
    """历史里的一幅图，对应日志里的一条 chart 条目。"""

    # 落进日志的那条条目 —— 与消息同一套：按 id 去重、按 seq 排序。
    #
    # 举例："e_000012" / 12
    entry_id: str
    entry_seq: int
    timestamp: datetime

    # 画的是什么、画给谁看。
    #
    # 举例："c_call_7f3a91" / "line" / "1# 空压机出口压力"
    chart_id: str
    tool_call_id: str
    kind: ChartKind
    title: str

    # 类目轴与系列。
    #
    # 举例：["08:00", "09:00"]
    x: list[str]
    series: list[ChartSeriesOut]

    # 两根轴的标题，没写就是 None。
    #
    # 举例："时刻" / "MPa"
    x_title: str | None = None
    y_title: str | None = None

    # 信号图的采样；模型画的那四种图是 None。
    #
    # 举例：SignalOut(encoding="base64-float32", …)
    signal: SignalOut | None = None

    @classmethod
    def from_entry(cls, entry: SessionEntry) -> ChartOut:
        """按条目填。传进来的必须真是 chart 条目，charts_of_page 已经筛过。

        举例：ChartOut.from_entry(session.entries()[3])
        """
        chart = cast(ChartEntryPayload, entry.payload).chart
        return cls(
            entry_id=entry.entry_id,
            entry_seq=entry.seq,
            timestamp=entry.timestamp,
            **chart_fields(chart),
        )


class TurnOut(BaseModel):
    """导航栏的一行：一条用户消息，和它这一轮的回答。"""

    # 第几行，从 1 开始。
    #
    # 举例：3
    turn: int

    # 这条用户消息的 seq —— 点这一行时跳过去的目标，也是"加载到没到它"的判据。
    #
    # 举例：42
    seq: int

    # 提示词预览：一行、最多 50 字。纯图片的消息为空串。
    #
    # 举例："帮我看看 1# 空压机昨天的出口压力"
    prompt: str

    # 回答预览：最多 3 行、120 字。这一轮还没定稿时为空串。
    #
    # 举例："昨天平均 0.64 MPa，09:20 有一次尖峰……"
    response: str


def charts_of_page(
    entries: Sequence[SessionEntry],
    page: Sequence[SessionEntry],
    *,
    before_seq: int | None = None,
    after_seq: int | None = None,
) -> list[SessionEntry]:
    """切出与这一页消息**同属一个 seq 窗口**的 chart 条目。

    每一幅图都属于"它前面最近的那条消息"所在的那一页，所以三种走法都不会让同一幅图
    出现两次，也不会漏：

    - 首屏（不给游标）：上界开到末尾 —— 图比最后一条消息还新是常态，不这么做它永远送不出去。
    - before_seq 往前翻：上界就是本页最后一条消息，比它新的图归后面的页。
    - after_seq 补尾部：窗口是 (after_seq, 末尾] —— 图不带消息，会先于下一条消息出现。

    举例：charts_of_page(entries, page) -> [SessionEntry(type=CHART, ...)]
    """
    if after_seq is not None:
        return _chart_entries(entries, lower=after_seq, upper=_last_seq(entries))

    messages = message_entries(entries)
    if not messages or not page:
        return []
    earlier = [entry.seq for entry in messages if entry.seq < page[0].seq]
    upper = page[-1].seq if before_seq is not None else _last_seq(entries)
    return _chart_entries(entries, lower=earlier[-1] if earlier else 0, upper=upper)


def _chart_entries(
    entries: Sequence[SessionEntry], *, lower: int, upper: int
) -> list[SessionEntry]:
    """取 seq 落在 (lower, upper] 里的 chart 条目，按 seq 升序（entries 本来就是升序）。

    两道筛子，都是为了"一次调用只留一幅图"：

    - **孤儿图不送**：图写在工具结果消息之前（位点 8 的 405 档），进程死在两者之间会留下
      一幅没有结果的图。那次调用恢复时会重跑，重画出来的图才是对的。
    - **同一个 chart_id 只留最后一条**：chart_id 由调用号推出来，重跑得到同一个号 ——
      按 chart_id 去重，重跑留下的新图自然顶掉挂掉的那条。

    举例：_chart_entries(entries, lower=10, upper=20) -> [SessionEntry(seq=12, ...)]
    """
    answered = _answered_call_ids(entries)
    kept: dict[str, SessionEntry] = {}
    for entry in entries:
        if entry.type is not SessionEntryType.CHART or not lower < entry.seq <= upper:
            continue
        chart = cast(ChartEntryPayload, entry.payload).chart
        if chart.tool_call_id not in answered:
            continue
        kept[chart.chart_id] = entry
    return sorted(kept.values(), key=lambda entry: entry.seq)


def _answered_call_ids(entries: Sequence[SessionEntry]) -> set[str]:
    """已经有工具结果消息的那些调用号 —— 图的"我在"证明。

    举例：_answered_call_ids(entries) -> {"call_1", "call_2"}
    """
    ids: set[str] = set()
    for entry in entries:
        if entry.type is not SessionEntryType.MESSAGE:
            continue
        message = cast(Message, entry.payload)
        if message.role is not MessageRole.TOOL:
            continue
        call_id = message.metadata.get("tool_call_id")
        if call_id:
            ids.add(str(call_id))
    return ids


def _last_seq(entries: Sequence[SessionEntry]) -> int:
    """日志里最大的 seq；空日志返回 0。

    举例：_last_seq(entries) -> 97
    """
    return entries[-1].seq if entries else 0


class SessionStatsOut(BaseModel):
    """一条会话的几个数字。**全部从日志数出来**，没有单独的计数器字段。"""

    # 已经开始的 trace 数（含还没收尾的那次）。
    traces: int

    # 已收尾 trace 的轮数之和 —— 正在跑的那次结束后才算进来。
    turns: int

    # 最后一次有记录的上下文占用。
    context_tokens: int

    # 上下文窗口上限，算百分比用。
    max_tokens: int


class TodoItemOut(BaseModel):
    """待办清单里的一条。**整份清单一起给** —— 前端拿到就整体替换。"""

    content: str
    status: str


class SessionDetailOut(SessionOut):
    """会话详情 + 一页消息 + 统计 + 待办。**前端渲染历史只用它。**"""

    is_running: bool
    messages: list[MessageOut]

    # 与这一页消息同属一个 seq 窗口的图。它们不在 messages 里，前端按 entry_seq 把两者
    # 合成一条时间线 —— 图在对话里的位置就是它在日志里的位置。
    charts: list[ChartOut] = []

    # 整份轮次大纲（最多 200 行）。右侧导航栏靠它，刷新之后才有刻度可点。
    turns: list[TurnOut] = []

    # 当前的待办清单。它不在消息里，所以单独带一份 —— 刷新之后界面才有东西可画。
    todos: list[TodoItemOut] = []

    # 这一页之外还有没有更多。前端翻历史靠它决定还拉不拉，别拿"这一页满了"去猜。
    #
    # 举例：True
    has_more: bool = False

    # 这份详情对应日志的末尾 seq（含不是消息的条目）。
    # 前端拿它跟 SSE 回执里的 latest_entry_seq 比：一样就说明已经跟到末尾，不必重拉。
    #
    # 举例：496
    latest_entry_seq: int = 0

    stats: SessionStatsOut

    @classmethod
    def from_live(
        cls,
        live: LiveSession,
        *,
        stats: SessionStatsOut,
        entries: Sequence[SessionEntry],
        has_more: bool,
        charts: Sequence[SessionEntry] = (),
        turns: Sequence[TurnOut] = (),
        latest_entry_seq: int = 0,
    ) -> SessionDetailOut:
        """按活状态填：元信息 + 是不是在跑 + 这一页消息与图 + 统计 + 轮次大纲。

        举例：SessionDetailOut.from_live(live, stats=..., entries=[...], has_more=True,
                                        charts=[...], turns=[...])
        """
        return cls(
            **session_fields(live.session.meta),
            is_running=live.is_running,
            messages=[MessageOut.from_entry(e) for e in entries],
            charts=[ChartOut.from_entry(e) for e in charts],
            turns=list(turns),
            has_more=has_more,
            latest_entry_seq=latest_entry_seq,
            stats=stats,
            todos=[
                TodoItemOut(content=todo.content, status=todo.status.value)
                for todo in live.session.todos()
            ],
        )


class SpanOut(BaseModel):
    """时间轴上的一格。时间都是**相对 trace 起点**的毫秒。"""

    # 这一格记的是什么：一个位点，还是一次模型调用。
    #
    # 举例：SpanKind.SITE
    kind: SpanKind

    # 位点取值（"llm:before"）或 "llm"。
    #
    # 举例："tool:during"
    name: str

    # 第几轮；trace 级的位点记的是当时的轮数。
    #
    # 举例：2
    turn: int

    # 起点与时长（毫秒）。
    #
    # 举例：120.5 / 3.2
    start_ms: float
    elapsed_ms: float

    # 附加说明：工具名、第几次尝试。没有就是空串。
    #
    # 举例："read"
    detail: str = ""

    @classmethod
    def from_span(cls, span: Span) -> SpanOut:
        """按契约里的 Span 填。

        举例：SpanOut.from_span(Span(kind=SpanKind.SITE, name="llm:before", turn=1,
                                     start_ms=1.0, elapsed_ms=3.2))
        """
        return cls(
            kind=span.kind,
            name=span.name,
            turn=span.turn,
            start_ms=span.start_ms,
            elapsed_ms=span.elapsed_ms,
            detail=span.detail,
        )


class TraceOut(BaseModel):
    """一条 trace 的画像：从哪来、怎么结束、花在哪。"""

    # 这次执行。同一会话里唯一。
    #
    # 举例："trace_9c8b7a"
    trace_id: str

    # 起点（trace_start 的时刻）与收尾（trace_end 的时刻）。
    #
    # 举例：datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
    started_at: datetime
    ended_at: datetime

    # 怎么结束的、为什么。
    #
    # 举例：TraceStatus.COMPLETED / None
    status: TraceStatus
    reason: str | None = None

    # 跑了几轮、收尾时的上下文量、总耗时（毫秒）。
    #
    # 举例：3 / 42815 / 8421.5
    turns: int = 0
    context_tokens: int = 0
    duration_ms: float = 0.0

    # 耗时明细，按发生顺序。瀑布的总长度取 duration_ms 与它的较大者 ——
    # trace:end 那一格不在里面（它记的是收尾本身）。
    #
    # 举例：[SpanOut(kind=SpanKind.LLM, name="llm", turn=1, start_ms=1.0, elapsed_ms=4200.0)]
    spans: list[SpanOut] = []

    @classmethod
    def from_entry(cls, entry: SessionEntry, *, started_at: datetime) -> TraceOut:
        """按 trace_end 条目填；started_at 由调用方从配对的 trace_start 取。

        举例：TraceOut.from_entry(trace_end_entry, started_at=trace_start_entry.timestamp)
        """
        payload = cast(TraceEndEntryPayload, entry.payload)
        return cls(
            trace_id=entry.trace_id,
            started_at=started_at,
            ended_at=entry.timestamp,
            status=payload.status,
            reason=payload.reason,
            turns=payload.turns,
            context_tokens=payload.context_tokens,
            duration_ms=payload.duration_ms,
            spans=[SpanOut.from_span(span) for span in payload.spans],
        )


class TraceListOut(BaseModel):
    """一次给定若干条 trace，最新的在前。"""

    # 举例：[TraceOut(trace_id="trace_9c8b7a", ...)]
    traces: list[TraceOut] = []


class MessageAcceptedOut(BaseModel):
    """消息已受理。**内容从 WS 的事件流拿**，这里只回受理结果。"""

    session_id: str
    accepted: bool
    queued: bool


class InterruptOut(BaseModel):
    """中断请求的结果。"""

    session_id: str
    interrupted: bool


class SessionDeletedOut(BaseModel):
    """会话删掉了。消息记录一起没的，不可恢复。"""

    session_id: str
    deleted: bool


class ApprovalPendingOut(BaseModel):
    """一份等着人工确认的请求。**审批不进日志**，所以它只能从待审批表里来。"""

    request_id: str
    session_id: str
    trace_id: str
    tool_call_id: str
    tool_name: str
    arguments: JsonValue
    reason: str

    @classmethod
    def from_request(cls, request: ApprovalRequest) -> ApprovalPendingOut:
        """按审批请求填。参数是**补齐并校验过的最终参数**，和判定用的是同一份。

        举例：ApprovalPendingOut.from_request(request)
        """
        return cls(
            request_id=request.request_id,
            session_id=request.session_id,
            trace_id=request.trace_id,
            tool_call_id=request.tool_call_id,
            tool_name=request.tool_name,
            arguments=request.arguments,
            reason=request.reason,
        )


class ApprovalListOut(BaseModel):
    """待审批列表。重连之后拉一次。"""

    approvals: list[ApprovalPendingOut]


class ApprovalDecisionIn(BaseModel):
    """回执。**没有"还没想好"这个取值** —— 关掉弹窗也得明确表态。"""

    decision: ApprovalDecision
    comment: str | None = None


class ApprovalAcceptedOut(BaseModel):
    """回执收下了。"""

    request_id: str
    decision: ApprovalDecision
    accepted: bool


class QuestionOptionOut(BaseModel):
    """一个可选项。"""

    label: str
    description: str | None = None


class QuestionOut(BaseModel):
    """一个问题。**options 空数组表示只能自由作答。**"""

    id: str
    header: str | None = None
    question: str
    options: list[QuestionOptionOut]
    multi_select: bool

    @classmethod
    def from_question(cls, question: Question) -> QuestionOut:
        """按契约里那个问题填。

        举例：QuestionOut.from_question(question)
        """
        return cls(
            id=question.id,
            header=question.header,
            question=question.question,
            options=[
                QuestionOptionOut(label=option.label, description=option.description)
                for option in question.options
            ],
            multi_select=question.multi_select,
        )


class QuestionRequestOut(BaseModel):
    """**一次**待回答的提问：一次 ask_user 调用，里面可能有好几个问题。

    提问不进日志，所以它只能从待回答表里来。
    """

    request_id: str
    session_id: str
    trace_id: str
    tool_call_id: str
    tool_name: str
    questions: list[QuestionOut]

    @classmethod
    def from_request(cls, request: QuestionRequest) -> QuestionRequestOut:
        """按契约里那次提问填。**问题原样带过去**，前端不再自己拼文案。

        举例：QuestionRequestOut.from_request(request)
        """
        return cls(
            request_id=request.request_id,
            session_id=request.session_id,
            trace_id=request.trace_id,
            tool_call_id=request.tool_call_id,
            tool_name=request.tool_name,
            questions=[QuestionOut.from_question(question) for question in request.questions],
        )


class PendingQuestionsOut(BaseModel):
    """这个会话里所有 **还没答的提问**（不是所有问题）。

    内层每条是一次 ask_user 的提问，它自己还带着若干问题。列表通常是空的或只有一条 ——
    一个会话同时只跑一条 trace，一条 trace 里一次只执行一条工具调用；写成列表是照
    `GET /approvals` 那个形状，前端两处补拉走同一段代码。重连之后拉一次。
    """

    questions: list[QuestionRequestOut]


class QuestionAnswerIn(BaseModel):
    """一个问题的答案。**selected 里的每一项都必须是它选项里的 label。**"""

    id: str
    selected: list[str] = []
    custom: str | None = None

    def to_answer(self) -> QuestionAnswer:
        """转成契约里的答案。校验留给路由 —— 那里才拿得到它对应的问题。

        举例：QuestionAnswerIn(id="go", selected=["继续"]).to_answer()
        """
        return QuestionAnswer(id=self.id, selected=tuple(self.selected), custom=self.custom)


class QuestionAnswersIn(BaseModel):
    """一次提问的全部答案。**缺一个问题也算对不上** —— 模型会以为用户没答那一条。"""

    answers: list[QuestionAnswerIn]


class QuestionAcceptedOut(BaseModel):
    """回答收下了。"""

    request_id: str
    accepted: bool


# --------------------------------------------------------------------------
# 导出 .docx
# --------------------------------------------------------------------------


class DocxHeadingIn(BaseModel):
    """一个标题。level 1–4，1 是最高一级。"""

    type: Literal["heading"] = "heading"
    level: int = Field(default=2, ge=1, le=4)
    text: str


class DocxParagraphIn(BaseModel):
    """一段正文。"""

    type: Literal["paragraph"] = "paragraph"
    text: str


class DocxListIn(BaseModel):
    """一组列表项。ordered 决定用编号还是圆点。"""

    type: Literal["list"] = "list"
    ordered: bool = False
    items: list[str] = []


class DocxCodeIn(BaseModel):
    """一段等宽文本（代码、命令、日志片段）。"""

    type: Literal["code"] = "code"
    text: str


class DocxTableIn(BaseModel):
    """一张表。headers 可以为空，每行长度也可以不等 —— 短的那几格留白。"""

    type: Literal["table"] = "table"
    headers: list[str] = []
    rows: list[list[str]] = []


class DocxImageIn(BaseModel):
    """一张图，PNG 的 base64。前端先把它自己的 SVG 画成 PNG —— python-docx 不支持 SVG。"""

    type: Literal["image"] = "image"

    # 单张上限 12 MB 的 base64，够放一张 2 倍图，也挡得住离谱的请求。
    #
    # 举例："iVBORw0KGgoAAAANSUhEUg..."
    png_base64: str = Field(max_length=12_000_000)

    # 在文档里放多宽（厘米）。A4 版心 16 厘米。
    #
    # 举例：16.0
    width_cm: float = Field(default=16.0, gt=0, le=40)


DocxBlockIn = Annotated[
    DocxHeadingIn | DocxParagraphIn | DocxListIn | DocxCodeIn | DocxTableIn | DocxImageIn,
    Field(discriminator="type"),
]


class DocxExportIn(BaseModel):
    """导出请求：标题 + 抬头小字 + 块列表。

    内容由客户端给：前端已经把回答渲染过了，后端只负责排成 Word 的样子，
    不读日志、不查会话、不认识模型。
    """

    # 文档标题。空串表示不要标题。
    #
    # 举例："包头铝业产量分析"
    title: str = Field(default="", max_length=200)

    # 标题下面那行小字（导出时间之类）。空串表示不写。
    #
    # 举例："导出自 agent-frame · 2026-10-23"
    subtitle: str = Field(default="", max_length=200)

    # 正文块，按顺序排。上限 2000 块是个防御 —— 一份回答用不到这么多。
    #
    # 举例：[DocxParagraphIn(text="整体呈上升趋势")]
    blocks: list[DocxBlockIn] = Field(default_factory=list, max_length=2000)

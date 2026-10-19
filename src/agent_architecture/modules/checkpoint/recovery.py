"""恢复七步：把耐久日志修成一份完整的会话。

七步与它们在本模块里的落点：

    ① 装 meta            store.load 已经做完（registry 调它）
    ② 装 entries         同上
    ③ 补悬空工具调用结果   rebalance.dangling_calls -> result_message
    ④ 补 trace_end(aborted)  只有"没有 trace_end"那一类才补
    ⑤ 把补出来的条目落盘   走 checkpoint 的屏障，一步到位
    ⑥ 重建投影、游标推末尾  Session 构造时已经全量投影过；追加的条目由 messages() 增量并入
    ⑦ 草稿槽置空          草稿槽从来不落盘，恢复出来的 Session 本来就是空的

⑥⑦ 因此没有代码 —— 它们是"没做多余的事"的自然结果，不是漏了。
③④ 只在 DANGLING 那一类才补 trace_end：末尾已经是 trace_end(aborted) 时，位点 10 早就走过了，
再补一条就多了一次收尾。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from agent_architecture.contracts import (
    SessionEntry,
    SessionEntryPayload,
    SessionEntryType,
    SessionHandle,
    Timestamp,
    ToolRegistry,
    TraceEndEntryPayload,
    TraceStatus,
)

from . import rebalance
from .crash import Tail, TailKind
from .scheduler import Checkpoint


@dataclass(frozen=True, slots=True)
class Repair:
    """一条要补写进日志的条目。entry_id / seq / 时刻由 Session 的日志分配。

    举例：Repair(trace_id="trace_x", type=SessionEntryType.MESSAGE, payload=msg)
    """

    # 这条补出来的条目算在哪次 trace 名下 —— 是**出事的那次**，不是当前这次。
    #
    # 举例："trace_9c8b7a"
    trace_id: str

    # 条目类型。
    #
    # 举例：SessionEntryType.MESSAGE
    type: SessionEntryType

    # 条目内容。
    #
    # 举例：Message(role=MessageRole.TOOL, ...)
    payload: SessionEntryPayload


def plan(
    entries: Sequence[SessionEntry],
    tail: Tail,
    *,
    tools: ToolRegistry | None = None,
) -> list[Repair]:
    """算出要补哪些条目，**不改日志**。

    顺序就是追加顺序：先每条悬空调用的结果，最后一条 trace_end(aborted)。
    结果消息必须排在 trace_end 前面，否则下一次投影会把它们挡在收尾之外。

    举例：plan(entries, classify_tail(entries)) -> [Repair(...), Repair(...)]
    """
    if tail.trace_id is None or not tail.needs_repair:
        return []

    repairs = [
        Repair(
            trace_id=tail.trace_id,
            type=SessionEntryType.MESSAGE,
            payload=rebalance.result_message(call, _declaration(tools, call.name)),
        )
        for call in rebalance.dangling_calls(entries, trace_id=tail.trace_id, tools=tools)
    ]

    if tail.kind is TailKind.DANGLING:
        repairs.append(
            Repair(
                trace_id=tail.trace_id,
                type=SessionEntryType.TRACE_END,
                payload=TraceEndEntryPayload(status=TraceStatus.ABORTED, reason="进程中断"),
            )
        )
    return repairs


async def apply(
    checkpoint: Checkpoint,
    session: SessionHandle,
    tail: Tail,
    *,
    now: Timestamp,
    tools: ToolRegistry | None = None,
) -> int:
    """③④⑤：把配平条目写进内存日志并立刻落盘，返回补了几条。

    落盘走的是 checkpoint 的屏障，所以补出来的内容在**新 trace 写下任何东西之前**就已经在盘上。
    落盘失败抛 FlushError，这次 trace 不开始 —— 带着没修好的日志往下跑比不跑更糟。

    举例：await apply(checkpoint, session, tail, now=clock.now()) -> 2
    """
    repairs = plan(session.entries(), tail, tools=tools)
    if not repairs:
        return 0

    for repair in repairs:
        session.append_entry(
            trace_id=repair.trace_id,
            type=repair.type,
            payload=repair.payload,
            now=now,
        )
    await checkpoint.ensure(session, reason="recovery")
    return len(repairs)


def _declaration(tools: ToolRegistry | None, name: str):
    """查一条工具声明；没有注册表就返回 None（按不可重试处理）。

    举例：_declaration(registry, "read") -> ToolDeclaration(...)
    """
    return None if tools is None else tools.get(name)

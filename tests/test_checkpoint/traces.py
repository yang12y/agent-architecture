"""构造各种尾部形态的日志，给 checkpoint 与 recovery 的测试共用。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from agent_architecture.contracts import (
    BudgetSnapshot,
    CompactionSnapshot,
    Permission,
    ToolCallPart,
    ToolStartEntryPayload,
    TraceEndEntryPayload,
    TraceStartEntryPayload,
    TraceStatus,
    TraceTrigger,
)
from fakes import assistant_message, make_entry, tool_message, user_message

# 出事的 trace。配平出来的条目都该算在它名下。
#
# 举例："trace_a"
TRACE_ID = "trace_a"


def trace_start(seq: int, trace_id: str = TRACE_ID):
    """一条 trace_start。

    举例：trace_start(1) -> SessionEntry(type=TRACE_START, ...)
    """
    return make_entry(
        seq,
        TraceStartEntryPayload(
            trigger=TraceTrigger.USER,
            parent_trace_id=None,
            model="fake-model",
            permission=Permission.EDITABLE,
            budget=BudgetSnapshot(max_turns=None, max_tokens=1000),
            compaction=CompactionSnapshot(
                compact_tokens=800,
                keep_recent=2,
                long_tool_result_max_tokens=100,
                long_tool_result_keep_recent=1,
            ),
        ),
        trace_id=trace_id,
    )


def user(seq: int, text: str = "帮我把 write 换掉", trace_id: str = TRACE_ID):
    """一条用户消息。

    举例：user(2) -> SessionEntry(type=MESSAGE, ...)
    """
    return make_entry(seq, user_message(text), trace_id=trace_id)


def assistant_calls(
    seq: int,
    calls: Sequence[tuple[str, str]],
    *,
    text: str = "我来处理",
    trace_id: str = TRACE_ID,
):
    """一条带若干工具调用的 assistant 消息。calls 是 (tool_call_id, 工具名)。

    举例：assistant_calls(3, [("call_1", "read")])
    """
    message = assistant_message(text)
    message.content.extend(
        ToolCallPart(tool_call_id=call_id, name=name, arguments={}) for call_id, name in calls
    )
    return make_entry(seq, message, trace_id=trace_id)


def dispatched(
    seq: int, call_id: str, name: str, *, idempotency_key: str = "", trace_id: str = TRACE_ID
):
    """一条 tool_start：这次调用已经派发出去，副作用可能已经发生了。

    举例：dispatched(4, "call_1", "read", idempotency_key="k1")
    """
    return make_entry(
        seq,
        ToolStartEntryPayload(tool_call_id=call_id, name=name, idempotency_key=idempotency_key),
        trace_id=trace_id,
    )


def answered(seq: int, call_id: str, *, trace_id: str = TRACE_ID):
    """一条工具结果消息。

    举例：answered(5, "call_1")
    """
    return make_entry(seq, tool_message("文件内容……", tool_call_id=call_id), trace_id=trace_id)


def trace_end(
    seq: int,
    status: TraceStatus = TraceStatus.COMPLETED,
    *,
    reason: str | None = None,
    trace_id: str = TRACE_ID,
):
    """一条 trace_end。

    举例：trace_end(9, TraceStatus.ABORTED, reason="用户中断")
    """
    return make_entry(seq, TraceEndEntryPayload(status=status, reason=reason), trace_id=trace_id)


def broken_log(
    *,
    calls: Sequence[tuple[str, str]] = (("call_1", "read"), ("call_2", "write")),
    did_dispatch: Sequence[str] = (),
    did_answer: Sequence[str] = (),
    idempotency_keys: Mapping[str, str] | None = None,
    end: TraceStatus | None = None,
):
    """造一份"崩在工具阶段"的日志：trace 开着，若干调用没结果。

    did_dispatch / did_answer 决定每条调用留下了什么痕迹，其余的一律欠着。
    end 给 None 就是"没走完位点 10"，给 ABORTED 就是"走完了但留了活"。

    举例：broken_log(did_dispatch=("call_1",), did_answer=("call_1",))
    """
    keys = idempotency_keys or {}
    names = dict(calls)
    entries = [trace_start(1), user(2), assistant_calls(3, calls)]
    seq = 4
    for call_id in did_dispatch:
        entries.append(
            dispatched(seq, call_id, names[call_id], idempotency_key=keys.get(call_id, ""))
        )
        seq += 1
    for call_id in did_answer:
        entries.append(answered(seq, call_id))
        seq += 1
    if end is not None:
        entries.append(trace_end(seq, end))
    return entries

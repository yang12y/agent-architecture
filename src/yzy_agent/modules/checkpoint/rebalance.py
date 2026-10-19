"""语义配平：把崩溃留下的悬空工具调用补成完整的日志。

要补的只有一种东西 —— **工具结果消息**。assistant 消息里的每个 tool_call 都必须有配对的
tool 结果，少一个下一轮请求就不合法（不变量 I10）。崩在工具阶段时，日志里就会缺这一条。

补出来的正文按三情形分岔（判据 = 有没有 tool_start × 副作用等级/是否接受幂等键）：

    没 tool_start                     调用在开始前就被打断，没产生副作用，可以安全重试
    有，且可安全重试                    结果未知，重试会命中同一次副作用（幂等键不变）
    有，且不可安全重试                  结果未知且不能自动重试，先确认外部状态

重试判定**复用工具声明里那一条**，不在这里另立规则。工具声明拿不到时按"不可安全重试"处理 ——
不知道能不能重试，就当不能。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from yzy_agent.contracts import (
    Message,
    MessageRole,
    SessionEntry,
    SessionEntryType,
    TextPart,
    ToolCallPart,
    ToolDeclaration,
    ToolRegistry,
    ToolStartEntryPayload,
)
from yzy_agent.contracts import (
    is_retryable as retryable,
)


@dataclass(frozen=True, slots=True)
class DanglingCall:
    """一条"派发过但没结果"的工具调用。

    举例：DanglingCall(tool_call_id="call_1", name="write", dispatched=True, idempotency_key="")
    """

    # 对应 assistant 消息里的那次调用。
    #
    # 举例："call_7f3a91"
    tool_call_id: str

    # 工具名。
    #
    # 举例："write"
    name: str

    # 有没有留下 tool_start —— 有就说明副作用可能已经发生了。
    #
    # 举例：True
    dispatched: bool

    # tool_start 里记下的幂等键；没派发过就是空串。
    #
    # 举例："a3f1c9e2b7d4..."
    idempotency_key: str


def is_retryable(declaration: ToolDeclaration | None) -> bool:
    """这次调用能不能安全重试。

    规则本身在 `contracts.tools.is_retryable` —— 运行时执行器读的是同一份，
    「此规则与恢复判定同一条」这条不变量才不会靠两份实现维持。

    举例：is_retryable(ToolDeclaration(side_effect=SideEffect.NONE, ...)) -> True
    """
    return retryable(declaration)


def dangling_calls(
    entries: Sequence[SessionEntry], *, trace_id: str, tools: ToolRegistry | None = None
) -> list[DanglingCall]:
    """找出这次 trace 里缺结果的工具调用，按模型给出的顺序返回。

    判定口径是"请求过但没结果"，不是"有 tool_start 但没结果"：被拒的调用、开始前就被打断的
    调用同样必须补上结果消息，否则 assistant 里那个 tool_call 就落单了（I10）。

    举例：dangling_calls(entries, trace_id="trace_x") -> [DanglingCall(...)]
    """
    del tools  # 声明只影响"补出来的正文怎么说"，不影响找出哪些要补。

    # 获取大模型声明的工具调用
    requested = _requested_calls(entries, trace_id)

    # 获取已经开始的工具调用
    started = _dispatched_calls(entries, trace_id)

    # 获取已经完成的工具调用
    answered = _answered_calls(entries, trace_id)

    return [
        DanglingCall(
            tool_call_id=call_id,
            name=name,
            dispatched=call_id in started,
            # 已经开始的工具调用需要记录幂等键，看看能不能继续调用
            idempotency_key=started.get(call_id, ""),
        )
        for call_id, name in requested.items()
        if call_id not in answered
    ]


def result_message(call: DanglingCall, declaration: ToolDeclaration | None) -> Message:
    """把一条悬空调用补成工具结果消息。

    举例：result_message(call, None) -> Message(role=MessageRole.TOOL, ...)
    """
    return Message(
        role=MessageRole.TOOL,
        content=[TextPart(text=_explain(call, declaration))],
        metadata={"tool_call_id": call.tool_call_id},
    )


def _explain(call: DanglingCall, declaration: ToolDeclaration | None) -> str:
    """按三情形写出给模型看的那句话。

    举例：_explain(call, None) -> "工具 write 的结果未知：……不能安全重试……"
    """
    if not call.dispatched:
        return f"工具 {call.name} 在开始执行前就被中断了，没有产生任何副作用，可以安全重试。"
    if is_retryable(declaration):
        # 键是空的时候不能写成"幂等键是 ，"：幂等调用本来就不靠键去重。
        if not call.idempotency_key:
            return (
                f"工具 {call.name} 的结果未知：调用已经派发出去，进程在拿到结果前中断了。"
                "这个操作是幂等的，可以安全重试。"
            )
        return (
            f"工具 {call.name} 的结果未知：调用已经派发出去，进程在拿到结果前中断了。"
            f"本次调用的幂等键是 {call.idempotency_key}，重试会命中同一次副作用。"
        )
    return (
        f"工具 {call.name} 的结果未知：调用已经派发出去，进程在拿到结果前中断，"
        "而且这个操作不能安全重试。请先确认外部状态，再决定要不要重来。"
    )


def tool_message_id(entry: SessionEntry) -> str | None:
    """取一条条目里配对的 tool_call_id；不是工具结果消息就返回 None。

    举例：tool_message_id(entry) -> "call_7f3a91"
    """
    if entry.type is not SessionEntryType.MESSAGE:
        return None
    payload = entry.payload
    if not isinstance(payload, Message) or payload.role is not MessageRole.TOOL:
        return None
    value = payload.metadata.get("tool_call_id")
    return value if isinstance(value, str) else None


def _requested_calls(entries: Sequence[SessionEntry], trace_id: str) -> dict[str, str]:
    """这次 trace 里模型请求过的调用，按出现顺序：tool_call_id -> 工具名。

    举例：_requested_calls(entries, "trace_x") -> {"call_1": "read"}
    """
    requested: dict[str, str] = {}
    for entry in entries:
        if entry.trace_id != trace_id or entry.type is not SessionEntryType.MESSAGE:
            continue
        payload = entry.payload
        if not isinstance(payload, Message) or payload.role is not MessageRole.ASSISTANT:
            continue
        for part in payload.content:
            if isinstance(part, ToolCallPart):
                requested.setdefault(part.tool_call_id, part.name)
    return requested


def _dispatched_calls(entries: Sequence[SessionEntry], trace_id: str) -> dict[str, str]:
    """这次 trace 里留下过 tool_start 的调用：tool_call_id -> 幂等键。

    举例：_dispatched_calls(entries, "trace_x") -> {"call_1": "a3f1..."}
    """
    dispatched: dict[str, str] = {}
    for entry in entries:
        if entry.trace_id != trace_id or entry.type is not SessionEntryType.TOOL_START:
            continue
        payload = entry.payload
        if isinstance(payload, ToolStartEntryPayload):
            dispatched[payload.tool_call_id] = payload.idempotency_key
    return dispatched


def _answered_calls(entries: Sequence[SessionEntry], trace_id: str) -> set[str]:
    """这次 trace 里已经有结果消息的调用。

    举例：_answered_calls(entries, "trace_x") -> {"call_2"}
    """
    answered: set[str] = set()
    for entry in entries:
        if entry.trace_id != trace_id:
            continue
        call_id = tool_message_id(entry)
        if call_id is not None:
            answered.add(call_id)
    return answered

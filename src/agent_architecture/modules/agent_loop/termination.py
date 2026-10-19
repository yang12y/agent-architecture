"""位点 9 之后的终止判定。

判定是**内核固有职责**，不交给 handler：它直接决定循环走不走下一轮。
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_architecture.contracts import RuntimeState, TraceStatus


@dataclass(frozen=True, slots=True)
class Termination:
    """ "这次 trace 该收尾了"的结论。"""

    # 按什么状态收尾。
    #
    # 举例：TraceStatus.COMPLETED
    status: TraceStatus

    # 收尾原因；正常完成时为 None。
    #
    # 举例："达到 max_turns=5"
    reason: str | None


def decide_after_turn(state: RuntimeState, *, requested_tools: bool) -> Termination | None:
    """看这一轮结束后该不该收尾；返回 None 表示还要继续下一轮。

    判定顺序（先到先算）：
        1. 撞到 max_turns —— budget_exceeded
        2. 这一轮没请求工具，且没有排队的中途消息 —— completed
        3. 其余 —— 继续

    **为什么用"这一轮有没有请求工具"，而不是"待执行队列空不空"**：
    工具一执行完就会从队列里摘掉，队列空并不代表事情办完了——模型还没看到工具结果，
    必须再来一轮把结果喂回去。用队列当判据会让"调了工具"的那一轮直接被判成完成。
    中途消息（pending_queue）仍然要看：模型虽然没请求工具，但用户又插了话，就得继续。

    举例：decide_after_turn(state, requested_tools=False) -> Termination(COMPLETED, None)
    """
    if state.max_turns is not None and state.turn_index >= state.max_turns:
        return Termination(TraceStatus.BUDGET_EXCEEDED, f"达到 max_turns={state.max_turns}")
    if not requested_tools and not state.pending_queue:
        return Termination(TraceStatus.COMPLETED, None)
    return None

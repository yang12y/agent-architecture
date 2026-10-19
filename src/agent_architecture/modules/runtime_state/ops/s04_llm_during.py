"""位点 4（llm:during）的 runtime_state 操作：记下这是本 turn 的第几次尝试。

粒度是"每次模型调用触发一次"（进入收流的那一刻），不是每个 token 一次；
流式增量不经过管线，由内核直接写草稿槽并推事件。
"""

from __future__ import annotations

from typing import cast

from agent_architecture.contracts import LlmDuringPayload, SiteContext


def apply(ctx: SiteContext) -> None:
    """把本次尝试的序号写进执行态，从 0 开始。

    举例：apply(ctx) -> state.llm_attempt_number == payload.attempt == 1
    """
    payload = cast(LlmDuringPayload, ctx.payload)
    ctx.state.llm_attempt_number = payload.attempt

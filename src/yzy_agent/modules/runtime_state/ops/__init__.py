"""各位点的 runtime_state 操作，一个位点一个模块。

每个模块只暴露一个 `apply(ctx)`：读入位点上下文，**原地改** `ctx.state`，
以及 payload 里属于它负责的那部分。没有隐藏全局状态，所以可以逐位点单独断言。

**位点 9 没有模块**：`max_turns` 判定是内核固有职责（见 agent_loop/termination.py），
`TurnEndPayload` 的三个字段也都是内核现算现填，没有留给执行态的东西。

**位点 2 的 `turn_index++` 与位点 10 的 `is_running=false` 也不在这里** —— 那是内核固有
职责（不变量 I9）：它们直接决定内核走不走下一轮，不能因为某个 handler 忘了注册就失效。
"""

from . import (
    s01_trace_before,
    s02_turn_before,
    s03_llm_before,
    s04_llm_during,
    s05_llm_after,
    s06_tool_before,
    s07_tool_during,
    s08_tool_after,
    s10_trace_end,
)

__all__ = [
    "s01_trace_before",
    "s02_turn_before",
    "s03_llm_before",
    "s04_llm_during",
    "s05_llm_after",
    "s06_tool_before",
    "s07_tool_during",
    "s08_tool_after",
    "s10_trace_end",
]

"""本轮工具成功/失败的聚合。

统计口径（文档 §5 D9）：**被拒绝（rejected）的调用不计入**——它根本没执行，
既不算成功也不算失败。这个计数只用来发现"某个工具是不是在反复失败"。

只在位点 8 累加，位点 2 清空。
"""

from __future__ import annotations

from agent_architecture.contracts import RuntimeState, ToolStat


def record_success(state: RuntimeState, tool_name: str) -> None:
    """给某个工具记一次成功。

    举例：record_success(state, "grep") -> state.turn_tool_stats["grep"].succeeded == 1
    """
    _bump(state, tool_name, success=True)


def record_failure(state: RuntimeState, tool_name: str) -> None:
    """给某个工具记一次失败。

    举例：record_failure(state, "grep") -> state.turn_tool_stats["grep"].failed == 1
    """
    _bump(state, tool_name, success=False)


def _bump(state: RuntimeState, tool_name: str, *, success: bool) -> None:
    """给某个工具的计数加一；这个工具本 turn 还没出现过就先建一条零计数。

    举例：state.turn_tool_stats == {} 时 _bump(state, "grep", success=True)
          -> {"grep": ToolStat(name="grep", succeeded=1, failed=0)}
    """
    stat = state.turn_tool_stats.get(tool_name)
    if stat is None:
        stat = ToolStat(name=tool_name, succeeded=0, failed=0)
        state.turn_tool_stats[tool_name] = stat
    if success:
        stat.succeeded += 1
    else:
        stat.failed += 1

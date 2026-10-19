"""位点 6 的并行/串行编排：把一批调用切成若干「波」。

**只有相邻的只读调用能并成一组。** 会改内容的工具前后可能有因果关系（改的是同一份东西），
所以分组**绝不重排** —— 模型给的顺序就是执行顺序，这里只把连续的只读段并起来：

    [read, read, write, read] -> [[read, read], [write], [read]]
    [read, read, read, write] -> [[read, read, read], [write]]

    [read, write, read]       -> [[read], [write], [read]]      ← 不跨过 write 合并

判定「能不能并行」看的是 `SideEffect.NONE`（纯读取）—— 那一条的定义就是「不修改内容」，
跟并行安全性是同一件事，不再单开一个字段。真出现「只读但不该并行」的工具
（比如有限流的搜索）再给它加，那时它才是真的多一个维度。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from yzy_agent.contracts import ExecutionPlan, SideEffect, ToolCall, ToolDeclaration

# 查一条工具声明；查不到返回 None（存在性检查会先把它拦下，这里只需要不炸）。
#
# 举例：catalog.get
Lookup = Callable[[str], ToolDeclaration | None]


def is_parallel_safe(declaration: ToolDeclaration | None) -> bool:
    """这条工具能不能跟同批的别的调用同时跑。

    **判不了（查不到声明）按不能处理** —— 判不了就别并。

    举例：is_parallel_safe(read_decl) -> True；is_parallel_safe(write_decl) -> False
    """
    return declaration is not None and declaration.side_effect is SideEffect.NONE


def plan_for(calls: Sequence[ToolCall], lookup: Lookup) -> ExecutionPlan:
    """把一批调用切成若干组：组内可并行，组间必须按顺序。

    一个调用自成一组也是合法的 —— 那就是串行。

    举例：plan_for([read, read, write], lookup).groups -> [["r1", "r2"], ["w"]]
    """
    groups: list[list[str]] = []
    wave: list[str] = []

    for call in calls:
        if is_parallel_safe(lookup(call.name)):
            wave.append(call.id)
            continue
        # 会改内容的调用：前面的只读段到此为止，它自己独占一组。
        if wave:
            groups.append(wave)
            wave = []
        groups.append([call.id])

    if wave:
        groups.append(wave)
    return ExecutionPlan(groups=groups)

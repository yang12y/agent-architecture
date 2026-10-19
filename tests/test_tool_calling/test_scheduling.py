"""并行/串行分组：只有相邻的只读调用能并起来。"""

from __future__ import annotations

from fakes import make_tool_declaration
from yzy_agent.contracts import SideEffect, ToolCall, ToolCallStatus
from yzy_agent.modules.tool_calling import is_parallel_safe, plan_for

# 一张最小的表：read 只读，write 会改。
#
# 举例：_TABLE["read"].side_effect -> SideEffect.NONE
_TABLE = {
    "read": make_tool_declaration("read"),
    "write": make_tool_declaration("write", side_effect=SideEffect.NON_IDEMPOTENT),
}


def _call(call_id: str, name: str) -> ToolCall:
    """造一条调用。

    举例：_call("c1", "read")
    """
    return ToolCall(id=call_id, name=name, arguments={}, status=ToolCallStatus.PENDING)


def _groups(*kinds: str) -> list[list[str]]:
    """按 read / write 的简写造一批调用，返回分组结果。

    举例：_groups("read", "read", "write") -> [["c1", "c2"], ["c3"]]
    """
    calls = [_call(f"c{i + 1}", name) for i, name in enumerate(kinds)]
    return plan_for(calls, _TABLE.get).groups


def test_a_lone_read_is_its_own_group() -> None:
    """一条调用自成一组也对 —— 那就是串行。"""
    assert _groups("read") == [["c1"]]


def test_neighbouring_reads_share_a_group() -> None:
    """相邻的只读调用并起来。"""
    assert _groups("read", "read") == [["c1", "c2"]]


def test_a_write_gets_its_own_group() -> None:
    """会改内容的调用独占一组：它跟谁都不能同时跑。"""
    assert _groups("write", "write") == [["c1"], ["c2"]]


def test_reads_do_not_merge_across_a_write() -> None:
    """**不跨过会改内容的调用合并** —— 合并就等于重排了。"""
    assert _groups("read", "write", "read") == [["c1"], ["c2"], ["c3"]]


def test_a_trailing_run_of_reads_merges() -> None:
    """write 之后那段只读照常并起来。"""
    assert _groups("read", "read", "write", "read", "read") == [
        ["c1", "c2"],
        ["c3"],
        ["c4", "c5"],
    ]


def test_the_order_of_calls_is_never_changed() -> None:
    """分组只影响"谁能同时跑"，不改变执行顺序 —— 拍平了要跟原来一模一样。"""
    kinds = ("read", "write", "read", "read", "write", "read")
    groups = _groups(*kinds)

    assert [call_id for group in groups for call_id in group] == [
        f"c{i + 1}" for i in range(len(kinds))
    ]


def test_an_unknown_tool_is_treated_as_serial() -> None:
    """查不到声明就按串行处理 —— 判不了就别并。"""
    calls = [_call("c1", "nobody"), _call("c2", "nobody")]

    assert plan_for(calls, _TABLE.get).groups == [["c1"], ["c2"]]


def test_is_parallel_safe_reads_the_side_effect() -> None:
    """并行安全性就是「会不会改内容」，不另设字段。"""
    assert is_parallel_safe(_TABLE["read"]) is True
    assert is_parallel_safe(_TABLE["write"]) is False
    assert is_parallel_safe(None) is False


def test_an_empty_batch_plans_nothing() -> None:
    """一条调用都没有时给一个空编排。"""
    assert plan_for([], _TABLE.get).groups == []

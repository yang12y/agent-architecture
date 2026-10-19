"""Permission 三档的比较。"""

from __future__ import annotations

from agent_architecture.contracts import Permission
from agent_architecture.modules.sandbox import PERMISSION_LADDER, at_least, rank


def test_the_ladder_runs_from_read_only_to_full_access() -> None:
    """档位链的次序就是能力的次序。"""
    assert PERMISSION_LADDER == (
        Permission.READ_ONLY,
        Permission.EDITABLE,
        Permission.FULL_ACCESS,
    )


def test_rank_orders_the_three_levels() -> None:
    """比较靠这条链，不靠枚举成员的书写顺序。"""
    assert rank(Permission.READ_ONLY) < rank(Permission.EDITABLE) < rank(Permission.FULL_ACCESS)


def test_at_least_is_inclusive() -> None:
    """刚好够也算够。"""
    assert at_least(Permission.EDITABLE, Permission.EDITABLE) is True
    assert at_least(Permission.FULL_ACCESS, Permission.READ_ONLY) is True
    assert at_least(Permission.EDITABLE, Permission.FULL_ACCESS) is False

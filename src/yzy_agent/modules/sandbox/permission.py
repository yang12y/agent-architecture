"""Permission 三档的比较。

三档能力递增：read_only < editable < full_access。比较只能靠这条链 ——
**枚举成员的书写顺序不该成为安全判定的依据**（有人调整 `Permission` 的定义顺序，
不该悄悄改变谁能写文件）。
"""

from __future__ import annotations

from yzy_agent.contracts import Permission

# 从低到高的档位链。**档位比较的唯一依据。**
#
# 举例：PERMISSION_LADDER[1] -> Permission.EDITABLE
PERMISSION_LADDER: tuple[Permission, ...] = (
    Permission.READ_ONLY,
    Permission.EDITABLE,
    Permission.FULL_ACCESS,
)


def rank(permission: Permission) -> int:
    """查一个档位在链上的位置，数值越大能力越强。

    举例：rank(Permission.EDITABLE) -> 1
    """
    return PERMISSION_LADDER.index(permission)


def at_least(have: Permission, required: Permission) -> bool:
    """手上的档位够不够要求的那一档，前者大于等于后者返回True，否则返回False。

    举例：at_least(Permission.EDITABLE, Permission.READ_ONLY) -> True
    """
    return rank(have) >= rank(required)

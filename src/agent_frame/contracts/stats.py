"""分位数与箱线统计量：图表与统计共用的一套数值口径。

只吃数值序列，不认任何业务字段 —— 谁有一列数，谁就能拿来算。
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def quantile(values: Sequence[float], fraction: float) -> float:
    """分位数：排序后按位置线性插值（与 numpy 的默认口径一致）。

    举例：quantile([1, 2, 3, 4], 0.25) -> 1.75
    """
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * fraction
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return float(ordered[low])
    return float(ordered[low] + (ordered[high] - ordered[low]) * (position - low))


def box_stats(values: Sequence[float]) -> dict[str, float | int]:
    """一组样本 → 箱线统计量。**须线取最小/最大值，不剔异常点。**

    举例：box_stats([1, 2, 3, 4]) -> {"n": 4, "min": 1.0, "p25": 1.75, "p50": 2.5, …}
    """
    return {
        "n": len(values),  # 样本数，举例 17
        "min": float(min(values)),  # 最小值，举例 0.8
        "p25": quantile(values, 0.25),  # 下四分位，举例 1.75
        "p50": quantile(values, 0.5),  # 中位数，举例 2.5
        "p75": quantile(values, 0.75),  # 上四分位，举例 3.25
        "p90": quantile(values, 0.9),  # P90：图上不画，正文里常要引用，举例 5.7
        "max": float(max(values)),  # 最大值，举例 8.0
    }

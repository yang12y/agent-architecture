"""分位数与箱线统计量：图表与统计共用的那套口径。"""

from __future__ import annotations

from agent_frame.contracts import box_stats, quantile


def test_quantiles_are_linearly_interpolated() -> None:
    """与 numpy 默认口径一致：位置线性插值。"""
    assert quantile([1, 2, 3, 4], 0.25) == 1.75
    assert quantile([5], 0.9) == 5.0


def test_box_stats_keeps_the_extremes_as_whiskers() -> None:
    """须线直接到最小/最大值，不剔异常点。"""
    stats = box_stats([1, 2, 3, 4])

    assert (stats["n"], stats["min"], stats["max"]) == (4, 1.0, 4.0)
    assert (stats["p50"], stats["p90"]) == (2.5, 3.7)

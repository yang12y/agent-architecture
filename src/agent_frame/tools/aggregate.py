"""分组计数与比率：统计与指标工具共用的纯函数。

统计类工具只差"按哪些维度分组"，所以分组这件事只写一遍；比率同理。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from agent_frame.contracts import JsonValue

# 分组取值：从一行里取这个桶的名字。
#
# 举例：lambda row: row.get("tDeviceClassificationName") or "未知"
KeyOf = Callable[[dict[str, JsonValue]], JsonValue]


def group_count(
    records: Iterable[dict[str, JsonValue]],
    key_of: KeyOf,
    *,
    order: str = "desc",
    limit: int | None = None,
) -> dict[str, int]:
    """按取值分组计数；桶按数量排序（同量按名字），可截断。

    举例：group_count([{"t": "泵"}, {"t": "泵"}, {"t": "阀"}], lambda r: r["t"])
          -> {"泵": 2, "阀": 1}
    """
    tally: dict[str, int] = {}
    for record in records:
        key = str(key_of(record) or "未知")
        tally[key] = tally.get(key, 0) + 1

    if order == "key":
        # 时间维：按键（时间）升序 —— 趋势要看顺序，不能按数量排。
        items = sorted(tally.items(), key=lambda item: item[0])
    else:
        items = sorted(tally.items(), key=lambda item: (item[1], item[0]), reverse=order == "desc")
    if limit is not None:
        items = items[:limit]
    return dict(items)


def ratio(numerator: int, denominator: int) -> float:
    """比率，保留 4 位；分母为 0 时返回 0.0（调用方负责说明这个 0 的含义）。

    举例：ratio(1, 3) -> 0.3333
    """
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)

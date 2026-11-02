"""下游行 → 模型看到的行：白名单投影。

映射表就是白名单：**没列出的键不下发**。这样后端加字段不会悄悄撑大上下文，
模型看到的字段名也稳定。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from agent_frame.contracts import JsonValue

# 一个输出键怎么取值：下游键名，或一个按行计算的函数。
#
# 举例：{"设备名称": "deviceName", "断线测点数": lambda row: row.get("itemCount") or 0}
Source = str | Callable[[dict[str, JsonValue]], JsonValue]

# 输出键 → 取值来源。
#
# 举例：{"缺陷编号": "defectCode"}
Projection = Mapping[str, Source]


def project(record: dict[str, JsonValue], fields: Projection | None) -> dict[str, JsonValue]:
    """按映射表取一行；fields 为 None 表示原样返回。

    举例：project({"deviceName": "H1172"}, {"设备名称": "deviceName"}) -> {"设备名称": "H1172"}
    """
    if fields is None:
        return dict(record)
    return {
        name: source(record) if callable(source) else record.get(source)
        for name, source in fields.items()
    }


def project_all(
    records: list[dict[str, JsonValue]], fields: Projection | None
) -> list[dict[str, JsonValue]]:
    """按映射表批量投影。

    举例：project_all([{"deviceName": "H1172"}], {"设备名称": "deviceName"})
          -> [{"设备名称": "H1172"}]
    """
    if fields is None:
        return [dict(record) for record in records]
    return [project(record, fields) for record in records]

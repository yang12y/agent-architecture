"""工具声明：一个接口 + 取数策略 + 输出投影。**声明即数据**。

一条声明只回答三件事：打哪个接口、怎么取数、结果怎么出。取数在 paging.py，
报文解析在 envelope.py，字段投影在 records.py；一个业务域的现成写法见 docs/TOOLS.md。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from agent_frame.contracts import JsonValue


class FetchMode(StrEnum):
    """一条工具怎么取数。写在声明里，不再有子类覆写主流程。"""

    # 只取首页：page=1 / limit=20，count 是后端总数。清单类默认。
    #
    # 举例：FetchMode.FIRST_PAGE
    FIRST_PAGE = "first_page"

    # 翻页取全：limit=1000，按 count 算页数并发取，最多 15 页。统计/指标用。
    #
    # 举例：FetchMode.ALL_PAGES
    ALL_PAGES = "all_pages"

    # 不带分页字段，单次请求。接口本身没有分页的用这个。
    #
    # 举例：FetchMode.SINGLE
    SINGLE = "single"

    # 不带分页字段，只取总数。
    #
    # 举例：FetchMode.COUNT_ONLY
    COUNT_ONLY = "count_only"

    # 逐页取到不满一页为止：下游不回 count，没法按总数算页数。
    #
    # 举例：FetchMode.UNTIL_SHORT
    UNTIL_SHORT = "until_short"


@dataclass(frozen=True, slots=True)
class Page:
    """一次取数的结果。**成功与"没取全"走同一个形状**。"""

    # 已取到的明细行，还是下游原始字段（投影之前）。
    #
    # 举例：[{"deviceName": "电解槽H1172", "deviceGuid": "2EBF..."}]
    records: list[dict[str, JsonValue]] = field(default_factory=list)

    # 总数：后端给了就用后端的，没给就用取到的条数。
    #
    # 举例：238
    count: int = 0

    # True 表示没取全（翻页到了上限）。
    #
    # 举例：False
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class Call:
    """一次下游请求的全部信息。取数层只认它，不认 HttpTool。"""

    # 请求方法：POST 或 GET。
    #
    # 举例："POST"
    method: str

    # 完整地址，已经拼过 base_url。
    #
    # 举例："http://host:48026/api/api-universal/device/getBindingDevice"
    url: str

    # 请求头：Accept / tenant / Authorization。
    #
    # 举例：{"Accept": "application/json", "tenant": "BF58..."}
    headers: Mapping[str, str]

    # 这次请求的超时（秒）。
    #
    # 举例：30.0
    timeout: float

    # 翻页时的并发页数（保护下游）。
    #
    # 举例：3
    concurrency: int = 3

    # POST 时是 body，GET 时是 query 串。**不含分页字段**，分页由取数层加。
    #
    # 举例：{"organizationGuid": "745E...", "defectStatus": "-1"}
    payload: dict[str, JsonValue] = field(default_factory=dict)


# 参数 → 下游 payload。
#
# 举例：lambda args: {"organizationGuid": args.get("organizationGuid")}
Body = Callable[[dict[str, JsonValue]], dict[str, JsonValue]]

# 自定义输出：拿到取数结果与原始参数，返回任意 JSON。
#
# 举例：lambda page, args: {"count": page.count, "items": [...]}
Shape = Callable[[Page, dict[str, JsonValue]], JsonValue]


@dataclass(frozen=True, slots=True)
class HttpTool:
    """一条工具声明。**加一个工具 = 加一条这样的声明**。"""

    # 模型看到的工具名，全局唯一。
    #
    # 举例："device_info_list"
    name: str

    # 给模型看的三段：能力 / 适用场景 / 禁止场景。
    #
    # 举例："【能力】查设备台账……【禁止场景】不返回台账之外的信息。"
    description: str

    # 参数 JSON Schema，由 params.py 的小 DSL 生成。
    #
    # 举例：{"type": "object", "properties": {"name": {"type": "string"}}}
    parameters: dict[str, JsonValue]

    # 接口路径，写死的常量。
    #
    # 举例："/api/api-universal/device/getBindingDevice"
    path: str

    # POST（缺省）或 GET。
    #
    # 举例："GET"
    method: str = "POST"

    # 参数 → 下游 payload；不写就把参数原样当 payload。
    #
    # 举例：lambda args: {"deviceGuid": args["deviceGuid"]}
    body: Body | None = None

    # 取数策略。
    #
    # 举例：FetchMode.FIRST_PAGE
    fetch: FetchMode = FetchMode.FIRST_PAGE

    # 默认视图里明细数组的键名。
    #
    # 举例："devices"
    list_key: str = "items"

    # 白名单投影：模型看到的键 → 下游键或取值函数；没列出的键不下发。
    #
    # 举例：{"设备名称": "deviceName"}
    fields: Mapping[str, str | Callable[[dict[str, JsonValue]], JsonValue]] | None = None

    # 自定义输出；不写就出默认视图（count + 明细数组）。
    #
    # 举例：lambda page, args: {"未检定": {...}, "即将到期（30天内）": {...}}
    shape: Shape | None = None

    # 输出契约的一句话说明，进 ToolDeclaration.output_contract。
    #
    # 举例："JSON：count 与 items（设备名称 / guid …）。"
    output: str = ""

    # 参数里缺 organizationGuid 时补配置里的机构。
    # **组织树工具设 False**：空 guid 在那里有独立语义（返回顶层组织）。
    #
    # 举例：organization_children_list.default_organization -> False
    default_organization: bool = True

"""五种取数与上限常量。

一条工具怎么取数由声明上的 FetchMode 决定，**没有子类、没有覆写钩子**：
五个函数各管一种形状，长度都在一屏内，错也只错在一种形状里。
"""

from __future__ import annotations

import asyncio
import math

from agent_frame.contracts import HttpTransport, JsonValue

from .envelope import count_of, rows_of
from .spec import Call, FetchMode, Page

# 首屏条数：清单类只回这么多，总数交给 count。
#
# 举例：238 条设备只回 20 行
FIRST_PAGE_SIZE = 20

# 翻页单页条数：再大就会拖慢下游（也超过了不少接口的上限）。
#
# 举例：page=2&limit=1000
FETCH_PAGE_SIZE = 1000

# 翻页上限：1000 × 15 = 1.5 万行，够覆盖当前所有统计口径；超了标 truncated。
#
# 举例：MAX_FETCH_PAGES -> 15
MAX_FETCH_PAGES = 15

# 一次取数最多回给模型多少行明细；超出的丢掉并标 truncated。
#
# 举例：全量 3000 行的清单，只回前 50 行
MAX_ITEMS = 50


async def fetch(transport: HttpTransport, call: Call, mode: FetchMode) -> Page:
    """按声明上的策略取一次数。

    举例：await fetch(transport, call, FetchMode.FIRST_PAGE) -> Page(records=[...], count=238)
    """
    if mode is FetchMode.ALL_PAGES:
        return await _all_pages(transport, call)
    if mode is FetchMode.UNTIL_SHORT:
        return await _until_short(transport, call)
    if mode is FetchMode.FIRST_PAGE:
        return await _single_request(transport, call, page=1, size=FIRST_PAGE_SIZE, keep=True)
    if mode is FetchMode.SINGLE:
        return await _single_request(transport, call, page=None, size=None, keep=True)
    return await _single_request(transport, call, page=None, size=None, keep=False)


async def _single_request(
    transport: HttpTransport,
    call: Call,
    *,
    page: int | None,
    size: int | None,
    keep: bool,
) -> Page:
    """一次请求的三种模式共用（首页 / 单次 / 只要总数）。

    举例：await _single_request(transport, call, page=1, size=20, keep=True)
    """
    payload = await _request(transport, call, page=page, size=size)
    records = rows_of(payload)
    count = count_of(payload)
    total = count if count is not None else len(records)
    return Page(records=records if keep else [], count=total)


async def _all_pages(transport: HttpTransport, call: Call) -> Page:
    """第 1 页拿总数，其余页并发取回；超过上限就停并标 truncated。

    举例：count=1303 的缺陷记录 -> 2 页，Page(records=1303 行, count=1303, truncated=False)
    """
    first = await _request(transport, call, page=1, size=FETCH_PAGE_SIZE)
    records = rows_of(first)
    count = count_of(first)
    total = count if count is not None else len(records)
    pages = math.ceil(total / FETCH_PAGE_SIZE) if total else 1
    wanted = min(max(pages, 1), MAX_FETCH_PAGES)

    if wanted > 1:
        records.extend(await _gather_pages(transport, call, wanted))
    return Page(records=records, count=total, truncated=wanted < pages)


async def _until_short(transport: HttpTransport, call: Call) -> Page:
    """下游不回 count：逐页取到不满一页为止，最多 MAX_FETCH_PAGES 页。

    没有总数就只能**串行**（不拿到这一页就不知道还要不要下一页），页数与行数一起封顶。

    举例：两整页 + 一小页 -> Page(records=[...2003 行], count=2003, truncated=False)
    """
    records: list[dict[str, JsonValue]] = []
    for page in range(1, MAX_FETCH_PAGES + 1):
        payload = await _request(transport, call, page=page, size=FETCH_PAGE_SIZE)
        batch = rows_of(payload)
        records.extend(batch)
        if len(batch) < FETCH_PAGE_SIZE:
            return Page(records=records, count=len(records))
    return Page(records=records, count=len(records), truncated=True)


async def _gather_pages(transport: HttpTransport, call: Call, pages: int) -> list[dict]:
    """并发取第 2..pages 页，按页序拼回一份列表。

    举例：await _gather_pages(transport, call, 3) -> 第 2、3 页的行
    """
    semaphore = asyncio.Semaphore(max(1, call.concurrency))

    async def one(page: int) -> list[dict[str, JsonValue]]:
        async with semaphore:
            payload = await _request(transport, call, page=page, size=FETCH_PAGE_SIZE)
            return rows_of(payload)

    batches = await asyncio.gather(*(one(page) for page in range(2, pages + 1)))
    return [row for batch in batches for row in batch]


async def _request(
    transport: HttpTransport,
    call: Call,
    *,
    page: int | None,
    size: int | None,
) -> JsonValue:
    """发一次请求；page/size 为 None 时不带分页字段。

    举例：await _request(transport, call, page=1, size=1000)
    """
    payload: dict[str, JsonValue] = dict(call.payload)
    if page is not None and size is not None:
        payload["page"] = page
        payload["limit"] = size

    if call.method == "GET":
        params = {key: str(value) for key, value in payload.items() if value is not None}
        return await transport.request(
            "GET", call.url, params=params, headers=call.headers, timeout=call.timeout
        )
    return await transport.request(
        call.method, call.url, json=payload, headers=call.headers, timeout=call.timeout
    )

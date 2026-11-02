"""取数策略的测试：请求形状、四种模式、翻页上限。"""

from __future__ import annotations

from agent_frame.contracts import JsonValue
from agent_frame.tools.paging import (
    FETCH_PAGE_SIZE,
    FIRST_PAGE_SIZE,
    MAX_FETCH_PAGES,
    fetch,
)
from agent_frame.tools.spec import Call, FetchMode, Page
from fakes.http import FakeTransport


def make_call(**overrides: JsonValue) -> Call:
    """造一次下游请求，只覆盖用得上的字段。

    举例：make_call(method="GET")
    """
    base: dict[str, JsonValue] = {
        "method": "POST",
        "url": "http://x/api/device",
        "headers": {"tenant": "t1"},
        "timeout": 30.0,
        "concurrency": 3,
        "payload": {"organizationGuid": "org1"},
    }
    base.update(overrides)
    return Call(**base)  # type: ignore[arg-type]


async def test_first_page_asks_for_twenty_and_keeps_the_total() -> None:
    """清单类固定 page=1 / limit=20，count 用后端的。"""
    transport = FakeTransport({"code": 200, "count": 238, "data": [{"name": "H1172"}]})

    page = await fetch(transport, make_call(), FetchMode.FIRST_PAGE)

    assert transport.sent[0].json == {"organizationGuid": "org1", "page": 1, "limit": 20}
    assert page == Page(records=[{"name": "H1172"}], count=238)
    assert FIRST_PAGE_SIZE == 20


async def test_single_sends_no_pagination_fields() -> None:
    """接口本身没有分页时，不能硬塞 page/limit。"""
    transport = FakeTransport({"data": [{"name": "A"}]})

    page = await fetch(transport, make_call(), FetchMode.SINGLE)

    assert transport.sent[0].json == {"organizationGuid": "org1"}
    assert page.count == 1


async def test_count_only_keeps_no_records() -> None:
    """只要条数时，明细不往回带。"""
    transport = FakeTransport({"count": 7})

    page = await fetch(transport, make_call(), FetchMode.COUNT_ONLY)

    assert page == Page(records=[], count=7)


async def test_get_sends_query_string_with_strings() -> None:
    """GET 把参数放 query 串，值统一转字符串。"""
    transport = FakeTransport({"data": []})

    await fetch(
        transport, make_call(method="GET", payload={"guid": "g1", "treeType": 0}), FetchMode.SINGLE
    )

    assert transport.sent[0].params == {"guid": "g1", "treeType": "0"}
    assert transport.sent[0].json is None


async def test_all_pages_walks_every_page_from_the_count() -> None:
    """按 count 算出页数并逐页取，行数与 count 对得上。"""

    def responder(sent) -> JsonValue:
        page = sent.json["page"]
        rows = [{"id": (page - 1) * FETCH_PAGE_SIZE + index} for index in range(FETCH_PAGE_SIZE)]
        return {"count": 2 * FETCH_PAGE_SIZE, "data": rows}

    transport = FakeTransport(responder, responder)

    page = await fetch(transport, make_call(concurrency=2), FetchMode.ALL_PAGES)

    assert [sent.json["limit"] for sent in transport.sent] == [FETCH_PAGE_SIZE] * 2
    assert page.count == 2 * FETCH_PAGE_SIZE
    assert len(page.records) == 2 * FETCH_PAGE_SIZE
    assert page.truncated is False


async def test_until_short_walks_pages_until_one_is_not_full() -> None:
    """下游不回 count：取到不满一页就停，总数就是取到的条数。"""

    def responder(sent) -> JsonValue:
        page = sent.json["page"]
        size = FETCH_PAGE_SIZE if page == 1 else 3
        return {"data": [{"id": (page - 1) * FETCH_PAGE_SIZE + index} for index in range(size)]}

    transport = FakeTransport(responder, responder)

    page = await fetch(transport, make_call(), FetchMode.UNTIL_SHORT)

    assert [sent.json["page"] for sent in transport.sent] == [1, 2]
    assert page.count == FETCH_PAGE_SIZE + 3
    assert len(page.records) == page.count
    assert page.truncated is False


async def test_until_short_flags_truncated_at_the_page_ceiling() -> None:
    """每页都满就得上限兜底，并且明确标出"没取全"。"""

    def responder(sent) -> JsonValue:
        return {"data": [{"id": index} for index in range(FETCH_PAGE_SIZE)]}

    transport = FakeTransport(*[responder] * MAX_FETCH_PAGES)

    page = await fetch(transport, make_call(), FetchMode.UNTIL_SHORT)

    assert len(transport.sent) == MAX_FETCH_PAGES
    assert page.truncated is True
    assert page.count == MAX_FETCH_PAGES * FETCH_PAGE_SIZE


async def test_all_pages_stops_at_the_ceiling_and_flags_truncated() -> None:
    """超过翻页上限就停，并明确告诉调用方"没取全"。"""

    def responder(sent) -> JsonValue:
        rows = [{"id": index} for index in range(FETCH_PAGE_SIZE)]
        return {"count": (MAX_FETCH_PAGES + 1) * FETCH_PAGE_SIZE, "data": rows}

    transport = FakeTransport(*[responder] * MAX_FETCH_PAGES)

    page = await fetch(transport, make_call(), FetchMode.ALL_PAGES)

    assert len(transport.sent) == MAX_FETCH_PAGES
    assert page.truncated is True

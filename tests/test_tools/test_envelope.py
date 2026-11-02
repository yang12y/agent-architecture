"""报文容器的测试：三种形状、count 的两处来源、两类失败。"""

from __future__ import annotations

import pytest

from agent_frame.contracts import DownstreamError
from agent_frame.tools.envelope import count_of, rows_of


def test_data_list_is_the_records() -> None:
    """data 直接是列表 —— 最常见的一种。"""
    assert rows_of({"code": 200, "data": [{"a": 1}]}) == [{"a": 1}]


def test_nested_list_under_data_is_found() -> None:
    """data 里再包一层 list。"""
    assert rows_of({"data": {"list": [{"a": 1}], "count": 7}}) == [{"a": 1}]


def test_top_level_list_is_accepted() -> None:
    """整个报文就是列表。"""
    assert rows_of([{"a": 1}]) == [{"a": 1}]


def test_count_only_payload_is_an_empty_list() -> None:
    """只回总数的报文是合法的空明细。"""
    assert rows_of({"count": 12}) == []


def test_business_error_raises_with_message() -> None:
    """既无列表又无 count，就是业务失败，把下游的话带出来。"""
    with pytest.raises(DownstreamError) as raised:
        rows_of({"code": 500, "message": "租户不存在"})
    assert "租户不存在" in raised.value.message


def test_count_reads_top_level_then_nested() -> None:
    """count 先看顶层，再看 data.count。"""
    assert count_of({"count": "238"}) == 238
    assert count_of({"data": {"count": 5}}) == 5
    assert count_of({}) is None


def test_non_object_rows_are_dropped() -> None:
    """标量行对模型没有意义，直接丢掉。"""
    assert rows_of({"data": [{"a": 1}, None, 3, "x"]}) == [{"a": 1}]

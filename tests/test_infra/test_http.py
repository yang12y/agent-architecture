"""HttpTransport 实现的测试：解析、失败收敛、参数透传。"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from agent_frame.contracts import DownstreamError, HttpTransport
from agent_frame.infra.http import HttpxTransport

Handler = Callable[[httpx.Request], httpx.Response]


def make_transport(handler: Handler) -> HttpxTransport:
    """建一条走 MockTransport 的传输，不联网。

    举例：make_transport(lambda r: httpx.Response(200, json={}))
    """
    return HttpxTransport(httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_request_returns_parsed_json() -> None:
    """200 且正文是 JSON 时，原样返回解析结果。"""
    transport = make_transport(lambda request: httpx.Response(200, json={"code": 200, "data": []}))

    assert await transport.request("GET", "http://x/api") == {"code": 200, "data": []}
    # 结构化满足端口，不显式继承。
    assert isinstance(transport, HttpTransport)
    await transport.aclose()


async def test_request_forwards_method_json_params_headers() -> None:
    """方法、body、查询串、请求头都按调用方给的走 —— 传输层不做任何改写。"""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content or b"{}")
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={"ok": True})

    transport = make_transport(handler)
    await transport.request(
        "POST",
        "http://x/api/device",
        json={"page": 1},
        params={"guid": "g1"},
        headers={"Authorization": "Bearer t"},
    )

    assert seen["method"] == "POST"
    assert "guid=g1" in str(seen["url"])
    assert seen["body"] == {"page": 1}
    assert seen["authorization"] == "Bearer t"
    await transport.aclose()


async def test_http_error_uses_business_message() -> None:
    """HTTP >= 400 时优先用报文里的 message，那就是给人看的话。"""
    transport = make_transport(lambda request: httpx.Response(400, json={"message": "租户不存在"}))

    with pytest.raises(DownstreamError) as raised:
        await transport.request("GET", "http://x/api")
    assert raised.value.message == "租户不存在"
    await transport.aclose()


async def test_http_error_without_message_carries_status() -> None:
    """报文里没有可读文案时，至少把状态码与一截正文带出来。"""
    transport = make_transport(lambda request: httpx.Response(500, text="boom"))

    with pytest.raises(DownstreamError) as raised:
        await transport.request("GET", "http://x/api")
    assert "500" in raised.value.message
    assert "boom" in raised.value.message
    await transport.aclose()


async def test_non_json_body_is_downstream_error() -> None:
    """正文不是 JSON 时算失败，并把原文截一段带出来。"""
    transport = make_transport(lambda request: httpx.Response(200, text="<html>502</html>"))

    with pytest.raises(DownstreamError) as raised:
        await transport.request("GET", "http://x/api")
    assert "非 JSON" in raised.value.message
    await transport.aclose()


async def test_empty_body_returns_none() -> None:
    """空正文当 null —— 它不是一个解析失败。"""
    transport = make_transport(lambda request: httpx.Response(204))

    assert await transport.request("POST", "http://x/api", json={}) is None
    await transport.aclose()


async def test_connection_error_is_downstream_error() -> None:
    """连不上也是下游失败，不会掀翻整次 trace。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("拒绝连接", request=request)

    transport = make_transport(handler)

    with pytest.raises(DownstreamError) as raised:
        await transport.request("GET", "http://x/api")
    assert "接口请求失败" in raised.value.message
    await transport.aclose()

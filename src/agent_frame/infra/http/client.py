"""HttpTransport 的实现：连接复用 + 超时 + 状态码 + JSON 解析。

**只做传输。** 业务报文的容器形状（data / list / count）与翻页在 `tools/` 层，
这里连"这条报文算不算成功"都不判 —— 只有 HTTP ≥ 400 与非 JSON 才算失败。
"""

from __future__ import annotations

import json
from collections.abc import Mapping

import httpx

from agent_frame.contracts import DownstreamError, JsonValue

# 一次业务请求的默认超时（秒）。工具可以按调用传更长的。
#
# 举例：DEFAULT_TIMEOUT_SECONDS -> 30.0
DEFAULT_TIMEOUT_SECONDS = 30.0


class HttpxTransport:
    """`HttpTransport` 端口的一条实现：所有工具共用同一个连接池。

    举例：transport = HttpxTransport(); await transport.request("GET", url)
    """

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        """记下可选的连接池；**默认到第一次请求才建**。

        构造 AsyncClient 会顺带建 SSL 上下文（几十毫秒），而装配阶段建容器的地方很多
        （每个测试一次），没必要为"可能用不到"的工具付这笔钱。传 client 是为了测试塞
        MockTransport。

        举例：HttpxTransport(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        """
        # 复用一条 AsyncClient：一个进程一条连接池，不按请求新建。
        #
        # 举例：httpx.AsyncClient()
        self._client = client

    async def request(
        self,
        method: str,
        url: str,
        *,
        json: JsonValue = None,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> JsonValue:
        """发一次请求；成功返回解析后的 JSON，失败抛 DownstreamError。

        举例：await request("POST", url, json={"page": 1}, headers=...) -> {"code": 200, "data": []}
        出错：连不上、超时、HTTP ≥ 400、返回的不是 JSON 时抛 DownstreamError。
        """
        try:
            response = await self._client_or_create().request(
                method,
                url,
                json=json,
                params=params,
                headers=headers,
                timeout=timeout if timeout is not None else DEFAULT_TIMEOUT_SECONDS,
            )
        except httpx.TimeoutException as exc:
            raise DownstreamError(f"接口超时：{method} {url}（{exc}）") from exc
        except httpx.HTTPError as exc:
            raise DownstreamError(f"接口请求失败：{method} {url}（{exc}）") from exc

        text = response.text
        if response.status_code >= 400:
            raise DownstreamError(_failure_message(text, response.status_code))
        return _parse(text)

    async def aclose(self) -> None:
        """关掉连接池。进程收尾时调一次；**没建过就什么都不做**。

        举例：await transport.aclose()
        """
        if self._client is not None:
            await self._client.aclose()

    def _client_or_create(self) -> httpx.AsyncClient:
        """第一次请求时才建连接池。

        举例：self._client_or_create().request(...)
        """
        if self._client is None:
            self._client = httpx.AsyncClient()
        return self._client


def build_http_transport() -> HttpxTransport:
    """建一条进程级传输。装配根用它。

    举例：build_http_transport() -> HttpxTransport()
    """
    return HttpxTransport()


def _parse(text: str) -> JsonValue:
    """把响应正文解析成 JSON；空正文当 null，解析不了就是失败。

    举例：_parse('{"code": 200}') -> {"code": 200}
    出错：正文不是 JSON 时抛 DownstreamError。
    """
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise DownstreamError(f"接口返回非 JSON 报文：{text[:200]}") from exc


def _failure_message(text: str, status_code: int) -> str:
    """从失败响应里取一句给人看的话：优先业务报文里的 message / msg。

    举例：_failure_message('{"message": "租户不存在"}', 400) -> "租户不存在"
    """
    try:
        payload = json.loads(text) if text.strip() else None
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        for key in ("message", "msg"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    head = text.strip()[:200]
    return f"接口返回 HTTP {status_code}：{head}" if head else f"接口返回 HTTP {status_code}"

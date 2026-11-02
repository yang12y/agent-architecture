"""假 HttpTransport：按顺序回放报文，并记下每一次请求。

它的存在让取数、信封、投影这几层的测试完全不联网 —— 也就不需要真凭证。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from agent_frame.contracts import JsonValue


class Sent:
    """记录一次请求长什么样。"""

    def __init__(
        self,
        *,
        method: str,
        url: str,
        json: JsonValue,
        params: Mapping[str, str] | None,
        headers: Mapping[str, str] | None,
        timeout: float | None,
    ) -> None:
        # 举例："POST"
        self.method = method

        # 举例："http://x/api/device"
        self.url = url

        # 举例：{"page": 1, "limit": 20}
        self.json = json

        # 举例：{"guid": "g1"}
        self.params = params

        # 举例：{"tenant": "t1"}
        self.headers = dict(headers or {})

        # 举例：30.0
        self.timeout = timeout


Responder = JsonValue | Callable[[Sent], JsonValue]


class FakeTransport:
    """按构造时给的顺序回放报文；给完就抛错，免得测试悄悄少发一次请求。"""

    def __init__(self, *responses: Responder) -> None:
        # 还没回放的报文。
        #
        # 举例：FakeTransport({"count": 0, "data": []})
        self._responses = list(responses)

        # 已经发出去的请求，按顺序。
        #
        # 举例：transport.sent[0].json -> {"page": 1, "limit": 20}
        self.sent: list[Sent] = []
        self.closed = False

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
        """记一条请求，回放下一条报文。"""
        sent = Sent(
            method=method, url=url, json=json, params=params, headers=headers, timeout=timeout
        )
        self.sent.append(sent)
        if not self._responses:
            raise AssertionError(f"没有多余的报文可回放，第 {len(self.sent)} 次请求：{url}")
        response = self._responses.pop(0)
        return response(sent) if callable(response) else response

    async def aclose(self) -> None:
        """记下被关过 —— 只有进程收尾会调它。"""
        self.closed = True

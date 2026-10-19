"""SSE 端点的测试。

自己驱动 ASGI 去读流 —— `TestClient` 会把整个响应缓冲下来，一条不结束的流它读不完。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode

import httpx
import pytest
from fastapi import FastAPI

from fakes import USER, ScriptedLlm, make_app_config, text_turn
from yzy_agent.api import create_app
from yzy_agent.api.sse import endpoint as sse_endpoint
from yzy_agent.api.sse import hub as hub_module
from yzy_agent.api.sse.frames import event_frame, to_text
from yzy_agent.api.sse.hub import Connection, Hub
from yzy_agent.composition import build_container
from yzy_agent.contracts import AssistantThought, TurnStart
from yzy_agent.infra.observability import InProcessEventBus


def test_an_event_frame_carries_its_session() -> None:
    """思考走独立的事件名，帧里带着会话 —— 一条流可能带多个会话，客户端靠它分派。"""
    frame = event_frame(
        session_id="sess_x",
        event=AssistantThought(session_id="sess_x", trace_id="t", turn_index=1, text="先想想"),
    )

    assert frame == {
        "type": "event",
        "session_id": "sess_x",
        "name": "assistant_thinking",
        "data": {"trace_id": "t", "turn_index": 1, "text": "先想想"},
    }


def test_a_frame_goes_out_as_one_data_line() -> None:
    """出去是 SSE 的一条消息：`data: {...}` 加一个空行。"""
    text = to_text({"type": "ready", "client_id": "c_1"})

    assert text == 'data: {"type": "ready", "client_id": "c_1"}\n\n'


def test_a_stream_only_gets_the_sessions_it_watches(recording_logger) -> None:
    """别的会话的事件不该进这条队列。"""
    bus = InProcessEventBus()
    hub = Hub(events=bus, logger=recording_logger)
    connection = Connection()
    hub.subscribe(connection, session_id="sess_a")

    bus.publish(TurnStart(session_id="sess_b", trace_id="t", turn_index=1))
    bus.publish(TurnStart(session_id="sess_a", trace_id="t", turn_index=1))

    assert connection.outbox.qsize() == 1
    assert connection.outbox.get_nowait()["session_id"] == "sess_a"


def test_a_full_queue_stops_accepting(monkeypatch: pytest.MonkeyPatch) -> None:
    """积压满了就不再收，由生成器负责收尾。"""
    monkeypatch.setattr(hub_module, "OUTBOX_LIMIT", 2)
    connection = Connection()
    connection.sessions.add("sess_a")

    for _ in range(3):
        connection.offer("sess_a", TurnStart(session_id="sess_a", trace_id="t", turn_index=1))

    assert connection.overflowed is True
    assert connection.outbox.qsize() == 2


@pytest.fixture
def app(scratch_dir: Path) -> FastAPI:
    """一个直连 ASGI 的应用，模型脚本化成"说一句就收工"。

    举例：app = create_app(build_container(config, llm=ScriptedLlm([text_turn("好的")])))
    """
    config = make_app_config(session_dir=str(scratch_dir / "sessions"))
    container = build_container(config, llm=ScriptedLlm([text_turn("好的")] * 3))
    return create_app(container)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """把 HTTP 请求直接打进 ASGI 应用，和 test_http 一样不占端口。

    举例：response = await client.get("/sessions", params={"user_id": USER})
    """
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as opened:
        yield opened


class _Stream:
    """一条 SSE 流的读端：应用跑在旁边的任务里，这里只拆它写出来的文本。

    举例：stream = _Stream(app, scope); stream.start(); await stream.next()
    """

    def __init__(self, app: FastAPI, scope: dict) -> None:
        # 要跑的 ASGI 应用。
        self._app = app

        # 这条请求的 scope。
        self._scope = scope

        # 应用发出来的 ASGI 消息。
        self._sent: asyncio.Queue[dict] = asyncio.Queue()

        # 应用要等的入站消息。SSE 没有请求体，它只会等我们给的 http.disconnect。
        self._received: asyncio.Queue[dict] = asyncio.Queue()

        # 断开之后没有回头路。
        self._disconnected = False

        # 拆好的帧；流结束之后排一个 None 表示没有了。
        self._frames: asyncio.Queue[dict | None] = asyncio.Queue()

        # 跑应用的那个任务。
        self._task: asyncio.Task[None] | None = None

        # 还没拆完的一段文本。
        self._buffer = ""

        # 响应状态与头，收到 http.response.start 之后才有。
        self.status_code = 0
        self.headers: dict[str, str] = {}

        # 收到过几条心跳。
        self.pings = 0

    def start(self) -> None:
        """把应用跑起来。

        举例：stream.start()
        """
        self._task = asyncio.create_task(self._run())

    async def next(self) -> dict | None:
        """读下一帧；流已经结束就返回 None。

        举例：frame = await stream.next()
        """
        return await self._frames.get()

    async def aclose(self) -> None:
        """按客户端断开处理：递一条 http.disconnect 过去，等应用收尾。

        举例：await stream.aclose()
        """
        self._disconnected = True
        self._received.put_nowait({"type": "http.disconnect"})
        if self._task is not None:
            await self._task

    async def _receive(self) -> dict:
        """应用来问"客户端还在吗"。断开之后再问，答案永远是断开 —— 和 uvicorn 一样。"""
        if self._disconnected:
            return {"type": "http.disconnect"}
        return await self._received.get()

    async def _run(self) -> None:
        """跑应用，把它写的每一段拆成帧。"""
        running = asyncio.create_task(self._app(self._scope, self._receive, self._sent.put))
        # 应用一结束就排个记号：断开时它不会补发收尾段，光等收尾段会一直等下去。
        running.add_done_callback(lambda _: self._sent.put_nowait({"type": "app.done"}))

        while True:
            message = await self._sent.get()
            if message["type"] == "app.done":
                break
            if message["type"] == "http.response.start":
                self.status_code = int(message["status"])
                self.headers = {key.decode(): value.decode() for key, value in message["headers"]}
                continue
            if message["type"] != "http.response.body":
                continue
            self._buffer += message.get("body", b"").decode("utf-8")
            self._split()

        self._frames.put_nowait(None)
        await asyncio.gather(running, return_exceptions=True)

    def _split(self) -> None:
        """从缓冲区里剥出完整的消息块。"""
        while "\n\n" in self._buffer:
            block, _, self._buffer = self._buffer.partition("\n\n")
            for line in block.splitlines():
                if line.startswith(":"):
                    self.pings += 1
                elif line.startswith("data: "):
                    self._frames.put_nowait(json.loads(line[len("data: ") :]))


@asynccontextmanager
async def _open_stream(
    app: FastAPI, *, user_id: str = USER, session_id: str | None = None, spec_version: str = "2.3"
) -> AsyncIterator[_Stream]:
    """开一条流，出作用域时自动断开。

    举例：async with _open_stream(app, session_id=sid) as stream: ...
    """
    params = {"user_id": user_id}
    if session_id is not None:
        params["session_id"] = session_id
    stream = _Stream(app, _scope("/events", params, spec_version=spec_version))
    stream.start()
    try:
        yield stream
    finally:
        await stream.aclose()


def _scope(path: str, params: dict[str, str], *, spec_version: str = "2.3") -> dict:
    """一条 GET 请求的 ASGI scope。

    spec_version 默认 2.3：低于 2.4 时 Starlette 自己听 http.disconnect。uvicorn 报的是
    2.4 —— 那时写失败不再报错，断开只能由生成器自己问出来。

    举例：_scope("/events", {"user_id": "u1"})
    """
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": spec_version},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": urlencode(params).encode(),
        "root_path": "",
        "headers": [(b"host", b"test")],
        "client": ("test", 1),
        "server": ("test", 80),
    }


async def _new_session(client: httpx.AsyncClient) -> str:
    """建一个会话，返回它的 id。

    举例：session_id = await _new_session(client)
    """
    response = await client.post("/sessions", params={"user_id": USER})
    assert response.status_code == 201
    return response.json()["session_id"]


async def _send(client: httpx.AsyncClient, session_id: str, text: str = "在吗") -> None:
    """发一条用户消息，把那次 trace 跑起来。

    举例：await _send(client, session_id)
    """
    response = await client.post(
        f"/sessions/{session_id}/messages",
        params={"user_id": USER},
        json={"content": [{"type": "text", "text": text}]},
    )
    assert response.status_code == 202


async def _wait_idle(client: httpx.AsyncClient, session_id: str) -> None:
    """等这个会话跑完 —— 轮询 HTTP，不碰流，免得把要断言的事件读掉。

    举例：await _wait_idle(client, session_id)
    """
    for _ in range(200):
        body = (await client.get(f"/sessions/{session_id}", params={"user_id": USER})).json()
        if not body["is_running"]:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("trace 一直没跑完")


async def _handshake(stream: _Stream) -> dict:
    """读掉 ready 与 subscribed，返回 subscribed 那一帧。

    举例：frame = await _handshake(stream)
    """
    assert (await stream.next())["type"] == "ready"
    subscribed = await stream.next()
    assert subscribed["type"] == "subscribed"
    return subscribed


async def test_ready_is_the_first_frame(app: FastAPI, client: httpx.AsyncClient) -> None:
    """连上就先给 ready —— 在它到达之前客户端不算连上。"""
    session_id = await _new_session(client)

    async with _open_stream(app, session_id=session_id) as stream:
        frame = await stream.next()
        content_type = stream.headers["content-type"]

    assert frame["type"] == "ready"
    assert isinstance(frame["client_id"], str)
    assert frame["heartbeat_ms"] == 2000
    assert frame["server_time"]
    assert content_type.startswith("text/event-stream")


async def test_the_stream_reports_the_session_state(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    """回执带 is_running / draft_text / 最新条目号。"""
    session_id = await _new_session(client)

    async with _open_stream(app, session_id=session_id) as stream:
        frame = await _handshake(stream)

    assert frame == {
        "type": "subscribed",
        "session_id": session_id,
        "is_running": False,
        "draft_text": "",
        "latest_entry_seq": 0,
    }


async def test_an_overflowing_stream_says_why_it_ends(
    app: FastAPI, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """客户端跟不上就收尾，并说一句为什么 —— 它重连之后会全量重拉。"""
    monkeypatch.setattr(hub_module, "OUTBOX_LIMIT", 1)
    session_id = await _new_session(client)
    bus = app.state.container.ports.events

    async with _open_stream(app, session_id=session_id) as stream:
        await _handshake(stream)
        for _ in range(3):
            bus.publish(TurnStart(session_id=session_id, trace_id="t", turn_index=1))
        first = await stream.next()
        last = await stream.next()
        ended = await stream.next()

    assert first["name"] == "turn_start"
    assert (last["type"], last["code"]) == ("error", "stream_overflowed")
    assert ended is None


async def test_a_trace_streams_its_events_in_order(app: FastAPI, client: httpx.AsyncClient) -> None:
    """发消息之后，事件按 §7.5 的顺序从流上下来，每条都带着会话。"""
    session_id = await _new_session(client)

    async with _open_stream(app, session_id=session_id) as stream:
        await _handshake(stream)
        await _send(client, session_id)
        frames = [await stream.next() for _ in range(5)]

    assert [frame["name"] for frame in frames] == [
        "trace_start",
        "turn_start",
        "assistant_delta",
        "assistant_message",
        "trace_end",
    ]
    assert all(frame["session_id"] == session_id for frame in frames)
    assert frames[0]["data"]["user_message"]["content"] == [{"type": "text", "text": "在吗"}]
    assert frames[2]["data"]["text"] == "好的"
    assert frames[4]["data"]["status"] == "completed"


async def test_two_streams_on_the_same_session_both_receive(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    """多表面共享：两条流看同一个会话，都收得到同样的事件。"""
    session_id = await _new_session(client)

    async with (
        _open_stream(app, session_id=session_id) as first,
        _open_stream(app, session_id=session_id) as second,
    ):
        await _handshake(first)
        await _handshake(second)
        await _send(client, session_id)
        names = [(await first.next())["name"] for _ in range(5)]
        other = [(await second.next())["name"] for _ in range(5)]

    assert names == other


async def test_closing_the_stream_drops_the_listener(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    """断开之后不再占着这个会话 —— 否则每次重连都漏一个听众。"""
    session_id = await _new_session(client)

    async with _open_stream(app, session_id=session_id) as stream:
        await _handshake(stream)
        assert app.state.hub.listener_count(session_id) == 1

    assert app.state.hub.listener_count(session_id) == 0


async def test_an_idle_stream_writes_a_ping(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    """空闲时也得动一下：客户端还在不在，只有真写一次才知道。"""
    monkeypatch.setattr(sse_endpoint, "HEARTBEAT_SECONDS", 0.01)

    async with _open_stream(app) as stream:
        assert (await stream.next())["type"] == "ready"
        await asyncio.sleep(0.05)

        assert stream.pings > 0


async def test_another_users_session_is_a_404(client: httpx.AsyncClient) -> None:
    """不是自己的会话，握手就 404 —— 不会开出一条空流。"""
    session_id = await _new_session(client)

    response = await client.get(
        "/events", params={"user_id": "user_别的", "session_id": session_id}
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"


async def test_a_stream_without_a_user_id_is_rejected(client: httpx.AsyncClient) -> None:
    """没有 user_id 就没法判会话归属。"""
    response = await client.get("/events")

    assert response.status_code == 422


async def test_a_disconnect_under_uvicorn_drops_the_listener(
    app: FastAPI, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ASGI 2.4 之后写失败不再报错：断开要自己问出来，否则听众一直挂着。"""
    monkeypatch.setattr(sse_endpoint, "HEARTBEAT_SECONDS", 0.01)
    session_id = await _new_session(client)

    async with _open_stream(app, session_id=session_id, spec_version="2.4") as stream:
        await _handshake(stream)
        assert app.state.hub.listener_count(session_id) == 1

    assert app.state.hub.listener_count(session_id) == 0

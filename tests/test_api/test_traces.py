"""轨迹端点的测试。

走真链路：真 Container、真 Agent，只把模型换成假件 —— 时间轴是内核在收尾时记的，
所以要真的跑一次 trace 才看得见。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from agent_frame.api import create_app
from agent_frame.composition import Container, build_container
from fakes import USER, BlockingLlm, ScriptedLlm, make_app_config, text_turn


def _client(scratch_dir: Path, llm: object) -> tuple[httpx.AsyncClient, Container]:
    """一个把请求直接打进 ASGI 应用的客户端，不占端口、不联网。

    举例：client, container = _client(scratch_dir, ScriptedLlm([...]))
    """
    config = make_app_config(session_dir=str(scratch_dir / "sessions"))
    container = build_container(config, llm=llm)  # type: ignore[arg-type]
    transport = httpx.ASGITransport(app=create_app(container))
    return httpx.AsyncClient(transport=transport, base_url="http://test"), container


@pytest.fixture
async def client(scratch_dir: Path) -> AsyncIterator[httpx.AsyncClient]:
    """默认那套：模型按脚本回三次话。

    举例：response = await client.get("/sessions", params={"user_id": USER})
    """
    opened, _ = _client(scratch_dir, ScriptedLlm([text_turn("好的")] * 3))
    async with opened as client:
        yield client


async def _new_session(client: httpx.AsyncClient) -> str:
    """建一个会话，返回它的 id。

    举例：session_id = await _new_session(client)
    """
    response = await client.post("/sessions", params={"user_id": USER})
    assert response.status_code == 201
    return response.json()["session_id"]


async def _wait_idle(client: httpx.AsyncClient, session_id: str) -> None:
    """等这个会话跑完。

    举例：await _wait_idle(client, session_id)
    """
    for _ in range(200):
        body = (await client.get(f"/sessions/{session_id}", params={"user_id": USER})).json()
        if not body["is_running"]:
            return
        await asyncio.sleep(0)
    raise AssertionError("trace 一直没跑完")


async def _say(client: httpx.AsyncClient, session_id: str, text: str) -> None:
    """问一句并等它跑完 —— 一次 trace 到此收尾。

    举例：await _say(client, session_id, "在吗")
    """
    await client.post(
        f"/sessions/{session_id}/messages",
        params={"user_id": USER},
        json={"content": [{"type": "text", "text": text}]},
    )
    await _wait_idle(client, session_id)


async def _traces(client: httpx.AsyncClient, session_id: str, **params) -> list[dict]:
    """取轨迹。

    举例：traces = await _traces(client, session_id)
    """
    response = await client.get(
        f"/sessions/{session_id}/traces", params={"user_id": USER, **params}
    )
    assert response.status_code == 200
    return response.json()["traces"]


async def test_a_finished_trace_comes_with_its_timeline(client: httpx.AsyncClient) -> None:
    """一次跑完的 trace：状态、轮数、总耗时、各格明细都在。"""
    session_id = await _new_session(client)
    await _say(client, session_id, "在吗")

    traces = await _traces(client, session_id)

    assert len(traces) == 1
    trace = traces[0]
    assert trace["status"] == "completed"
    assert trace["turns"] == 1
    assert trace["duration_ms"] >= 0
    assert trace["started_at"] <= trace["ended_at"]

    names = [span["name"] for span in trace["spans"]]
    assert names[0] == "trace:before"
    assert "llm" in names
    assert "turn:end" in names
    # 收尾那一格不在里面：它记的是收尾本身。
    assert "trace:end" not in names
    assert all(span["start_ms"] >= 0 and span["elapsed_ms"] >= 0 for span in trace["spans"])
    assert {span["kind"] for span in trace["spans"]} == {"site", "llm"}


async def test_traces_are_newest_first(client: httpx.AsyncClient) -> None:
    """两次执行：最近那次排在最前面 —— 面板一打开就想看刚才那次。"""
    session_id = await _new_session(client)
    await _say(client, session_id, "第一问")
    await _say(client, session_id, "第二问")

    traces = await _traces(client, session_id)

    assert len(traces) == 2
    assert traces[0]["started_at"] > traces[1]["started_at"]


async def test_a_running_trace_is_not_listed(scratch_dir: Path) -> None:
    """还在跑的那次没有 trace_end，耗时没有答案，不出现。"""
    opened, container = _client(scratch_dir, BlockingLlm())
    async with opened as client:
        session_id = await _new_session(client)
        await client.post(
            f"/sessions/{session_id}/messages",
            params={"user_id": USER},
            json={"content": [{"type": "text", "text": "在吗"}]},
        )

        traces = await _traces(client, session_id)

        # 收工：BlockingLlm 永远不会自己结束，把挂住的那次 trace 掐掉。
        live = container.sessions.get(session_id)
        assert live is not None and live.task is not None
        live.task.cancel()
        await asyncio.sleep(0)

    assert traces == []


async def test_the_limit_caps_what_comes_back(client: httpx.AsyncClient) -> None:
    """limit 只影响给多少条，不影响里面是什么。"""
    session_id = await _new_session(client)
    await _say(client, session_id, "第一问")
    await _say(client, session_id, "第二问")

    traces = await _traces(client, session_id, limit=1)

    assert len(traces) == 1


async def test_another_user_cannot_read_the_traces(client: httpx.AsyncClient) -> None:
    """别人的会话连轨迹都看不到 —— 与"不存在"回同一个 404。"""
    session_id = await _new_session(client)

    response = await client.get(
        f"/sessions/{session_id}/traces", params={"user_id": "user_别的"}
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"


async def test_a_fresh_session_has_no_traces(client: httpx.AsyncClient) -> None:
    """刚建好的会话一次都没跑过。"""
    session_id = await _new_session(client)

    assert await _traces(client, session_id) == []

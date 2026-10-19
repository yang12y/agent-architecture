"""HTTP 一元端点的测试。

走真链路：真 Container、真 Agent，只把模型换成假件。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from agent_architecture.api import create_app
from agent_architecture.api.schemas import DEFAULT_PAGE_SIZE
from agent_architecture.composition import Container, build_container
from agent_architecture.contracts import SessionEntryType
from fakes import (
    START,
    USER,
    BlockingLlm,
    ScriptedLlm,
    make_app_config,
    text_turn,
    thought_turn,
    user_message,
)


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
    """默认那套：模型按脚本回三次话，够跑几条用例。

    举例：response = await client.get("/sessions", params={"user_id": USER})
    """
    opened, _ = _client(scratch_dir, ScriptedLlm([text_turn("好的")] * 3))
    async with opened as client:
        yield client


async def _new_session(client: httpx.AsyncClient, **extra) -> str:
    """建一个会话，返回它的 id。

    举例：session_id = await _new_session(client)
    """
    response = await client.post("/sessions", params={"user_id": USER}, json=extra or None)
    assert response.status_code == 201
    return response.json()["session_id"]


async def _wait_idle(client: httpx.AsyncClient, session_id: str) -> None:
    """等这个会话跑完 —— 轮询 is_running，前端也是这么做的。

    举例：await _wait_idle(client, session_id)
    """
    for _ in range(200):
        body = (await client.get(f"/sessions/{session_id}", params={"user_id": USER})).json()
        if not body["is_running"]:
            return
        await asyncio.sleep(0)
    raise AssertionError("trace 一直没跑完")


async def test_health_is_ok(client: httpx.AsyncClient) -> None:
    """活着就回 ok。"""
    response = await client.get("/health")

    assert (response.status_code, response.json()) == (200, {"status": "ok"})


async def test_creating_a_session_returns_its_meta(client: httpx.AsyncClient) -> None:
    """新建会话回 201，元信息里带着它的主人。"""
    response = await client.post(
        "/sessions", params={"user_id": USER}, json={"title": "重构 session 模块"}
    )

    body = response.json()
    assert response.status_code == 201
    assert body["user_id"] == USER
    assert body["title"] == "重构 session 模块"
    assert body["status"] == "active"


async def test_a_session_can_be_created_without_a_body(client: httpx.AsyncClient) -> None:
    """body 可以整个省略，标题留给首条用户消息去截。"""
    response = await client.post("/sessions", params={"user_id": USER})

    assert response.status_code == 201
    assert response.json()["title"] is None


async def test_the_list_only_shows_one_users_sessions(client: httpx.AsyncClient) -> None:
    """列表按 user_id 分：别人的一条都不出现。"""
    mine = await _new_session(client)
    await client.post("/sessions", params={"user_id": "user_别的"}, json={"title": "他的"})

    response = await client.get("/sessions", params={"user_id": USER})

    assert [item["session_id"] for item in response.json()["sessions"]] == [mine]


async def test_another_user_cannot_read_the_session(client: httpx.AsyncClient) -> None:
    """ "不存在"与"不是你的"回同一个 404。"""
    session_id = await _new_session(client)

    response = await client.get(f"/sessions/{session_id}", params={"user_id": "user_别的"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"


async def test_an_unknown_session_is_a_404(client: httpx.AsyncClient) -> None:
    """根本没这个会话。"""
    response = await client.get("/sessions/sess_没有", params={"user_id": USER})

    assert response.status_code == 404


async def test_a_fresh_session_has_no_messages(client: httpx.AsyncClient) -> None:
    """刚建好的会话是空的，而且没在跑。"""
    session_id = await _new_session(client)

    body = (await client.get(f"/sessions/{session_id}", params={"user_id": USER})).json()

    assert body["is_running"] is False
    assert body["messages"] == []


async def test_a_message_lands_in_the_history(client: httpx.AsyncClient) -> None:
    """发一条消息 -> 202 -> 等 trace 跑完 -> 历史里有用户与模型两条。"""
    session_id = await _new_session(client)

    accepted = await client.post(
        f"/sessions/{session_id}/messages",
        params={"user_id": USER},
        json={"content": [{"type": "text", "text": "在吗"}], "client_message_id": "web-1"},
    )
    await _wait_idle(client, session_id)

    body = (await client.get(f"/sessions/{session_id}", params={"user_id": USER})).json()
    assert accepted.status_code == 202
    assert accepted.json() == {"session_id": session_id, "accepted": True, "queued": False}
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    assert body["messages"][0]["content"] == [{"type": "text", "text": "在吗"}]
    assert body["messages"][1]["content"] == [{"type": "text", "text": "好的"}]


async def test_interrupting_an_idle_session_is_a_no_op(client: httpx.AsyncClient) -> None:
    """没在跑就回 200 + interrupted=false，不报错。"""
    session_id = await _new_session(client)

    response = await client.post(f"/sessions/{session_id}/interrupt", params={"user_id": USER})

    assert response.status_code == 200
    assert response.json()["interrupted"] is False


async def test_an_empty_message_is_a_validation_error(client: httpx.AsyncClient) -> None:
    """content 不能是空数组。"""
    session_id = await _new_session(client)

    response = await client.post(
        f"/sessions/{session_id}/messages", params={"user_id": USER}, json={"content": []}
    )

    assert response.status_code == 422


async def test_a_client_cannot_forge_a_tool_call(client: httpx.AsyncClient) -> None:
    """收不进来 —— 伪造工具调用等于绕过位点 6 的检查链。"""
    session_id = await _new_session(client)

    response = await client.post(
        f"/sessions/{session_id}/messages",
        params={"user_id": USER},
        json={
            "content": [
                {"type": "tool_call", "tool_call_id": "c1", "name": "write", "arguments": {}}
            ]
        },
    )

    assert response.status_code == 422


async def test_a_second_message_while_running_is_a_conflict(scratch_dir: Path) -> None:
    """会话正在跑时暂时不收新消息 —— 运行中入队还没做。"""
    opened, container = _client(scratch_dir, BlockingLlm())
    async with opened as client:
        session_id = await _new_session(client)
        first = await client.post(
            f"/sessions/{session_id}/messages",
            params={"user_id": USER},
            json={"content": [{"type": "text", "text": "在吗"}]},
        )
        second = await client.post(
            f"/sessions/{session_id}/messages",
            params={"user_id": USER},
            json={"content": [{"type": "text", "text": "再说一次"}]},
        )

        assert first.status_code == 202
        assert second.status_code == 409
        assert second.json()["error"]["code"] == "session_busy"

        # 收工：BlockingLlm 永远不会自己结束，把挂住的那次 trace 掐掉。
        live = container.sessions.get(session_id)
        assert live is not None and live.task is not None
        live.task.cancel()
        await asyncio.sleep(0)


async def test_thinking_lands_in_the_history(scratch_dir: Path) -> None:
    """推理模型的思考进日志、进历史，而且排在正文前面 —— 它本来就是先发生的。"""
    opened, _ = _client(scratch_dir, ScriptedLlm([thought_turn("先看看", "好了")]))
    async with opened as client:
        session_id = await _new_session(client)
        await client.post(
            f"/sessions/{session_id}/messages",
            params={"user_id": USER},
            json={"content": [{"type": "text", "text": "在吗"}]},
        )
        await _wait_idle(client, session_id)
        messages = (await client.get(f"/sessions/{session_id}", params={"user_id": USER})).json()[
            "messages"
        ]

    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] == [
        {"type": "thinking", "text": "先看看"},
        {"type": "text", "text": "好了"},
    ]


async def test_deleting_a_session_removes_it(client: httpx.AsyncClient) -> None:
    """删掉之后列表里没有它、详情 404 —— 消息记录一起没了，不可恢复。"""
    session_id = await _new_session(client)

    response = await client.delete(f"/sessions/{session_id}", params={"user_id": USER})
    listed = await client.get("/sessions", params={"user_id": USER})
    detail = await client.get(f"/sessions/{session_id}", params={"user_id": USER})

    assert response.status_code == 200
    assert response.json() == {"session_id": session_id, "deleted": True}
    assert listed.json() == {"sessions": []}
    assert detail.status_code == 404


async def test_deleting_another_users_session_is_a_404(client: httpx.AsyncClient) -> None:
    """别人的会话连"能不能删"都不告诉 —— 与"不存在"回同一个 404。"""
    session_id = await _new_session(client)

    response = await client.delete(f"/sessions/{session_id}", params={"user_id": "user_别的"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"


async def test_deleting_a_running_session_is_a_conflict(scratch_dir: Path) -> None:
    """跑着的时候不许删：那一刻 checkpoint 正在往它的目录里推条目。"""
    opened, container = _client(scratch_dir, BlockingLlm())
    async with opened as client:
        session_id = await _new_session(client)
        await client.post(
            f"/sessions/{session_id}/messages",
            params={"user_id": USER},
            json={"content": [{"type": "text", "text": "在吗"}]},
        )

        response = await client.delete(f"/sessions/{session_id}", params={"user_id": USER})

        # 收工：BlockingLlm 永远不会自己结束，把挂住的那次 trace 掐掉。
        live = container.sessions.get(session_id)
        assert live is not None and live.task is not None
        live.task.cancel()
        await asyncio.sleep(0)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "session_busy"


async def test_a_fresh_session_has_empty_stats(client: httpx.AsyncClient) -> None:
    """新会话三个数都是 0，只有上限是已知的 —— 界面那个圈得有分母。"""
    session_id = await _new_session(client)

    stats = (await client.get(f"/sessions/{session_id}", params={"user_id": USER})).json()["stats"]

    assert stats == {"traces": 0, "turns": 0, "context_tokens": 0, "max_tokens": 1000000}


async def test_stats_grow_after_a_trace(client: httpx.AsyncClient) -> None:
    """跑完一次之后 trace 数与轮数各加一，上下文量也有了实测值。"""
    session_id = await _new_session(client)
    await client.post(
        f"/sessions/{session_id}/messages",
        params={"user_id": USER},
        json={"content": [{"type": "text", "text": "在吗"}]},
    )
    await _wait_idle(client, session_id)

    stats = (await client.get(f"/sessions/{session_id}", params={"user_id": USER})).json()["stats"]

    assert stats["traces"] == 1
    assert stats["turns"] == 1
    assert stats["context_tokens"] > 0


# ---------------------------------------------------------------------------
# 历史分页
# ---------------------------------------------------------------------------


async def _long_session(scratch_dir: Path, count: int = 120) -> tuple[httpx.AsyncClient, str]:
    """一个塞了 count 条用户消息的会话 —— 直接往 Session 里写，不跑 trace。

    举例：client, session_id = await _long_session(scratch_dir)
    """
    opened, container = _client(scratch_dir, ScriptedLlm())
    live = await container.sessions.create(user_id=USER)
    for index in range(count):
        live.session.append_entry(
            trace_id="trace_page",
            type=SessionEntryType.MESSAGE,
            payload=user_message(f"第 {index} 条"),
            now=START,
        )
    return opened, live.session_id


async def test_the_detail_returns_only_the_last_page(scratch_dir: Path) -> None:
    """默认只给最后一页：打开会话先看最新的一屏，前面还有多少用 has_more 告诉客户端。"""
    client, session_id = await _long_session(scratch_dir)

    async with client:
        body = (await client.get(f"/sessions/{session_id}", params={"user_id": USER})).json()

    assert len(body["messages"]) == DEFAULT_PAGE_SIZE
    assert body["has_more"] is True
    assert [m["entry_seq"] for m in body["messages"]] == list(range(71, 121))
    assert body["messages"][-1]["content"] == [{"type": "text", "text": "第 119 条"}]


async def test_paging_backwards_with_before_seq(scratch_dir: Path) -> None:
    """往前翻：给已加载的最早那条的 entry_seq，回它之前的一页。"""
    client, session_id = await _long_session(scratch_dir)

    async with client:
        older = (
            await client.get(
                f"/sessions/{session_id}",
                params={"user_id": USER, "before_seq": 71},
            )
        ).json()
        first = (
            await client.get(
                f"/sessions/{session_id}",
                params={"user_id": USER, "before_seq": 21},
            )
        ).json()

    assert [m["entry_seq"] for m in older["messages"]] == list(range(21, 71))
    assert older["has_more"] is True
    assert [m["entry_seq"] for m in first["messages"]] == list(range(1, 21))
    assert first["has_more"] is False


async def test_paging_forwards_with_after_seq(scratch_dir: Path) -> None:
    """往后补：给最新那条的 entry_seq，只回新增的那几条 —— 增量刷新走的就是它。"""
    client, session_id = await _long_session(scratch_dir)

    async with client:
        tail = (
            await client.get(
                f"/sessions/{session_id}",
                params={"user_id": USER, "after_seq": 100},
            )
        ).json()
        chunk = (
            await client.get(
                f"/sessions/{session_id}",
                params={"user_id": USER, "after_seq": 3, "limit": 10},
            )
        ).json()

    assert [m["entry_seq"] for m in tail["messages"]] == list(range(101, 121))
    assert tail["has_more"] is False
    assert [m["entry_seq"] for m in chunk["messages"]] == list(range(4, 14))
    assert chunk["has_more"] is True


async def test_two_cursors_at_once_is_rejected(scratch_dir: Path) -> None:
    """两个方向同时给是没意义的 —— 与其猜，不如直接说不行。"""
    client, session_id = await _long_session(scratch_dir, count=3)

    async with client:
        response = await client.get(
            f"/sessions/{session_id}",
            params={"user_id": USER, "before_seq": 2, "after_seq": 1},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_cursor"

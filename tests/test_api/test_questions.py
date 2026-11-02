"""提问端点：列待回答、交回答，以及没人看着时会怎样。

走真链路：真 Container、真工具、真 QuestionGate，只把模型换成假件。
**每个用例自己决定有没有"看着这个会话的界面"** —— 那正是分岔点（Presence）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
from fastapi import FastAPI

from agent_frame.api import create_app
from agent_frame.composition import Container, build_container
from fakes import USER, ScriptedLlm, make_app_config, text_turn, tool_turn

# 模型要问的那个问题。
#
# 举例：ASK["questions"][0]["id"] == "go"
ASK = {
    "questions": [
        {
            "id": "go",
            "header": "确认",
            "question": "要继续吗？",
            "options": [{"label": "继续", "description": "往下做"}, {"label": "先停"}],
        }
    ]
}


def _workspace(scratch_dir: Path) -> Path:
    """建一个工作目录。

    举例：_workspace(scratch_dir) -> Path(".tmp/tests/x/ws")
    """
    workspace = scratch_dir / "ws"
    workspace.mkdir()
    return workspace


def _stack(workspace: Path, session_dir: Path) -> tuple[FastAPI, Container]:
    """模型：先问一句，拿到回答之后再回一句话。

    举例：app, container = _stack(workspace, session_dir)
    """
    llm = ScriptedLlm([tool_turn(("call_1", "ask_user", ASK)), text_turn("好，我接着做")])
    config = make_app_config(workspace=str(workspace), session_dir=str(session_dir))
    container = build_container(config, llm=llm)
    return create_app(container), container


def _client(app: FastAPI) -> httpx.AsyncClient:
    """一个打到这个应用上的客户端。

    举例：async with _client(app) as client
    """
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _new_session(client: httpx.AsyncClient) -> str:
    """建一个会话，返回它的 id。

    举例：session_id = await _new_session(client)
    """
    response = await client.post("/sessions", params={"user_id": USER})
    assert response.status_code == 201
    return response.json()["session_id"]


async def _send(client: httpx.AsyncClient, session_id: str) -> None:
    """发一条消息，trace 在后台跑起来。

    举例：await _send(client, session_id)
    """
    response = await client.post(
        f"/sessions/{session_id}/messages",
        params={"user_id": USER},
        json={"content": [{"type": "text", "text": "帮我确认一下"}]},
    )
    assert response.status_code == 202


async def _pending(client: httpx.AsyncClient, session_id: str) -> list[dict]:
    """拉一次待回答列表。

    举例：await _pending(client, session_id) -> []
    """
    response = await client.get("/questions", params={"user_id": USER, "session_id": session_id})
    assert response.status_code == 200
    return response.json()["questions"]


async def _wait_for_question(client: httpx.AsyncClient, session_id: str) -> dict:
    """等提问冒出来 —— trace 是后台跑的，只能轮询。

    举例：request = await _wait_for_question(client, session_id)
    """
    for _ in range(500):
        found = await _pending(client, session_id)
        if found:
            return found[0]
        await asyncio.sleep(0)
    raise AssertionError("一直没有待回答的提问出现")


async def _answer(
    client: httpx.AsyncClient, session_id: str, request_id: str, answers: list[dict]
) -> httpx.Response:
    """交一份回答。

    举例：await _answer(client, session_id, "q_1", [{"id": "go", "selected": ["继续"]}])
    """
    return await client.post(
        f"/questions/{request_id}",
        params={"user_id": USER, "session_id": session_id},
        json={"answers": answers},
    )


async def _wait_idle(client: httpx.AsyncClient, session_id: str) -> None:
    """等这次 trace 跑完。

    举例：await _wait_idle(client, session_id)
    """
    for _ in range(500):
        body = (await client.get(f"/sessions/{session_id}", params={"user_id": USER})).json()
        if not body["is_running"]:
            return
        await asyncio.sleep(0)
    raise AssertionError("trace 一直没跑完")


async def _messages(client: httpx.AsyncClient, session_id: str) -> list[dict]:
    """这次会话的全部消息。

    举例：messages = await _messages(client, session_id)
    """
    body = (await client.get(f"/sessions/{session_id}", params={"user_id": USER})).json()
    return body["messages"]


async def test_without_a_watcher_the_call_fails_instead_of_hanging(scratch_dir: Path) -> None:
    """没有界面连着就当场失败，而且**把原因交给模型** —— 挂在那儿只会等到超时。"""
    workspace = _workspace(scratch_dir)
    app, _ = _stack(workspace, scratch_dir / "sessions")
    async with _client(app) as client:
        session_id = await _new_session(client)
        await _send(client, session_id)
        await _wait_idle(client, session_id)
        messages = await _messages(client, session_id)

    assert "没有连着的界面" in messages[2]["content"][0]["text"]
    # 这次调用失败了，但 trace 照常往下走。
    assert messages[3]["content"][0]["text"] == "好，我接着做"


async def test_a_question_shows_up_with_its_options(scratch_dir: Path) -> None:
    """有界面看着的时候提问挂起，列表里带着问题与选项。"""
    workspace = _workspace(scratch_dir)
    app, container = _stack(workspace, scratch_dir / "sessions")
    async with _client(app) as client:
        session_id = await _new_session(client)
        container.presence.attach(session_id)
        await _send(client, session_id)

        request = await _wait_for_question(client, session_id)

    assert request["tool_name"] == "ask_user"
    assert request["session_id"] == session_id
    assert request["questions"][0]["question"] == "要继续吗？"
    assert request["questions"][0]["header"] == "确认"
    assert [option["label"] for option in request["questions"][0]["options"]] == ["继续", "先停"]


async def test_answering_lets_the_call_finish(scratch_dir: Path) -> None:
    """回答之后工具拿到答案，结果作为一条普通工具消息进日志。"""
    workspace = _workspace(scratch_dir)
    app, container = _stack(workspace, scratch_dir / "sessions")
    async with _client(app) as client:
        session_id = await _new_session(client)
        container.presence.attach(session_id)
        await _send(client, session_id)
        request = await _wait_for_question(client, session_id)

        response = await _answer(
            client, session_id, request["request_id"], [{"id": "go", "selected": ["继续"]}]
        )
        await _wait_idle(client, session_id)
        left = await _pending(client, session_id)
        messages = await _messages(client, session_id)

    assert response.status_code == 200
    assert response.json() == {"request_id": request["request_id"], "accepted": True}
    assert left == []
    assert "继续" in messages[2]["content"][0]["text"]
    assert messages[3]["content"][0]["text"] == "好，我接着做"


async def test_a_custom_answer_needs_no_option(scratch_dir: Path) -> None:
    """只打字、不点选项也是合法答案 —— 界面上永远留着一句自己说的位置。"""
    workspace = _workspace(scratch_dir)
    app, container = _stack(workspace, scratch_dir / "sessions")
    async with _client(app) as client:
        session_id = await _new_session(client)
        container.presence.attach(session_id)
        await _send(client, session_id)
        request = await _wait_for_question(client, session_id)

        response = await _answer(
            client,
            session_id,
            request["request_id"],
            [{"id": "go", "selected": [], "custom": "先别做"}],
        )
        await _wait_idle(client, session_id)
        messages = await _messages(client, session_id)

    assert response.status_code == 200
    assert "先别做" in messages[2]["content"][0]["text"]


async def test_an_option_that_does_not_exist_is_a_400(scratch_dir: Path) -> None:
    """选项对不上就整份拒掉，**不挑能用的收** —— 收半份会让模型以为用户只答了一半。"""
    workspace = _workspace(scratch_dir)
    app, container = _stack(workspace, scratch_dir / "sessions")
    async with _client(app) as client:
        session_id = await _new_session(client)
        container.presence.attach(session_id)
        await _send(client, session_id)
        request = await _wait_for_question(client, session_id)

        response = await _answer(
            client, session_id, request["request_id"], [{"id": "go", "selected": ["也许"]}]
        )
        # 拒掉之后提问还挂着：界面上那张卡不该消失。
        left = await _pending(client, session_id)

        await _answer(
            client, session_id, request["request_id"], [{"id": "go", "selected": ["先停"]}]
        )
        await _wait_idle(client, session_id)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_answers"
    assert left != []


async def test_a_missing_answer_is_a_400(scratch_dir: Path) -> None:
    """少答一个问题也算对不上。"""
    workspace = _workspace(scratch_dir)
    app, container = _stack(workspace, scratch_dir / "sessions")
    async with _client(app) as client:
        session_id = await _new_session(client)
        container.presence.attach(session_id)
        await _send(client, session_id)
        request = await _wait_for_question(client, session_id)

        response = await _answer(client, session_id, request["request_id"], [])
        await _answer(
            client, session_id, request["request_id"], [{"id": "go", "selected": ["继续"]}]
        )
        await _wait_idle(client, session_id)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_answers"


async def test_answering_twice_is_a_404(scratch_dir: Path) -> None:
    """已经答过的再答一次，是 404 而不是改主意。"""
    workspace = _workspace(scratch_dir)
    app, container = _stack(workspace, scratch_dir / "sessions")
    async with _client(app) as client:
        session_id = await _new_session(client)
        container.presence.attach(session_id)
        await _send(client, session_id)
        request = await _wait_for_question(client, session_id)
        answers = [{"id": "go", "selected": ["继续"]}]

        await _answer(client, session_id, request["request_id"], answers)
        again = await _answer(client, session_id, request["request_id"], answers)
        await _wait_idle(client, session_id)

    assert again.status_code == 404
    assert again.json()["error"]["code"] == "question_not_found"


async def test_another_user_cannot_reach_them(scratch_dir: Path) -> None:
    """别人的会话连列表都拉不到 —— 与"不存在"回同一个 404。"""
    workspace = _workspace(scratch_dir)
    app, container = _stack(workspace, scratch_dir / "sessions")
    async with _client(app) as client:
        session_id = await _new_session(client)
        container.presence.attach(session_id)
        await _send(client, session_id)
        request = await _wait_for_question(client, session_id)

        listed = await client.get(
            "/questions", params={"user_id": "user_别的", "session_id": session_id}
        )
        answered = await client.post(
            f"/questions/{request['request_id']}",
            params={"user_id": "user_别的", "session_id": session_id},
            json={"answers": [{"id": "go", "selected": ["继续"]}]},
        )

        await _answer(
            client, session_id, request["request_id"], [{"id": "go", "selected": ["继续"]}]
        )
        await _wait_idle(client, session_id)

    assert listed.status_code == 404
    assert listed.json()["error"]["code"] == "session_not_found"
    assert answered.status_code == 404
    assert answered.json()["error"]["code"] == "session_not_found"

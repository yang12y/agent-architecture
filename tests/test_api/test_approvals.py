"""审批端点：列待审批、收回执，以及回执真的驱动了工具。

走真链路：真 Container、真沙箱、真工具、真 ApprovalGate，只把模型换成假件。
要审批的场景选**读工作目录之外的文件** —— 不产生副作用，也不起进程。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from fakes import USER, ScriptedLlm, make_app_config, text_turn, tool_turn
from yzy_agent.api import create_app
from yzy_agent.composition import build_container

# 越界的那个文件，相对工作目录的位置。工作目录是 <scratch>/ws，于是它落在 <scratch>/outside.txt。
#
# 举例：OUTSIDE == "../outside.txt"
OUTSIDE = "../outside.txt"


def _workspace(scratch_dir: Path) -> Path:
    """建一个工作目录。

    它比 scratch_dir 低一层，所以 ../outside.txt 落在 scratch_dir 里 —— 每个用例各自一份，
    互不干扰。

    举例：_workspace(scratch_dir) -> Path(".tmp/tests/x/ws")
    """
    workspace = scratch_dir / "ws"
    workspace.mkdir()
    return workspace


def _client(workspace: Path, session_dir: Path) -> httpx.AsyncClient:
    """模型：先要读那个越界文件，拿到结果之后再回一句话。

    举例：client = _client(workspace, session_dir)
    """
    llm = ScriptedLlm([tool_turn(("call_1", "read", {"path": OUTSIDE})), text_turn("那我换个地方")])
    config = make_app_config(workspace=str(workspace), session_dir=str(session_dir))
    container = build_container(config, llm=llm)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(container)), base_url="http://test"
    )


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
        json={"content": [{"type": "text", "text": "读一下外面的文件"}]},
    )
    assert response.status_code == 202


async def _pending(client: httpx.AsyncClient, session_id: str) -> list[dict]:
    """拉一次待审批列表。

    举例：await _pending(client, session_id) -> []
    """
    response = await client.get("/approvals", params={"user_id": USER, "session_id": session_id})
    assert response.status_code == 200
    return response.json()["approvals"]


async def _wait_for_approval(client: httpx.AsyncClient, session_id: str) -> dict:
    """等第一次「要人确认」冒出来 —— trace 是后台跑的，只能轮询。

    举例：request = await _wait_for_approval(client, session_id)
    """
    for _ in range(500):
        found = await _pending(client, session_id)
        if found:
            return found[0]
        await asyncio.sleep(0)
    raise AssertionError("一直没有待审批出现")


async def _answer(
    client: httpx.AsyncClient,
    session_id: str,
    request_id: str,
    decision: str,
    comment: str | None = None,
) -> httpx.Response:
    """交一份回执。

    举例：await _answer(client, session_id, request_id, "approve")
    """
    body: dict = {"decision": decision}
    if comment is not None:
        body["comment"] = comment
    return await client.post(
        f"/approvals/{request_id}",
        params={"user_id": USER, "session_id": session_id},
        json=body,
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


async def test_a_fresh_session_has_nothing_pending(scratch_dir: Path) -> None:
    """没在跑就没人在等。"""
    workspace = _workspace(scratch_dir)
    async with _client(workspace, scratch_dir / "sessions") as client:
        session_id = await _new_session(client)

        assert await _pending(client, session_id) == []


async def test_a_call_over_the_boundary_shows_up(scratch_dir: Path) -> None:
    """越界的调用会挂在待审批列表上，带够用户判断所需的信息。"""
    workspace = _workspace(scratch_dir)
    async with _client(workspace, scratch_dir / "sessions") as client:
        session_id = await _new_session(client)
        await _send(client, session_id)

        request = await _wait_for_approval(client, session_id)

        assert request["tool_name"] == "read"
        assert request["session_id"] == session_id
        # 给用户看的是**补齐默认值之后的最终参数**，与判定用的是同一份（不是模型的原始输入）。
        assert request["arguments"] == {"path": OUTSIDE, "start_line": 1, "max_lines": 2000}
        # 理由里给的是**解析后的绝对路径** —— 用户要看的是到底会碰到哪个文件。
        assert "之外" in request["reason"]
        assert "outside.txt" in request["reason"]


async def test_approving_lets_the_tool_run(scratch_dir: Path) -> None:
    """点同意之后工具真的跑了，结果进了会话。"""
    (scratch_dir / "outside.txt").write_text("外面的内容", encoding="utf-8")
    workspace = _workspace(scratch_dir)
    async with _client(workspace, scratch_dir / "sessions") as client:
        session_id = await _new_session(client)
        await _send(client, session_id)
        request = await _wait_for_approval(client, session_id)

        response = await _answer(client, session_id, request["request_id"], "approve")
        await _wait_idle(client, session_id)
        messages = await _messages(client, session_id)

    assert response.status_code == 200
    assert response.json() == {
        "request_id": request["request_id"],
        "decision": "approve",
        "accepted": True,
    }
    assert "外面的内容" in messages[2]["content"][0]["text"]
    assert messages[3]["content"][0]["text"] == "那我换个地方"


async def test_denying_refuses_the_call_and_says_so(scratch_dir: Path) -> None:
    """点拒绝之后调用不执行，而且**要告诉模型是人拒的** —— 只说"越界"它会再发一遍。"""
    (scratch_dir / "outside.txt").write_text("外面的内容", encoding="utf-8")
    workspace = _workspace(scratch_dir)
    async with _client(workspace, scratch_dir / "sessions") as client:
        session_id = await _new_session(client)
        await _send(client, session_id)
        request = await _wait_for_approval(client, session_id)

        await _answer(client, session_id, request["request_id"], "deny", comment="这个文件别动")
        await _wait_idle(client, session_id)
        messages = await _messages(client, session_id)

    result = messages[2]["content"][0]["text"]
    assert "用户拒绝了这次调用" in result
    assert "这个文件别动" in result
    assert "外面的内容" not in result


async def test_answering_twice_is_a_404(scratch_dir: Path) -> None:
    """已经处理过的请求再回一次，是 404 而不是改主意。"""
    workspace = _workspace(scratch_dir)
    async with _client(workspace, scratch_dir / "sessions") as client:
        session_id = await _new_session(client)
        await _send(client, session_id)
        request = await _wait_for_approval(client, session_id)
        await _answer(client, session_id, request["request_id"], "deny")

        again = await _answer(client, session_id, request["request_id"], "approve")

    assert again.status_code == 404
    assert again.json()["error"]["code"] == "approval_not_found"


async def test_an_unknown_request_is_a_404(scratch_dir: Path) -> None:
    """根本没这个审批。"""
    workspace = _workspace(scratch_dir)
    async with _client(workspace, scratch_dir / "sessions") as client:
        session_id = await _new_session(client)

        response = await _answer(client, session_id, "appr_没有", "approve")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "approval_not_found"


async def test_another_user_cannot_reach_them(scratch_dir: Path) -> None:
    """别人的会话连列表都拉不到 —— 与"不存在"回同一个 404。"""
    workspace = _workspace(scratch_dir)
    async with _client(workspace, scratch_dir / "sessions") as client:
        session_id = await _new_session(client)
        await _send(client, session_id)
        request = await _wait_for_approval(client, session_id)

        listed = await client.get(
            "/approvals", params={"user_id": "user_别的", "session_id": session_id}
        )
        answered = await client.post(
            f"/approvals/{request['request_id']}",
            params={"user_id": "user_别的", "session_id": session_id},
            json={"decision": "approve"},
        )

    assert listed.status_code == 404
    assert listed.json()["error"]["code"] == "session_not_found"
    assert answered.status_code == 404
    assert answered.json()["error"]["code"] == "session_not_found"


async def test_a_bad_decision_is_a_validation_error(scratch_dir: Path) -> None:
    """决定只有同意与拒绝两种，「还没想好」不是一种回执。"""
    workspace = _workspace(scratch_dir)
    async with _client(workspace, scratch_dir / "sessions") as client:
        session_id = await _new_session(client)

        response = await _answer(client, session_id, "appr_1", "maybe")

    assert response.status_code == 422

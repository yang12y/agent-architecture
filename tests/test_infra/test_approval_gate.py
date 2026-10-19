"""待审批内存表的测试。

它只有一条要紧的性质：**回执到了才返回，没到就一直等**。其余用例都是围着这条的边界。
"""

from __future__ import annotations

import asyncio

from yzy_agent.contracts import ApprovalDecision, ApprovalRequest, ApprovalResult
from yzy_agent.infra.approvals import ApprovalGate


def _request(request_id: str = "appr_1", session_id: str = "sess_1") -> ApprovalRequest:
    """造一份审批请求。

    举例：_request("appr_2", "sess_2")
    """
    return ApprovalRequest(
        request_id=request_id,
        session_id=session_id,
        trace_id="trace_1",
        tool_call_id="call_1",
        tool_name="write",
        arguments={"path": "a.py"},
        reason="路径在工作目录之外",
    )


def _approve(request_id: str = "appr_1") -> ApprovalResult:
    """造一份同意的回执。

    举例：await gate.respond("sess_1", _approve())
    """
    return ApprovalResult(request_id=request_id, decision=ApprovalDecision.APPROVE, comment=None)


async def test_a_request_waits_until_someone_answers() -> None:
    """没人回执就一直等 —— 这是它的全部意义。"""
    gate = ApprovalGate()
    waiter = asyncio.ensure_future(gate.request(_request()))
    await asyncio.sleep(0)

    assert not waiter.done()

    await gate.respond("sess_1", _approve())

    assert (await waiter).decision is ApprovalDecision.APPROVE


async def test_pending_lists_one_session_in_the_order_they_arrived() -> None:
    """按会话过滤，顺序就是请求发出的顺序 —— 界面上先来的排前面。"""
    gate = ApprovalGate()
    waiters = [
        asyncio.ensure_future(gate.request(_request("appr_1", "sess_1"))),
        asyncio.ensure_future(gate.request(_request("appr_2", "sess_1"))),
        asyncio.ensure_future(gate.request(_request("appr_3", "sess_2"))),
    ]
    await asyncio.sleep(0)

    assert [request.request_id for request in await gate.pending("sess_1")] == ["appr_1", "appr_2"]
    assert [request.request_id for request in await gate.pending("sess_2")] == ["appr_3"]

    for waiter in waiters:
        waiter.cancel()
    await asyncio.gather(*waiters, return_exceptions=True)


async def test_an_answered_request_is_no_longer_pending() -> None:
    """回执之后它就从列表里消失了 —— 重连不会再弹一个已经点过的框。"""
    gate = ApprovalGate()
    waiter = asyncio.ensure_future(gate.request(_request()))
    await asyncio.sleep(0)

    await gate.respond("sess_1", _approve())

    assert await gate.pending("sess_1") == []
    await waiter


async def test_answering_twice_is_a_miss() -> None:
    """第一次收下，第二次就找不到它了 —— api 层据此回 404。"""
    gate = ApprovalGate()
    waiter = asyncio.ensure_future(gate.request(_request()))
    await asyncio.sleep(0)

    assert await gate.respond("sess_1", _approve()) is True
    assert await gate.respond("sess_1", _approve()) is False
    await waiter


async def test_another_session_cannot_answer() -> None:
    """别的会话拿着同一个 request_id 回不了执，**也不会把它吞掉**。"""
    gate = ApprovalGate()
    waiter = asyncio.ensure_future(gate.request(_request()))
    await asyncio.sleep(0)

    assert await gate.respond("sess_别的", _approve()) is False
    assert not waiter.done()

    await gate.respond("sess_1", _approve())

    assert (await waiter).request_id == "appr_1"


async def test_a_cancelled_request_leaves_the_table() -> None:
    """没人再等的审批不能留在列表上 —— 那会让重连的界面弹出一个永远点不动的框。"""
    gate = ApprovalGate()
    waiter = asyncio.ensure_future(gate.request(_request()))
    await asyncio.sleep(0)

    waiter.cancel()
    await asyncio.gather(waiter, return_exceptions=True)

    assert await gate.pending("sess_1") == []

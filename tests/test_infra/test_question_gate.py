"""待回答内存表的测试。

和审批共用同一张表（infra/pending.py），所以这里只测提问这一层多出来的性质：
**没人连着就当场失败**，有人连着就挂起等回答。
"""

from __future__ import annotations

import asyncio

import pytest

from agent_frame.contracts import (
    NoAnswererError,
    Question,
    QuestionAnswer,
    QuestionOption,
    QuestionRequest,
    QuestionRequired,
    QuestionResult,
)
from agent_frame.infra.observability import InProcessEventBus
from agent_frame.infra.presence import Presence
from agent_frame.infra.questions import QuestionGate


def _request(request_id: str = "q_1", session_id: str = "sess_1") -> QuestionRequest:
    """造一份提问请求。

    举例：_request("q_2", "sess_2")
    """
    return QuestionRequest(
        request_id=request_id,
        session_id=session_id,
        trace_id="trace_1",
        tool_call_id="call_1",
        tool_name="ask_user",
        questions=(
            Question(
                id="go",
                question="继续吗？",
                header=None,
                options=(QuestionOption(label="继续", description=None),),
                multi_select=False,
            ),
        ),
    )


def _answer(request_id: str = "q_1") -> QuestionResult:
    """造一份回答。

    举例：await gate.respond("sess_1", _answer())
    """
    return QuestionResult(
        request_id=request_id,
        answers=(QuestionAnswer(id="go", selected=("继续",), custom=None),),
    )


def _gate(*session_ids: str) -> tuple[QuestionGate, InProcessEventBus]:
    """造一张表，并把给定的会话标成"有人在看"。

    举例：_gate("sess_1")
    """
    presence = Presence()
    for session_id in session_ids:
        presence.attach(session_id)
    bus = InProcessEventBus()
    return QuestionGate(events=bus, presence=presence), bus


async def test_without_a_listener_it_fails_at_once() -> None:
    """没人连着就当场失败 —— **不挂起**，否则这条 trace 会白等到工具超时。"""
    gate, _ = _gate()

    with pytest.raises(NoAnswererError):
        await gate.request(_request())

    assert await gate.pending("sess_1") == []


async def test_a_question_waits_until_someone_answers() -> None:
    """有人连着就挂起，回答到了才返回 —— 和审批同一条性质。"""
    gate, _ = _gate("sess_1")
    waiter = asyncio.ensure_future(gate.request(_request()))
    await asyncio.sleep(0)

    assert not waiter.done()

    await gate.respond("sess_1", _answer())

    assert (await waiter).answers[0].selected == ("继续",)


async def test_the_event_carries_the_questions_as_asked() -> None:
    """事件里带的是原样的问题 —— 前端不再自己拼文案。"""
    gate, bus = _gate("sess_1")
    seen: list[QuestionRequired] = []
    bus.subscribe(QuestionRequired, seen.append)  # type: ignore[arg-type]

    waiter = asyncio.ensure_future(gate.request(_request()))
    await asyncio.sleep(0)

    assert len(seen) == 1
    assert seen[0].request_id == "q_1"
    assert seen[0].tool_name == "ask_user"
    assert seen[0].questions[0].question == "继续吗？"

    waiter.cancel()
    await asyncio.gather(waiter, return_exceptions=True)


async def test_pending_lists_one_session_in_the_order_they_arrived() -> None:
    """按会话过滤，顺序就是问出去的顺序。"""
    gate, _ = _gate("sess_1", "sess_2")
    waiters = [
        asyncio.ensure_future(gate.request(_request("q_1", "sess_1"))),
        asyncio.ensure_future(gate.request(_request("q_2", "sess_1"))),
        asyncio.ensure_future(gate.request(_request("q_3", "sess_2"))),
    ]
    await asyncio.sleep(0)

    assert [item.request_id for item in await gate.pending("sess_1")] == ["q_1", "q_2"]
    assert [item.request_id for item in await gate.pending("sess_2")] == ["q_3"]

    for waiter in waiters:
        waiter.cancel()
    await asyncio.gather(*waiters, return_exceptions=True)


async def test_answering_twice_is_a_miss() -> None:
    """第一次收下，第二次就找不到它了 —— api 层据此回 404。"""
    gate, _ = _gate("sess_1")
    waiter = asyncio.ensure_future(gate.request(_request()))
    await asyncio.sleep(0)

    assert await gate.respond("sess_1", _answer()) is True
    assert await gate.respond("sess_1", _answer()) is False
    await waiter


async def test_another_session_cannot_answer() -> None:
    """别的会话拿着同一个 request_id 答不了，**也不会把它吞掉**。"""
    gate, _ = _gate("sess_1")
    waiter = asyncio.ensure_future(gate.request(_request()))
    await asyncio.sleep(0)

    assert await gate.respond("sess_别的", _answer()) is False
    assert not waiter.done()

    await gate.respond("sess_1", _answer())

    assert (await waiter).request_id == "q_1"


async def test_a_cancelled_request_leaves_the_table() -> None:
    """没人再等的提问不能留在列表上 —— 那会让重连的界面弹出一个点不动的卡。"""
    gate, _ = _gate("sess_1")
    waiter = asyncio.ensure_future(gate.request(_request()))
    await asyncio.sleep(0)

    waiter.cancel()
    await asyncio.gather(waiter, return_exceptions=True)

    assert await gate.pending("sess_1") == []

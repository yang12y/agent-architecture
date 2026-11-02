"""会话详情里的图与轮次大纲。

图是**按 seq 窗口跟着消息页走的**，轮次大纲是**整份替换**的 —— 这两条是导航与历史的基础，
所以单独钉住。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from agent_frame.api import create_app
from agent_frame.api.schemas import charts_of_page, page_messages
from agent_frame.api.turns import PROMPT_CHARS, TURN_OUTLINE_MAX, turn_outline
from agent_frame.composition import Container, build_container
from agent_frame.contracts import (
    Chart,
    ChartEntryPayload,
    ChartKind,
    ChartSeries,
    ImagePart,
    Message,
    MessageRole,
    SessionEntry,
    TextPart,
)
from fakes import (
    USER,
    ScriptedLlm,
    assistant_message,
    make_app_config,
    make_entry,
    text_turn,
    tool_turn,
    user_message,
)

# 一次干净的画图调用参数。
#
# 举例：_CHART_ARGS["x"] == ["08:00", "09:00"]
_CHART_ARGS: dict = {
    "title": "出口压力",
    "x": ["08:00", "09:00"],
    "series": [{"name": "压力", "values": [0.62, 0.71]}],
}


def _chart(seq: int) -> ChartEntryPayload:
    """造一条图条目，图号按 seq 编，方便断言是哪一幅。

    举例：_chart(2) -> ChartEntryPayload(chart=Chart(chart_id="c_call_2", ...))
    """
    return ChartEntryPayload(
        chart=Chart(
            chart_id=f"c_call_{seq}",
            tool_call_id=f"call_{seq}",
            kind=ChartKind.LINE,
            title=f"图{seq}",
            x=("08:00", "09:00"),
            series=(ChartSeries(name="压力", values=(0.62, 0.71)),),
            x_title=None,
            y_title=None,
        )
    )


def _tool_message(call_id: str) -> Message:
    """造一条工具结果消息 —— 有它这幅图才算"这次调用真的回来了"。

    举例：_tool_message("call_3") -> Message(role=TOOL, metadata={"tool_call_id": "call_3"})
    """
    return Message(
        role=MessageRole.TOOL,
        content=[TextPart(text="ok")],
        metadata={"tool_call_id": call_id},
    )


def _seqs(entries: list[SessionEntry]) -> list[int]:
    """一批条目的 seq，断言窗口切得对不对就看它。

    举例：_seqs(entries) -> [1, 3]
    """
    return [entry.seq for entry in entries]


def test_the_newest_chart_comes_with_the_latest_page() -> None:
    """图比最后一条消息还新：不把上界开到末尾，它就永远送不出去。"""
    entries = [
        make_entry(1, user_message("问")),
        make_entry(2, assistant_message("答")),
        make_entry(3, _tool_message("call_4")),
        make_entry(4, _chart(4)),
    ]
    page, _ = page_messages(entries, limit=50)

    assert _seqs(charts_of_page(entries, page)) == [4]


def test_a_chart_travels_with_the_page_it_sits_in() -> None:
    """往前翻页：图跟着它所在的那一段 seq 走，不漏也不重复。"""
    entries = [
        make_entry(1, user_message("问一")),
        make_entry(2, user_message("问二")),
        make_entry(3, _tool_message("call_4")),
        make_entry(4, _chart(4)),
    ]
    older, _ = page_messages(entries, limit=1, before_seq=3)
    newer, _ = page_messages(entries, limit=1)

    assert _seqs(older) == [2]
    assert _seqs(charts_of_page(entries, older, before_seq=3)) == []
    assert _seqs(charts_of_page(entries, newer)) == [4]


def test_a_tail_pull_picks_up_a_chart_without_a_message() -> None:
    """补尾部：图不带消息，会先于下一条消息出现，after_seq 之后的全归它。"""
    entries = [
        make_entry(1, user_message("问")),
        make_entry(2, _chart(2)),
        make_entry(3, _tool_message("call_2")),
    ]

    assert _seqs(charts_of_page(entries, [], after_seq=1)) == [2]


def test_the_same_chart_is_sent_once_even_after_a_retry() -> None:
    """同一次调用重跑会再写一条图（chart_id 相同）：只送最后那条，界面上不留两张。"""
    entries = [
        make_entry(1, user_message("问")),
        make_entry(2, _chart(2)),
        make_entry(3, _tool_message("call_2")),
        make_entry(4, _chart(2)),
        make_entry(5, _tool_message("call_2")),
    ]

    assert _seqs(charts_of_page(entries, [], after_seq=1)) == [4]


def test_a_chart_without_its_tool_result_is_not_sent() -> None:
    """孤儿图不送：图写在工具结果之前，死在两者之间就会留下没有结果的那幅图。"""
    entries = [
        make_entry(1, user_message("问")),
        make_entry(2, _chart(2)),
    ]

    assert charts_of_page(entries, [], after_seq=1) == []


def test_a_session_without_messages_has_no_charts() -> None:
    """一条消息都没有时窗口无从谈起。"""
    entries = [make_entry(1, _chart(1))]

    assert charts_of_page(entries, []) == []


def test_the_outline_is_one_row_per_user_message() -> None:
    """一轮 = 一条用户消息；回复取这一轮最后一条有正文的 assistant 消息。"""
    entries = [
        make_entry(1, user_message("问一")),
        make_entry(2, assistant_message("先查一下")),
        make_entry(3, assistant_message("答一")),
        make_entry(4, _chart(4)),
        make_entry(5, user_message("问二")),
    ]

    rows = turn_outline(entries)

    assert [(row.turn, row.seq, row.prompt, row.response) for row in rows] == [
        (1, 1, "问一", "答一"),
        (2, 5, "问二", ""),
    ]


def test_the_prompt_is_cut_to_one_line() -> None:
    """提示词只留一行、50 字，截断时省略号算在上限内。"""
    entries = [make_entry(1, user_message("第一行\n第二行 " + "字" * 80))]

    prompt = turn_outline(entries)[0].prompt

    assert prompt.startswith("第一行 第二行")
    assert len(prompt) == PROMPT_CHARS
    assert prompt.endswith("…")


def test_a_message_without_text_shows_up_as_an_empty_row() -> None:
    """纯图片的提问也占一行，只是没有预览 —— 导航栏按轮次号标注。"""
    image = Message(
        role=MessageRole.USER,
        content=[ImagePart(media_type="image/png", data_base64="aGk=", url=None)],
    )
    entries = [make_entry(1, image)]

    assert turn_outline(entries)[0].prompt == ""


def test_only_the_newest_turns_are_kept() -> None:
    """大纲封顶 200 行，行号仍是绝对序号 —— 否则界面上第几轮会变。"""
    entries = [
        make_entry(index + 1, user_message(f"问{index}")) for index in range(TURN_OUTLINE_MAX + 5)
    ]

    rows = turn_outline(entries)

    assert len(rows) == TURN_OUTLINE_MAX
    assert rows[0].turn == 6
    assert rows[-1].turn == TURN_OUTLINE_MAX + 5


def _client(scratch_dir: Path, llm: object) -> tuple[httpx.AsyncClient, Container]:
    """把请求直接打进 ASGI 应用的客户端，不占端口、不联网。

    举例：client, container = _client(scratch_dir, ScriptedLlm([...]))
    """
    config = make_app_config(session_dir=str(scratch_dir / "sessions"))
    container = build_container(config, llm=llm)  # type: ignore[arg-type]
    transport = httpx.ASGITransport(app=create_app(container))
    return httpx.AsyncClient(transport=transport, base_url="http://test"), container


@pytest.fixture
async def drawn(scratch_dir: Path) -> AsyncIterator[httpx.AsyncClient]:
    """模型先画一张图、再给一句结论 —— 画完的会话。"""
    llm = ScriptedLlm([tool_turn(("call_1", "chart_line", _CHART_ARGS)), text_turn("画好了")])
    async with _client(scratch_dir, llm)[0] as client:
        yield client


async def _wait_idle(client: httpx.AsyncClient, session_id: str) -> None:
    """等这个会话跑完 —— 轮询 is_running。

    举例：await _wait_idle(client, session_id)
    """
    for _ in range(200):
        body = (await client.get(f"/sessions/{session_id}", params={"user_id": USER})).json()
        if not body["is_running"]:
            return
        await asyncio.sleep(0)
    raise AssertionError("trace 一直没跑完")


async def test_a_drawn_chart_lands_in_the_history(drawn: httpx.AsyncClient) -> None:
    """画完一张图：详情里带一幅图，条目录在工具结果消息**之前**。"""
    session_id = (await drawn.post("/sessions", params={"user_id": USER})).json()["session_id"]
    await drawn.post(
        f"/sessions/{session_id}/messages",
        params={"user_id": USER},
        json={"content": [{"type": "text", "text": "画个图"}]},
    )
    await _wait_idle(drawn, session_id)

    body = (await drawn.get(f"/sessions/{session_id}", params={"user_id": USER})).json()

    assert len(body["charts"]) == 1
    chart = body["charts"][0]
    assert chart["chart_id"] == "c_call_1"
    assert chart["kind"] == "line"
    assert chart["series"] == [{"name": "压力", "values": [0.62, 0.71]}]
    # 图在工具结果之前落盘：工具结果那条消息的 seq 比它大。
    # 图只是"先写下来"，摆在哪里由正文里的 [[chart]] 标记决定。
    tool_seq = max(
        message["entry_seq"] for message in body["messages"] if message["role"] == "tool"
    )
    assert chart["entry_seq"] < tool_seq
    assert [row["prompt"] for row in body["turns"]] == ["画个图"]

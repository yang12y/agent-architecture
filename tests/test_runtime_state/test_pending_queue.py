"""中途消息队列的测试。"""

from __future__ import annotations

from fakes import make_runtime_state
from yzy_agent.contracts import Message, MessageRole, TextPart
from yzy_agent.modules.runtime_state import drain_messages, enqueue_message


def _msg(text: str) -> Message:
    """造一条用户消息，正文就是 text。

    举例：_msg("顺便看下 b.py")
    """
    return Message(role=MessageRole.USER, content=[TextPart(text=text)])


def text_of(message: Message) -> str:
    """取一条消息第一个文本部件的正文，断言里少写点索引。

    举例：text_of(_msg("你好")) -> "你好"
    """
    part = message.content[0]
    return part.text if isinstance(part, TextPart) else ""


def test_enqueue_keeps_arrival_order() -> None:
    """先进先出：用户连发两条，模型看到的顺序要和发出来的一样。"""
    state = make_runtime_state()

    enqueue_message(state, _msg("先"))
    enqueue_message(state, _msg("后"))

    assert [text_of(m) for m in drain_messages(state)] == ["先", "后"]


def test_drain_empties_the_queue() -> None:
    """取出即清空 —— 否则同一批消息会被写成两条 user 条目。"""
    state = make_runtime_state()
    enqueue_message(state, _msg("顺便看下 b.py"))

    assert len(drain_messages(state)) == 1
    assert drain_messages(state) == []
    assert list(state.pending_queue) == []


def test_drain_on_empty_queue_returns_empty_list() -> None:
    """空队列取出来是空列表，不报错。"""
    assert drain_messages(make_runtime_state()) == []

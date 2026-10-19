"""中途消息队列的入队与出队。

用户在当前动作还没跑完时又发了一条消息，不能打断正在进行的模型调用或工具调用，
所以先入队；等**模型调用返回后（位点 5）**或**工具结果返回后（位点 8）**再整批取出，
由 session 追加成 user 消息，并入下一轮上下文。

任何时刻都可以入队；只有位点 5 与位点 8 出队。
"""

from __future__ import annotations

from yzy_agent.contracts import Message, RuntimeState


def enqueue_message(state: RuntimeState, message: Message) -> None:
    """把一条中途消息放到队尾。

    举例：enqueue_message(state, Message(role=MessageRole.USER, content=[TextPart(text="顺便看下 b.py")]))
    """
    state.pending_queue.append(message)


def drain_messages(state: RuntimeState) -> list[Message]:
    """把队列里的消息全部取出、清空队列，并保持原来的先后顺序。

    为什么要"取出即清空"：出队方（位点 5 / 8）后续会把这些消息写成 user 条目，
    留在队列里会让它们被写第二遍。所以取和清必须是同一个动作。

    举例：drain_messages(state) -> [Message(...), Message(...)]；紧接着再调一次得到 []
    """
    taken = list(state.pending_queue)
    state.pending_queue.clear()
    return taken

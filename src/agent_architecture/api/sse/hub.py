"""订阅登记表：谁在听哪个会话。

事件总线的分发是**同步**的，而往流里写是异步的 —— 所以 hub 只把帧塞进每条流自己的队列，
由那条流的生成器去发。一条流只有一个写者，帧不会交错。

它订阅一次事件总线，所有流共用这一次订阅：别的层不需要知道有没有人在听。
"""

from __future__ import annotations

import asyncio

from agent_architecture.contracts import EventBus, Logger

from .frames import EVENT_TYPES, event_frame

# 一条流最多积压多少帧。满了就不再收，由生成器负责收尾 ——
# 客户端跟不上时，重连全量重拉（§7.6）比拖着一条越积越多的流更好。
#
# 举例：512
OUTBOX_LIMIT = 512


class Connection:
    """一条 SSE 流：一个发送队列 + 它看着哪些会话。

    举例：connection = Connection(); connection.offer("sess_x", event)
    """

    def __init__(self) -> None:
        """建一条空流。

        举例：Connection()
        """
        # 待发的帧。生成器是唯一的消费者，也是唯一碰响应的人。
        #
        # 举例：asyncio.Queue(maxsize=512)
        self.outbox: asyncio.Queue[dict] = asyncio.Queue(maxsize=OUTBOX_LIMIT)

        # 这条流看着的会话。连接时就定下来，之后不再变 —— 换会话等于重连。
        #
        # 举例：{"sess_x"}
        self.sessions: set[str] = set()

        # 队列满过没有。满了就不再收，由生成器负责收尾。
        #
        # 举例：False
        self.overflowed = False

    def offer(self, session_id: str, event: object) -> None:
        """把事件转成帧排进去；这条流没看着这个会话就丢掉。

        举例：connection.offer("sess_x", TurnStart(...))
        """
        if session_id not in self.sessions:
            return
        frame = event_frame(session_id=session_id, event=event)
        if frame is not None:
            self._put(frame)

    def _put(self, frame: dict) -> None:
        """入队；已经积压了就不再收。

        举例：connection._put({"type": "ready", ...})
        """
        if self.overflowed:
            return
        try:
            self.outbox.put_nowait(frame)
        except asyncio.QueueFull:
            self.overflowed = True


class Hub:
    """按 session_id 把事件分发给看着它的流。

    举例：hub = Hub(events=bus, logger=logger)
    """

    def __init__(self, *, events: EventBus, logger: Logger) -> None:
        """订阅全部会话事件。

        举例：Hub(events=container.ports.events, logger=container.ports.logger)
        """
        # 结构化日志；流积压时留一条。
        #
        # 举例：StdLogger("INFO")
        self._logger = logger

        # session_id -> 正在听它的流。
        #
        # 举例：{"sess_x": {Connection(), Connection()}}
        self._listeners: dict[str, set[Connection]] = {}

        # 事件总线的订阅凭据。留着是为了将来能退订（现在 hub 活到进程结束）。
        #
        # 举例：[unsubscribe]
        self._unsubscribes = [events.subscribe(kind, self._dispatch) for kind in EVENT_TYPES]

    def subscribe(self, connection: Connection, *, session_id: str) -> None:
        """记下"这条流要看这个会话"。

        举例：hub.subscribe(connection, session_id="sess_x")
        """
        connection.sessions.add(session_id)
        self._listeners.setdefault(session_id, set()).add(connection)

    def detach(self, connection: Connection) -> None:
        """流结束了：把它从所有会话的听众里摘掉。

        举例：hub.detach(connection)
        """
        for session_id in list(connection.sessions):
            listeners = self._listeners.get(session_id)
            if listeners is None:
                continue
            listeners.discard(connection)
            if not listeners:
                del self._listeners[session_id]
        connection.sessions.clear()

    def listener_count(self, session_id: str) -> int:
        """这个会话现在有几条流在看；测试与排错用。

        举例：hub.listener_count("sess_x") -> 2
        """
        return len(self._listeners.get(session_id, ()))

    def _dispatch(self, event: object) -> None:
        """事件总线的回调。**同步**：只入队，不碰响应。

        举例：bus.publish(TurnStart(...)) -> hub._dispatch(event)
        """
        session_id = getattr(event, "session_id", None)
        if not isinstance(session_id, str):
            return
        for connection in list(self._listeners.get(session_id, ())):
            connection.offer(session_id, event)

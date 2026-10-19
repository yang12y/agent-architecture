"""进程内事件总线的实现。

它承载"给别人看"的事件（比如 SSE 推送），和十个注入位点是两回事：
位点是"往里插逻辑"，事件是"往外报进展"。
"""

from __future__ import annotations

from collections.abc import Callable

from yzy_agent.contracts import Logger

from .logging import NullLogger


class InProcessEventBus:
    """同步分发的进程内事件总线。

    匹配规则：**按事件的确切类型匹配**，不做 isinstance 继承匹配。
    这样做的好处是行为完全可预测——订阅 AssistantDelta 就只会收到 AssistantDelta，
    不会被某个基类事件的子类意外触发。

    举例：bus = InProcessEventBus(); bus.subscribe(AssistantDelta, push); bus.publish(evt)
    """

    def __init__(self, logger: Logger | None = None) -> None:
        """建一条事件总线。

        举例：InProcessEventBus()、InProcessEventBus(StdLogger("INFO"))
        说明：不传 logger 时订阅者的异常只被吞掉不记录。**装配根应当传真的 logger**，
              不然后面 SSE 推送一挂，日志里什么都看不到。
        """
        # 订阅者出错时用来留现场的 logger。
        #
        # 作用：单个订阅者（比如 SSE 推送）抛异常时记一条 warning，否则它静默失效没人发现。
        # 举例：StdLogger("INFO")
        self.logger: Logger = logger if logger is not None else NullLogger()

        self._handlers: dict[type, list[Callable[[object], None]]] = {}

    def subscribe(self, event_type: type, handler: Callable[[object], None]) -> Callable[[], None]:
        """订阅某类事件，返回退订函数。handler就是api/sse/hub.py中的_dispatch函数

        举例：unsubscribe = bus.subscribe(AssistantDelta, push_to_sse); unsubscribe()
        """
        self._handlers.setdefault(event_type, []).append(handler)

        def unsubscribe() -> None:
            handlers = self._handlers.get(event_type)
            if handlers and handler in handlers:
                handlers.remove(handler)

        return unsubscribe

    def publish(self, event: object) -> None:
        """把事件同步分发给所有订阅者。订阅者抛异常不会影响其他订阅者。这个handler就是api/sse/hub.py中的_dispatch函数

        举例：bus.publish(AssistantDelta(trace_id="trace_9c8b7a", text="我"))
        """
        for handler in list(self._handlers.get(type(event), ())):
            try:
                # 将事件封装成帧发送到队列中去
                handler(event)
            # 刻意捕获所有异常：一个订阅者出错不能拖垮整条分发链，否则订阅了同一个事件的
            # 其他订阅者（比如 SSE 推送与审计日志）会跟着一起失效。
            # 但不能静默——记一条 warning 留下现场，否则事件流从哪断的就没人查得出来。
            except Exception as exc:
                # 字段名不能叫 event —— 它和 Logger.warning 的第一个位置参数重名，会撞成 TypeError。
                self.logger.warning(
                    "event.subscriber_failed",
                    event_type=type(event).__name__,
                    handler=getattr(handler, "__qualname__", repr(handler)),
                    error=f"{type(exc).__name__}: {exc}",
                )
                continue

    def subscriber_count(self, event_type: type) -> int:
        """看某类事件当前有几个订阅者，测试与排错用。

        举例：bus.subscriber_count(AssistantDelta) -> 1
        """
        return len(self._handlers.get(event_type, ()))

"""日志与事件总线的测试。"""

from __future__ import annotations

import io

from yzy_agent.infra.observability import (
    InProcessEventBus,
    NullLogger,
    RecordingLogger,
    StdLogger,
)


def test_std_logger_filters_below_threshold() -> None:
    """低于阈值的日志直接丢弃。"""
    stream = io.StringIO()
    logger = StdLogger("WARNING", stream=stream)

    logger.debug("d")
    logger.info("i")
    logger.warning("w")
    logger.error("e")

    text = stream.getvalue()
    assert "w" in text
    assert "e" in text
    assert " d" not in text and "info" not in text


def test_std_logger_writes_event_and_fields() -> None:
    """一行日志包含时刻、级别、事件名与结构化字段。"""
    stream = io.StringIO()
    StdLogger("INFO", stream=stream).info("trace.start", session_id="sess_test", turn=3)

    line = stream.getvalue().strip()
    assert "INFO" in line
    assert "trace.start" in line
    assert "session_id=sess_test" in line
    assert "turn=3" in line


def test_std_logger_unknown_level_falls_back_to_info() -> None:
    """级别名写错时不崩，按 INFO 处理。"""
    stream = io.StringIO()
    StdLogger("VERBOSE", stream=stream).info("x")

    assert "x" in stream.getvalue()


def test_recording_logger_keeps_every_level() -> None:
    """RecordingLogger 把四个级别都记下来，顺序不变。"""
    logger = RecordingLogger()

    logger.debug("a")
    logger.info("b", k=1)
    logger.warning("c")
    logger.error("d")

    assert [record[0] for record in logger.records] == ["DEBUG", "INFO", "WARNING", "ERROR"]
    assert logger.records[1] == ("INFO", "b", {"k": 1})


def test_null_logger_is_silent() -> None:
    """NullLogger 什么都不做，也不报错。"""
    logger = NullLogger()

    logger.debug("a")
    logger.info("b")
    logger.warning("c")
    logger.error("d")


class _Ping:
    """测试用事件类型。"""

    def __init__(self, text: str) -> None:
        self.text = text


class _PingChild(_Ping):
    """_Ping 的子类，用来验证"按确切类型匹配"。"""


def test_event_bus_delivers_to_subscriber() -> None:
    """订阅后能收到同类事件。"""
    bus = InProcessEventBus()
    seen: list[str] = []
    bus.subscribe(_Ping, lambda e: seen.append(e.text))  # type: ignore[attr-defined]

    bus.publish(_Ping("hello"))

    assert seen == ["hello"]


def test_event_bus_unsubscribe_stops_delivery() -> None:
    """退订之后不再收到事件。"""
    bus = InProcessEventBus()
    seen: list[str] = []
    unsubscribe = bus.subscribe(_Ping, lambda e: seen.append(e.text))  # type: ignore[attr-defined]

    bus.publish(_Ping("first"))
    unsubscribe()
    bus.publish(_Ping("second"))

    assert seen == ["first"]
    assert bus.subscriber_count(_Ping) == 0


def test_event_bus_matches_exact_type_only() -> None:
    """只按确切类型匹配：订阅父类不会收到子类事件，行为可预测。"""
    bus = InProcessEventBus()
    seen: list[str] = []
    bus.subscribe(_Ping, lambda e: seen.append(e.text))  # type: ignore[attr-defined]

    bus.publish(_PingChild("child"))

    assert seen == []


def test_event_bus_isolates_failing_subscriber() -> None:
    """一个订阅者抛异常，不影响其他订阅者收到事件。"""
    bus = InProcessEventBus()
    seen: list[str] = []

    def boom(event: object) -> None:
        raise RuntimeError("订阅者自身出错")

    bus.subscribe(_Ping, boom)
    bus.subscribe(_Ping, lambda e: seen.append(e.text))  # type: ignore[attr-defined]

    bus.publish(_Ping("still delivered"))

    assert seen == ["still delivered"]


def test_event_bus_logs_a_warning_when_a_subscriber_fails() -> None:
    """订阅者抛异常不能静默：至少要在日志里留下事件类型、订阅者与原因。

    不记的话，SSE 推送这类订阅者一旦失效，现象是"前端忽然不更新了"，
    而日志里干干净净，无从查起。
    """
    logger = RecordingLogger()
    bus = InProcessEventBus(logger)

    def boom(event: object) -> None:
        raise RuntimeError("推送失败")

    bus.subscribe(_Ping, boom)

    bus.publish(_Ping("hello"))

    level, event_name, fields = logger.records[-1]
    assert level == "WARNING"
    assert event_name == "event.subscriber_failed"
    assert fields["event_type"] == "_Ping"
    assert "boom" in str(fields["handler"])
    assert fields["error"] == "RuntimeError: 推送失败"

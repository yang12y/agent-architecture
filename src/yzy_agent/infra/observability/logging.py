"""结构化日志的实现。

日志的用法是"事件名 + 字段"，不是拼字符串：

    logger.info("tool.start", name="read", trace_id="trace_9c8b7a")

这样 trace_id / turn_index 这些字段可以被检索，而不是埋在文本里。
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from typing import TextIO

# 级别名 -> 数值。数值越大越严重，用于过滤。
#
# 举例：LEVELS["INFO"] -> 20
LEVELS: dict[str, int] = {
    "DEBUG": 10,
    "INFO": 20,
    "WARNING": 30,
    "ERROR": 40,
    "CRITICAL": 50,
}


class StdLogger:
    """把日志按"一行一条"写到流的 Logger 实现。

    举例：logger = StdLogger("INFO"); logger.info("trace.start", session_id="sess_ab12")
    """

    def __init__(self, level: str = "INFO", stream: TextIO | None = None) -> None:
        # 最低输出级别。低于它的日志直接丢弃。
        #
        # 举例："INFO"
        self.level = level.upper()

        # 输出目标流。默认写 stderr，避免污染 stdout 的 SSE 输出。
        #
        # 举例：sys.stderr
        self.stream = sys.stderr if stream is None else stream

        self._threshold = LEVELS.get(self.level, LEVELS["INFO"])

    def debug(self, event: str, **fields: object) -> None:
        """见 contracts.Logger.debug。

        举例：logger.debug("site.start", site="turn:before")
        """
        self._emit("DEBUG", event, fields)

    def info(self, event: str, **fields: object) -> None:
        """见 contracts.Logger.info。

        举例：logger.info("trace.start", session_id="sess_ab12")
        """
        self._emit("INFO", event, fields)

    def warning(self, event: str, **fields: object) -> None:
        """见 contracts.Logger.warning。

        举例：logger.warning("retry", attempt=1, tool="grep")
        """
        self._emit("WARNING", event, fields)

    def error(self, event: str, **fields: object) -> None:
        """见 contracts.Logger.error。

        举例：logger.error("checkpoint.flush_failed", seq=42)
        """
        self._emit("ERROR", event, fields)

    def _emit(self, level: str, event: str, fields: dict[str, object]) -> None:
        """按阈值过滤后写出一行。

        举例：_emit("INFO", "trace.start", {"session_id": "sess_ab12"})
              -> "2025-01-01T12:00:00+00:00 INFO trace.start session_id=sess_ab12"
        """
        if LEVELS[level] < self._threshold:
            return
        stamp = datetime.now(UTC).isoformat(timespec="seconds")
        tail = " ".join(f"{key}={value}" for key, value in fields.items())
        line = f"{stamp} {level:<8} {event}"
        if tail:
            line = f"{line} {tail}"
        print(line, file=self.stream, flush=True)


class NullLogger:
    """什么都不做的 Logger。测试里想静音时用它。

    举例：logger = NullLogger(); logger.error("x")  # 无输出
    """

    def debug(self, event: str, **fields: object) -> None: ...
    def info(self, event: str, **fields: object) -> None: ...
    def warning(self, event: str, **fields: object) -> None: ...
    def error(self, event: str, **fields: object) -> None: ...


class RecordingLogger:
    """把日志收进列表的 Logger，测试里用来断言"某条日志确实打出来了"。

    举例：logger = RecordingLogger(); logger.info("trace.start")
          logger.records -> [("INFO", "trace.start", {})]
    """

    def __init__(self) -> None:
        # 按顺序记下的 (级别, 事件名, 字段) 三元组。
        #
        # 举例：[("INFO", "trace.start", {"session_id": "sess_ab12"})]
        self.records: list[tuple[str, str, dict[str, object]]] = []

    def debug(self, event: str, **fields: object) -> None:
        self.records.append(("DEBUG", event, fields))

    def info(self, event: str, **fields: object) -> None:
        self.records.append(("INFO", event, fields))

    def warning(self, event: str, **fields: object) -> None:
        self.records.append(("WARNING", event, fields))

    def error(self, event: str, **fields: object) -> None:
        self.records.append(("ERROR", event, fields))

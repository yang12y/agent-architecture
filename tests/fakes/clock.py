"""确定性的时钟假件。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

# 默认起始时刻，固定值让断言可以写死。
#
# 举例：datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
DEFAULT_START = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)


class FakeClock:
    """时间完全由测试控制的 Clock 实现。

    举例：clock = FakeClock(); clock.advance(1.5); clock.monotonic() -> 1.5
    """

    def __init__(self, start: datetime = DEFAULT_START) -> None:
        self._now = start
        self._monotonic = 0.0

    def now(self) -> datetime:
        """当前（假的）时刻。

        举例：clock.now() -> datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
        """
        return self._now

    def monotonic(self) -> float:
        """当前（假的）单调秒数。

        举例：clock.monotonic() -> 0.0
        """
        return self._monotonic

    def advance(self, seconds: float) -> None:
        """把时间往前推，now() 和 monotonic() 同步前进。

        举例：clock.advance(2.5) -> monotonic 变成 2.5，now 变成 12:00:02.5
        """
        self._now = self._now + timedelta(seconds=seconds)
        self._monotonic += seconds

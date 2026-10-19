"""时间来源的实现。"""

from __future__ import annotations

import time
from datetime import UTC, datetime


class SystemClock:
    """读真实系统时间的 Clock 实现。

    举例：clock = SystemClock(); clock.now()
    """

    def now(self) -> datetime:
        """当前 UTC 时刻。

        举例：clock.now() -> datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
        """
        return datetime.now(UTC)

    def monotonic(self) -> float:
        """单调递增秒数，用来算耗时。

        举例：clock.monotonic() -> 1735689.123
        """
        return time.monotonic()

"""全局 fixture。

只保留这一个 conftest：各模块目录不再放 conftest，避免 fixture 来源分散。
"""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from fakes import (
    FakeClock,
    SequentialIds,
    StubSession,
    make_ports,
    make_runtime_state,
    make_trace_context,
)
from yzy_agent.contracts import Ports, TraceContext
from yzy_agent.infra.observability import InProcessEventBus, RecordingLogger


@pytest.fixture
def fake_clock() -> FakeClock:
    """确定性的时钟，时间不会自己走。"""
    return FakeClock()


@pytest.fixture
def seq_ids() -> SequentialIds:
    """可预测的 id 生成器，同一前缀内递增。"""
    return SequentialIds()


@pytest.fixture
def recording_logger() -> RecordingLogger:
    """把日志收进列表的日志实现。"""
    return RecordingLogger()


@pytest.fixture
def event_bus() -> InProcessEventBus:
    """进程内事件总线。"""
    return InProcessEventBus()


@pytest.fixture
def ports(
    fake_clock: FakeClock,
    seq_ids: SequentialIds,
    recording_logger: RecordingLogger,
    event_bus: InProcessEventBus,
) -> Ports:
    """五个基础端口齐备的 Ports。"""
    return make_ports(clock=fake_clock, ids=seq_ids, logger=recording_logger, events=event_bus)


@pytest.fixture
def trace_ctx(ports: Ports) -> TraceContext:
    """一个可用的 TraceContext，state 全是默认值。"""
    return make_trace_context(state=make_runtime_state(), session=StubSession(), ports=ports)


# ---------------------------------------------------------------------------
# 落盘测试用的临时目录
# ---------------------------------------------------------------------------

# 临时目录的根。**建在项目内的 .tmp 下**，不进系统临时目录 —— 跑完还能翻。
#
# 举例：Path(".tmp/tests")
SCRATCH_ROOT = Path(".tmp") / "tests"


def _scratch_path(node_id: str) -> Path:
    """给一个用例算确定性的目录名。

    名字必须是确定的：每次跑用例都指到同一个地方，进来先清空，才谈得上"干净"。
    用例 id 可能带中文，所以只留 ASCII 字母数字下划线，再拼一段短哈希防撞。

    举例：_scratch_path("tests/test_x.py::test_y")
          -> Path(".tmp/tests/test_x__test_y-3f9a1c2d")
    """
    stem, _, name = node_id.partition("::")
    readable = "_".join(part for part in (Path(stem).stem, name) if part)
    safe = "".join(ch if ch.isascii() and (ch.isalnum() or ch in "_-") else "_" for ch in readable)
    digest = hashlib.sha1(node_id.encode("utf-8")).hexdigest()[:8]
    return SCRATCH_ROOT / f"{safe[:60]}-{digest}"


@pytest.fixture
def scratch_dir(request: pytest.FixtureRequest) -> Iterator[Path]:
    """一个属于本用例的空目录；进来先清空，出去再清一次。

    落盘的存储实现拿它当会话目录的根，所以每个用例拿到的都是干净的盘。

    举例：store = FileSessionStore(scratch_dir / "sessions")
    """
    path = _scratch_path(request.node.nodeid)
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)

"""会话日志的测试。"""

from __future__ import annotations

from fakes import SequentialIds, make_entry, user_message
from yzy_agent.contracts import SessionEntryType
from yzy_agent.modules.session import SessionLog


def _log(entries=()) -> SessionLog:
    """造一个用固定 id 生成器的日志，断言才能写死。

    举例：_log() -> SessionLog(ids=SequentialIds())
    """
    return SessionLog(entries, ids=SequentialIds())


def test_seq_starts_at_one_and_increases_by_one() -> None:
    """seq 决定重放顺序，必须从 1 开始会话内全局递增。"""
    log = _log()

    first = log.append(
        trace_id="t", type=SessionEntryType.MESSAGE, payload=user_message("a"), now=None
    )  # type: ignore[arg-type]
    second = log.append(
        trace_id="t", type=SessionEntryType.MESSAGE, payload=user_message("b"), now=None
    )  # type: ignore[arg-type]

    assert (first.seq, second.seq) == (1, 2)


def test_entry_id_comes_from_the_id_generator() -> None:
    """entry_id 由端口分配，日志不自己编。"""
    log = _log()

    entry = log.append(
        trace_id="t", type=SessionEntryType.MESSAGE, payload=user_message("a"), now=None
    )  # type: ignore[arg-type]

    assert entry.entry_id.startswith("e_")


def test_resumed_log_continues_the_seq() -> None:
    """从磁盘恢复出来的日志接着写，seq 不能从头开始，否则重放顺序会乱。"""
    loaded = [make_entry(1, user_message("旧")), make_entry(2, user_message("旧2"))]
    log = SessionLog(loaded, ids=SequentialIds())

    entry = log.append(
        trace_id="t", type=SessionEntryType.MESSAGE, payload=user_message("新"), now=None
    )  # type: ignore[arg-type]

    assert entry.seq == 3


def test_find_and_index_of() -> None:
    """按 entry_id 找条目与找下标 —— 游标与 compaction.entry_ids 都靠它。"""
    log = _log(
        [
            make_entry(1, user_message("a"), entry_id="e_a"),
            make_entry(2, user_message("b"), entry_id="e_b"),
        ]
    )

    assert log.find("e_b") is not None
    assert log.index_of("e_b") == 1
    assert log.find("e_不存在") is None
    assert log.index_of("e_不存在") is None


def test_last_returns_the_latest_of_that_type_only() -> None:
    """last 取某一类里 seq 最大的那条，别的类型不算。"""
    entries = [
        make_entry(1, user_message("a")),
        make_entry(2, user_message("b")),
        make_entry(3, user_message("c")),
    ]
    log = _log(entries)

    assert log.last(SessionEntryType.MESSAGE).seq == 3  # type: ignore[union-attr]
    assert log.last(SessionEntryType.COMPACTION) is None


def test_add_bumps_next_seq_past_the_given_entry() -> None:
    """手工塞条目的场景（恢复、分叉）：后面自动分配的 seq 不能跟它撞上。"""
    log = _log()
    log.add(make_entry(7, user_message("手塞的")))

    entry = log.append(
        trace_id="t", type=SessionEntryType.MESSAGE, payload=user_message("新"), now=None
    )  # type: ignore[arg-type]

    assert entry.seq == 8


def test_entries_returns_a_snapshot() -> None:
    """给出去的是新列表，调用方改它不影响日志本身。"""
    log = _log([make_entry(1, user_message("a"))])

    snapshot = log.entries()
    snapshot.clear()

    assert len(log) == 1

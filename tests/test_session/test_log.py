"""会话日志的测试。"""

from __future__ import annotations

from agent_frame.contracts import SessionEntry, SessionEntryType
from agent_frame.modules.session import SessionLog, fork_prefix
from fakes import (
    SequentialIds,
    assistant_message,
    make_entry,
    trace_end_payload,
    trace_start_payload,
    user_message,
)


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


# ---- 分叉点：一段日志从哪一条截断 ----


def _two_turns() -> list[SessionEntry]:
    """两轮完整对话：第一轮的用户消息在 seq=2，第二轮在 seq=6。

    举例：fork_prefix(_two_turns(), 2) -> 前四条
    """
    return [
        make_entry(1, trace_start_payload(), trace_id="trace_1"),
        make_entry(2, user_message("第一种问法"), trace_id="trace_1"),
        make_entry(3, assistant_message("第一种问法的回答"), trace_id="trace_1"),
        make_entry(4, trace_end_payload(), trace_id="trace_1"),
        make_entry(5, trace_start_payload(), trace_id="trace_2"),
        make_entry(6, user_message("第二种问法"), trace_id="trace_2"),
        make_entry(7, assistant_message("第二种问法的回答"), trace_id="trace_2"),
        make_entry(8, trace_end_payload(), trace_id="trace_2"),
    ]


def test_fork_prefix_cuts_at_the_trace_end_of_that_turn() -> None:
    """切到这一轮的 trace_end 为止 —— 下一轮的 trace_start 不能跟过去。"""
    prefix = fork_prefix(_two_turns(), 2)

    assert prefix is not None
    assert [entry.seq for entry in prefix] == [1, 2, 3, 4]
    assert prefix[-1].type is SessionEntryType.TRACE_END


def test_fork_prefix_on_the_last_turn_takes_everything() -> None:
    """最后一轮之后没有下一条用户消息了：整份都是前缀，等于克隆。"""
    entries = _two_turns()

    prefix = fork_prefix(entries, 6)

    assert prefix is not None
    assert len(prefix) == len(entries)


def test_fork_prefix_needs_a_user_message() -> None:
    """不是用户消息的 seq 不能当分叉点（模型回答、trace_start 都不行）。"""
    assert fork_prefix(_two_turns(), 1) is None
    assert fork_prefix(_two_turns(), 3) is None


def test_fork_prefix_needs_a_closed_turn() -> None:
    """这一轮还没收尾（没有 trace_end）就分不出来 —— 截在半截 trace 上没有意义。"""
    entries = [
        make_entry(1, trace_start_payload(), trace_id="trace_1"),
        make_entry(2, user_message("还没答完"), trace_id="trace_1"),
    ]

    assert fork_prefix(entries, 2) is None


def test_fork_prefix_of_an_unknown_seq_is_none() -> None:
    """seq 不存在、日志是空的。"""
    assert fork_prefix(_two_turns(), 99) is None
    assert fork_prefix([], 1) is None

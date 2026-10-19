"""落盘屏障的测试。"""

from __future__ import annotations

from pathlib import Path

from agent_architecture.infra.storage import FileSessionStore
from agent_architecture.modules.checkpoint.barriers import ensure_persisted
from agent_architecture.modules.session import Session
from fakes import USER, SequentialIds, make_session_meta
from test_checkpoint.traces import broken_log

SESSION_ID = "sess_ab12"


def _store(scratch_dir: Path) -> FileSessionStore:
    """会话目录开在临时目录下的存储。

    举例：_store(scratch_dir).root -> scratch_dir/"sessions"
    """
    return FileSessionStore(scratch_dir / "sessions")


def _session(entries=()) -> Session:
    """一个装了若干条目的会话。

    举例：_session(broken_log()).entries()[-1].seq -> 5
    """
    return Session(make_session_meta(SESSION_ID), entries, ids=SequentialIds())


async def test_it_pushes_the_whole_log_to_disk(scratch_dir: Path) -> None:
    """水位是 0，屏障就该把整份日志推出去。"""
    store = _store(scratch_dir)
    await store.save_meta(make_session_meta(SESSION_ID))
    session = _session(broken_log())

    watermark = await ensure_persisted(store, session, 0, reason="llm:after")

    loaded = await store.load(SESSION_ID, USER)
    assert watermark == session.entries()[-1].seq
    assert [entry.seq for entry in loaded[1]] == [entry.seq for entry in session.entries()]  # type: ignore[index]


async def test_it_returns_the_watermark_unchanged_when_nothing_is_new(scratch_dir: Path) -> None:
    """第二次屏障没有新条目 —— 水位不动，也不该再写一遍文件。"""
    store = _store(scratch_dir)
    await store.save_meta(make_session_meta(SESSION_ID))
    session = _session(broken_log())
    first = await ensure_persisted(store, session, 0, reason="llm:after")

    second = await ensure_persisted(store, session, first, reason="tool:before")

    assert second == first
    assert store.pending_count(SESSION_ID) == 0


async def test_an_empty_log_is_a_no_op(scratch_dir: Path) -> None:
    """空日志没什么可推的，水位保持原样。"""
    store = _store(scratch_dir)

    assert await ensure_persisted(store, _session(), 0, reason="llm:before") == 0
    assert store.pending_count(SESSION_ID) == 0


async def test_it_only_pushes_what_is_past_the_watermark(scratch_dir: Path) -> None:
    """幂等的关键：给同一个水位推两遍，盘上不会多出重复的条目。"""
    store = _store(scratch_dir)
    await store.save_meta(make_session_meta(SESSION_ID))
    session = _session(broken_log())
    await ensure_persisted(store, session, 0, reason="llm:after")

    await ensure_persisted(store, session, 0, reason="重推一遍")

    loaded = await store.load(SESSION_ID, USER)
    assert len(loaded[1]) == len(session.entries())  # type: ignore[index]

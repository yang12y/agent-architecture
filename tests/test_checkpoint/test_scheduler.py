"""落盘调度的测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from fakes import START, USER, SequentialIds, make_entry, make_session_meta
from test_checkpoint.traces import broken_log
from yzy_agent.contracts import CompactionEntryPayload, FlushError
from yzy_agent.infra.storage import FileSessionStore
from yzy_agent.modules.checkpoint import Checkpoint
from yzy_agent.modules.session import Session

SESSION_ID = "sess_ab12"


async def _ready(scratch_dir: Path, entries=None) -> tuple[FileSessionStore, Session]:
    """落好 meta 的存储 + 一份日志（默认是崩过的那份）。

    举例：store, session = await _ready(scratch_dir)
    """
    store = FileSessionStore(scratch_dir / "sessions")
    await store.save_meta(make_session_meta(SESSION_ID))
    log = broken_log() if entries is None else entries
    return store, Session(make_session_meta(SESSION_ID), log, ids=SequentialIds())


async def _on_disk(scratch_dir: Path):
    """从盘上重新读回来的那份会话 —— 这才是"屏障到底落了什么"的判据。

    举例：(await _on_disk(scratch_dir))[0].updated_at
    """
    return await FileSessionStore(scratch_dir / "sessions").load(SESSION_ID, USER)


def test_the_watermark_starts_at_zero(scratch_dir: Path) -> None:
    """新调度器什么都不知道，从 0 开始 —— 那只是跳过空跑的缓存，不是事实来源。"""
    assert Checkpoint(FileSessionStore(scratch_dir / "sessions")).persisted_upto(SESSION_ID) == 0


async def test_ensure_pushes_the_log_and_records_the_watermark(scratch_dir: Path) -> None:
    """屏障推完之后，水位跟着走到日志末尾。"""
    store, session = await _ready(scratch_dir)
    checkpoint = Checkpoint(store)

    await checkpoint.ensure(session, reason="llm:after")

    assert checkpoint.persisted_upto(SESSION_ID) == session.entries()[-1].seq


async def test_ensure_is_a_no_op_when_nothing_is_new(scratch_dir: Path) -> None:
    """已经是末尾了，第二次屏障不写盘。"""
    store, session = await _ready(scratch_dir)
    checkpoint = Checkpoint(store)
    await checkpoint.ensure(session, reason="llm:after")

    await checkpoint.ensure(session, reason="tool:before")

    assert store.pending_count(SESSION_ID) == 0


async def test_a_lost_watermark_does_not_duplicate_entries(scratch_dir: Path) -> None:
    """重新装配出一个调度器（缓存没了），重推一遍也不会写出重复行 —— 幂等在存储那一侧。"""
    store, session = await _ready(scratch_dir)
    await Checkpoint(store).ensure(session, reason="llm:after")

    await Checkpoint(store).ensure(session, reason="llm:after")

    loaded = await store.load(SESSION_ID, USER)
    assert len(loaded[1]) == len(session.entries())  # type: ignore[index]


async def test_a_cold_store_does_not_duplicate_entries(scratch_dir: Path) -> None:
    """连存储都是新的（真的重启过）：水位从盘上读回来，照样不重复。"""
    store, session = await _ready(scratch_dir)
    await Checkpoint(store).ensure(session, reason="llm:after")

    cold = FileSessionStore(scratch_dir / "sessions")
    await Checkpoint(cold).ensure(session, reason="llm:after")

    loaded = await cold.load(SESSION_ID, USER)
    assert len(loaded[1]) == len(session.entries())  # type: ignore[index]


async def test_ensure_refreshes_the_meta_on_disk(scratch_dir: Path) -> None:
    """屏障落的不只是条目 —— 位点 1/10 刷的 updated_at 也要跟着落下去。"""
    store, session = await _ready(scratch_dir)
    session.meta.updated_at = START
    session.set_title("重构 session 模块")

    await Checkpoint(store).ensure(session, reason="trace:end")

    meta = (await _on_disk(scratch_dir))[0]  # type: ignore[index]
    assert meta.updated_at == START
    assert meta.title == "重构 session 模块"


async def test_ensure_carries_the_cursor_to_disk(scratch_dir: Path) -> None:
    """日志里有压缩时，投影起点会跟着 meta 一起落盘 —— 不需要为它单开一条路。"""
    log = [*broken_log(), make_entry(6, CompactionEntryPayload(summary="前情提要", entry_ids=[]))]
    store, session = await _ready(scratch_dir, log)
    assert session.meta.context_cursor == "e_0006"  # Session 构造时 rebuild 算出来的

    await Checkpoint(store).ensure(session, reason="llm:before")

    meta = (await _on_disk(scratch_dir))[0]  # type: ignore[index]
    assert meta.context_cursor == "e_0006"


async def test_a_failed_meta_write_leaves_the_watermark_alone(
    scratch_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """写 meta 失败时水位不许动 —— 否则这一段再也没人补。"""
    store, session = await _ready(scratch_dir)
    checkpoint = Checkpoint(store)

    async def boom(meta) -> None:
        raise FlushError("元信息写不进去")

    monkeypatch.setattr(store, "save_meta", boom)

    with pytest.raises(FlushError):
        await checkpoint.ensure(session, reason="llm:after")

    assert checkpoint.persisted_upto(SESSION_ID) == 0


async def test_a_failed_flush_leaves_the_watermark_alone(scratch_dir: Path) -> None:
    """落盘失败时水位不许动 —— 下一次屏障得把这一段重推一遍。"""
    store, session = await _ready(scratch_dir)
    checkpoint = Checkpoint(store)
    store.fail_next_flush()

    with pytest.raises(FlushError):
        await checkpoint.ensure(session, reason="llm:after")

    assert checkpoint.persisted_upto(SESSION_ID) == 0


async def test_a_retry_after_a_failure_still_lands(scratch_dir: Path) -> None:
    """失败之后再推一次，这一批照样能落下去。"""
    store, session = await _ready(scratch_dir)
    checkpoint = Checkpoint(store)
    store.fail_next_flush()
    with pytest.raises(FlushError):
        await checkpoint.ensure(session, reason="llm:after")

    await checkpoint.ensure(session, reason="tool:before")

    loaded = await store.load(SESSION_ID, USER)
    assert len(loaded[1]) == len(session.entries())  # type: ignore[index]

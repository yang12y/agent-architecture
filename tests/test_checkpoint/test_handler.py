"""checkpoint 位点 handler 的测试。"""

from __future__ import annotations

from pathlib import Path

from agent_architecture.contracts import (
    LlmAfterPayload,
    Permission,
    Site,
    TraceBeforePayload,
    TraceStatus,
    TraceTrigger,
)
from agent_architecture.infra.storage import FileSessionStore
from agent_architecture.modules.agent_loop import HandlerRegistry
from agent_architecture.modules.checkpoint import (
    Checkpoint,
    TailKind,
    build_handlers,
    classify_tail,
)
from agent_architecture.modules.checkpoint.barriers import BARRIER_SITES
from agent_architecture.modules.session import Session
from fakes import (
    USER,
    SequentialIds,
    assistant_message,
    make_session_meta,
    make_site_context,
    make_trace_context,
    user_message,
)
from test_checkpoint.traces import broken_log

SESSION_ID = "sess_ab12"


async def _ready(scratch_dir: Path, entries=None) -> tuple[FileSessionStore, Session]:
    """落好 meta 的存储 + 一份崩过的日志。

    举例：store, session = await _ready(scratch_dir)
    """
    store = FileSessionStore(scratch_dir / "sessions")
    await store.save_meta(make_session_meta(SESSION_ID))
    meta = make_session_meta(SESSION_ID)
    return store, Session(meta, broken_log() if entries is None else entries, ids=SequentialIds())


def _handlers(checkpoint: Checkpoint, **kwargs) -> dict:
    """按 id 索引 checkpoint 的全部 handler。

    举例：_handlers(checkpoint)["checkpoint.trace_before"]
    """
    return {handler.id: handler for handler in build_handlers(checkpoint, **kwargs)}


def _trace_before_ctx(session: Session):
    """位点 1 的上下文。

    举例：_trace_before_ctx(session)
    """
    payload = TraceBeforePayload(
        session_id=session.meta.session_id,
        trigger=TraceTrigger.USER,
        parent_trace_id=None,
        user_message=user_message("再来一次"),
        model="fake-model",
        permission=Permission.EDITABLE,
    )
    return make_site_context(Site.TRACE_BEFORE, payload, trace=make_trace_context(session=session))


def test_build_handlers_covers_recovery_and_every_barrier(scratch_dir: Path) -> None:
    """一个恢复判据，加五个落盘屏障，一个不多一个不少。"""
    handlers = _handlers(Checkpoint(FileSessionStore(scratch_dir / "sessions")))

    assert set(handlers) == {"checkpoint.trace_before"} | {
        f"checkpoint.{site.name.lower()}" for site in BARRIER_SITES
    }


def test_priorities_land_in_the_contract_bands(scratch_dir: Path) -> None:
    """只有 50 与 900 两个取值 —— 别的值过不了 HandlerRegistry 的段位校验。"""
    registry = HandlerRegistry(build_handlers(Checkpoint(FileSessionStore(scratch_dir / "s"))))

    assert {handler.priority for handler in registry.handlers()} == {50, 900}


def test_the_barrier_runs_after_every_dimension_handler(scratch_dir: Path) -> None:
    """屏障必须排在最后：排在它后面的 handler 改了什么，都落不下去。"""
    registry = HandlerRegistry(build_handlers(Checkpoint(FileSessionStore(scratch_dir / "s"))))

    for site in BARRIER_SITES:
        last = registry.for_site(site)[-1]
        assert last.id == f"checkpoint.{site.name.lower()}"


async def test_a_clean_tail_is_left_alone(scratch_dir: Path) -> None:
    """正常收尾的日志不该被动。"""
    store, session = await _ready(scratch_dir, broken_log(end=TraceStatus.COMPLETED))
    before = len(session.entries())

    await _handlers(Checkpoint(store))["checkpoint.trace_before"].run(_trace_before_ctx(session))

    assert len(session.entries()) == before


async def test_a_dangling_tail_is_repaired_and_persisted(scratch_dir: Path) -> None:
    """崩过的日志：补上欠的结果与收尾，而且立刻就在盘上。"""
    store, session = await _ready(scratch_dir)

    await _handlers(Checkpoint(store))["checkpoint.trace_before"].run(_trace_before_ctx(session))

    assert classify_tail(session.entries()).kind is TailKind.ABORTED
    loaded = await store.load(SESSION_ID, USER)
    assert len(loaded[1]) == len(session.entries())  # type: ignore[index]


async def test_a_barrier_handler_pushes_the_log(scratch_dir: Path) -> None:
    """屏障 handler 干的事就是"把日志推到盘上"。"""
    store, session = await _ready(scratch_dir)
    payload = LlmAfterPayload(assistant_message=assistant_message("好的"))
    ctx = make_site_context(Site.LLM_AFTER, payload, trace=make_trace_context(session=session))

    await _handlers(Checkpoint(store))["checkpoint.llm_after"].run(ctx)

    loaded = await store.load(SESSION_ID, USER)
    assert [entry.seq for entry in loaded[1]] == [  # type: ignore[index]
        entry.seq for entry in session.entries()
    ]

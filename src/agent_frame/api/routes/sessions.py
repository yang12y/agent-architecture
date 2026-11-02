"""会话的建、列、查。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from agent_frame.composition import Container

from ..deps import get_container
from ..errors import ApiError, bad_turn, session_busy, session_not_found
from ..schemas import (
    DEFAULT_PAGE_SIZE,
    CreateSessionIn,
    SessionDeletedOut,
    SessionDetailOut,
    SessionListOut,
    SessionOut,
    TraceListOut,
    charts_of_page,
    page_messages,
)
from ..stats import session_stats
from ..traces import TRACE_MAX, trace_outlines
from ..turns import turn_outline

router = APIRouter(tags=["sessions"])


@router.post("/sessions", status_code=201, response_model=SessionOut)
async def create_session(
    user_id: str,
    body: CreateSessionIn | None = None,
    container: Container = Depends(get_container),
) -> SessionOut:
    """新建一个会话。body 可以整个省略。

    举例：POST /sessions?user_id=u1 -> 201 {"session_id": "sess_x", ...}
    """
    live = await container.sessions.create(
        user_id=user_id, title=body.title if body is not None else None
    )
    return SessionOut.from_meta(live.session.meta)


@router.post("/sessions/{session_id}/fork", status_code=201, response_model=SessionOut)
async def fork_session(
    session_id: str,
    user_id: str,
    turn_seq: Annotated[int, Query(ge=1)],
    container: Container = Depends(get_container),
) -> SessionOut:
    """把到 turn_seq 那一轮结束为止的对话复制成一个**新会话**，原会话一条都不动。

    turn_seq 是那一轮用户消息的 seq —— 大纲里每行都有，点哪行就传哪行的 seq。

    **跑着的时候不许分叉**（409）：那一刻这一轮还没收尾，没有 trace_end 可截。
    截在 trace_end 上是硬要求，否则复制出来的日志会被恢复侧当成崩在 trace 中途。

    新会话只在 parent_session_id 上记着来源，其余完全独立：不共享日志、不级联删除、
    父会话被删之后它照常能打开。父会话的配置（workspace / model / permission /
    工具结果截断尺度）跟着抄，因为分叉里那段日志就是从父会话抄来的。

    举例：POST /sessions/sess_x/fork?user_id=u1&turn_seq=42 -> 201 {"session_id": "sess_y", ...}
    """
    live = await container.sessions.get_or_load(session_id, user_id)
    if live is None:
        raise session_not_found(session_id)
    if live.is_running:
        raise session_busy(session_id)

    forked = await container.sessions.fork(live, turn_seq=turn_seq)
    if forked is None:
        raise bad_turn(turn_seq)
    return SessionOut.from_meta(forked.session.meta)


@router.get("/sessions", response_model=SessionListOut)
async def list_sessions(
    user_id: str, container: Container = Depends(get_container)
) -> SessionListOut:
    """列出这个用户的会话。别人的一条都不出现。

    举例：GET /sessions?user_id=u1 -> {"sessions": [...]}
    """
    metas = await container.sessions.stored_metas(user_id)
    return SessionListOut(sessions=[SessionOut.from_meta(meta) for meta in metas])


@router.get("/sessions/{session_id}", response_model=SessionDetailOut)
async def get_session(
    session_id: str,
    user_id: str,
    limit: Annotated[int, Query(ge=1, le=500)] = DEFAULT_PAGE_SIZE,
    before_seq: Annotated[int | None, Query()] = None,
    after_seq: Annotated[int | None, Query()] = None,
    container: Container = Depends(get_container),
) -> SessionDetailOut:
    """会话详情 + 一页消息 + 统计。**重连时拉的就是它。**

    消息是**分页**的：默认给最后 limit 条（has_more 说明还有没有更早的）。
    before_seq 往前翻（给已加载的最早那条的 entry_seq），after_seq 往后补
    （给最新那条的，增量刷新只回新增的）。

    统计是**现数**的：遍历条目数 trace_start、把 trace_end 里的轮数与上下文量加起来。
    没有计数器字段，所以也不会出现"计数器与日志对不上"这种事。

    图（charts）按 seq 窗口跟着这一页走，轮次大纲（turns）每次都是整份给 —— 前端拿到就整体替换。

    举例：GET /sessions/sess_x?user_id=u1&limit=50 -> {"messages": [...], "has_more": true}
    """
    if before_seq is not None and after_seq is not None:
        raise ApiError(400, "invalid_cursor", "before_seq 与 after_seq 不能同时给")

    live = await container.sessions.get_or_load(session_id, user_id)
    if live is None:
        raise session_not_found(session_id)

    entries = live.session.entries()
    page, has_more = page_messages(entries, limit=limit, before_seq=before_seq, after_seq=after_seq)
    stats = session_stats(entries, max_tokens=container.config.max_tokens)
    return SessionDetailOut.from_live(
        live,
        stats=stats,
        entries=page,
        has_more=has_more,
        charts=charts_of_page(entries, page, before_seq=before_seq, after_seq=after_seq),
        turns=turn_outline(entries),
        latest_entry_seq=entries[-1].seq if entries else 0,
    )


@router.get("/sessions/{session_id}/traces", response_model=TraceListOut)
async def list_traces(
    session_id: str,
    user_id: str,
    limit: Annotated[int, Query(ge=1, le=100)] = TRACE_MAX,
    container: Container = Depends(get_container),
) -> TraceListOut:
    """这个会话跑过的 trace，最新的在前 —— 每条带它的耗时瀑布。

    **只收过尾的**：正在跑的那次还没有 trace_end，耗时没有答案。

    每一格都是收尾时记进 trace_end 条目的：一个位点一格、一次模型调用一格，
    时间都相对这次 trace 的起点。所以它不依赖任何事件，重开、换机器都一样。

    举例：GET /sessions/sess_x/traces?user_id=u1 -> {"traces": [{"duration_ms": 8421.5, ...}]}
    """
    live = await container.sessions.get_or_load(session_id, user_id)
    if live is None:
        raise session_not_found(session_id)

    return TraceListOut(traces=trace_outlines(live.session.entries(), limit=limit))


@router.delete("/sessions/{session_id}", response_model=SessionDeletedOut)
async def delete_session(
    session_id: str,
    user_id: str,
    container: Container = Depends(get_container),
) -> SessionDeletedOut:
    """删掉一个会话，**消息记录一起没，不可恢复**。

    正在跑的不许删（409 session_busy）：那一刻 checkpoint 正在往它的目录里推条目，
    删了目录下次屏障还会把它写回来一半。要删就先把 trace 停下来。

    举例：DELETE /sessions/sess_x?user_id=u1 -> {"session_id": "sess_x", "deleted": true}
    """
    live = await container.sessions.get_or_load(session_id, user_id)
    if live is None:
        raise session_not_found(session_id)
    if live.is_running:
        raise session_busy(session_id)

    await container.sessions.delete(session_id, user_id)
    return SessionDeletedOut(session_id=session_id, deleted=True)

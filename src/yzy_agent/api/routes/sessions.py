"""会话的建、列、查。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from yzy_agent.composition import Container

from ..deps import get_container
from ..errors import ApiError, session_busy, session_not_found
from ..schemas import (
    DEFAULT_PAGE_SIZE,
    CreateSessionIn,
    SessionDeletedOut,
    SessionDetailOut,
    SessionListOut,
    SessionOut,
    page_messages,
)
from ..stats import session_stats

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
    return SessionDetailOut.from_live(live, stats=stats, entries=page, has_more=has_more)


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

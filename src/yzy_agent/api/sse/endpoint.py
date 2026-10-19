"""`/events` 端点：握手、写流。

一条流一个生成器，它是唯一碰响应的人。**它是长活的** —— 不随 trace 结束而关闭，
什么时候不要了由客户端断开决定。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from yzy_agent.composition import Container

from ..errors import session_not_found
from .frames import HEARTBEAT_MS, PING, error_frame, ready_frame, subscribed_frame, to_text
from .hub import Connection, Hub

router = APIRouter()

# 空闲多久写一次心跳。帧里报的是毫秒，等的是秒。
#
# 举例：2.0
HEARTBEAT_SECONDS = HEARTBEAT_MS / 1000

# 响应头。X-Accel-Buffering 是给 nginx 的：不关掉它，代理会把整条流攒着不发。
#
# 举例：{"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}


@router.get("/events")
async def stream(
    request: Request,
    user_id: str,
    session_id: Annotated[list[str] | None, Query()] = None,
) -> StreamingResponse:
    """一条流多路复用：看着若干个会话，收它们的事件。集合定在连接时，换会话就重连。

    会话在这儿先校验 —— 不属于这个用户就是 404，不会开出一条空流。

    举例：GET /events?user_id=u1&session_id=sess_x, 返回 text/event-stream
    """
    container: Container = request.app.state.container
    hub: Hub = request.app.state.hub
    wanted = session_id or []

    for each in wanted:
        if await container.sessions.get_or_load(each, user_id) is None:
            raise session_not_found(each)

    return StreamingResponse(
        _pump(
            request=request,
            container=container,
            hub=hub,
            user_id=user_id,
            session_ids=wanted,
            client_id=container.ports.ids.new_id("client"),
        ),
        media_type="text/event-stream",
        headers=HEADERS,
    )


async def _pump(
    *,
    request: Request,
    container: Container,
    hub: Hub,
    user_id: str,
    session_ids: Sequence[str],
    client_id: str,
) -> AsyncIterator[str]:
    """把队列里的事件写成 SSE 文本。**一条流只有这一个写者。**

    举例：StreamingResponse(_pump(...), media_type="text/event-stream")
    """
    connection = Connection()
    try:
        # ready 必须在任何事件之前到：客户端要等它拿到 client_id 与心跳间隔。
        yield to_text(
            ready_frame(
                client_id=client_id,
                server_time=container.ports.clock.now(),
                heartbeat_ms=HEARTBEAT_MS,
            )
        )
        for session_id in session_ids:
            live = await container.sessions.get_or_load(session_id, user_id)
            if live is None:
                continue
            # 先登记再回执：两步之间没有 await，事件只会排在 subscribed 后面。
            hub.subscribe(connection, session_id=session_id)
            entries = live.session.entries()
            yield to_text(
                subscribed_frame(
                    session_id=session_id,
                    is_running=live.is_running,
                    draft_text=live.session.draft_text(),
                    latest_entry_seq=entries[-1].seq if entries else 0,
                )
            )

        while True:
            # 客户端还在不在，得自己问。ASGI 2.4 之后往断掉的连接写不再报错，只能靠这一问。
            if await request.is_disconnected():
                return
            try:
                async with asyncio.timeout(HEARTBEAT_SECONDS):
                    frame = await connection.outbox.get()
            except TimeoutError:
                # 空闲时也得写一行：中间的代理会把久不说话的连接掐掉。
                yield PING
                continue
            yield to_text(frame)
            if connection.overflowed:
                # 客户端跟不上：说一句为什么，然后收尾 —— 它重连之后会全量重拉（§7.6）。
                container.ports.logger.warning("sse.outbox_overflowed", client_id=client_id)
                yield to_text(
                    error_frame(code="stream_overflowed", message="事件积压，重连后会全量重拉")
                )
                return
    finally:
        hub.detach(connection)

"""发消息与中断。"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request, Response

from agent_frame.composition import Container
from agent_frame.contracts import CancelToken, Message, MessageRole

from ..deps import get_container
from ..errors import session_busy, session_not_found
from ..schemas import InterruptOut, MessageAcceptedOut, MessageIn, to_parts

router = APIRouter(tags=["chat"])


@router.post("/sessions/{session_id}/messages", status_code=202, response_model=MessageAcceptedOut)
async def post_message(
    session_id: str,
    user_id: str,
    body: MessageIn,
    request: Request,
    container: Container = Depends(get_container),
) -> MessageAcceptedOut:
    """收下一条用户消息，必要时开一次 trace。**不回内容** —— 结果从 WS 的事件流拿。

    这样做的好处是发消息不受连接状态影响：断着线也能发，重连之后照样看得到结果。

    举例：POST /sessions/sess_x/messages?user_id=u1 {"content": [...]} -> 202
    """
    live = await container.sessions.get_or_load(session_id, user_id)
    if live is None:
        raise session_not_found(session_id)
    if live.is_running:
        raise session_busy(session_id)

    message = Message(role=MessageRole.USER, content=to_parts(body.content))
    signal = CancelToken()
    # 请求头里的凭证与租户随这次 trace 走：后端不存，只透传给业务工具。
    variables = trace_variables(request)
    # trace 跑在独立任务里，与这条 HTTP 请求无关：请求一返回它照样跑。
    task = asyncio.create_task(
        container.agent.run(
            session=live.session,
            user_message=message,
            signal=signal,
            variables=variables,
        )
    )
    container.sessions.attach_task(live.session_id, task=task, signal=signal)
    task.add_done_callback(lambda _: container.sessions.detach_task(live.session_id))

    return MessageAcceptedOut(session_id=live.session_id, accepted=True, queued=False)


def trace_variables(request: Request) -> dict[str, str]:
    """把请求头里的凭证与租户收成一份 trace 变量；没有的项不放进去。

    **值由前端给、后端不存**：工具拿到的是这一次请求的那份，不会跨请求串味。

    举例：trace_variables(request) -> {"authorization": "Bearer x", "tenant": "BF58..."}
    """
    headers = {
        "authorization": request.headers.get("authorization"),
        "tenant": request.headers.get("tenant"),
    }
    return {key: value.strip() for key, value in headers.items() if value and value.strip()}


@router.post("/sessions/{session_id}/interrupt", response_model=InterruptOut)
async def interrupt(
    session_id: str,
    user_id: str,
    response: Response,
    container: Container = Depends(get_container),
) -> InterruptOut:
    """请求中断正在跑的那次 trace。**幂等**：没在跑就是 200 + interrupted=false。

    取消是异步的 —— 这里只表示"已请求"，真正的结束状态由 WS 上的 TraceEnd 通知。

    举例：POST /sessions/sess_x/interrupt?user_id=u1 -> 202 {"interrupted": true}
    """
    live = await container.sessions.get_or_load(session_id, user_id)
    if live is None:
        raise session_not_found(session_id)

    interrupted = container.sessions.interrupt(live.session_id)
    if interrupted:
        response.status_code = 202
    return InterruptOut(session_id=live.session_id, interrupted=interrupted)

"""待审批列表与回执。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from yzy_agent.composition import Container
from yzy_agent.contracts import ApprovalResult

from ..deps import get_container
from ..errors import approval_not_found, session_not_found
from ..schemas import (
    ApprovalAcceptedOut,
    ApprovalDecisionIn,
    ApprovalListOut,
    ApprovalPendingOut,
)

router = APIRouter(tags=["approvals"])


@router.get("/approvals", response_model=ApprovalListOut)
async def list_approvals(
    user_id: str,
    session_id: str,
    container: Container = Depends(get_container),
) -> ApprovalListOut:
    """这个会话里还没回执的审批。

    **断线期间到达的审批只能从这里补** —— 它没有对应的 Session 条目，事件补不回来（§7.6）。

    举例：GET /approvals?user_id=u1&session_id=sess_x -> {"approvals": [...]}
    """
    live = await container.sessions.get_or_load(session_id, user_id)
    if live is None:
        raise session_not_found(session_id)

    pending = await container.approvals.pending(session_id)
    return ApprovalListOut(
        approvals=[ApprovalPendingOut.from_request(request) for request in pending]
    )


@router.post("/approvals/{request_id}", response_model=ApprovalAcceptedOut)
async def answer(
    request_id: str,
    user_id: str,
    session_id: str,
    body: ApprovalDecisionIn,
    container: Container = Depends(get_container),
) -> ApprovalAcceptedOut:
    """交一份回执，那条挂着的工具调用立刻往下走。

    已处理过、或者不属于这个会话的请求，一律 404 —— 不告诉外人这个 id 存不存在（§7.8）。

    举例：POST /approvals/appr_1?user_id=u1&session_id=sess_x {"decision": "approve"}
    """
    live = await container.sessions.get_or_load(session_id, user_id)
    if live is None:
        raise session_not_found(session_id)

    result = ApprovalResult(request_id=request_id, decision=body.decision, comment=body.comment)
    if not await container.approvals.respond(session_id, result):
        raise approval_not_found(request_id)
    return ApprovalAcceptedOut(request_id=request_id, decision=body.decision, accepted=True)

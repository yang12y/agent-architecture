"""待审批表：沙箱在位点 6 里挂起，等一个人点那一下。

表本身在 `infra/pending.py`（提问用的是同一张表），这里只写审批这一份的三件事。
"""

from __future__ import annotations

from agent_frame.contracts import ApprovalRequest, ApprovalResult

from ..pending import PendingTable


class ApprovalGate:
    """审批端口的进程内实现。

    举例：gate = ApprovalGate(); await gate.request(ApprovalRequest(...))
    """

    def __init__(self) -> None:
        """一张空表。

        举例：ApprovalGate()
        """
        # 举例：PendingTable[ApprovalRequest, ApprovalResult]()
        self._table: PendingTable[ApprovalRequest, ApprovalResult] = PendingTable()

    async def request(self, request: ApprovalRequest) -> ApprovalResult:
        """挂起等回执。

        **被取消时自己从表里摘掉**：用户中断或进程收尾会掐掉这个 await，
        留下的条目会让重连的界面弹出一个永远点不动的框。

        举例：result = await gate.request(ApprovalRequest(request_id="appr_1", ...))
        """
        future = self._table.hold(request.request_id, request.session_id, request)
        try:
            # 协程在这里让出控制权，去执行其他的任务
            return await future
        finally:
            self._table.release(request.request_id)

    async def pending(self, session_id: str) -> list[ApprovalRequest]:
        """这个会话里还没回执的审批，按发出的先后排。

        举例：await gate.pending("sess_ab12") -> [ApprovalRequest(...)]
        """
        return self._table.pending(session_id)

    async def respond(self, session_id: str, result: ApprovalResult) -> bool:
        """交一份回执，释放相关的 future，继续执行接下来的任务。

        举例：await gate.respond("sess_ab12", ApprovalResult(...)) -> True
        """
        return self._table.resolve(session_id, result.request_id, result)

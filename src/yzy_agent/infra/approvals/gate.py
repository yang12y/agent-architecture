"""待审批表：把人点的那一下变成一次 `await` 的返回。

**不落盘**（§7.5）：审批只在进程活着的时候有意义 —— 进程重启后那次 trace 已经死了，
它等的回执也没人要了。所以重连时靠 `GET /approvals` 全量重拉，而不是从日志里补。
"""

from __future__ import annotations

import asyncio

from yzy_agent.contracts import ApprovalRequest, ApprovalResult


class ApprovalGate:
    """一张内存表：谁在等、等了什么、回执到了没有。

    **只在事件循环里用，不加锁** —— 每个方法里没有 await，读改写之间不会被插进来。

    举例：gate = ApprovalGate(); await gate.request(ApprovalRequest(...))
    """

    def __init__(self) -> None:
        """一张空表。

        举例：ApprovalGate()
        """
        # request_id -> (这次请求, 它挂起的那个 future)。**插入顺序就是请求发出的顺序**。
        #
        # 举例：{"appr_1": (ApprovalRequest(...), <Future pending>)}
        self._waiting: dict[str, tuple[ApprovalRequest, asyncio.Future[ApprovalResult]]] = {}

    async def request(self, request: ApprovalRequest) -> ApprovalResult:
        """挂起等回执。

        **被取消时自己从表里摘掉**：用户中断或进程收尾会掐掉这个 await，
        留下的条目会让重连的界面弹出一个永远点不动的框。

        举例：result = await gate.request(ApprovalRequest(request_id="appr_1", ...))
        """
        future: asyncio.Future[ApprovalResult] = asyncio.get_running_loop().create_future()
        self._waiting[request.request_id] = (request, future)
        try:
            return await future
        finally:
            self._waiting.pop(request.request_id, None)

    async def pending(self, session_id: str) -> list[ApprovalRequest]:
        """这个会话里还没回执的审批，按发出的先后排。

        举例：await gate.pending("sess_ab12") -> [ApprovalRequest(...)]
        """
        return [
            request for request, _ in self._waiting.values() if request.session_id == session_id
        ]

    async def respond(self, session_id: str, result: ApprovalResult) -> bool:
        """交一份回执。

        不存在、已经处理过、或者不属于这个会话，三种情况都返回 False ——
        前两个本来就该是同一个 404，第三个更不该告诉外人这个 id 是存在的。

        举例：await gate.respond("sess_ab12", ApprovalResult(...)) -> True
        """
        entry = self._waiting.get(result.request_id)
        if entry is None:
            return False
        request, future = entry
        if request.session_id != session_id or future.done():
            return False
        del self._waiting[result.request_id]
        future.set_result(result)
        return True

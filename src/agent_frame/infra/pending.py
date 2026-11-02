"""待办表：把人点的那一下变成一次 `await` 的返回。

审批与提问**机制一样**（挂起、列还没回执的、交回执），差别只在语义：
审批的返回是控制流（放行/拒绝），提问的返回是数据（进工具结果）。
所以表放在这里，两个 gate 各拿一个实例。

**不落盘**：只在进程活着的时候有意义 —— 进程重启后那条 trace 已经死了，
它等的回执也没人要了。重连时靠一次全量重拉，而不是从日志里补。
"""

from __future__ import annotations

import asyncio


class PendingTable[RequestT, ResultT]:
    """谁在等、等了什么、回执到了没有。

    **只在事件循环里用，不加锁** —— 每个方法里没有 await，读改写之间不会被插进来。

    举例：table = PendingTable(); future = table.hold("q_1", "sess_x", request)
    """

    def __init__(self) -> None:
        """一张空表。

        举例：PendingTable()
        """
        # request_id -> (会话, 这次请求, 挂起的那个 future)。**插入顺序就是请求发出的顺序**。
        #
        # 举例：{"q_1": ("sess_x", QuestionRequest(...), <Future pending>)}
        self._waiting: dict[str, tuple[str, RequestT, asyncio.Future[ResultT]]] = {}

    def hold(self, request_id: str, session_id: str, request: RequestT) -> asyncio.Future[ResultT]:
        """登记一条，返回等它的那个 future。

        **调用方必须保证最后 release**（放在 finally 里）：被取消时留下的条目，
        会让重连的界面弹出一个永远点不动的框。

        举例：future = table.hold("q_1", "sess_ab12", request); return await future
        """
        # 造一个"还没有结果的占位"。协程 await 它的时候把控制权让出去。
        future: asyncio.Future[ResultT] = asyncio.get_running_loop().create_future()
        self._waiting[request_id] = (session_id, request, future)
        return future

    def release(self, request_id: str) -> None:
        """摘掉一条，有没有回执都摘。

        举例：table.release("q_1")
        """
        self._waiting.pop(request_id, None)

    def pending(self, session_id: str) -> list[RequestT]:
        """这个会话里还没回执的，按发出的先后排。

        举例：table.pending("sess_ab12") -> [QuestionRequest(...)]
        """
        return [request for owner, request, _ in self._waiting.values() if owner == session_id]

    def resolve(self, session_id: str, request_id: str, result: ResultT) -> bool:
        """交一份回执并唤醒等待者。

        不存在、已经处理过、或者不属于这个会话，三种情况都返回 False ——
        前两个本来就该是同一个 404，第三个更不该告诉外人这个 id 是存在的。

        举例：table.resolve("sess_ab12", "q_1", result) -> True
        """
        entry = self._waiting.get(request_id)
        if entry is None:
            return False
        owner, _, future = entry
        if owner != session_id or future.done():
            return False
        del self._waiting[request_id]
        future.set_result(result)
        return True

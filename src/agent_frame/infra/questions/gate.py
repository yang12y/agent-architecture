"""待回答表：把用户点的那个选项变成一次 `await` 的返回。

表本身和审批共用（`infra/pending.py`），差别只在语义：审批拦的是控制流，提问取的是数据。

**没人连着就当场失败**：用户不在时挂起等下去，只会让这条 trace 白占着会话到工具超时。
"""

from __future__ import annotations

from agent_frame.contracts import (
    EventBus,
    NoAnswererError,
    QuestionRequest,
    QuestionRequired,
    QuestionResult,
)

from ..pending import PendingTable
from ..presence import Presence

# 没人能答时给模型看的话。**它要能指导下一步动作** —— 光说"失败了"模型只会重试一次。
#
# 举例："现在没有连着的界面能回答这个问题。请自己判断，或者把要确认的点写进回复里。"
NO_ANSWERER = "现在没有连着的界面能回答这个问题。请自己判断，或者把要确认的点写进回复里。"


class QuestionGate:
    """提问端口的进程内实现。

    举例：gate = QuestionGate(events=bus, presence=presence)
    """

    def __init__(self, *, events: EventBus, presence: Presence) -> None:
        """把事件总线与在线表接上，表留空。

        举例：QuestionGate(events=InProcessEventBus(logger), presence=Presence())
        """
        # 提问事件从这儿出去：api 层的 hub 订阅它，转成帧推给浏览器。
        #
        # 举例：InProcessEventBus(logger)
        self._events = events

        # 谁在看哪个会话。**只用来判"有没有人能答"**，不看内容。
        #
        # 举例：Presence()
        self._presence = presence

        # 举例：PendingTable[QuestionRequest, QuestionResult]()
        self._table: PendingTable[QuestionRequest, QuestionResult] = PendingTable()

    async def request(self, request: QuestionRequest) -> QuestionResult:
        """发出提问并等回答。**没人连着就当场失败，不挂起。**

        举例：result = await gate.request(QuestionRequest(request_id="q_1", ...))
        出错：这个会话没有任何界面连着时抛 NoAnswererError。
        """
        future = self._table.hold(request.request_id, request.session_id, request)
        try:
            # 查与推之间不能有 await：这两步一起才是"送达"，中间插一个 await 就有
            # "查的时候有人在、推的时候人走了"的窗口，那一帧永久丢失。
            if not self._presence.has_listener(request.session_id):
                raise NoAnswererError(NO_ANSWERER)
            self._events.publish(
                QuestionRequired(
                    session_id=request.session_id,
                    request_id=request.request_id,
                    tool_call_id=request.tool_call_id,
                    tool_name=request.tool_name,
                    questions=request.questions,
                )
            )
            return await future
        finally:
            self._table.release(request.request_id)

    async def pending(self, session_id: str) -> list[QuestionRequest]:
        """这个会话里还没答的提问，按发出的先后排。

        举例：await gate.pending("sess_ab12") -> [QuestionRequest(...)]
        """
        return self._table.pending(session_id)

    async def respond(self, session_id: str, result: QuestionResult) -> bool:
        """交一份回答，唤醒挂着的那个工具调用。

        举例：await gate.respond("sess_ab12", QuestionResult(...)) -> True
        """
        return self._table.resolve(session_id, result.request_id, result)

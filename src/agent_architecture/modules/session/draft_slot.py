"""会话级草稿槽：模型输出的唯一落脚点。

**单槽是自然结果而非取舍**：一段输出被并入上下文（位点 3 取材）或定稿（位点 5）之后就腾空了，
同一时刻槽里只会有一段。所以这里不需要队列。

草稿**永不落盘**：它既不属于磁盘上的 Session，也不属于 Runtime State（那是一次 trace 一组，
而草稿要活过 trace 边界）。
"""

from __future__ import annotations

from agent_architecture.contracts import Draft, Timestamp
from agent_architecture.contracts import DraftSlot as DraftSlotSnapshot


class DraftSlot:
    """一个会话的草稿槽。

    举例：slot = DraftSlot("sess_ab12")
          slot.append("我来帮你", trace_id="trace_1", turn_index=1, now=now)
          slot.text() -> "我来帮你"
    """

    def __init__(self, session_id: str) -> None:
        """建一个空槽。

        举例：DraftSlot("sess_ab12")
        """
        # 这个槽属于哪个会话，只用来填快照。
        #
        # 举例："sess_20250101_ab12"
        self._session_id = session_id

        # 当前草稿；None 表示槽是空的。
        #
        # 举例：Draft(trace_id="trace_1", turn_index=1, created_at=..., content="我来帮你")
        self._draft: Draft | None = None

    def append(self, text: str, *, trace_id: str, turn_index: int, now: Timestamp) -> None:
        """追加一段正文增量；槽是空的就先新建一条草稿。

        举例：slot.append("读一下", trace_id="trace_1", turn_index=1, now=now)
        """
        self._ensure(trace_id=trace_id, turn_index=turn_index, now=now).content += text

    def append_reasoning(
        self, text: str, *, trace_id: str, turn_index: int, now: Timestamp
    ) -> None:
        """追加一段思考增量。**和正文分开记** —— 它进日志、进界面，但不进下一轮请求。

        举例：slot.append_reasoning("先看看", trace_id="trace_1", turn_index=1, now=now)
        """
        self._ensure(trace_id=trace_id, turn_index=turn_index, now=now).reasoning += text

    def _ensure(self, *, trace_id: str, turn_index: int, now: Timestamp) -> Draft:
        """拿到当前草稿，没有就新建一条空的。

        举例：slot._ensure(trace_id="trace_1", turn_index=1, now=now).content -> ""
        """
        if self._draft is None:
            self._draft = Draft(
                trace_id=trace_id, turn_index=turn_index, created_at=now, content=""
            )
        return self._draft

    def text(self) -> str:
        """槽里的文本；槽是空的就返回空串。**只读**，不动槽里的内容。

        作用：给"这次尝试有没有产出"这类判断用（不变量 I11 的重试前提）。

        举例：slot.text() -> "我来帮你读"
        """
        return self._draft.content if self._draft is not None else ""

    def reasoning(self) -> str:
        """槽里的思考过程；槽是空的就返回空串。

        作用：判断"这次尝试有没有产出过东西"时，思考也算产出 —— 重试会把两段思考接在一起。

        举例：slot.reasoning() -> "先看看有没有这个文件"
        """
        return self._draft.reasoning if self._draft is not None else ""

    def take(self) -> Draft | None:
        """取出草稿并**清空**槽；槽本来就是空的就返回 None。

        举例：slot.take().content -> "我来帮你读 a.py"；紧接着 slot.text() -> ""
        """
        draft = self._draft
        self._draft = None
        return draft

    def is_empty(self) -> bool:
        """槽是不是空的（有没有未定稿的输出）。

        举例：DraftSlot("sess_1").is_empty() -> True
        """
        return self._draft is None

    def snapshot(self) -> DraftSlotSnapshot:
        """拍一张契约层的快照，给日志或调试用。

        作用：草稿本身不能落盘，但"槽里有没有东西"这件事有时要给别人看。

        举例：slot.snapshot() -> DraftSlot(session_id="sess_ab12", draft=None)
        """
        return DraftSlotSnapshot(session_id=self._session_id, draft=self._draft)

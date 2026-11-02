"""谁在看哪个会话：一条 SSE 流连上时记一笔，断开时抹掉。

提问之前要问它一句"现在有没有人能答" —— **没人连着就当场失败**，
免得一条 trace 白等到工具超时。它只记数、不碰连接，所以不依赖 api 层。
"""

from __future__ import annotations


class Presence:
    """会话 -> 正在看它的流有几条。

    **只在事件循环里用，不加锁** —— 每个方法里没有 await，读改写之间不会被插进来。

    举例：presence = Presence(); presence.attach("sess_ab12")
    """

    def __init__(self) -> None:
        """一张空表。

        举例：Presence()
        """
        # session_id -> 流的条数。**减到 0 就删键** —— 长进程里会话越开越多，不删会一直涨。
        #
        # 举例：{"sess_ab12": 2}
        self._counts: dict[str, int] = {}

    def attach(self, session_id: str) -> None:
        """一条流开始看这个会话。

        举例：presence.attach("sess_ab12")
        """
        self._counts[session_id] = self._counts.get(session_id, 0) + 1

    def detach(self, session_id: str) -> None:
        """一条流不再看了。**没记过就什么都不做** —— 断开比连上晚到不算错。

        举例：presence.detach("sess_ab12")
        """
        count = self._counts.get(session_id)
        if count is None:
            return
        if count <= 1:
            del self._counts[session_id]
            return
        self._counts[session_id] = count - 1

    def has_listener(self, session_id: str) -> bool:
        """现在有没有界面在看这个会话。

        举例：presence.has_listener("sess_ab12") -> True
        """
        return self._counts.get(session_id, 0) > 0

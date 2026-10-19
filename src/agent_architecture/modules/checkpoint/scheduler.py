"""落盘调度：谁在什么时候必须已经在盘上。

屏障落的东西有**两样**：

    entries     append-only 的事实流
    meta        可覆盖的当前值（updated_at / context_cursor / title）

两样都属于"这个会话已经在盘上"，所以一起落 —— 不另开"只写游标"或"只写时间"的口子。

**它自己不记账本**："已经落盘到哪" 是存储的事实，不是调度器的猜测；调度器只缓存
"上一次推到哪"用来跳过空跑，缓存丢了最坏也只是多推一遍，而多推是幂等的。

批量与定时刷盘还没做：现在日志的写入点（位点 1/5/6/8）全都落在屏障上，屏障之间没有条目，
阈值刷盘与定时刷盘都是空转。等压缩、并行工具这类"屏障之间大量写条目"的模块落地再加，
见文档 §9。
"""

from __future__ import annotations

from agent_architecture.contracts import Logger, SessionHandle, SessionStore

from . import barriers


class Checkpoint:
    """屏障落盘的调度器。

    举例：checkpoint = Checkpoint(store)
          await checkpoint.ensure(session, reason="llm:before")
    """

    def __init__(self, store: SessionStore, *, logger: Logger | None = None) -> None:
        """绑定一个存储。

        举例：Checkpoint(FileSessionStore(".data/sessions"))
        """
        # 会话日志的持久化端口。
        #
        # 举例：FileSessionStore(".data/sessions")
        self._store = store

        # 结构化日志；没给就不记。
        #
        # 举例：StdLogger("INFO")
        self._logger = logger

        # 上一次成功推到第几号，按 session_id 索引。**只是跳过空跑的缓存，不是事实来源**。
        #
        # 举例：{"sess_ab12": 42}
        self._persisted: dict[str, int] = {}

    @property
    def store(self) -> SessionStore:
        """它绑定的存储。恢复流程要直接读它。

        举例：await checkpoint.store.load("sess_ab12")
        """
        return self._store

    def persisted_upto(self, session_id: str) -> int:
        """这个会话上一次推到第几号；从没推过就是 0。

        举例：checkpoint.persisted_upto("sess_ab12") -> 0
        """
        return self._persisted.get(session_id, 0)

    async def ensure(self, session: SessionHandle, *, reason: str) -> None:
        """屏障：走到这里，这个会话必须已经在盘上 —— 条目与元信息都算。

        meta 跟着条目一起落。位点 1 与 10 刷的 updated_at、压缩后 rebuild 推的
        context_cursor、将来生成标题写的 title，都是这个会话的**当前值**；
        落条目却不落 meta，盘上就会留一份过期的元信息。

        **顺序有讲究：先落 meta，再推进水位。** 写 meta 失败时水位停在原地，
        下一次屏障会把这一段重试一遍 —— 条目重推是幂等的，meta 重写也是。
        反过来先推水位的话，这次失败就再也没人补了。

        只在有新条目时才写 meta。现在 meta 的每次改动后面都紧跟着有新条目的屏障
        （位点 1/10 改 updated_at 的同时都写了条目），所以不会漏。

        reason 只进日志，用来定位是哪一次屏障推的。
        落盘失败抛 FlushError —— 内核据此拦掉下游动作（位点 3 拦模型调用，位点 6 拦工具）。

        举例：await checkpoint.ensure(session, reason="tool:before")
        """
        session_id = session.meta.session_id
        before = self.persisted_upto(session_id)
        target = await barriers.ensure_persisted(self._store, session, before, reason=reason)
        if target == before:
            return

        await self._store.save_meta(session.meta)

        self._persisted[session_id] = target
        if self._logger is not None:
            self._logger.debug(
                "checkpoint.persisted",
                session_id=session_id,
                seq=target,
                reason=reason,
            )

"""会话注册表：session_id -> 这份会话在进程里的活状态。

它是"多表面共享"能成立的前提：两个浏览器挂着同一个会话时，要看到**同一份**内存 Session、
**同一份**事件订阅、**同一个**正在跑的 task。查一次表就够，不必每次重建。

**它不落盘。** 里面的东西全是进程内的活状态，进程没了就没了；持久化归 `SessionStore`。
所以它和 `SessionStore` 是两件事：前者管"现在活着什么"，后者管"记下来了什么"。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass

from agent_architecture.contracts import (
    AppConfig,
    CancelToken,
    Clock,
    IdGenerator,
    SessionEntry,
    SessionMeta,
    SessionStatus,
    SessionStore,
)
from agent_architecture.modules.session import Session


@dataclass(slots=True)
class LiveSession:
    """一个会话在进程里的活状态。

    举例：live = registry.create(); live.session.messages() -> []
    """

    # 内存 Session：投影 + 游标 + 草稿槽。所有表面读的都是它。
    #
    # 举例：Session(meta=SessionMeta(session_id="sess_ab12", ...), ...)
    session: Session

    # 正在跑的那次 trace 的 task；没有正在跑的就是 None。
    #
    # 举例：asyncio.Task(...)
    task: asyncio.Task[None] | None = None

    # 那次 trace 的取消信号。中断就是 set 它。
    #
    # 举例：CancelToken()
    signal: CancelToken | None = None

    @property
    def session_id(self) -> str:
        """这个会话的标识。

        举例：live.session_id -> "sess_ab12"
        """
        return self.session.meta.session_id

    @property
    def is_running(self) -> bool:
        """此刻有没有一次 trace 在跑。

        举例：live.is_running -> True
        """
        return self.task is not None and not self.task.done()


class SessionRegistry:
    """进程内的会话注册表。

    举例：registry = SessionRegistry(store=..., ids=..., clock=..., config=...)
          live = await registry.create()
    """

    def __init__(
        self, *, store: SessionStore, ids: IdGenerator, clock: Clock, config: AppConfig
    ) -> None:
        """建一张空表。

        举例：SessionRegistry(store=FileSessionStore(".data/sessions"), ids=UuidGenerator(),
                            clock=SystemClock(), config=load_app_config())
        """
        # 会话日志的持久化端口。注册表只在"第一次见到某个会话"时读它。
        #
        # 举例：FileSessionStore(".data/sessions")
        self._store = store

        # 标识生成器。新建会话与 Session 自己分配 entry_id 都用它。
        #
        # 举例：UuidGenerator()
        self._ids = ids

        # 时间来源。新建会话时取 created_at / updated_at。
        #
        # 举例：SystemClock()
        self._clock = clock

        # 进程级配置：新会话的 workspace / model / permission 从这里取默认值。
        #
        # 举例：AppConfig(workspace="D:/proj", model="deepseek-chat", ...)
        self._config = config

        # 进程内的活状态，按 session_id 索引。
        #
        # 举例：{"sess_ab12": LiveSession(session=Session(...), task=None)}
        self._live: dict[str, LiveSession] = {}

    async def create(self, *, user_id: str, title: str | None = None) -> LiveSession:
        """新建一个会话：造 meta、写进 store、登记为活状态。

        user_id 必填 —— 每个会话都属于某个人，没有"无主会话"这种东西。

        举例：live = await registry.create(user_id="user_7f3a91", title="重构 session 模块")
        """
        now = self._clock.now()
        meta = SessionMeta(
            session_id=self._ids.new_id("sess"),
            user_id=user_id,
            created_at=now,
            updated_at=now,
            parent_session_id=None,
            title=title,
            workspace=self._config.workspace,
            model=self._config.model,
            permission=self._config.permission,
            tool_result_max_chars=self._config.tool_result_max_chars,
            status=SessionStatus.ACTIVE,
            context_cursor="",
        )
        await self._store.save_meta(meta)
        return self._register(meta, [])

    def get(self, session_id: str) -> LiveSession | None:
        """只查活状态，不自动装载，**也不做归属判断**。没有就返回 None。

        它是进程内的裸查表：回答的是"这个进程里有没有这个会话"，不是"你能不能看它"。
        对外的入口一律走 get_or_load —— 那里会按 user_id 拦一道。

        举例：registry.get("sess_ab12") -> LiveSession(...)
        """
        return self._live.get(session_id)

    async def delete(self, session_id: str, user_id: str) -> bool:
        """把一个会话连同日志一起删掉，并从活状态里摘掉。

        **只管删，不管它是不是在跑** —— "正在跑的不许删"是接口层的规矩，不是存储的。
        删掉之后再装载就返回 None，所以"不存在"与"刚被删"对调用方是同一件事。

        举例：await registry.delete("sess_ab12", "user_7f3a91") -> True
        """
        deleted = await self._store.delete(session_id, user_id)
        if deleted:
            self._live.pop(session_id, None)
        return deleted

    async def get_or_load(self, session_id: str, user_id: str) -> LiveSession | None:
        """取活状态；进程里还没有就从 store 装一份。不属于这个用户时返回 None。

        从磁盘装出来的会话与一直在内存里的走同一条构造路径，所以投影结果完全一样。

        **"不存在"与"不是你的"都返回 None**，调用方一律按 404 处理 —— 分开回话等于
        告诉别人"这个 id 确实存在"。

        举例：await registry.get_or_load("sess_ab12", "user_7f3a91") -> LiveSession(...)
        """
        live = self._live.get(session_id)
        if live is not None:
            # 已经在内存里也要判归属：注册表是进程级的，多个用户共用同一张表。
            return live if live.session.meta.user_id == user_id else None

        loaded = await self._store.load(session_id, user_id)
        if loaded is None:
            return None
        meta, entries = loaded
        return self._register(meta, entries)

    def attach_task(
        self, session_id: str, *, task: asyncio.Task[None], signal: CancelToken
    ) -> None:
        """记下"这个会话正在跑一次 trace"，以及它的取消信号。

        举例：registry.attach_task("sess_ab12", task=task, signal=token)
        """
        live = self._live.get(session_id)
        if live is None:
            return
        live.task = task
        live.signal = signal

    def detach_task(self, session_id: str) -> None:
        """trace 跑完了，把 task 与取消信号清掉。

        注意只清这两个：`live.session` 要留着，多表面还要继续读它。

        举例：registry.detach_task("sess_ab12")
        """
        live = self._live.get(session_id)
        if live is None:
            return
        live.task = None
        live.signal = None

    def interrupt(self, session_id: str) -> bool:
        """请求中断这个会话正在跑的 trace；没有在跑的返回 False。

        它只 set 取消信号 —— 真正的收尾由内核在安全点做（写 trace_end(aborted)）。

        举例：registry.interrupt("sess_ab12") -> True
        """
        live = self._live.get(session_id)
        if live is None or not live.is_running or live.signal is None:
            return False
        live.signal.set()
        return True

    def live_ids(self) -> list[str]:
        """此刻登记在表里的 session_id。

        举例：registry.live_ids() -> ["sess_ab12"]
        """
        return list(self._live)

    async def stored_metas(self, user_id: str) -> list[SessionMeta]:
        """store 里**这个用户的**会话元信息，用于会话列表。

        举例：await registry.stored_metas("user_7f3a91") -> [SessionMeta(session_id="sess_ab12", ...)]
        """
        return await self._store.list_sessions(user_id)

    def _register(self, meta: SessionMeta, entries: Sequence[SessionEntry]) -> LiveSession:
        """把一份 meta + 条目登记成活状态。

        举例：registry._register(meta, []) -> LiveSession(...)
        """
        live = LiveSession(session=Session(meta, entries, ids=self._ids))
        self._live[meta.session_id] = live
        return live

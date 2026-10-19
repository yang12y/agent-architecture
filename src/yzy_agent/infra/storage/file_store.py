"""文件版的存储实现：会话常驻内存，日志落到磁盘。

一个会话一个目录：

    <root>/<session_id>/meta.json       元信息，可被覆盖，save_meta 整份替换
    <root>/<session_id>/entries.jsonl   条目，一行一条，flush 整份替换

**落盘是整份替换**：先写 .tmp、fsync、再 os.replace。所以盘上要么是上一次的完整快照、
要么是这一次的，不会留下半条条目 —— 恢复侧因此不需要截断。

**load 只读文件，不读内存。** 它服务的场景就是"这个会话已经不在内存里"（重启之后的
get_or_load），读内存既没意义、也测不出崩溃语义。

内存里只留一样东西：append 进来、还没 flush 的**增量**。它不是会话的第二份，flush
之后立刻清空。
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Sequence
from pathlib import Path

from yzy_agent.contracts import FlushError, SessionEntry, SessionMeta

from . import codec

# 元信息文件名。
#
# 举例："meta.json"
META_NAME = "meta.json"

# 条目文件名。
#
# 举例："entries.jsonl"
ENTRIES_NAME = "entries.jsonl"


class FileSessionStore:
    """SessionStore 的文件实现。

    举例：store = FileSessionStore("D:/proj/.data/sessions")
          await store.save_meta(meta)
          await store.append("sess_ab12", [entry])
          await store.flush("sess_ab12", 1)
    """

    def __init__(self, root: str | Path) -> None:
        """指定会话目录的根。目录不存在也没关系，第一次写的时候会建。

        举例：FileSessionStore("D:/proj/.data/sessions")
        """
        # 全部会话的存放根目录。
        #
        # 举例：Path("D:/proj/.data/sessions")
        self._root = Path(root)

        # 已 append、还没落盘的增量条目，按 session_id 索引。flush 之后清空。
        #
        # 举例：{"sess_ab12": [SessionEntry(seq=43, ...)]}
        self._pending: dict[str, list[SessionEntry]] = {}

        # 盘上已经有的最大 seq，按 session_id 索引。load 与 flush 都会刷新它。
        #
        # 作用：让 append / flush 幂等。调用方重推一段已经落过盘的条目不会写出重复行。
        #
        # 举例：{"sess_ab12": 42}
        self._durable_upto: dict[str, int] = {}

        # 下一次 flush 是否要失败。给屏障/恢复测试注入用。
        #
        # 举例：store.fail_next_flush(); await store.flush("sess_ab12", 42) -> FlushError
        self._fail_next_flush = False

    @property
    def root(self) -> Path:
        """会话目录的根。

        举例：store.root -> Path("D:/proj/.data/sessions")
        """
        return self._root

    async def load(
        self, session_id: str, user_id: str
    ) -> tuple[SessionMeta, list[SessionEntry]] | None:
        """见 contracts.SessionStore.load。读的是文件，读不到就是 None。

        没 flush 过的那一截不在文件里，所以读不回来 —— 这正是崩溃之后真实的样子。
        会话在、但不属于这个用户时也返回 None：归属判断就这一处，别处不用再查一遍。

        举例：await store.load("sess_ab12", "user_7f3a91") -> (meta, [SessionEntry(seq=1, ...)])
        """
        meta_path = self._session_dir(session_id) / META_NAME
        if not meta_path.is_file():
            return None
        meta = codec.load_meta(meta_path.read_text(encoding="utf-8"))
        if meta.user_id != user_id:
            return None
        entries = self._read_entries(session_id)
        self._durable_upto[session_id] = entries[-1].seq if entries else 0
        return meta, entries

    async def save_meta(self, meta: SessionMeta) -> None:
        """见 contracts.SessionStore.save_meta。整份替换写 meta.json。

        失败时抛 FlushError，与 flush 一致 —— 屏障处的失败一律按"失败即阻止"处理。

        举例：await store.save_meta(make_session_meta())
        """
        path = self._session_dir(meta.session_id) / META_NAME
        try:
            self._write(path, codec.dump_meta(meta))
        except OSError as exc:
            raise FlushError(f"会话 {meta.session_id} 的元信息落盘失败：{exc}") from exc

    async def append(self, session_id: str, entries: Sequence[SessionEntry]) -> None:
        """见 contracts.SessionStore.append。只进内存缓冲区，不碰磁盘。

        **幂等**：已经在盘上、或者已经在缓冲区里的条目会被丢掉。调用方可以放心地
        每次都把整份日志推过来，不必自己记"推到哪了"。

        举例：await store.append("sess_ab12", [entry])
        """
        floor = self._watermark(session_id)
        pending = self._pending.get(session_id, [])
        if pending:
            floor = max(floor, pending[-1].seq)
        fresh = [entry for entry in entries if entry.seq > floor]
        if fresh:
            self._pending[session_id] = [*pending, *fresh]

    async def flush(self, session_id: str, up_to_seq: int) -> None:
        """见 contracts.SessionStore.flush。把序号不超过 up_to_seq 的增量并进 jsonl。

        写路径不解码：读回已有文本、拼上新行、整份替换。失败时抛 FlushError，
        而且**不动缓冲区** —— 失败之后重试还能把这一批推出去。

        举例：await store.flush("sess_ab12", up_to_seq=42)
        """
        if self._fail_next_flush:
            self._fail_next_flush = False
            raise FlushError(f"存储被要求在第 {up_to_seq} 号之前失败")

        watermark = self._watermark(session_id)
        pending = self._pending.get(session_id, [])
        ready = [entry for entry in pending if watermark < entry.seq <= up_to_seq]
        rest = [entry for entry in pending if entry.seq > up_to_seq]

        # 要写的这一段盘上已经有了 —— 空操作，但仍然把缓冲区里过期的那截丢掉。
        if not ready:
            self._pending[session_id] = rest
            return

        path = self._session_dir(session_id) / ENTRIES_NAME
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
        text = existing + "".join(codec.dump_entry(entry) + "\n" for entry in ready)
        try:
            self._write(path, text)
        except OSError as exc:
            raise FlushError(f"会话 {session_id} 落盘失败：{exc}") from exc

        self._durable_upto[session_id] = ready[-1].seq
        self._pending[session_id] = rest

    async def list_sessions(self, user_id: str) -> list[SessionMeta]:
        """见 contracts.SessionStore.list_sessions。扫根目录，只留这个用户的。

        **目录不按用户分片**：会话目录名就是 session_id，user_id 存在 meta.json 里。
        分片会让路径也依赖用户，而 PG 那边只是一个 WHERE —— 两边形状一致更省事。

        举例：await store.list_sessions("user_7f3a91") -> [SessionMeta(session_id="sess_ab12", ...)]
        """
        if not self._root.is_dir():
            return []
        metas: list[SessionMeta] = []
        for child in sorted(self._root.iterdir()):
            meta_path = child / META_NAME
            if not (child.is_dir() and meta_path.is_file()):
                continue
            meta = codec.load_meta(meta_path.read_text(encoding="utf-8"))
            if meta.user_id == user_id:
                metas.append(meta)
        return metas

    async def delete(self, session_id: str, user_id: str) -> bool:
        """见 contracts.SessionStore.delete。整个目录一起删。

        缓冲区里还压着没落盘的条目也一并丢掉 —— 会话都没了，再把它推出去只会
        在盘上重新长出一个只有半个日志的目录。

        举例：await store.delete("sess_ab12", "user_7f3a91") -> True
        """
        meta_path = self._session_dir(session_id) / META_NAME
        if not meta_path.is_file():
            return False
        if codec.load_meta(meta_path.read_text(encoding="utf-8")).user_id != user_id:
            return False

        try:
            shutil.rmtree(self._session_dir(session_id))
        except OSError as exc:
            raise FlushError(f"会话 {session_id} 删除失败：{exc}") from exc

        self._pending.pop(session_id, None)
        self._durable_upto.pop(session_id, None)
        return True

    def fail_next_flush(self) -> None:
        """让下一次 flush 抛 FlushError，用来测"落盘失败必须阻止下游动作"。

        这是实现独有、不进端口的测试钩子。

        举例：store.fail_next_flush()
        """
        self._fail_next_flush = True

    def pending_count(self, session_id: str) -> int:
        """还没落盘的条目有几条；测试用来断言屏障确实把缓冲区推出去了。

        举例：store.pending_count("sess_ab12") -> 0
        """
        return len(self._pending.get(session_id, []))

    def _watermark(self, session_id: str) -> int:
        """盘上已经有的最大 seq；一条都没有就是 0。

        缓存里没有才会去读文件，而且只解析最后一行 —— 这是冷启动那条兜底路径，
        正常情况下 load 或 flush 早就把缓存填上了。

        举例：store._watermark("sess_ab12") -> 42
        """
        known = self._durable_upto.get(session_id)
        if known is not None:
            return known

        value = 0
        path = self._session_dir(session_id) / ENTRIES_NAME
        if path.is_file():
            lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            if lines:
                value = codec.load_entry(lines[-1]).seq
        self._durable_upto[session_id] = value
        return value

    def _session_dir(self, session_id: str) -> Path:
        """这个会话的目录。

        举例：store._session_dir("sess_ab12") -> Path(".../sessions/sess_ab12")
        """
        return self._root / session_id

    def _read_entries(self, session_id: str) -> list[SessionEntry]:
        """读回 entries.jsonl 的全部条目；文件不在就是空日志。

        举例：store._read_entries("sess_ab12") -> [SessionEntry(seq=1, ...)]
        """
        path = self._session_dir(session_id) / ENTRIES_NAME
        if not path.is_file():
            return []
        text = path.read_text(encoding="utf-8")
        return [codec.load_entry(line) for line in text.splitlines() if line.strip()]

    def _write(self, path: Path, text: str) -> None:
        """原子替换写：临时文件 -> fsync -> os.replace。

        漏掉 fsync 就可能出现"新文件是半截、旧文件已经被换掉"，所以顺序不能动。

        举例：store._write(path, "....")
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)

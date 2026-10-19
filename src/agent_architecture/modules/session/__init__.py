"""维度 4：Session。

跨 trace 长期存活的事实档案，**唯一真相源**。
结构（SessionMeta / SessionEntry / Draft / DraftSlot）定义在 contracts/models.py，
这里放算法：日志、投影、游标、草稿槽、标题、只读视图、位点 handler。

**磁盘形态**只有 meta + entries；**内存形态**多出物化上下文、游标、草稿槽。
草稿永不落盘 —— 它不属于磁盘上的 Session。
"""

from . import cursor, title
from .draft_slot import DraftSlot
from .handler import build_handlers
from .log import SessionLog
from .projection import SUMMARY_PREFIX, project, project_entries
from .reader import SessionReader
from .session import Session
from .title import TITLE_MAX_CHARS, title_from

__all__ = [
    "DraftSlot",
    "Session",
    "SessionLog",
    "SUMMARY_PREFIX",
    "SessionReader",
    "TITLE_MAX_CHARS",
    "build_handlers",
    "cursor",
    "project",
    "project_entries",
    "title",
    "title_from",
]

"""存储实现：Session 日志的持久化。

文件实现（先用）与 PG 实现（待做）共用同一套契约测试。
"""

from .codec import CodecError
from .file_store import FileSessionStore

__all__ = [
    "CodecError",
    "FileSessionStore",
]

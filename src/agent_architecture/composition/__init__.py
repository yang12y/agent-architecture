"""L3 装配层：唯一知道有哪些具体实现的地方。

`container` 装出整套（端口 + handler + 门面 + 注册表）；`registry` 是进程内的会话活状态。
"""

from .container import Container, build_container
from .registry import LiveSession, SessionRegistry

__all__ = ["Container", "LiveSession", "SessionRegistry", "build_container"]

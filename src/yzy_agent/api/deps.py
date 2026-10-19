"""依赖注入：路由从 app.state 里拿装好的 Container，不自己造。"""

from __future__ import annotations

from fastapi import Request

from yzy_agent.composition import Container


def get_container(request: Request) -> Container:
    """取出进程级唯一的那个 Container。

    举例：async def route(container: Container = Depends(get_container)): ...
    """
    container: Container = request.app.state.container
    return container

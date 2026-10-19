"""ASGI 入口：`uvicorn agent_architecture.api.main:app`。

这是全项目**唯一**在模块级建 `app` 的地方，装配根也在这里只调一次 ——
启动阶段的配置错误（缺密钥、阈值矛盾、目录不可写）当场就炸，不拖到第一个请求才炸。
"""

from __future__ import annotations

from fastapi import FastAPI

from agent_architecture.composition import build_container

from .app import create_app

# uvicorn 找的那个对象。
#
# 举例：uv run uvicorn agent_architecture.api.main:app --port 8000
app: FastAPI = create_app(build_container())

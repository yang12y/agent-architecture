"""L4 接入层：HTTP 一元调用 + SSE 流与事件。

只依赖 L3（composition）与 L0（contracts）—— 不直接碰任何维度模块。
"""

from .app import create_app

__all__ = ["create_app"]

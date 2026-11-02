"""HttpTransport 的实现：一个进程一条连接池。"""

from .client import DEFAULT_TIMEOUT_SECONDS, HttpxTransport, build_http_transport

__all__ = ["DEFAULT_TIMEOUT_SECONDS", "HttpxTransport", "build_http_transport"]

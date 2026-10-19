"""接口层的错误：状态码 + §7.8 的错误码。路由抛它，由 app 的处理器转成响应体。"""

from __future__ import annotations


class ApiError(Exception):
    """一次请求该怎么失败。

    举例：raise ApiError(404, "session_not_found", "会话不存在：sess_x")
    """

    def __init__(self, status_code: int, code: str, message: str) -> None:
        """连状态码一起带上，处理器就不用再猜。

        举例：ApiError(409, "session_busy", "会话正在跑一次 trace")
        """
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def session_not_found(session_id: str) -> ApiError:
    """会话不存在，**或者不属于这个 user_id** —— 两种情况回同一句。

    分开回话等于告诉别人"这个 id 确实存在"。

    举例：raise session_not_found("sess_x")
    """
    return ApiError(404, "session_not_found", f"会话不存在：{session_id}")


def approval_not_found(request_id: str) -> ApiError:
    """审批请求不存在、已经处理过，或者不属于这个会话 —— 三种情况回同一句。

    举例：raise approval_not_found("appr_x")
    """
    return ApiError(404, "approval_not_found", f"审批请求不存在或已处理：{request_id}")


def session_busy(session_id: str) -> ApiError:
    """这个会话正在跑一次 trace，暂时不收新消息。

    举例：raise session_busy("sess_x")
    """
    return ApiError(409, "session_busy", f"会话正在跑一次 trace：{session_id}")

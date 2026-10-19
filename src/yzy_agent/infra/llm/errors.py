"""把 OpenAI SDK 的异常翻译成契约层的异常。

分类**只在这里做一次**：SDK 那一堆类型在这里收敛成 LlmCallError.kind，
主循环按 kind 决定是重试还是直接上抛，不需要认识任何 SDK 类型。

映射表（左 -> 右，右为"种类 + 处置"）：

    AuthenticationError                     -> AUTH        不重试
    PermissionDeniedError                   -> AUTH        不重试
    NotFoundError                           -> REQUEST     不重试
    BadRequestError 且说的是上下文放不下    -> OVERFLOW    不重试
    BadRequestError 其余                    -> REQUEST     不重试
    RateLimitError                          -> RATE_LIMIT  重试
    APITimeoutError                         -> TIMEOUT     重试
    APIConnectionError                      -> CONNECTION  重试
    APIStatusError 且 status >= 500         -> SERVER      重试
    APIStatusError 且 status < 500          -> REQUEST     不重试
    其余（含认不出来的普通异常）            -> UNKNOWN     不重试
"""

from __future__ import annotations

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)

from yzy_agent.contracts import AgentError, ContextOverflowError, LlmCallError, LlmErrorKind

# 错误信息里出现这些片段，就认定是"超出上下文窗口"。
#
# 各家措辞不统一，所以只能按关键词认。
# 举例：错误信息含 "maximum context length is 65536 tokens" -> 命中
OVERFLOW_MARKERS = (
    "context length",
    "context_length_exceeded",
    "maximum context",
    "too many tokens",
    "reduce the length",
    "exceeded the maximum",
)

# 状态码达到它就算服务端的锅。
#
# 举例：503 -> SERVER；400 -> REQUEST
SERVER_ERROR_FLOOR = 500


def translate_error(exc: BaseException) -> AgentError:
    """把一个 SDK 异常翻译成契约层异常。

    判定的顺序有讲究：下面大多数类型都是 APIStatusError 的子类，必须先判更具体的那个。
    举例：translate_error(RateLimitError(...)) -> LlmCallError(kind=RATE_LIMIT)
    """
    if isinstance(exc, AuthenticationError):
        return LlmCallError(
            "鉴权失败：检查 .env 里的 YZY_LLM_API_KEY 与 YZY_LLM_BASE_URL",
            kind=LlmErrorKind.AUTH,
        )

    if isinstance(exc, PermissionDeniedError):
        return LlmCallError(f"这个密钥没有访问权限：{exc}", kind=LlmErrorKind.AUTH)

    if isinstance(exc, NotFoundError):
        return LlmCallError(f"模型或接口不存在：{exc}", kind=LlmErrorKind.REQUEST)

    if isinstance(exc, BadRequestError):
        if is_context_overflow(exc):
            return ContextOverflowError(f"超出上下文窗口：{exc}")
        return LlmCallError(f"请求被拒绝：{exc}", kind=LlmErrorKind.REQUEST)

    if isinstance(exc, RateLimitError):
        return LlmCallError(f"被限流：{exc}", kind=LlmErrorKind.RATE_LIMIT)

    # APITimeoutError 是 APIConnectionError 的子类，所以它排在前面。
    if isinstance(exc, APITimeoutError):
        return LlmCallError(f"请求超时：{exc}", kind=LlmErrorKind.TIMEOUT)

    if isinstance(exc, APIConnectionError):
        return LlmCallError(f"连不上模型服务：{exc}", kind=LlmErrorKind.CONNECTION)

    if isinstance(exc, APIStatusError):
        server_side = exc.status_code >= SERVER_ERROR_FLOOR
        kind = LlmErrorKind.SERVER if server_side else LlmErrorKind.REQUEST
        return LlmCallError(f"模型服务返回 {exc.status_code}：{exc}", kind=kind)

    # 认不出来的一律不重试：赌一次可能白花钱，还会把同一个错误重放三遍。
    return LlmCallError(f"模型调用失败：{exc}", kind=LlmErrorKind.UNKNOWN)


def is_context_overflow(exc: BaseException) -> bool:
    """判断这个错误是不是"超出上下文窗口"。

    先看错误码，再看错误文本——两边的措辞各家不同，认得出就行。
    举例：错误码为 "context_length_exceeded" -> True
    """
    code = getattr(exc, "code", None)
    if isinstance(code, str) and "context" in code:
        return True
    text = str(exc).lower()
    return any(marker in text for marker in OVERFLOW_MARKERS)

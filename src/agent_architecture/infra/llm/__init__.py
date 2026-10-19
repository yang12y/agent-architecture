"""模型接入实现：OpenAI 兼容的流式客户端。"""

from .client import (
    FINISH_REASON_TO_STOP,
    OpenAiCompatibleClient,
    build_openai_client,
    build_payload,
    parse_arguments,
)
from .errors import OVERFLOW_MARKERS, is_context_overflow, translate_error
from .messages import to_openai_messages

__all__ = [
    "OpenAiCompatibleClient",
    "build_openai_client",
    "build_payload",
    "parse_arguments",
    "FINISH_REASON_TO_STOP",
    "to_openai_messages",
    "translate_error",
    "is_context_overflow",
    "OVERFLOW_MARKERS",
]

"""测试用的假实现。

约定：假实现只放在这里，测试文件不自己现造轮子。
每个假实现都实现 contracts 里的某个 Protocol，且行为完全确定（无随机、无真实时钟）。
"""

from .approvals import FakeApprovals
from .clock import FakeClock
from .context import (
    make_app_config,
    make_frozen_config,
    make_ports,
    make_runtime_state,
    make_site_context,
    make_trace_context,
)
from .handlers import HandlerSpy, RecordingHandler
from .ids import SequentialIds
from .openai_sdk import REQUEST, FakeOpenAiSdk, chunk, http_response
from .scripted_llm import (
    BlockingLlm,
    FailingLlm,
    PartialThenFailLlm,
    ScriptedLlm,
    StopDuringStreamLlm,
    text_turn,
    thought_turn,
    tool_turn,
)
from .session import (
    START,
    USER,
    StubSession,
    assistant_message,
    make_entry,
    make_session_meta,
    tool_message,
    user_message,
)
from .tools import FakeToolRegistry, make_tool_declaration

__all__ = [
    "FakeClock",
    "FakeApprovals",
    "SequentialIds",
    "START",
    "USER",
    "StubSession",
    "assistant_message",
    "make_entry",
    "make_session_meta",
    "tool_message",
    "user_message",
    "FakeToolRegistry",
    "FakeOpenAiSdk",
    "chunk",
    "http_response",
    "REQUEST",
    "make_tool_declaration",
    "ScriptedLlm",
    "FailingLlm",
    "PartialThenFailLlm",
    "BlockingLlm",
    "StopDuringStreamLlm",
    "text_turn",
    "thought_turn",
    "tool_turn",
    "RecordingHandler",
    "HandlerSpy",
    "make_ports",
    "make_frozen_config",
    "make_runtime_state",
    "make_trace_context",
    "make_site_context",
    "make_app_config",
]

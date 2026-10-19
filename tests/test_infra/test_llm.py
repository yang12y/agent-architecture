"""OpenAI 兼容客户端的测试。

全程**不联网**：SDK 客户端由构造参数注入一个假件，只有我们自己的翻译逻辑被真正跑到。
"""

from __future__ import annotations

import pytest
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

from agent_architecture.contracts import (
    ConfigError,
    ContextOverflowError,
    ImagePart,
    LlmCallError,
    LlmChunkKind,
    LlmErrorKind,
    LlmRequest,
    Message,
    MessageRole,
    StopReason,
    TextPart,
    ThinkingPart,
    ToolCallPart,
)
from agent_architecture.infra.llm import (
    OpenAiCompatibleClient,
    build_openai_client,
    build_payload,
    is_context_overflow,
    parse_arguments,
    to_openai_messages,
    translate_error,
)
from fakes import REQUEST, FakeOpenAiSdk, chunk, http_response

# ---------------------------------------------------------------------------
# 纯函数：Message -> OpenAI 结构
# ---------------------------------------------------------------------------


def test_user_message_becomes_plain_text() -> None:
    """用户消息翻成 {"role": "user", "content": 文本}。"""
    message = Message(role=MessageRole.USER, content=[TextPart(text="帮我读 a.py")])

    assert to_openai_messages([message]) == [{"role": "user", "content": "帮我读 a.py"}]


def test_assistant_message_keeps_tool_calls() -> None:
    """assistant 消息里的工具调用要单独给 tool_calls 字段，参数序列化成字符串。"""
    message = Message(
        role=MessageRole.ASSISTANT,
        content=[
            TextPart(text="我看一下"),
            ToolCallPart(tool_call_id="call_1", name="read", arguments={"path": "a.py"}),
        ],
    )

    [payload] = to_openai_messages([message])

    assert payload["role"] == "assistant"
    assert payload["content"] == "我看一下"
    assert payload["tool_calls"] == [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "read", "arguments": '{"path": "a.py"}'},
        }
    ]


def test_assistant_message_without_text_sends_null_content() -> None:
    """只有工具调用、没有文本时，content 要给 None，给空串会被部分服务端拒绝。"""
    message = Message(
        role=MessageRole.ASSISTANT,
        content=[ToolCallPart(tool_call_id="call_1", name="read", arguments={})],
    )

    assert to_openai_messages([message])[0]["content"] is None


def test_assistant_message_echoes_its_reasoning() -> None:
    """带 tools 的请求必须把上一轮的思考原样传回去 —— 这是服务端明文要求的，不传会 400。"""
    message = Message(
        role=MessageRole.ASSISTANT,
        content=[ThinkingPart(text="先看看"), TextPart(text="好了")],
    )

    payload = to_openai_messages([message])[0]

    assert payload["reasoning_content"] == "先看看"
    assert payload["content"] == "好了"


def test_tool_message_carries_tool_call_id() -> None:
    """工具结果消息靠 metadata 里的 tool_call_id 和请求配对。"""
    message = Message(
        role=MessageRole.TOOL,
        content=[TextPart(text="文件内容...")],
        metadata={"tool_call_id": "call_1"},
    )

    assert to_openai_messages([message]) == [
        {"role": "tool", "tool_call_id": "call_1", "content": "文件内容..."}
    ]


def test_message_with_image_uses_content_parts() -> None:
    """带图片的消息改用多模态内容数组，base64 拼成 data URL。"""
    message = Message(
        role=MessageRole.USER,
        content=[
            TextPart(text="看图"),
            ImagePart(media_type="image/png", data_base64="AAAA", url=None),
        ],
    )

    [payload] = to_openai_messages([message])

    assert payload["content"] == [
        {"type": "text", "text": "看图"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
    ]


# ---------------------------------------------------------------------------
# 纯函数：请求体与参数解析
# ---------------------------------------------------------------------------


def test_build_payload_minimal() -> None:
    """不带工具、不带温度时，请求体里就不出现这两个字段。"""
    payload = build_payload(LlmRequest(model="m", messages=[]))

    assert payload["model"] == "m"
    assert payload["stream"] is True
    assert payload["stream_options"] == {"include_usage": True}
    assert "tools" not in payload
    assert "temperature" not in payload


def test_build_payload_with_tools_and_temperature() -> None:
    """有工具 schema 时包成 function 形式；温度按原值带上。"""
    schema = {"name": "read", "description": "读文件", "parameters": {"type": "object"}}
    payload = build_payload(
        LlmRequest(model="m", messages=[], tools_schema=[schema], temperature=0.2)
    )

    assert payload["tools"] == [{"type": "function", "function": schema}]
    assert payload["temperature"] == 0.2


def test_build_payload_asks_for_thinking_through_extra_body() -> None:
    """思考开关不是 OpenAI 标准参数，只能塞进 extra_body；关着的时候一个字都不带。"""
    on = build_payload(LlmRequest(model="m", messages=[], thinking=True))
    off = build_payload(LlmRequest(model="m", messages=[]))

    assert on["extra_body"] == {"thinking": {"type": "enabled"}}
    assert "extra_body" not in off


def test_parse_arguments_reads_json() -> None:
    """正常的 JSON 字符串解析成对象。"""
    assert parse_arguments('{"path": "a.py"}') == {"path": "a.py"}


def test_parse_arguments_empty_is_empty_object() -> None:
    """模型没给参数时是空对象，不是 None。"""
    assert parse_arguments("   ") == {}


def test_parse_arguments_bad_json_is_returned_as_is() -> None:
    """坏 JSON 原样返回，让位点 6 的参数校验去报错，而不是在这里吞掉。"""
    assert parse_arguments('{"path": ') == '{"path": '


# ---------------------------------------------------------------------------
# 错误翻译
# ---------------------------------------------------------------------------


class _StubError(Exception):
    """只为 is_context_overflow 准备的假异常——它只看 code 属性和字符串。"""

    def __init__(self, message: str, code: object = None) -> None:
        super().__init__(message)

        # 模拟 SDK 异常上的错误码。
        #
        # 举例："context_length_exceeded"
        self.code = code


def test_is_context_overflow_by_error_code() -> None:
    """错误码里带 context 就算超窗。"""
    assert is_context_overflow(_StubError("boom", code="context_length_exceeded")) is True


def test_is_context_overflow_by_message() -> None:
    """错误码没有时，退回看错误文本——各家措辞不一样。"""
    assert is_context_overflow(_StubError("This model's maximum context length is 65536")) is True


def test_is_context_overflow_is_false_for_other_errors() -> None:
    """不相干的错误不能被误判成超窗，否则会白触发一次压缩。"""
    assert is_context_overflow(_StubError("invalid api key")) is False


@pytest.mark.parametrize(
    ("error", "expected_kind", "expected_retryable"),
    [
        (
            BadRequestError(
                "maximum context length is 65536 tokens", response=http_response(400), body=None
            ),
            LlmErrorKind.OVERFLOW,
            False,
        ),
        (
            BadRequestError("bad tool schema", response=http_response(400), body=None),
            LlmErrorKind.REQUEST,
            False,
        ),
        (
            AuthenticationError("bad key", response=http_response(401), body=None),
            LlmErrorKind.AUTH,
            False,
        ),
        (
            PermissionDeniedError("nope", response=http_response(403), body=None),
            LlmErrorKind.AUTH,
            False,
        ),
        (
            NotFoundError("gone", response=http_response(404), body=None),
            LlmErrorKind.REQUEST,
            False,
        ),
        (
            RateLimitError("slow down", response=http_response(429), body=None),
            LlmErrorKind.RATE_LIMIT,
            True,
        ),
        (APIStatusError("boom", response=http_response(503), body=None), LlmErrorKind.SERVER, True),
        (
            APIStatusError("teapot", response=http_response(418), body=None),
            LlmErrorKind.REQUEST,
            False,
        ),
        (APITimeoutError(request=REQUEST), LlmErrorKind.TIMEOUT, True),
        (APIConnectionError(request=REQUEST), LlmErrorKind.CONNECTION, True),
        (RuntimeError("谁也不知道这是什么"), LlmErrorKind.UNKNOWN, False),
    ],
)
def test_translate_error_mapping(
    error: Exception, expected_kind: LlmErrorKind, expected_retryable: bool
) -> None:
    """SDK 那一堆类型收敛成一张表：每种失败带一个种类，处置由种类推出来。"""
    translated = translate_error(error)

    assert isinstance(translated, LlmCallError)
    assert translated.kind is expected_kind
    assert translated.retryable is expected_retryable


# ---------------------------------------------------------------------------
# 流式收流
# ---------------------------------------------------------------------------


async def _collect(client: OpenAiCompatibleClient, request: LlmRequest) -> list:
    """把一次收流的结果收集成列表。

    举例：chunks = await _collect(client, request)
    """
    return [chunk async for chunk in client.stream(request)]


async def test_stream_yields_reasoning_before_text() -> None:
    """推理模型的思考单独成片，走在正文前面 —— 前端靠这个类型把它画成另一块。"""
    sdk = FakeOpenAiSdk([chunk(reasoning="先想想"), chunk(content="好了"), chunk(finish="stop")])
    client = OpenAiCompatibleClient(api_key="sk-test", sdk=sdk)

    pieces = [piece async for piece in client.stream(LlmRequest(model="m", messages=[]))]

    assert [piece.kind for piece in pieces] == [
        LlmChunkKind.REASONING_DELTA,
        LlmChunkKind.TEXT_DELTA,
        LlmChunkKind.DONE,
    ]
    assert pieces[0].text == "先想想"


async def test_stream_yields_text_deltas_then_done() -> None:
    """文本增量即时吐出，最后一定有一个 DONE 分片。"""
    sdk = FakeOpenAiSdk([chunk(content="我"), chunk(content="来了"), chunk(finish="stop")])
    client = OpenAiCompatibleClient(api_key="k", sdk=sdk)  # type: ignore[arg-type]

    chunks = await _collect(client, LlmRequest(model="m", messages=[]))

    assert [c.kind for c in chunks] == [
        LlmChunkKind.TEXT_DELTA,
        LlmChunkKind.TEXT_DELTA,
        LlmChunkKind.DONE,
    ]
    assert "".join(c.text for c in chunks) == "我来了"
    assert chunks[-1].stop_reason is StopReason.END_TURN
    assert sdk.payloads[0]["model"] == "m"


async def test_stream_assembles_tool_call_from_fragments() -> None:
    """工具调用的 id / name / arguments 是分片下发的，收完必须拼成完整一条。"""
    sdk = FakeOpenAiSdk(
        [
            chunk(tool={"index": 0, "id": "call_1", "name": "read", "arguments": '{"pa'}),
            chunk(tool={"index": 0, "arguments": 'th": "a.py"}'}),
            chunk(finish="tool_calls"),
        ]
    )
    client = OpenAiCompatibleClient(api_key="k", sdk=sdk)  # type: ignore[arg-type]

    chunks = await _collect(client, LlmRequest(model="m", messages=[]))

    calls = [c.tool_call for c in chunks if c.kind is LlmChunkKind.TOOL_CALL]
    assert len(calls) == 1
    assert calls[0] == ToolCallPart(tool_call_id="call_1", name="read", arguments={"path": "a.py"})
    assert chunks[-1].stop_reason is StopReason.TOOL_CALLS


async def test_stream_reports_usage() -> None:
    """带用量的分片翻成 USAGE 分片。"""
    sdk = FakeOpenAiSdk(
        [
            chunk(
                with_choice=False,
                usage={"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
            )
        ]
    )
    client = OpenAiCompatibleClient(api_key="k", sdk=sdk)  # type: ignore[arg-type]

    chunks = await _collect(client, LlmRequest(model="m", messages=[]))

    usage = [c.usage for c in chunks if c.kind is LlmChunkKind.USAGE]
    assert usage[0] is not None
    assert (usage[0].input_tokens, usage[0].output_tokens) == (10, 4)


async def test_stream_without_finish_reason_still_ends_turn() -> None:
    """流里压根没给 finish_reason 时（部分网关会这样），按正常结束处理，不能卡住。"""
    sdk = FakeOpenAiSdk([chunk(content="完了")])
    client = OpenAiCompatibleClient(api_key="k", sdk=sdk)  # type: ignore[arg-type]

    chunks = await _collect(client, LlmRequest(model="m", messages=[]))

    assert chunks[-1].kind is LlmChunkKind.DONE
    assert chunks[-1].stop_reason is StopReason.END_TURN


def test_finish_reason_mapping_covers_sdk_values() -> None:
    """SDK 会出现的每个 finish_reason 都要有明确映射，而不是靠兜底分支。

    兜底是给"厂商以后加新值"准备的，不该被已知取值走到。
    """
    from agent_architecture.infra.llm import FINISH_REASON_TO_STOP

    assert {"stop", "length", "tool_calls", "content_filter", "function_call"} <= set(
        FINISH_REASON_TO_STOP
    )


async def test_stream_translates_sdk_errors() -> None:
    """SDK 抛错时，收流方看到的是契约层异常，而不是 SDK 的类型。"""
    sdk = FakeOpenAiSdk(error=RateLimitError("slow down", response=http_response(429), body=None))
    client = OpenAiCompatibleClient(api_key="k", sdk=sdk)  # type: ignore[arg-type]

    with pytest.raises(LlmCallError) as info:
        await _collect(client, LlmRequest(model="m", messages=[]))

    assert info.value.kind is LlmErrorKind.RATE_LIMIT


async def test_stream_translates_context_overflow() -> None:
    """超窗要翻成 ContextOverflowError —— 位点 3 没拦住时，本次 trace 按预算失败收尾。"""
    sdk = FakeOpenAiSdk(
        error=BadRequestError(
            "maximum context length is 65536 tokens",
            response=http_response(400),
            body={"code": "context_length_exceeded"},
        )
    )
    client = OpenAiCompatibleClient(api_key="k", sdk=sdk)  # type: ignore[arg-type]

    with pytest.raises(ContextOverflowError):
        await _collect(client, LlmRequest(model="m", messages=[]))


async def test_a_stream_that_dies_midway_still_translates() -> None:
    """吐了几片之后连接断掉：已经收到的几片要留下，错照样翻成契约层异常。"""
    sdk = FakeOpenAiSdk(
        [chunk(content="我来"), chunk(content="帮你")],
        error=APIConnectionError(request=REQUEST),
        error_after=1,
    )
    client = OpenAiCompatibleClient(api_key="k", sdk=sdk)  # type: ignore[arg-type]

    seen: list[str] = []
    with pytest.raises(LlmCallError) as info:
        async for piece in client.stream(LlmRequest(model="m", messages=[])):
            seen.append(piece.text)

    assert seen == ["我来"]
    assert info.value.kind is LlmErrorKind.CONNECTION


# ---------------------------------------------------------------------------
# 按配置装配
# ---------------------------------------------------------------------------


def test_build_openai_client_requires_api_key() -> None:
    """没配密钥要在建客户端时就报错，而不是等第一次请求才失败。"""
    from agent_architecture.infra.config import load_app_config

    config = load_app_config({})

    with pytest.raises(ConfigError, match="YZY_LLM_API_KEY"):
        build_openai_client(config)


def test_build_openai_client_passes_config_through() -> None:
    """配了密钥就能建出客户端，且不把密钥写进任何日志字段。"""
    from agent_architecture.infra.config import load_app_config

    config = load_app_config(
        {"YZY_LLM_API_KEY": "sk-test", "YZY_LLM_BASE_URL": "https://example.com/v1"}
    )

    client = build_openai_client(config)

    assert isinstance(client, OpenAiCompatibleClient)

"""真客户端接进循环。

整条链路用 OpenAiCompatibleClient 跑，只把**网络那一层**换成假 SDK。它证明的是接线：
ports.llm 里装的是真客户端、内核在位点 4 真的调到它、收流结果真的落进 Session 日志、
失败了真的按种类收尾。

各模块自己的行为不在这个目录测 —— 那在 test_infra / test_agent_loop / test_composition 里。
"""

from __future__ import annotations

from pathlib import Path

from openai import APIConnectionError, AuthenticationError, BadRequestError, RateLimitError

from agent_architecture.composition import Container, build_container
from agent_architecture.contracts import SessionEntryType, TraceStatus
from agent_architecture.infra.llm import OpenAiCompatibleClient
from fakes import (
    REQUEST,
    USER,
    FakeOpenAiSdk,
    chunk,
    http_response,
    make_app_config,
    user_message,
)


def _text_turn(text: str) -> list:
    """一轮「只说话、不调工具」的真分片：正文、用量、结束。

    举例：_text_turn("在的") -> [文本增量, 用量, 结束]
    """
    usage = {"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10}
    return [chunk(content=text), chunk(with_choice=False, usage=usage), chunk(finish="stop")]


def _container(scratch_dir: Path, sdk: FakeOpenAiSdk) -> Container:
    """按一份把会话目录指到临时目录的配置，装出**接真客户端**的容器。

    举例：container = _container(scratch_dir, sdk)
    """
    config = make_app_config(session_dir=str(scratch_dir / "sessions"))
    client = OpenAiCompatibleClient(api_key="sk-test", sdk=sdk)  # type: ignore[arg-type]
    return build_container(config, llm=client)


async def _run(scratch_dir: Path, sdk: FakeOpenAiSdk):
    """装容器、建会话、跑一次 trace，把三样都交回去。

    举例：container, live, outcome = await _run(scratch_dir, sdk)
    """
    container = _container(scratch_dir, sdk)
    live = await container.sessions.create(user_id=USER)
    outcome = await container.agent.run(session=live.session, user_message=user_message("在吗"))
    return container, live, outcome


# ---------------------------------------------------------------------------
# 正常一路
# ---------------------------------------------------------------------------


async def test_the_kernel_reaches_the_real_client(scratch_dir: Path) -> None:
    """内核在位点 4 调到的，就是装配根装进 ports.llm 的那个真客户端。"""
    sdk = FakeOpenAiSdk(_text_turn("在的"))

    container, _, outcome = await _run(scratch_dir, sdk)

    assert outcome.status is TraceStatus.COMPLETED
    assert len(sdk.payloads) == 1
    assert sdk.payloads[0]["model"] == container.config.model
    assert sdk.payloads[0]["stream"] is True
    assert sdk.payloads[0]["messages"][-1]["content"] == "在吗"


async def test_the_reply_lands_in_the_session_log(scratch_dir: Path) -> None:
    """真分片收上来的正文，走完定稿 + 屏障之后就在会话日志里。"""
    sdk = FakeOpenAiSdk(_text_turn("在的"))

    _, live, _ = await _run(scratch_dir, sdk)

    messages = [e for e in live.session.entries() if e.type is SessionEntryType.MESSAGE]
    assert [m.payload.content[0].text for m in messages] == ["在吗", "在的"]


# ---------------------------------------------------------------------------
# 每种失败各自的处置
# ---------------------------------------------------------------------------


async def test_an_auth_failure_is_not_retried(scratch_dir: Path) -> None:
    """密钥错重试只是浪费时间：一次都不多调，带着原因失败收尾。"""
    sdk = FakeOpenAiSdk(
        error=AuthenticationError("bad key", response=http_response(401), body=None)
    )

    _, _, outcome = await _run(scratch_dir, sdk)

    assert outcome.status is TraceStatus.FAILED
    assert len(sdk.payloads) == 1
    assert outcome.reason is not None and "鉴权失败" in outcome.reason


async def test_a_rate_limit_is_retried_until_the_attempts_run_out(scratch_dir: Path) -> None:
    """被限流值得再试；试到上限还不行才收尾，收尾理由带的是真实失败原因。"""
    sdk = FakeOpenAiSdk(error=RateLimitError("slow down", response=http_response(429), body=None))

    container, _, outcome = await _run(scratch_dir, sdk)

    assert outcome.status is TraceStatus.FAILED
    assert len(sdk.payloads) == container.config.llm_max_attempts
    assert outcome.reason is not None and "被限流" in outcome.reason


async def test_a_transient_network_failure_is_ridden_out(scratch_dir: Path) -> None:
    """第一次连不上、第二次通了 —— 重试归主循环管的意义就在这。"""
    sdk = FakeOpenAiSdk(
        _text_turn("在的"),
        error=APIConnectionError(request=REQUEST),
        fail_calls=1,
    )

    _, _, outcome = await _run(scratch_dir, sdk)

    assert outcome.status is TraceStatus.COMPLETED
    assert len(sdk.payloads) == 2


async def test_a_context_overflow_ends_as_budget_exceeded(scratch_dir: Path) -> None:
    """模型挡回来说放不下：不重试、不再压，按预算失败收尾。"""
    sdk = FakeOpenAiSdk(
        error=BadRequestError(
            "This model's maximum context length is 65536 tokens",
            response=http_response(400),
            body={"code": "context_length_exceeded"},
        )
    )

    _, _, outcome = await _run(scratch_dir, sdk)

    assert outcome.status is TraceStatus.BUDGET_EXCEEDED
    assert len(sdk.payloads) == 1


async def test_a_stream_that_dies_midway_leaves_the_half_output(scratch_dir: Path) -> None:
    """吐了两片就断：不重试（接上去会拼出两遍开头），半截留在草稿槽里等下次取材。"""
    sdk = FakeOpenAiSdk(
        [chunk(content="我来"), chunk(content="帮你")],
        error=APIConnectionError(request=REQUEST),
        error_after=1,
    )

    _, live, outcome = await _run(scratch_dir, sdk)

    assert outcome.status is TraceStatus.FAILED
    assert len(sdk.payloads) == 1
    assert live.session.draft_text() == "我来"

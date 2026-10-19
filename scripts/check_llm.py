"""对着真实模型跑一次最小调用，确认 .env 里的配置能用。

用法（在项目根目录执行，因为 .env 是相对当前工作目录找的）：

    uv run python scripts/check_llm.py

只做一件事：发一条最短的消息，把回复与用量打出来。
**不会打印密钥**——打出来的都是契约层异常，里面只有原因、没有凭据。
"""

from __future__ import annotations

import asyncio
import sys

from agent_architecture.contracts import (
    AgentError,
    ConfigError,
    LlmChunkKind,
    LlmRequest,
    Message,
    MessageRole,
    TextPart,
)
from agent_architecture.infra.config import load_app_config
from agent_architecture.infra.llm import build_openai_client


async def main() -> int:
    """发一次最小请求并打印结果。

    举例：exit_code = asyncio.run(main())
    """
    config = load_app_config()
    print(f"模型      : {config.model}")
    print(f"接入地址  : {config.llm_base_url or '(用官方默认)'}")
    print(f"密钥      : {'已配置' if config.llm_api_key else '未配置'}")
    print(f"超时      : {config.llm_timeout_seconds} 秒")
    print("-" * 44)

    client = build_openai_client(config)
    request = LlmRequest(
        model=config.model,
        messages=[Message(role=MessageRole.USER, content=[TextPart(text="回复两个字：收到")])],
    )

    text = ""
    stop = None
    usage = None
    async for chunk in client.stream(request):
        if chunk.kind is LlmChunkKind.TEXT_DELTA:
            text += chunk.text
            print(chunk.text, end="", flush=True)
        elif chunk.kind is LlmChunkKind.USAGE:
            usage = chunk.usage
        elif chunk.kind is LlmChunkKind.DONE:
            stop = chunk.stop_reason

    print()
    print("-" * 44)
    print(f"结束原因  : {stop.value if stop else '(流里没给)'}")
    if usage is not None:
        print(f"用量      : 输入 {usage.input_tokens} / 输出 {usage.output_tokens}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except ConfigError as exc:
        print(f"配置有问题：{exc}", file=sys.stderr)
        sys.exit(2)
    except AgentError as exc:
        print(f"调用失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)

"""把当前注册的全部 handler 打成 markdown 表格。

用法（项目根目录）：

    uv run python scripts/dump_handlers.py

输出就是 docs/HANDLERS.md 里的那两张表。加了 handler 之后重跑一次、
把结果贴回去，文档就不会过期。

它读的是**装配根真正装出来的那张表**，不是某个模块自己声明的 —— 所以看到的就是跑起来的。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from yzy_agent.composition import build_container
from yzy_agent.contracts import LlmChunk, LlmRequest, Site
from yzy_agent.infra.config import load_app_config


class _NoLlm:
    """一个不会被调到的模型端口：这份文档只关心 handler 表，不跑 trace。

    举例：build_container(load_app_config({}), llm=_NoLlm())
    """

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmChunk]:
        """见 contracts.LlmClient.stream。

        举例：async for chunk in client.stream(request): ...
        """
        raise AssertionError("dump_handlers 不该调模型")
        yield  # pragma: no cover - 上面那行不会返回，这里只是让它是异步生成器


def main() -> None:
    """打出两张表。

    举例：main()
    """
    registry = build_container(load_app_config({}), llm=_NoLlm()).pipeline.registry

    print("### 一个位点一张表（已按 priority 排好）")
    for site in Site:
        print(f"\n**`{site.value}`**\n")
        print("| priority | 模块 | handler id |")
        print("|---|---|---|")
        for handler in registry.for_site(site):
            print(f"| {handler.priority} | {handler.id.split(chr(46))[0]} | `{handler.id}` |")


if __name__ == "__main__":
    main()

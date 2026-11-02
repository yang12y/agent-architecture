"""工具注册表：声明与实现放在一起，对外只露声明。

`contracts.ToolRegistry` 是**只读视角** —— 沙箱按它查声明、位点 3 按它取 schema。
写入只发生在这里，所以"运行期注册表不再变"是天然的，不需要额外的冻结动作。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from agent_frame.contracts import (
    ToolContext,  # noqa: F401  转出给 tool_calling/__init__：现场类型已搬到 contracts
    ToolDeclaration,
    ToolRunner,
)

# 工具实现的老名字。现场与签名都搬到了 contracts（业务工具只允许依赖 contracts），
# 这里只转出 —— 老代码 from ...tool_calling.registry import ToolContext 照样能用。
#
# 举例：ToolFn == ToolRunner -> True
ToolFn = ToolRunner


@dataclass(frozen=True, slots=True)
class Tool:
    """注册表里的一条工具：声明 + 实现。**声明是给外面的，实现只留在这里。**"""

    # 静态事实。沙箱判越界、位点 3 取 schema、重试判定都读它。
    #
    # 举例：ToolDeclaration(name="read", side_effect=SideEffect.NONE, ...)
    declaration: ToolDeclaration

    # 真正干活的那段。
    #
    # 举例：read
    run: ToolFn


class ToolCatalog:
    """工具注册表。装配期 `register()`，运行期只读。

    它**结构化地满足** `contracts.ToolRegistry`（`get` + `declarations`），不显式继承 ——
    端口是给消费方看的，实现方不必反过来依赖它。

    举例：catalog = ToolCatalog(); catalog.register(READ, read)
    """

    def __init__(self) -> None:
        """建一张空表。

        举例：ToolCatalog()
        """
        # 工具名 -> 工具。
        #
        # 举例：{"read": Tool(declaration=..., run=read)}
        self._tools: dict[str, Tool] = {}

    def register(self, declaration: ToolDeclaration, run: ToolFn) -> None:
        """登记一条工具。

        举例：catalog.register(make_declaration("read"), read)
        出错：名字已被占用时抛 ValueError —— 同名工具会让模型与排错都无从下手。
        """
        if declaration.name in self._tools:
            raise ValueError(f"工具名重复：{declaration.name}")
        self._tools[declaration.name] = Tool(declaration=declaration, run=run)

    def get(self, name: str) -> ToolDeclaration | None:
        """见 contracts.ToolRegistry.get。

        举例：catalog.get("read").name -> "read"
        """
        tool = self._tools.get(name)
        return None if tool is None else tool.declaration

    def tool(self, name: str) -> Tool | None:
        """连实现一起取出来 —— 执行要用它，沙箱不该用。

        举例：catalog.tool("read").run -> read
        """
        return self._tools.get(name)

    def declarations(self, scope: str | None = None) -> Sequence[ToolDeclaration]:
        """见 contracts.ToolRegistry.declarations：scope 为 None 时给全部。

        举例：catalog.declarations(scope="coding") -> [ToolDeclaration(...)]
        """
        return [
            tool.declaration
            for tool in self._tools.values()
            if scope is None or tool.declaration.scope is None or tool.declaration.scope == scope
        ]

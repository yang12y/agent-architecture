"""工具注册表的假件。"""

from __future__ import annotations

from collections.abc import Sequence

from agent_architecture.contracts import Permission, SideEffect, ToolDeclaration


def make_tool_declaration(
    name: str = "read",
    *,
    properties: Sequence[str] = (),
    required: Sequence[str] | None = None,
    side_effect: SideEffect = SideEffect.NONE,
    accepts_idempotency_key: bool = False,
    required_permission: Permission = Permission.READ_ONLY,
    scope: str | None = None,
) -> ToolDeclaration:
    """造一条字段齐全的工具声明，只把关心的那几项留成参数。

    properties 是 schema 里声明的参数名 —— 沙箱按它判"这工具能碰什么"。
    required 缺省就是全部 properties（内置工具的参数基本都必填，有缺省的才另说）。

    举例：make_tool_declaration("write", properties=["path"], side_effect=SideEffect.NON_IDEMPOTENT)
    """
    return ToolDeclaration(
        name=name,
        description=f"假的 {name} 工具。",
        parameters_schema={
            "type": "object",
            "properties": {name: {"type": "string"} for name in properties},
            "required": list(properties if required is None else required),
        },
        output_contract="返回文本。",
        side_effect=side_effect,
        accepts_idempotency_key=accepts_idempotency_key,
        required_permission=required_permission,
        scope=scope,
    )


class FakeToolRegistry:
    """只读视角的工具注册表，按名字查。

    举例：registry = FakeToolRegistry([make_tool_declaration("read")])
          registry.get("read").name -> "read"
    """

    def __init__(self, declarations: Sequence[ToolDeclaration] = ()) -> None:
        """按名字建索引。

        举例：FakeToolRegistry([make_tool_declaration("read")])
        """
        # 名字 -> 声明。
        #
        # 举例：{"read": ToolDeclaration(name="read", ...)}
        self._by_name: dict[str, ToolDeclaration] = {d.name: d for d in declarations}

    def get(self, name: str) -> ToolDeclaration | None:
        """见 contracts.ToolRegistry.get。

        举例：registry.get("read") -> ToolDeclaration(name="read", ...)
        """
        return self._by_name.get(name)

    def declarations(self, scope: str | None = None) -> list[ToolDeclaration]:
        """见 contracts.ToolRegistry.declarations。scope 为 None 时给全部。

        举例：registry.declarations(scope="coding") -> [ToolDeclaration(...)]
        """
        if scope is None:
            return list(self._by_name.values())
        return [d for d in self._by_name.values() if d.scope is None or d.scope == scope]

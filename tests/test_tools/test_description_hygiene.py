"""工具描述里的自引用规矩：工具名由框架给，描述里不必重复自己。

跨工具引用（"整份重写用 write"这类）在这套工具里是**故意留的引导**：七个文件工具
长得像，讲清各自的边界比避免提名字更有用。所以这里只钉"描述不提自己"这一条。
"""

from __future__ import annotations

from agent_frame.contracts import ToolDeclaration
from agent_frame.modules import tool_calling
from agent_frame.tools.chat_tools import build as build_chat


def _declarations() -> list[ToolDeclaration]:
    """装配出全部工具声明：内置文件工具 + 聊天类的画图工具。

    举例：_declarations()[0].name -> "read"
    """
    catalog = tool_calling.build_catalog(include_files=True)
    return list(catalog.declarations()) + [declaration for declaration, _ in build_chat()]


def test_every_tool_declares_its_name_only_in_the_schema() -> None:
    """没人把自己的名字写进描述：工具名由框架给，描述里不必重复。"""
    for declaration in _declarations():
        assert declaration.name not in (declaration.description or "")

"""内置工具：文件读写、搜索、命令执行。**通用智能体的基本盘，默认全部装**。

业务型部署（只做外部接口查询）可以把它们关掉：容器按配置传 include_files=False。

**参数名是有约定的**：碰文件的工具必须把路径参数叫 `path`，碰命令的叫 `command` ——
沙箱的路由只认这套名字（ARCHITECTURE.md §5 D8），名字换了那一类判定就整类消失。
"""

from __future__ import annotations

from agent_frame.contracts import ToolDeclaration

from ..registry import ToolCatalog, ToolFn
from .edit import EDIT, edit
from .glob import GLOB, glob
from .grep import GREP, grep
from .list_dir import LIST_DIR, list_dir
from .read import READ, read
from .run_command import RUN_COMMAND, run_command
from .write import WRITE, write

__all__ = [
    "EDIT",
    "GLOB",
    "GREP",
    "LIST_DIR",
    "READ",
    "RUN_COMMAND",
    "WRITE",
    "build_catalog",
    "edit",
    "glob",
    "grep",
    "list_dir",
    "read",
    "run_command",
    "write",
]

# 内置工具集合。这里的顺序只决定位点 3 推给模型的 schema 顺序，不影响任何判定。
#
# 七个工具的 schema 每轮都要发；不要它们就在配置里关掉（AGENT_FILE_TOOLS=false）。
#
# 举例：_FILE_TOOLS[0] == (READ, read)
_FILE_TOOLS: tuple[tuple[ToolDeclaration, ToolFn], ...] = (
    (READ, read),
    (WRITE, write),
    (EDIT, edit),
    (LIST_DIR, list_dir),
    (GLOB, glob),
    (GREP, grep),
    (RUN_COMMAND, run_command),
)


def build_catalog(*, include_files: bool = True) -> ToolCatalog:
    """建一份内置工具的注册表。**默认装齐七个**，关掉开关就是一个空注册表。

    举例：build_catalog() -> 装了七个；build_catalog(include_files=False) -> 空注册表
    """
    catalog = ToolCatalog()
    if include_files:
        for declaration, run in _FILE_TOOLS:
            catalog.register(declaration, run)
    return catalog

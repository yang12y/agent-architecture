"""资源分类与三类策略（纯函数）。

**不判"危险"，判"越界"。** 危险是主观的、也永远有绕过；而这三个问题都答得出来：

    1. 这次调用**碰的是什么**（工具声明了哪类参数，这一次又传成了什么样）
    2. 当前档位**允许碰什么**
    3. 在不在里面

路由分两层，一层静态一层动态：

    - **这工具能碰什么** —— 看 `parameters_schema` 声明了哪些参数名（`route`）
    - **这一次碰成什么样** —— 看实际传进来的值（`resolve_path`）

只看参数值是不行的：`write` 少给一个 `path` 就变成「不碰文件」，整类判定凭空消失。
参数名是形状，值是内容 —— 判不了内容不等于没有形状。

内置工具遵守这套参数名约定；MCP 工具的参数名由外面来、不可控，
等 S7 接进来时再定（见 ARCHITECTURE.md §9）。
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from agent_architecture.contracts import (
    JsonValue,
    ToolArgumentError,
    ToolDeclaration,
    resolve_in_workspace,
)


class ResourceKind(StrEnum):
    """这次调用碰的是什么。"""

    # 工作目录里的文件或目录，由 workspace 划边界。
    #
    # 举例：ResourceKind.FILE
    FILE = "file"

    # 网络地址。现在**只有能力档位判定**（要 full_access），
    # 按 host 判的参数级判定等允许列表落地（见 ARCHITECTURE.md §9）。
    #
    # 举例：ResourceKind.NETWORK
    NETWORK = "network"

    # 一段交给操作系统的命令 —— **无法静态判定**。
    #
    # 举例：ResourceKind.COMMAND
    COMMAND = "command"


# 参数名 -> 资源类。**路由的唯一依据。**
#
# 举例：ARGUMENT_KINDS["path"] -> ResourceKind.FILE
ARGUMENT_KINDS: dict[str, ResourceKind] = {
    "path": ResourceKind.FILE,
    "url": ResourceKind.NETWORK,
    "command": ResourceKind.COMMAND,
}


def route(declaration: ToolDeclaration) -> frozenset[ResourceKind]:
    """通过工具参数（path,url,command）判断这个工具能碰哪些受管资源（file,network,command)；一个都碰不到就是空集。

    看的是 `parameters_schema` 里声明了哪些参数名，**不是这一次实际传了什么**。
    `write` 少给一个 `path` 就变成「不碰文件」？那正是漏判 —— 声明了 `path` 就是
    文件类工具，参数缺失该走「判不出来 → 问人」，不该把整类判定跳过。

    返回集合而不是「最严的那一类」：一个工具可能同时声明 `path` 与 `url`，
    只取最严的那类会让另一类漏检。

    举例：route(make_tool_declaration("read", properties=["path"])) -> frozenset({FILE})
    """

    properties = declaration.parameters_schema.get("properties")
    if not isinstance(properties, dict):
        return frozenset()
    return frozenset(ARGUMENT_KINDS[key] for key in properties if key in ARGUMENT_KINDS)


def resolve_path(arguments: JsonValue, *, workspace: str) -> Path:
    """把参数里的路径解析成绝对路径。

    **规范化不是可选的**：`workspace/../../etc/passwd`、软链接、`~` 全靠它挡。
    相对路径按 workspace 展开，展开后再 resolve。

    举例：resolve_path({"path": "a.py"}, workspace="D:/proj") -> Path("D:/proj/a.py")
    解析规则本身在 `contracts.resolve_in_workspace` —— 工具开文件时读的是同一份，
    「沙箱放行的路径」和「工具真正打开的路径」才不会走岔。

    举例：resolve_path({"path": "a.py"}, workspace="D:/proj") -> Path("D:/proj/a.py")
    出错：没有 path、不是非空字符串、或路径非法时抛 ToolArgumentError。
    """
    return resolve_in_workspace(_string_arg(arguments, "path"), workspace)


def inside(path: Path, *, workspace: str) -> bool:
    """这个路径在不在工作目录里。**两侧都要规范化。**

    只 resolve 一边的话，`D:/proj` 与 `d:/proj` 就会被判成越界。

    举例：inside(Path("D:/proj/a.py"), workspace="D:/proj") -> True
    """
    try:
        return path.is_relative_to(Path(workspace).resolve())
    except (OSError, ValueError):
        return False


def _string_arg(arguments: JsonValue, key: str) -> str:
    """取一个必须是非空字符串的参数。

    举例：_string_arg({"path": "a.py"}, "path") -> "a.py"
    出错：缺失、类型不对、空串时抛 ToolArgumentError。
    """
    if not isinstance(arguments, dict):
        raise ToolArgumentError(f"参数不是对象：{arguments!r}")
    value = arguments.get(key)
    if not isinstance(value, str) or not value:
        raise ToolArgumentError(f"参数 {key} 不是非空字符串：{value!r}")
    return value

"""声明 → (ToolDeclaration, ToolRunner)。

**唯一的注册面**：声明式（HttpTool）在这里变成"声明 + 实现"，函数式（直接给
ToolDeclaration + ToolRunner）走同一个注册入口，装配处不区分两者。

工具名唯一性在装配期校验 —— 重名会让模型二选一、也会让排错无从下手。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from agent_frame.contracts import (
    HttpTransport,
    JsonValue,
    Permission,
    SideEffect,
    ToolContext,
    ToolDeclaration,
    ToolRunner,
)

from .paging import MAX_ITEMS, fetch
from .records import project_all
from .spec import Call, HttpTool, Page

# 把"一条声明 + 参数 + 执行现场"变成一次下游请求。业务域提供：它知道地址、
# 请求头与机构缺省。
#
# 举例：lambda tool, args, ctx: Call(method="POST", url=..., headers=..., timeout=30.0)
Prepare = Callable[[HttpTool, dict[str, JsonValue], ToolContext], Call]

# 声明没写输出说明时用的那句。够模型判断"这结果是完整的还是被截断的"就行。
#
# 举例："JSON：count 与明细数组；truncated 为真表示没取全。"
DEFAULT_OUTPUT = "JSON：count 与明细数组；truncated 为真表示没取全。"


def build(
    tools: Sequence[HttpTool],
    transport: HttpTransport,
    prepare: Prepare,
) -> tuple[tuple[ToolDeclaration, ToolRunner], ...]:
    """把一批声明变成可注册的 (声明, 实现) 序列。

    举例：build(TOOLS, transport, prepare) -> ((ToolDeclaration(name="device_info_list"), run),)
    出错：工具名重复时抛 ValueError。
    """
    seen: set[str] = set()
    pairs: list[tuple[ToolDeclaration, ToolRunner]] = []
    for tool in tools:
        # 工具去重
        if tool.name in seen:
            raise ValueError(f"工具名重复：{tool.name}")
        seen.add(tool.name)
        pairs.append((_declaration(tool), _runner(tool, transport, prepare)))
    return tuple(pairs)


def view_of(page: Page, tool: HttpTool) -> JsonValue:
    """默认输出：总数 + 明细数组（投影后，最多 MAX_ITEMS 行）。

    举例：view_of(Page(records=[{"deviceName": "H1172"}], count=238), tool)
          -> {"count": 238, "devices": [{"设备名称": "H1172"}]}
    """
    view: dict[str, JsonValue] = {
        "count": page.count,
        tool.list_key: project_all(page.records[:MAX_ITEMS], tool.fields),
    }
    if page.truncated:
        view["truncated"] = True
    return view


def _runner(tool: HttpTool, transport: HttpTransport, prepare: Prepare) -> ToolRunner:
    """把一条声明包成工具实现：造请求 → 取数 → 出视图。

    举例：run = _runner(tool, transport, prepare); await run(ctx, {"name": "H1172"})
    """

    async def run(ctx: ToolContext, arguments: JsonValue) -> JsonValue:
        args = arguments if isinstance(arguments, dict) else {}
        # 造请求
        call = prepare(tool, args, ctx)
        # 取数
        page = await fetch(transport, call, tool.fetch)
        # 出结果
        return tool.shape(page, args) if tool.shape is not None else view_of(page, tool)

    return run


def declaration_of(tool: HttpTool) -> ToolDeclaration:
    """单独取一条声明的对外形状（实现要自己写时用）。

    举例：declaration_of(DEVICE_COUNT_STATISTICS).name -> "device_count_statistics"
    """
    return _declaration(tool)


def _declaration(tool: HttpTool) -> ToolDeclaration:
    """声明是给外面看的：只读、幂等、不需要幂等键。

    举例：_declaration(tool).side_effect -> SideEffect.NONE
    """
    return ToolDeclaration(
        name=tool.name,
        description=tool.description,
        parameters_schema=tool.parameters,
        output_contract=tool.output or DEFAULT_OUTPUT,
        side_effect=SideEffect.NONE,
        accepts_idempotency_key=False,
        required_permission=Permission.READ_ONLY,
        scope=None,
    )

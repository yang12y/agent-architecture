"""`todo_write`：模型用来维护待办清单的工具。

一次调用做两件事：**校验整份列表**、**把计数回给模型**。清单落进日志是模块的事
（见 handler.py）—— 工具不写日志，这条线不破。

返回给模型的只有计数：清单本身就在它自己的工具调用参数里，不必再回显一遍。
"""

from __future__ import annotations

from collections.abc import Sequence

from agent_frame.contracts import (
    JsonValue,
    Permission,
    SideEffect,
    TodoItem,
    TodoStatus,
    ToolArgumentError,
    ToolDeclaration,
)

# 工具声明。幂等：同样的整份列表写两次，结果一样。
#
# 顺带一个好处：scheduling 只让 SideEffect.NONE 的调用并行，幂等调用天然串行 ——
# 两次整表替换并发会互相覆盖，必须一条一条来。
#
# 举例：TODO_TOOL.name == "todo_write"
TODO_TOOL = ToolDeclaration(
    name="todo_write",
    description=(
        "记录并更新这份工作的待办清单。**每次调用都发整份列表**，它替换上一份，没有增量更新。"
        "开干之前先把具体步骤列成待办；做完一条立刻标 completed；同一时刻至多一条 in_progress。"
        "清单只跟当前这一次任务走 —— 用户问下一件事时会自动清空，新任务要重新列。"
        "简单的单步任务不要用它。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "description": "整份待办列表，替换上一份。空数组表示清空。",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "一句话任务，用祈使句。"},
                        "status": {
                            "type": "string",
                            "enum": [status.value for status in TodoStatus],
                            "description": "pending 未开始 | in_progress 正在做 | completed 已完成。",
                        },
                    },
                    "required": ["content", "status"],
                },
            }
        },
        "required": ["todos"],
    },
    output_contract="一句计数：几条待办、几条进行中、几条完成。",
    side_effect=SideEffect.IDEMPOTENT,
    accepts_idempotency_key=False,
    required_permission=Permission.READ_ONLY,
    scope=None,
)


async def run(ctx: object, arguments: JsonValue) -> JsonValue:
    """校验整份列表，回三态的计数。**不写日志、不发事件** —— 那是 handler.py 的活。

    举例：await run(ctx, {"todos": [{"content": "跑测试", "status": "pending"}]})
          -> {"counts": {"pending": 1, "in_progress": 0, "completed": 0}}
    出错：清单不合法时抛 ToolArgumentError，由执行器收成给模型看的结果。
    """
    del ctx  # 只碰参数，不看工作目录与取消信号
    return {"counts": counts(parse_todos(arguments))}


def parse_todos(arguments: JsonValue) -> tuple[TodoItem, ...]:
    """把工具参数校验成一份规范清单。

    规则与工具描述里说的一致：内容 trim 后非空、不许重复、状态合法、至多一条 in_progress。
    存进去的是 trim 过的内容 —— 日志里的形状不取决于模型有没有多打空格。

    举例：parse_todos({"todos": [{"content": "跑测试", "status": "pending"}]})
          -> (TodoItem(content="跑测试", status=TodoStatus.PENDING),)
    出错：任何一条不合法时抛 ToolArgumentError。
    """
    raw = arguments.get("todos") if isinstance(arguments, dict) else None
    if not isinstance(raw, list):
        raise ToolArgumentError("todos 必须是一个数组")

    todos: list[TodoItem] = []
    seen: set[str] = set()
    active = 0
    for item in raw:
        if not isinstance(item, dict):
            raise ToolArgumentError(f"todos 里每一条都要是对象，收到 {type(item).__name__}")
        content = _content(item.get("content"))
        if content in seen:
            raise ToolArgumentError(f"待办内容重复：{content}")
        seen.add(content)
        status = _status(item.get("status"))
        if status is TodoStatus.IN_PROGRESS:
            active += 1
        todos.append(TodoItem(content=content, status=status))

    if active > 1:
        raise ToolArgumentError(f"同一时刻至多一条 in_progress（收到 {active} 条）")
    return tuple(todos)


def counts(todos: Sequence[TodoItem]) -> dict[str, int]:
    """三态各自几条。**现算不落库** —— 和"统计不存计数器"是同一条规矩。

    举例：counts((TodoItem("跑测试", TodoStatus.PENDING),))
          -> {"pending": 1, "in_progress": 0, "completed": 0}
    """
    tally = {status.value: 0 for status in TodoStatus}
    for todo in todos:
        tally[todo.status.value] += 1
    return tally


def _content(raw: JsonValue) -> str:
    """取出一条待办的内容：必须是 trim 之后非空的字符串。

    举例：_content("  跑测试 ") -> "跑测试"
    出错：不是字符串或空串时抛 ToolArgumentError。
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ToolArgumentError("每条待办的 content 必须是非空字符串")
    return raw.strip()


def _status(raw: JsonValue) -> TodoStatus:
    """取出一条待办的状态。

    举例：_status("in_progress") -> TodoStatus.IN_PROGRESS
    出错：不在三态里时抛 ToolArgumentError。
    """
    try:
        return TodoStatus(raw)  # type: ignore[arg-type]
    except ValueError:
        raise ToolArgumentError(
            f"待办状态只能是 pending / in_progress / completed，收到 {raw!r}"
        ) from None

"""位点 1 与位点 8：待办清单的两次落盘。

- **位点 1**：新一轮开始，把上一轮留下的清单清掉 —— 清单跟着一次任务走，不是会话的常驻属性。
- **位点 8**：`todo_write` 的整份清单落进日志并广播。

工具不写日志（它只碰参数）；写日志、发事件在这一侧 —— 和 session 在位点 8 写工具结果、
checkpoint 在位点 8 落盘是同一种分工：**谁拥有这个事实，谁在位点上写**。
"""

from __future__ import annotations

from typing import cast

from agent_frame.contracts import (
    Handler,
    SessionEntryType,
    Site,
    SiteContext,
    TodoEntryPayload,
    TodoItem,
    TodoUpdated,
    ToolAfterPayload,
)

from .tool import TODO_TOOL, parse_todos

# 排在 session（410）之后：先让工具结果那条消息落进日志，再写待办；
# 也早于 hook（500）与落盘屏障（900）—— 屏障会把这一条一起推上盘。
#
# 举例：420
PRIORITY_TODO = 420

# 位点 1 上清空清单的 handler。排在 session.trace_before（200）之后：先让 trace_start 与
# 本次用户消息落进日志，清空那一条才落在这次 trace 里。
#
# 举例：210
PRIORITY_TODO_RESET = 210


class _ResetHandler:
    """位点 1：上一轮的待办随本轮开始作废。

    为什么要清：清单是**一次任务**的进度条。用户问下一件事时，上一件的「进行中」还挂在那里
    只会误导人 —— 而且模型会以为那件事还没做完。

    举例：_ResetHandler()
    """

    def __init__(self) -> None:
        # handler 唯一标识。
        #
        # 举例："todo.trace_before"
        self.id = "todo.trace_before"

        # 只挂位点 1。
        #
        # 举例：frozenset({Site.TRACE_BEFORE})
        self.sites: frozenset[Site] = frozenset({Site.TRACE_BEFORE})

        # 见上面的 PRIORITY_TODO_RESET。
        #
        # 举例：210
        self.priority = PRIORITY_TODO_RESET

    async def run(self, ctx: SiteContext) -> bool | None:
        """上一轮有清单就写一条空清单、发一条事件；本来就空则什么都不做。

        写空清单而不是"删掉上一条"：日志只追加，读的一侧认的永远是最后一条。

        举例：await handler.run(ctx) -> 日志多一条 todos=() 的 todo 条目，面板随即收起
        """
        if not ctx.session.todos():
            return None

        todos: tuple[TodoItem, ...] = ()
        ctx.session.append_entry(
            trace_id=ctx.state.trace_id,
            type=SessionEntryType.TODO,
            payload=TodoEntryPayload(todos=todos),
            now=ctx.ports.clock.now(),
        )
        ctx.ports.events.publish(
            TodoUpdated(
                session_id=ctx.session.meta.session_id,
                trace_id=ctx.state.trace_id,
                todos=todos,
            )
        )
        return None


class _TodoHandler:
    """认领自己那一条工具调用的结果。一个实例只挂位点 8。

    举例：_TodoHandler()
    """

    def __init__(self) -> None:
        # handler 唯一标识。
        #
        # 举例："todo.tool_after"
        self.id = "todo.tool_after"

        # 只挂位点 8：位点 7 才是执行，位点 6 还没跑。
        #
        # 举例：frozenset({Site.TOOL_AFTER})
        self.sites: frozenset[Site] = frozenset({Site.TOOL_AFTER})

        # 见上面的 PRIORITY_TODO。
        #
        # 举例：420
        self.priority = PRIORITY_TODO

    async def run(self, ctx: SiteContext) -> bool | None:
        """是 todo_write 且没失败，就写一条条目并发一条事件。

        举例：await handler.run(ctx) -> 日志多一条 todo 条目，事件总线多一条 TodoUpdated
        """
        payload = cast(ToolAfterPayload, ctx.payload)
        call = payload.call
        if call is None or call.name != TODO_TOOL.name:
            return None
        if payload.error is not None:
            # 校验没过：模型已经拿到失败说明，别把坏清单写进日志。
            return None

        todos = parse_todos(call.arguments)
        ctx.session.append_entry(
            trace_id=ctx.state.trace_id,
            type=SessionEntryType.TODO,
            payload=TodoEntryPayload(todos=todos),
            now=ctx.ports.clock.now(),
        )
        ctx.ports.events.publish(
            TodoUpdated(
                session_id=ctx.session.meta.session_id,
                trace_id=ctx.state.trace_id,
                todos=todos,
            )
        )
        return None


def build_handlers() -> tuple[Handler, ...]:
    """造出 todo 要注册的 handler，交给装配根。

    两个：位点 1 清空、位点 8 落盘。工具本身由 composition 登记进工具表。

    举例：registry = HandlerRegistry(build_handlers())
    """
    return (_ResetHandler(), _TodoHandler())

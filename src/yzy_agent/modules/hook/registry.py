"""Hook 的注册与匹配。

一件事：把一个回调挂到某个位点事件上，走到那个位点就按注册顺序跑一遍。

同一事件内按**注册顺序**跑，不另给 hook 排优先级：十个位点之间的先后已经由主循环写死，
hook 之间再分一层优先级只会让"谁先谁后"多一个要查的地方。要 A 先跑就先注册 A。

**内置与用户的边界**：两者同表、同排序，区别只有一条 —— 用户注册的摘得掉，
内置的摘不掉（它是模块行为的一部分，要删就得改代码）。名字在同一张表内唯一，
所以用户 hook 冒名顶替不了内置 hook。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum

from yzy_agent.contracts import SiteContext

from .events import SITE_EVENT

# 位点事件 hook：拿到整个位点上下文，原地改 payload。
# 返回 False 是否决，但只有位点 1 与位点 6 认这个返回值，别处忽略（见 runner）。
#
# 举例：async def audit(ctx): ctx.ports.logger.info("hook.audit")
SiteHook = Callable[[SiteContext], Awaitable[bool | None]]


class HookSource(StrEnum):
    """这条 hook 是谁注册的。"""

    # 模块自己用的 hook —— 它是这个模块行为的一部分，摘不掉。
    #
    # 举例：HookSource.BUILTIN
    BUILTIN = "builtin"

    # 外部注入的 hook —— 随时可以摘。
    #
    # 举例：HookSource.USER
    USER = "user"


@dataclass(frozen=True, slots=True)
class Hook:
    """一条挂在位点事件上的 hook。"""

    # 事件名，取值见 events.py。
    #
    # 举例："agent/turn_end"
    event: str

    # 出日志与报错时用它定位。
    #
    # 举例："audit_log"
    name: str

    # 要执行的那段逻辑。
    #
    # 举例：async def audit_log(ctx): ...
    fn: SiteHook

    # 谁注册的。
    #
    # 举例：HookSource.USER
    source: HookSource

    # 注册序号；同一事件内按它排。
    #
    # 举例：3
    order: int


class HookRegistry:
    """事件名 -> 有序 hook 的索引。装配期注册，运行期只读。

    跟 HandlerRegistry 一样，注册完就不再变，所以"这位点上跑哪些 hook"天然是固定的，
    不需要额外的冻结动作。

    举例：hooks = HookRegistry(); hooks.on(AGENT_TURN_END, audit)
    """

    def __init__(self) -> None:
        """建一张空表。

        举例：HookRegistry()
        """
        # 位点事件与守卫分开存：两者的回调签名不一样，混一张表里只会到处 cast。
        #
        # 举例：{"agent/turn_end": [Hook(...)]}
        self._site_hooks: dict[str, list[Hook]] = {}

        # 注册序号；同事件内排序与"注册顺序"语义都靠它。
        #
        # 举例：3
        self._counter = 0

        # 已用掉的名字，跨事件与守卫统一一个命名空间。
        #
        # 举例：{"audit_log", "no_rm"}
        self._names: set[str] = set()

    def on(
        self, event: str, fn: SiteHook, *, name: str = "", source: HookSource = HookSource.USER
    ) -> Hook:
        """注册一条位点事件 hook。name 省略时取函数名。

        举例：hooks.on(AGENT_TRACE_END, count_tokens, name="count")
        出错：
            - event 不是十个位点事件之一时抛 ValueError——错的事件名会让这条 hook 永远不跑。
            - name 已经被占时抛 ValueError。
        """
        if event not in SITE_EVENT.values():
            raise ValueError(f"不认识的事件名：{event}；合法取值：{sorted(SITE_EVENT.values())}")
        hook = Hook(
            event=event,
            name=self._claim(name or getattr(fn, "__name__", "hook")),
            fn=fn,
            source=source,
            order=self._next_order(),
        )
        self._site_hooks.setdefault(event, []).append(hook)
        return hook

    def remove(self, name: str) -> None:
        """按名字摘掉一条 hook。

        **只摘得动用户注册的**：内置 hook 属于模块行为，摘掉它等于悄悄改掉模块的语义。

        举例：hooks.remove("audit_log")
        出错：名字不存在、或那条是内置的，抛 ValueError。
        """
        for event, hooks in self._site_hooks.items():
            for hook in hooks:
                if hook.name == name:
                    self._drop_source(hook.source, name)
                    hooks.remove(hook)
                    self._names.discard(name)
                    if not hooks:
                        del self._site_hooks[event]
                    return
        raise ValueError(f"没有叫 {name} 的 hook")

    def hooks_for(self, event: str) -> tuple[Hook, ...]:
        """取一个事件上的 hook，已按注册顺序排好。

        举例：hooks.hooks_for(AGENT_TURN_END) -> (Hook(name="audit"),)
        """
        return tuple(sorted(self._site_hooks.get(event, ()), key=lambda h: h.order))

    def _claim(self, name: str) -> str:
        """占一个名字；已被占就报错。

        举例：registry._claim("audit") -> "audit"
        """
        if name in self._names:
            raise ValueError(f"hook 名字重复：{name}")
        self._names.add(name)
        return name

    def _next_order(self) -> int:
        """取下一个注册序号。

        举例：registry._next_order() -> 0
        """
        self._counter += 1
        return self._counter

    def _drop_source(self, source: HookSource, name: str) -> None:
        """挡住对内置 hook 的注销。

        举例：registry._drop_source(HookSource.BUILTIN, "audit_log")
        出错：source 是内置时抛 ValueError。
        """
        if source is HookSource.BUILTIN:
            raise ValueError(f"{name} 是内置 hook，摘不掉")

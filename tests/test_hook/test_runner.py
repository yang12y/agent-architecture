"""位点事件的 hook 执行：顺序、否决、异常口径。"""

from __future__ import annotations

import pytest

from agent_architecture.contracts import Site, SiteAbort, SiteContext, TurnBeforePayload
from agent_architecture.infra.observability import RecordingLogger
from agent_architecture.modules.hook import HookRegistry, events, run_hooks
from fakes import make_ports, make_site_context, make_trace_context


def _ctx(site: Site) -> tuple[SiteContext, RecordingLogger]:
    """造一个位点上下文，顺带把记录日志交回来。

    run_hooks 不看 payload，所以这里给一个最简单的 —— 它只关心 site 与日志。
    举例：ctx, logger = _ctx(Site.TURN_END)
    """
    logger = RecordingLogger()
    trace = make_trace_context(ports=make_ports(logger=logger))
    return make_site_context(site, TurnBeforePayload(turn_index=1), trace=trace), logger


def _logged(logger: RecordingLogger) -> list[str]:
    """日志里的事件名，按顺序。

    举例：_logged(logger) -> ["hook.failed"]
    """
    return [event for _, event, _ in logger.records]


async def test_hooks_run_in_registration_order() -> None:
    """同一条事件上的 hook 按注册顺序跑。"""
    seen: list[str] = []

    async def first(ctx) -> None:
        seen.append("first")

    async def second(ctx) -> None:
        seen.append("second")

    registry = HookRegistry()
    registry.on(events.AGENT_TURN_END, first)
    registry.on(events.AGENT_TURN_END, second)
    ctx, _ = _ctx(Site.TURN_END)

    await run_hooks(ctx, registry)

    assert seen == ["first", "second"]


async def test_only_hooks_on_this_event_run() -> None:
    """挂在别的事件上的 hook 不该被跑到。"""
    seen: list[str] = []

    async def elsewhere(ctx) -> None:
        seen.append("elsewhere")

    registry = HookRegistry()
    registry.on(events.AGENT_TRACE_END, elsewhere)
    ctx, _ = _ctx(Site.TURN_END)

    await run_hooks(ctx, registry)

    assert seen == []


async def test_a_hook_changes_the_payload_in_place() -> None:
    """hook 改的是 ctx.payload 本身，改动对同一个位点后面的人可见。"""

    async def renumber(ctx) -> None:
        ctx.payload.turn_index = 7

    registry = HookRegistry()
    registry.on(events.AGENT_TURN_END, renumber)
    ctx, _ = _ctx(Site.TURN_END)

    await run_hooks(ctx, registry)

    assert ctx.payload.turn_index == 7


async def test_a_hook_can_stop_the_trace_at_site_one() -> None:
    """位点 1 的 False 有语义：这次 trace 不开始，本位点后面的 hook 也不再跑。"""
    order: list[str] = []

    async def deny(ctx) -> bool:
        order.append("deny")
        return False

    async def later(ctx) -> None:
        order.append("later")

    registry = HookRegistry()
    registry.on(events.AGENT_TRACE_BEFORE, deny)
    registry.on(events.AGENT_TRACE_BEFORE, later)
    ctx, logger = _ctx(Site.TRACE_BEFORE)

    assert await run_hooks(ctx, registry) is False
    assert order == ["deny"]
    assert "hook.rejected" in _logged(logger)


async def test_a_hook_can_stop_the_batch_at_site_six() -> None:
    """位点 6 的 False 也有语义：这一批工具一条都不执行。"""

    async def deny(ctx) -> bool:
        return False

    registry = HookRegistry()
    registry.on(events.AGENT_TOOL_BEFORE, deny)
    ctx, _ = _ctx(Site.TOOL_BEFORE)

    assert await run_hooks(ctx, registry) is False


async def test_false_at_other_sites_is_ignored_but_logged() -> None:
    """其余八个位点没有否决这回事：返回值不看，但要记一条 warning 让它暴露出来。"""
    order: list[str] = []

    async def confused(ctx) -> bool:
        order.append("confused")
        return False

    async def later(ctx) -> None:
        order.append("later")

    registry = HookRegistry()
    registry.on(events.AGENT_TURN_END, confused)
    registry.on(events.AGENT_TURN_END, later)
    ctx, logger = _ctx(Site.TURN_END)

    assert await run_hooks(ctx, registry) is True
    assert order == ["confused", "later"]
    assert "hook.reject_out_of_contract" in _logged(logger)


async def test_a_broken_hook_rejects_at_a_rejection_site() -> None:
    """放行判定出错的默认答案是"不放行"。"""

    async def broken(ctx) -> None:
        raise RuntimeError("hook 自己写挂了")

    registry = HookRegistry()
    registry.on(events.AGENT_TRACE_BEFORE, broken, name="broken")
    ctx, logger = _ctx(Site.TRACE_BEFORE)

    assert await run_hooks(ctx, registry) is False
    assert "hook.failed" in _logged(logger)


async def test_a_broken_hook_elsewhere_is_skipped() -> None:
    """别处挂掉的 hook 只跳过它自己：观察型 hook 挂了不该弄死整次 trace。"""
    order: list[str] = []

    async def broken(ctx) -> None:
        order.append("broken")
        raise RuntimeError("hook 自己写挂了")

    async def later(ctx) -> None:
        order.append("later")

    registry = HookRegistry()
    registry.on(events.AGENT_TURN_END, broken, name="broken")
    registry.on(events.AGENT_TURN_END, later, name="later")
    ctx, logger = _ctx(Site.TURN_END)

    assert await run_hooks(ctx, registry) is True
    assert order == ["broken", "later"]
    assert "hook.failed" in _logged(logger)


async def test_site_abort_from_a_hook_goes_straight_up() -> None:
    """SiteAbort 是 hook 有意的控制流，不能被当成"写挂了"记一笔就吞掉。"""

    async def stop(ctx) -> None:
        raise SiteAbort("用户中断")

    registry = HookRegistry()
    registry.on(events.AGENT_TRACE_BEFORE, stop, name="stop")
    ctx, logger = _ctx(Site.TRACE_BEFORE)

    with pytest.raises(SiteAbort):
        await run_hooks(ctx, registry)

    assert "hook.failed" not in _logged(logger)

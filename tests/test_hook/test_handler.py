"""hook 挂在位点上的两个 handler：id、挂载面、以及排序。"""

from __future__ import annotations

from fakes import RecordingHandler
from yzy_agent.contracts import Site
from yzy_agent.modules.agent_loop import HandlerRegistry
from yzy_agent.modules.hook import PRIORITY_HOOKS, HookRegistry, build_handlers


def _handlers() -> dict:
    """造出 hook 的两个 handler，按 id 索引。

    举例：_handlers()["hook.site"].priority -> 500
    """
    return {handler.id: handler for handler in build_handlers(HookRegistry())}


def test_the_site_handler_covers_all_ten_sites() -> None:
    """位点事件的 hook 十位点全挂 —— 挂载点就是位点本身。"""
    handler = _handlers()["hook.site"]

    assert handler.sites == frozenset(Site)
    assert handler.priority == PRIORITY_HOOKS


def test_the_hook_band_sits_after_dimension_and_before_the_barrier() -> None:
    """500 段要排在维度（100-499）之后、落盘屏障（900）之前。"""
    registry = HandlerRegistry(
        [
            RecordingHandler("dimension", {Site.TURN_END}, priority=200),
            *build_handlers(HookRegistry()),
            RecordingHandler("barrier", {Site.TURN_END}, priority=900),
        ]
    )

    assert [h.id for h in registry.for_site(Site.TURN_END)] == [
        "dimension",
        "hook.site",
        "barrier",
    ]

"""Hook 注册表：注册、匹配、命名、注销。"""

from __future__ import annotations

import pytest

from yzy_agent.modules.hook import HookRegistry, HookSource, events


async def _audit(ctx) -> None: ...


async def _noop(ctx) -> None: ...


def test_a_hook_lands_on_its_event() -> None:
    """注册在哪个事件上，就只在哪个事件上取得到。"""
    registry = HookRegistry()
    registry.on(events.AGENT_TURN_END, _audit, name="audit")

    assert [h.name for h in registry.hooks_for(events.AGENT_TURN_END)] == ["audit"]
    assert registry.hooks_for(events.AGENT_TRACE_END) == ()


def test_hooks_on_one_event_keep_registration_order() -> None:
    """同一事件内按注册顺序跑 —— 要 A 先跑就先注册 A，不另设优先级。"""
    registry = HookRegistry()
    registry.on(events.AGENT_TURN_END, _noop, name="first")
    registry.on(events.AGENT_TURN_END, _audit, name="second")

    assert [h.name for h in registry.hooks_for(events.AGENT_TURN_END)] == ["first", "second"]


def test_the_name_defaults_to_the_function_name() -> None:
    """不给名字时取函数名，日志里不会是空的。"""
    registry = HookRegistry()
    registry.on(events.AGENT_TURN_END, _audit)

    assert registry.hooks_for(events.AGENT_TURN_END)[0].name == "_audit"


def test_an_unknown_event_is_rejected_at_registration() -> None:
    """事件名写错要在注册期就炸 —— 否则这条 hook 永远不跑，事后查不出来。"""
    registry = HookRegistry()

    with pytest.raises(ValueError, match="不认识的事件名"):
        registry.on("agent/turn_endd", _audit)


def test_every_site_has_an_event_name() -> None:
    """十位点一个不落 —— 挂载点就是位点，少一个就等于那个位点挂不上 hook。"""
    from yzy_agent.contracts import Site

    assert set(events.SITE_EVENT) == set(Site)


def test_two_hooks_cannot_share_a_name() -> None:
    """名字在整张表内唯一：用户 hook 冒名顶替不了内置 hook。"""
    registry = HookRegistry()
    registry.on(events.AGENT_TURN_END, _audit, name="audit")

    with pytest.raises(ValueError, match="名字重复"):
        registry.on(events.AGENT_TURN_END, _noop, name="audit")


def test_a_user_hook_can_be_removed() -> None:
    """用户注册的 hook 摘得掉。"""
    registry = HookRegistry()
    registry.on(events.AGENT_TURN_END, _audit, name="audit")

    registry.remove("audit")

    assert registry.hooks_for(events.AGENT_TURN_END) == ()
    # 名字要还回去，不然重注册同一个名字会被判重复。
    registry.on(events.AGENT_TURN_END, _audit, name="audit")


def test_a_builtin_hook_cannot_be_removed() -> None:
    """内置 hook 是模块行为的一部分，摘掉它等于悄悄改掉模块的语义。"""
    registry = HookRegistry()
    registry.on(events.AGENT_TURN_END, _audit, name="audit", source=HookSource.BUILTIN)

    with pytest.raises(ValueError, match="内置"):
        registry.remove("audit")


def test_removing_something_that_is_not_there_is_an_error() -> None:
    """摘一个不存在的名字要报错，不能静默当成成功。"""
    with pytest.raises(ValueError, match="没有叫"):
        HookRegistry().remove("nobody")

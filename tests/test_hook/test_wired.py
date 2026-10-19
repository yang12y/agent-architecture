"""装进容器之后真的会跑：注册一条 hook，跑一次 trace 看它有没有被调到。"""

from __future__ import annotations

from pathlib import Path

from fakes import USER, ScriptedLlm, make_app_config, text_turn, user_message
from yzy_agent.composition import build_container
from yzy_agent.contracts import TraceStatus
from yzy_agent.modules.hook import events


def _container(scratch_dir: Path, llm: ScriptedLlm):
    """按一份把会话目录指到临时目录的配置装出容器。

    举例：container = _container(scratch_dir, ScriptedLlm([text_turn("在的")]))
    """
    config = make_app_config(session_dir=str(scratch_dir / "sessions"))
    return build_container(config, llm=llm)


async def test_a_hook_registered_on_the_container_fires(scratch_dir: Path) -> None:
    """拿到容器的人往注册表上挂一条，跑一次 trace 就能看到它被调到。"""
    seen: list[str] = []

    async def note(ctx) -> None:
        seen.append(ctx.site.value)

    container = _container(scratch_dir, ScriptedLlm([text_turn("在的")]))
    container.hooks.on(events.AGENT_TURN_END, note, name="note")
    live = await container.sessions.create(user_id=USER)

    await container.agent.run(session=live.session, user_message=user_message("在吗"))

    assert seen == ["turn:end"]


async def test_a_hook_can_stop_the_trace_before_it_starts(scratch_dir: Path) -> None:
    """位点 1 的否决有语义：这次 trace 不开始，模型一次都没被调到。"""

    async def never(ctx) -> bool:
        return False

    # 空脚本：模型真被调到的话，ScriptedLlm 会当场断言失败。
    llm = ScriptedLlm()
    container = _container(scratch_dir, llm)
    container.hooks.on(events.AGENT_TRACE_BEFORE, never, name="never")
    live = await container.sessions.create(user_id=USER)

    outcome = await container.agent.run(session=live.session, user_message=user_message("在吗"))

    assert outcome.status is TraceStatus.FAILED
    assert llm.requests == []


async def test_a_removed_hook_stops_firing(scratch_dir: Path) -> None:
    """摘掉之后就不该再跑了。"""
    seen: list[str] = []

    async def note(ctx) -> None:
        seen.append("hit")

    container = _container(scratch_dir, ScriptedLlm([text_turn("在的"), text_turn("好的")]))
    container.hooks.on(events.AGENT_TURN_END, note, name="note")
    live = await container.sessions.create(user_id=USER)

    await container.agent.run(session=live.session, user_message=user_message("在吗"))
    container.hooks.remove("note")
    await container.agent.run(session=live.session, user_message=user_message("再说一次"))

    assert seen == ["hit"]

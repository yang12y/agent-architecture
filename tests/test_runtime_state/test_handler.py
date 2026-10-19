"""runtime_state 的 SiteHandler 装配测试。

这里测的是「模块 × 位点」这一层的接线，不是各位点操作本身的行为——那是 test_sNN_*.py 的事。
"""

from __future__ import annotations

from agent_architecture.contracts import Handler, Site, ToolStat, TurnBeforePayload
from agent_architecture.modules.agent_loop import HandlerRegistry, SitePipeline
from agent_architecture.modules.runtime_state import build_handlers
from fakes import make_runtime_state, make_site_context, make_trace_context

# runtime_state 声明要参与的九个位点。位点 9 不在其中，理由见 ops/__init__.py。
EXPECTED_SITES = frozenset(
    {
        Site.TRACE_BEFORE,
        Site.TURN_BEFORE,
        Site.LLM_BEFORE,
        Site.LLM_DURING,
        Site.LLM_AFTER,
        Site.TOOL_BEFORE,
        Site.TOOL_DURING,
        Site.TOOL_AFTER,
        Site.TRACE_END,
    }
)


def _by_id() -> dict[str, Handler]:
    """按 id 索引全部 handler，方便单独取一个断言它的优先级。

    举例：_by_id()["runtime_state.turn_before"].priority -> 100
    """
    return {handler.id: handler for handler in build_handlers()}


def test_covers_exactly_the_nine_sites_it_owns() -> None:
    """九个位点各挂一个；位点 9 不该出现。"""
    covered = {site for handler in build_handlers() for site in handler.sites}

    assert covered == EXPECTED_SITES


def test_site_nine_is_deliberately_absent() -> None:
    """位点 9 的 max_turns 判定是内核固有职责，TurnEndPayload 三个字段也都是内核现算的。"""
    covered = {site for handler in build_handlers() for site in handler.sites}

    assert Site.TURN_END not in covered


def test_ids_are_module_dot_site() -> None:
    """id 形如 "runtime_state.turn.before"，日志里一眼能定位到模块与位点。"""
    for handler in build_handlers():
        (site,) = handler.sites
        assert handler.id == f"runtime_state.{site.value.replace(':', '_')}"


def test_trace_before_runs_after_session() -> None:
    """位点 1 必须晚于 session —— session 先建或恢复会话，这里才绑得到正确的 session_id。"""
    assert _by_id()["runtime_state.trace_before"].priority > 100


def test_llm_before_runs_before_compression() -> None:
    """compression（200）就排在后面，它要读 context_tokens；110 也给 100 的提示词组装让了位。"""
    assert _by_id()["runtime_state.llm_before"].priority == 110


def test_tool_before_runs_after_the_decisions_are_filled() -> None:
    """位点 6 读的是 tool_calling 与 sandbox 填好的 decisions，必须排在它们之后。"""
    assert _by_id()["runtime_state.tool_before"].priority > 100


def test_tool_after_runs_last() -> None:
    """要等 tool_calling 规范化完、session 写完结果条目，这里才统计与摘队列。"""
    assert _by_id()["runtime_state.tool_after"].priority == 400


async def test_run_never_vetoes() -> None:
    """runtime_state 没有否决语义：只改数据，返回值必须是 None（表示继续）。

    九个 handler 共用同一个 run 实现，所以测其中一个就代表了全部。
    """
    ctx = make_site_context(Site.TURN_BEFORE, TurnBeforePayload(1))

    assert await _by_id()["runtime_state.turn_before"].run(ctx) is None


def test_the_whole_set_passes_registry_validation() -> None:
    """装配冒烟：id 不重复、priority 全落在合法段位里。"""
    registry = HandlerRegistry(build_handlers())

    assert len(registry.handlers()) == 9
    assert [h.id for h in registry.for_site(Site.TURN_BEFORE)] == ["runtime_state.turn_before"]
    assert [h.id for h in registry.for_site(Site.TRACE_END)] == ["runtime_state.trace_end"]
    assert [h.id for h in registry.for_site(Site.TOOL_BEFORE)] == ["runtime_state.tool_before"]
    assert registry.for_site(Site.TURN_END) == ()


async def test_pipeline_dispatch_actually_updates_the_state() -> None:
    """装配冒烟：注册进管线后跑一个位点，执行态真的被改了。"""
    pipeline = SitePipeline(HandlerRegistry(build_handlers()))
    trace = make_trace_context(
        state=make_runtime_state(
            llm_attempt_number=2, turn_tool_stats={"grep": ToolStat("grep", 1, 0)}
        )
    )

    await pipeline.run(Site.TURN_BEFORE, TurnBeforePayload(turn_index=1), trace)

    assert trace.state.llm_attempt_number == 0
    assert trace.state.turn_tool_stats == {}

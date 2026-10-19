"""位点 4（llm:during）的操作测试。"""

from __future__ import annotations

from fakes import make_runtime_state, make_site_context, make_trace_context
from yzy_agent.contracts import LlmDuringPayload, Site
from yzy_agent.modules.runtime_state import s04_llm_during


def test_attempt_number_is_recorded() -> None:
    """把"这是本 turn 的第几次尝试"写进执行态。"""
    ctx = make_site_context(Site.LLM_DURING, LlmDuringPayload(model="fake-model", attempt=2))

    s04_llm_during.apply(ctx)

    assert ctx.state.llm_attempt_number == 2


def test_first_attempt_records_zero() -> None:
    """第 0 次是"首次调用"，不是"还没调用过"，所以 0 必须真的写进去。"""
    ctx = make_site_context(
        Site.LLM_DURING,
        LlmDuringPayload(model="fake-model", attempt=0),
        trace=make_trace_context(state=make_runtime_state(llm_attempt_number=9)),
    )

    s04_llm_during.apply(ctx)

    assert ctx.state.llm_attempt_number == 0

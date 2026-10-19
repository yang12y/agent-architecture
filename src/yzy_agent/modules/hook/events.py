"""Hook 的事件名。

挂载点**就是十位点**，这里不新增扩展点 —— 只是给每个位点起一个事件名，
让注册方写起来像"挂一个事件"，不必去记 Site 枚举。

SITE_EVENT 是位点与事件名之间**唯一**的映射处，加位点时只改它。
"""

from __future__ import annotations

from yzy_agent.contracts import Site

# 十个位点各一个事件名：位点名把冒号换成下划线，加 agent/ 前缀。
#
# 举例：AGENT_TRACE_BEFORE == "agent/trace_before"
AGENT_TRACE_BEFORE = "agent/trace_before"
AGENT_TURN_BEFORE = "agent/turn_before"
AGENT_LLM_BEFORE = "agent/llm_before"
AGENT_LLM_DURING = "agent/llm_during"
AGENT_LLM_AFTER = "agent/llm_after"
AGENT_TOOL_BEFORE = "agent/tool_before"
AGENT_TOOL_DURING = "agent/tool_during"
AGENT_TOOL_AFTER = "agent/tool_after"
AGENT_TURN_END = "agent/turn_end"
AGENT_TRACE_END = "agent/trace_end"


# 位点 -> 事件名。**唯一映射处**。
#
# 举例：SITE_EVENT[Site.TOOL_BEFORE] -> "agent/tool_before"
SITE_EVENT: dict[Site, str] = {
    Site.TRACE_BEFORE: AGENT_TRACE_BEFORE,
    Site.TURN_BEFORE: AGENT_TURN_BEFORE,
    Site.LLM_BEFORE: AGENT_LLM_BEFORE,
    Site.LLM_DURING: AGENT_LLM_DURING,
    Site.LLM_AFTER: AGENT_LLM_AFTER,
    Site.TOOL_BEFORE: AGENT_TOOL_BEFORE,
    Site.TOOL_DURING: AGENT_TOOL_DURING,
    Site.TOOL_AFTER: AGENT_TOOL_AFTER,
    Site.TURN_END: AGENT_TURN_END,
    Site.TRACE_END: AGENT_TRACE_END,
}


def event_of(site: Site) -> str:
    """查一个位点对应的事件名。

    举例：event_of(Site.TRACE_END) -> "agent/trace_end"
    """
    return SITE_EVENT[site]

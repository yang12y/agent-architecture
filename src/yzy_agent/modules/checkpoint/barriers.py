"""四个落盘屏障：位点 3 / 5 / 6 / 8，外加收尾的位点 10。

屏障只有一个含义 —— **走到这里，日志必须已经在盘上**。它是下界，不是唯一的落盘时机。

为什么必须是这几个位点：

    3 llm:before    请求发出前已落盘，响应前崩溃不会重放一次没记下来的请求
    5 llm:after     本轮的 assistant 消息（含它请求的工具调用）先落盘再进工具阶段
    6 tool:before   外部副作用发生前落盘
    8 tool:after    本批工具结果先落盘再进下一轮
    10 trace:end    收尾事实落盘，下次启动才判得出"上次是正常结束的"
"""

from __future__ import annotations

from yzy_agent.contracts import SessionHandle, SessionStore, Site

# 屏障位点。装配后自检用：checkpoint 必须挂满这几个位点。
#
# 举例：Site.LLM_BEFORE in BARRIER_SITES -> True
BARRIER_SITES: frozenset[Site] = frozenset(
    {Site.LLM_BEFORE, Site.LLM_AFTER, Site.TOOL_BEFORE, Site.TOOL_AFTER, Site.TRACE_END}
)


async def ensure_persisted(
    store: SessionStore,
    session: SessionHandle,
    persisted_upto: int,
    *,
    reason: str,
) -> int:
    """把内存日志推到末尾并落盘，返回新的落盘水位。

    水位已经到末尾就什么都不做 —— 屏障空跑是常态（位点 3 与 5 之间往往一条条目都没有），
    不该每次重写一遍文件。

    上一次没成功的话，水位停在原地，下一次屏障会把这一段重新推一遍。推重复了也不要紧：
    存储自己按水位丢弃已有的条目，落盘因此是幂等的。

    出错：存储落盘失败时抛 FlushError，由内核按"失败即阻止"处理。

    举例：watermark = await ensure_persisted(store, session, 0, reason="llm:before")
    """
    entries = session.entries()
    if not entries:
        return persisted_upto

    # 获取最后一条条目的序数
    target = entries[-1].seq

    # 如果最后一条条目的序数小于等于（一般不会小于）推送的下标，就代表没有东西需要持久化
    if target <= persisted_upto:
        return persisted_upto

    # 持久化，刷盘
    await store.append(session.meta.session_id, entries)
    await store.flush(session.meta.session_id, target)
    return target

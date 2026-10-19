"""位点管线：在**单个**位点上，把注册的 handler 按优先级跑完。

它不遍历十个位点。位点之间的先后由主循环的代码写死：

    位点1 -> while{ 位点2 / 3 / 4 / 5 / 6 / 7 / 8 / 9 } -> 位点10

管线只回答一个问题：主循环走到某个位点时，这一刻该跑哪些 handler、按什么顺序跑。

三条约定：
    1. handler 是"模块 x 位点"的组合。同一个模块在多个位点注册多个 handler，
       每个 handler 有自己的 priority。
    2. 数据走 ctx.payload（原地改），返回值只表达控制（None/True 继续，False 否决）。
       否决只在 REJECTION_SITES 里的位点（1 与 6）有语义；别处返回 False 是 handler 的
       bug，这里记一条 site.reject_out_of_contract 错误日志让它暴露出来。
    3. 出错走异常：想中止整次 trace 抛 SiteAbort，落盘失败抛 FlushError，
       两者都由主循环统一处理，管线不吞异常。
"""

from __future__ import annotations

from collections.abc import Iterable

from agent_architecture.contracts import (
    PRIORITY_BANDS,
    REJECTION_SITES,
    Handler,
    Site,
    SiteContext,
    SitePayload,
    TraceContext,
)


def band_of(priority: int) -> str | None:
    """查一个 priority 落在哪个段位；不落在任何段位里返回 None。

    段位表在 contracts/sites.py 的 PRIORITY_BANDS，这里只做查询不做判断。

    举例：band_of(200) -> "dimension"；band_of(900) -> "barrier"；band_of(12345) -> None
    """
    for name, low, high in PRIORITY_BANDS:
        if low <= priority <= high:
            return name
    return None


def band_summary() -> str:
    """把段位表拼成一句给人看的提示，用在报错信息里。

    举例：band_summary() -> "recovery=50、dimension=100-499、hook=500-899、barrier=900"
    """
    return "、".join(
        f"{name}={low}" if low == high else f"{name}={low}-{high}"
        for name, low, high in PRIORITY_BANDS
    )


class HandlerRegistry:
    """位点 -> 有序 handler 列表的索引。

    装配期构造一次，运行期只读。因为运行期不会变，所以"位点 4 的 handler 集合
    在开始收流前就固定了"这件事是天然的，不需要额外的冻结动作。
    """

    def __init__(self, handlers: Iterable[Handler]) -> None:
        """建立索引。

        排序规则：先按 priority 升序；priority 相同时按注册顺序（sorted 是稳定排序）。
        举例：HandlerRegistry([runtime_state_handler, checkpoint_handler])
        出错：
            - handler.id 重复时抛 ValueError——重复的 id 会让日志无法定位到具体 handler。
            - priority 不落在 PRIORITY_BANDS 的任何段位里时抛 ValueError——分段被绕过后
              "落盘屏障必须最后跑"这类依赖只会静默失效，事后查不出来。
        """
        collected = list(handlers)
        seen: set[str] = set()
        for handler in collected:
            if handler.id in seen:
                raise ValueError(f"handler id 重复：{handler.id}")
            seen.add(handler.id)
            if band_of(handler.priority) is None:
                raise ValueError(
                    f"handler {handler.id} 的 priority={handler.priority} 不在任何段位里；"
                    f"合法段位：{band_summary()}"
                )

        self._handlers: tuple[Handler, ...] = tuple(collected)
        self._by_site: dict[Site, tuple[Handler, ...]] = {
            site: tuple(
                sorted(
                    (h for h in collected if site in h.sites),
                    key=lambda h: h.priority,
                )
            )
            for site in Site
        }

    def for_site(self, site: Site) -> tuple[Handler, ...]:
        """取出挂在这个位点上的 handler，已排好序。

        举例：registry.for_site(Site.TOOL_BEFORE) -> (tool_calling, sandbox, session, checkpoint)
        """
        return self._by_site[site]

    def handlers(self) -> tuple[Handler, ...]:
        """全部 handler，按注册顺序。

        举例：registry.handlers() -> (h1, h2, h3)
        """
        return self._handlers

    def sites_of(self, handler_id: str) -> frozenset[Site]:
        """查一个 handler 挂在哪些位点上；不存在时返回空集合。

        作用：装配后自检用，比如断言"checkpoint 确实挂满了四个屏障位点"。
        举例：registry.sites_of("checkpoint.llm_before") -> frozenset({Site.LLM_BEFORE})
        """
        for handler in self._handlers:
            if handler.id == handler_id:
                return frozenset(handler.sites)
        return frozenset()


class SitePipeline:
    """位点管线。内核在每个位点上调用它一次（位点 6/7/8 按粒度调用）。

    举例：pipeline = SitePipeline(HandlerRegistry([...]))
          allowed = await pipeline.run(Site.TOOL_BEFORE, payload, trace)
    """

    def __init__(self, registry: HandlerRegistry) -> None:
        # handler 索引。
        #
        # 举例：HandlerRegistry([...])
        self.registry = registry

    async def run(self, site: Site, payload: SitePayload, trace: TraceContext) -> bool:
        """在当前位点上依次执行 handler。

        返回值：
            - True   全部 handler 都放行（或被位点接受），可以继续下游动作
            - False  被某个 handler 否决，下游动作必须跳过

        异常：
            - handler 抛出的异常原样向上传递，由主循环按位点决定怎么收尾
              （位点 3 抛 FlushError 就拦掉模型调用，位点 7 抛异常就走重试规则）

        举例：if not await pipeline.run(Site.TOOL_BEFORE, batch, trace): 跳过执行
        """
        ctx = SiteContext(trace=trace, site=site, payload=payload)
        logger = trace.ports.logger
        logger.debug(
            "site.begin",
            site=site.value,
            trace_id=trace.state.trace_id,
            turn=trace.state.turn_index,
        )

        for handler in self.registry.for_site(site):
            decision = await handler.run(ctx)
            if decision is False:
                # 这个位点有没有否决语义，决定了这条 False 该记 INFO 还是 ERROR：
                # 位点 1/6 的 False 是契约内的正常控制，别处的 False 是 handler 写错了。
                # 两种情况都照旧短路本位点，只是日志级别不同——不改变运行语义。
                if site in REJECTION_SITES:
                    logger.info(
                        "site.rejected",
                        site=site.value,
                        handler=handler.id,
                        trace_id=trace.state.trace_id,
                    )
                else:
                    logger.error(
                        "site.reject_out_of_contract",
                        site=site.value,
                        handler=handler.id,
                        trace_id=trace.state.trace_id,
                    )
                return False

        logger.debug("site.end", site=site.value, trace_id=trace.state.trace_id)
        return True

"""一次 trace 的主循环：位点 1 -> while{位点 2..9} -> 位点 10。

它只做三件事：

    1. 按顺序走位点 —— **位点之间跑什么，全看注册了哪些 handler**；
    2. 把异常翻译成收尾状态，并保证**位点 10 一定跑到**（除非进程没了）；
    3. 每轮末尾判定要不要继续。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from agent_architecture.contracts import (
    ContextOverflowError,
    Site,
    SiteAbort,
    TraceBeforePayload,
    TraceContext,
    TraceEndPayload,
    TraceStatus,
)

from .pipeline import SitePipeline
from .termination import decide_after_turn
from .turn_runner import TurnRunner


@dataclass(frozen=True, slots=True)
class TraceOutcome:
    """一次 trace 的结果。"""

    # 按什么状态收尾。
    #
    # 举例：TraceStatus.COMPLETED
    status: TraceStatus

    # 收尾原因；正常完成时为 None。
    #
    # 举例："达到 max_turns=5"
    reason: str | None

    # 一共跑了几轮。
    #
    # 举例：3
    turns: int

    # 总耗时（毫秒），由单调时钟前后相减得出。
    #
    # 举例：8421.5
    duration_ms: float


class TraceRunner:
    """跑一次 trace。**无状态**，可以跨 trace、跨会话复用。

    举例：outcome = await TraceRunner(pipeline).run(trace, trigger_payload)
    """

    def __init__(self, pipeline: SitePipeline) -> None:
        # 位点管线。
        #
        # 举例：SitePipeline(HandlerRegistry([...]))
        self.pipeline = pipeline

        # 每轮 turn 的执行器。
        #
        # 举例：TurnRunner(pipeline)
        self.turns = TurnRunner(pipeline)

    async def run(self, trace: TraceContext, trigger: TraceBeforePayload) -> TraceOutcome:
        """跑完一次 trace。

        异常在这里被翻译成收尾状态，绝不往外抛——只有进程级取消（CancelledError）
        会先收尾再往上抛。

        举例：outcome = await runner.run(trace, payload)
        """
        started = trace.ports.clock.monotonic()
        status, reason = TraceStatus.COMPLETED, None

        try:
            status, reason = await self._drive(trace, trigger)
        except SiteAbort as exc:
            status, reason = TraceStatus.ABORTED, exc.message
        except asyncio.CancelledError:
            # 进程级取消也要收尾。它是 BaseException，下面的 except Exception 抓不到 ——
            # 不收的话日志末尾就没有 trace_end，下次启动会把这次执行当成崩溃，
            # 触发一次本不该有的恢复。
            await self._close(
                trace, TraceStatus.ABORTED, "任务被取消", self._elapsed(trace, started)
            )
            raise
        except ContextOverflowError as exc:
            status, reason = TraceStatus.BUDGET_EXCEEDED, exc.message
        # 刻意捕获所有异常：任何未预期异常都算这次 trace 失败，不能让异常逃出 run()，
        # 否则位点 10 就没机会收尾了。
        except Exception as exc:
            status, reason = TraceStatus.FAILED, f"{type(exc).__name__}: {exc}"

        duration_ms = self._elapsed(trace, started)
        await self._close(trace, status, reason, duration_ms)

        return TraceOutcome(
            status=status,
            reason=reason,
            turns=trace.state.turn_index,
            duration_ms=duration_ms,
        )

    async def _drive(
        self, trace: TraceContext, trigger: TraceBeforePayload
    ) -> tuple[TraceStatus, str | None]:
        """位点 1 开跑，然后一轮一轮转到收尾为止。

        举例：status, reason = await runner._drive(trace, payload)
        """
        allowed = await self.pipeline.run(Site.TRACE_BEFORE, trigger, trace)
        if not allowed:
            return TraceStatus.FAILED, "trace:before 位点被否决，本次执行不开始"

        while True:
            # 安全点：每轮开头看一眼取消信号。turn 之内还有两个检查点（收流的每个分片、
            # 每条工具调用之前），见 turn_runner。
            if trace.signal.is_set():
                raise SiteAbort("用户中断")

            turn = await self.turns.run(trace)
            stop = decide_after_turn(trace.state, requested_tools=turn.requested_tools)
            if stop is not None:
                return stop.status, stop.reason

    def _elapsed(self, trace: TraceContext, started: float) -> float:
        """从 started 到现在的毫秒数。

        举例：runner._elapsed(trace, 12.5) -> 8421.5
        """
        return (trace.ports.clock.monotonic() - started) * 1000

    async def _close(
        self,
        trace: TraceContext,
        status: TraceStatus,
        reason: str | None,
        duration_ms: float,
    ) -> None:
        """位点 10：无论上面是正常还是异常，都要走到这里。

        收尾本身再出错也不能把异常抛出去——那时候已经没有"上一级"可以处理它了，
        记一条日志即可。

        举例：await runner._close(trace, TraceStatus.COMPLETED, None, 12.5)
        """
        trace.state.is_running = False
        payload = TraceEndPayload(status=status, reason=reason, duration_ms=duration_ms)
        try:
            await self.pipeline.run(Site.TRACE_END, payload, trace)
        # 刻意捕获所有异常：收尾阶段已经没有"上一级"能处理它了，只记日志。
        except Exception as exc:
            trace.ports.logger.error(
                "trace.close_failed",
                trace_id=trace.state.trace_id,
                error=f"{type(exc).__name__}: {exc}",
            )

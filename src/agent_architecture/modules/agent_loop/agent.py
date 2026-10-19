"""对外门面：把一条用户消息跑成一次 trace。

它负责**组装执行现场**（trace_id、Runtime State、TraceContext），
然后把控制权交给 TraceRunner。至于执行过程中发生什么，全看注册了哪些 handler。
"""

from __future__ import annotations

from agent_architecture.contracts import (
    AppConfig,
    CancelToken,
    FrozenConfig,
    Message,
    Ports,
    SessionHandle,
    TraceBeforePayload,
    TraceContext,
    TraceTrigger,
)
from agent_architecture.infra.config import freeze_config
from agent_architecture.modules.runtime_state import bootstrap_runtime_state

from .pipeline import SitePipeline
from .trace_runner import TraceOutcome, TraceRunner


class Agent:
    """一个可以反复使用的 agent 门面。

    举例：agent = Agent(pipeline=pipeline, ports=ports, app_config=config)
          outcome = await agent.run(session=session, user_message=msg)
    """

    def __init__(self, *, pipeline: SitePipeline, ports: Ports, app_config: AppConfig) -> None:
        # 位点管线，装着全部已注册的 handler。
        #
        # 举例：SitePipeline(HandlerRegistry([...]))
        self.pipeline = pipeline

        # 基础端口集合。
        #
        # 举例：Ports(clock=..., ids=..., logger=..., events=..., tokens=..., llm=...)
        self.ports = ports

        # 进程级全局配置；每次 trace 会从它冻出一份快照。
        #
        # 举例：load_app_config()
        self.app_config = app_config

        self._runner = TraceRunner(pipeline)

    async def run(
        self,
        *,
        session: SessionHandle,
        user_message: Message,
        trigger: TraceTrigger = TraceTrigger.USER,
        parent_trace_id: str | None = None,
        signal: CancelToken | None = None,
    ) -> TraceOutcome:
        """跑一次 trace。

        这里把"这次 trace 用什么模型、什么权限"冻进快照：默认值取会话自己的偏好，
        之后配置再改也不影响本次执行。

        举例：outcome = await agent.run(session=session, user_message=msg)
        """
        config: FrozenConfig = freeze_config(self.app_config, session_meta=session.meta)
        state = bootstrap_runtime_state(
            session_id=session.meta.session_id,
            trace_id=self.ports.ids.new_id("trace"),
            parent_trace_id=parent_trace_id,
            user_message=user_message,
            config=config,
            started_at=self.ports.clock.now(),
        )
        trace = TraceContext(
            state=state,
            session=session,
            config=config,
            ports=self.ports,
            signal=signal or CancelToken(),
        )
        payload = TraceBeforePayload(
            session_id=session.meta.session_id,
            trigger=trigger,
            parent_trace_id=parent_trace_id,
            user_message=user_message,
            model=config.model,
            permission=config.permission,
        )
        return await self._runner.run(trace, payload)

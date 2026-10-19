"""装配根：把配置、端口、handler、门面装成一个能直接用的整体。

**只有这一层知道有哪些具体实现。** 别的层都只认 contracts 里的 Protocol ——
想换存储、换模型接入、换 token 计数器，改这里一处就够。
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_architecture.contracts import AppConfig, ApprovalProvider, LlmClient, Ports
from agent_architecture.infra.approvals import ApprovalGate
from agent_architecture.infra.config import load_app_config
from agent_architecture.infra.llm import build_openai_client
from agent_architecture.infra.observability import InProcessEventBus, StdLogger
from agent_architecture.infra.storage import FileSessionStore
from agent_architecture.infra.system import HeuristicTokenCounter, SystemClock, UuidGenerator
from agent_architecture.modules import checkpoint as checkpoint_module
from agent_architecture.modules import (
    compression,
    hook,
    runtime_state,
    sandbox,
    session,
    tool_calling,
)
from agent_architecture.modules.agent_loop import Agent, HandlerRegistry, SitePipeline

from .registry import SessionRegistry


@dataclass(frozen=True, slots=True)
class Container:
    """装好的一整套。是进程级、一整个智能体共用一份，不是会话级的。

    举例：c = build_container(); live = await c.sessions.create(); await c.agent.run(...)
    """

    # 进程级配置。
    #
    # 举例：AppConfig(workspace="D:/proj", model="deepseek-chat", ...)
    config: AppConfig

    # 基础端口集合，由 Agent 带进每一次 trace。
    #
    # 举例：Ports(clock=SystemClock(), ids=UuidGenerator(), ...)
    ports: Ports

    # 位点管线，里面挂着全部已注册的 handler。
    #
    # 举例：SitePipeline(HandlerRegistry([...]))
    pipeline: SitePipeline

    # 对外门面：跑一次 trace。
    #
    # 举例：Agent(pipeline=..., ports=..., app_config=...)
    agent: Agent

    # 进程内的会话注册表：多表面共享靠它。
    #
    # 举例：SessionRegistry(store=FileSessionStore(".data/sessions"), ...)
    sessions: SessionRegistry

    # 用户 hook 的注册表。
    #
    # 作用：拿到容器的人靠它往十个位点上插逻辑，不用碰管线。
    # 举例：container.hooks.on(events.AGENT_TURN_END, audit)
    hooks: hook.HookRegistry

    # 待审批表：沙箱在位点 6 里等它，api 层按它列列表与收回执。
    #
    # 举例：ApprovalGate()
    approvals: ApprovalProvider


def build_container(config: AppConfig | None = None, *, llm: LlmClient | None = None) -> Container:
    """按配置装出一整套。

    config 省略时从环境变量与 .env 读一份；llm 省略时按配置建一个 OpenAI 兼容客户端
    （测试可以塞假件进来，那是不联网跑通整条链路的唯一办法）。

    举例：container = build_container()
    """
    app_config = config if config is not None else load_app_config()
    logger = StdLogger(app_config.log_level)

    # 工具只建一份：沙箱按它查声明，tool_calling 按它取 schema 与实现。
    tools = tool_calling.build_catalog()

    # 待审批表也只建一份：沙箱往里放请求，api 层从它取出回执。
    approvals = ApprovalGate()
    ports = Ports(
        clock=SystemClock(),
        ids=UuidGenerator(),
        logger=logger,
        events=InProcessEventBus(logger),
        tokens=HeuristicTokenCounter(),
        llm=llm if llm is not None else build_openai_client(app_config),
        tools=tools,
        approvals=approvals,
    )

    # 存储只建一份：注册表读它，checkpoint 写它。
    store = FileSessionStore(app_config.session_dir)
    checkpoint = checkpoint_module.Checkpoint(store, logger=logger)

    # 现在有七个模块在位点上注册。技能落地后加在这里。
    #
    # 三个模块的 build_handlers 要带参数：tool_calling 要那张工具表，
    # hook 要用户注册表（它注册的 handler 干的事就是替用户跑他们挂上来的那些 hook），
    # checkpoint 也要工具表 —— 恢复时按声明的副作用等级判"能不能安全重试"。
    # 两张表都交出去，拿到容器的人往上面挂。
    hooks = hook.HookRegistry()
    pipeline = SitePipeline(
        HandlerRegistry(
            [
                *runtime_state.build_handlers(),
                *session.build_handlers(),
                *compression.build_handlers(),
                *tool_calling.build_handlers(tools),
                *hook.build_handlers(hooks),
                *sandbox.build_handlers(),
                *checkpoint_module.build_handlers(checkpoint, tools=tools),
            ]
        )
    )
    agent = Agent(pipeline=pipeline, ports=ports, app_config=app_config)

    sessions = SessionRegistry(
        store=store,
        ids=ports.ids,
        clock=ports.clock,
        config=app_config,
    )

    return Container(
        config=app_config,
        ports=ports,
        pipeline=pipeline,
        agent=agent,
        sessions=sessions,
        hooks=hooks,
        approvals=approvals,
    )

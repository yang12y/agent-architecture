"""装配根：把配置、端口、handler、门面装成一个能直接用的整体。

**只有这一层知道有哪些具体实现。** 别的层都只认 contracts 里的 Protocol ——
想换存储、换模型接入、换 token 计数器，改这里一处就够。
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_frame.contracts import (
    AppConfig,
    ApprovalProvider,
    HttpTransport,
    LlmClient,
    Ports,
    QuestionProvider,
)
from agent_frame.infra.approvals import ApprovalGate
from agent_frame.infra.config import load_app_config
from agent_frame.infra.http import build_http_transport
from agent_frame.infra.llm import build_openai_client
from agent_frame.infra.observability import InProcessEventBus, StdLogger
from agent_frame.infra.presence import Presence
from agent_frame.infra.questions import QuestionGate
from agent_frame.infra.storage import FileSessionStore
from agent_frame.infra.system import HeuristicTokenCounter, SystemClock, UuidGenerator
from agent_frame.modules import (
    ask_user,
    charts,
    compression,
    hook,
    prompts,
    runtime_state,
    sandbox,
    session,
    skill,
    todo,
    tool_calling,
)
from agent_frame.modules import checkpoint as checkpoint_module
from agent_frame.modules.agent_loop import Agent, HandlerRegistry, SitePipeline
from agent_frame.tools import chat_tools

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

    # 提问表：ask_user 工具往里挂，api 层从它取回答。
    #
    # 举例：QuestionGate(events=bus, presence=presence)
    questions: QuestionProvider

    # 谁在看哪个会话。SSE 流连上/断开时改它，提问前由它判"有没有人能答"。
    #
    # 举例：Presence()
    presence: Presence

    # 业务工具的传输层（一条进程级连接池）。进程收尾时由 api 层关掉。
    #
    # 举例：HttpxTransport()
    http: HttpTransport


def build_container(config: AppConfig | None = None, *, llm: LlmClient | None = None) -> Container:
    """按配置装出一整套。

    config 省略时从环境变量与 .env 读一份；llm 省略时按配置建一个 OpenAI 兼容客户端
    （测试可以塞假件进来，那是不联网跑通整条链路的唯一办法）。

    举例：container = build_container()
    """
    app_config = config if config is not None else load_app_config()
    logger = StdLogger(app_config.log_level)
    events = InProcessEventBus(logger)

    # 谁在看哪个会话：SSE 流连上时记一笔，提问前用它判"有没有人能答"。
    presence = Presence()

    # 提问表也只建一份：ask_user 往里挂，api 层从它取回答。
    questions = QuestionGate(events=events, presence=presence)

    # 工具只建一份：沙箱按它查声明、tool_calling 按它取 schema 与实现。
    # 文件与命令工具默认不装（逻辑删除），要装就把 AGENT_FILE_TOOLS 打开。
    tools = tool_calling.build_catalog(include_files=app_config.file_tools)

    # 提问工具要那个端口，所以实现也只能在这儿生成。
    tools.register(ask_user.ASK_USER_TOOL, ask_user.make_runner(questions))

    # 业务工具用的 HTTP 传输层：一条进程级连接池，进程收尾时由 api 层关掉。
    http = build_http_transport()

    # 技能表也只建一份：目录给位点 3，正文给 skill 工具。
    skills = skill.SkillRegistry.load(skill.default_roots(app_config.workspace))
    tools.register(skill.SKILL_TOOL, skill.make_runner(skills))

    # 待办工具只碰参数；把整份清单落进日志是模块在位点 8 的事（见 modules/todo）。
    tools.register(todo.TODO_TOOL, todo.run)

    # 画图工具同理：工具只校验参数，图由 modules/charts 在位点 8 写进日志。
    for declaration, runner in chat_tools.build():
        tools.register(declaration, runner)

    # 待审批表也只建一份：沙箱往里放请求，api 层从它取出回执。
    approvals = ApprovalGate()
    ports = Ports(
        clock=SystemClock(),
        ids=UuidGenerator(),
        logger=logger,
        events=events,
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
                *prompts.build_handlers(),
                *skill.build_handlers(skills),
                *runtime_state.build_handlers(),
                *session.build_handlers(),
                *compression.build_handlers(),
                *tool_calling.build_handlers(tools),
                *todo.build_handlers(),
                *charts.build_handlers(),
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
        questions=questions,
        presence=presence,
        http=http,
    )

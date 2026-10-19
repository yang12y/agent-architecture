"""全局配置与 trace 级冻结快照。

分工：
    - `AppConfig`：进程启动时读一次的全局配置（环境变量 + 默认值）。
    - `FrozenConfig`：位点 1 冻结、整个 trace 期间不变的快照。

位点 1 之后即使改了 AppConfig，也不会影响已经开始的 trace——trace 用的是快照。
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Permission


@dataclass(frozen=True, slots=True)
class AppConfig:
    """进程级全局配置。整个进程只加载一次，运行期不再变。"""

    # 工作目录，同时也是沙箱的根目录：文件类工具只能在这个目录树内读写。
    #
    # 举例："D:/CodeProjects/agent_architecture"
    workspace: str

    # 会话日志的存放目录，每个会话一个 .jsonl 文件。
    #
    # 举例："D:/CodeProjects/agent_architecture/.data/sessions"
    session_dir: str

    # 默认使用的大模型标识。会被 SessionMeta.model 覆盖（会话可指定自己的模型）。
    #
    # 举例："deepseek-chat"
    model: str

    # 生成压缩摘要用的模型，可以和主模型不同（通常选更便宜的）。
    #
    # 举例："deepseek-chat"；None 表示复用主模型。
    summary_model: str | None

    # 模型服务的密钥。只存在进程内存里，**不写进 trace 快照、不落 Session 日志**。
    #
    # 举例："sk-..."；None 表示没配，构造客户端时会报错。
    llm_api_key: str | None

    # 模型服务的接入地址，用于 OpenAI 兼容端点或自建网关。
    #
    # 举例："https://api.deepseek.com/v1"；None 表示用官方地址。
    llm_base_url: str | None

    # 单次模型请求的超时秒数。
    #
    # 举例：60.0
    llm_timeout_seconds: float

    # 本 turn 内模型调用最多尝试几次（含首次）。用满就按失败收尾。
    #
    # 举例：3
    llm_max_attempts: int

    # 新会话是否让模型开思考模式（推理模型才有用）。
    #
    # 打开之后请求会带上厂商的开关；代价是每轮多花一段思考的 token，
    # 而且**带 tools 的请求必须把上一轮的思考原样传回去**，否则服务端会 400。
    #
    # 举例：True
    thinking: bool

    # 默认权限档位。会被 SessionMeta.permission 覆盖。
    #
    # 举例：Permission.EDITABLE
    permission: Permission

    # 上下文窗口上限，超过就无法再发给模型。
    #
    # 举例：1000000
    max_tokens: int

    # 单次 trace 允许的最大 turn 数，防止无限循环。
    #
    # 举例：50；None 表示不限。
    max_turns: int | None

    # 压缩阈值占上下文窗口的比例：算出的 context_tokens 达到 max_tokens 的这个比例就触发压缩。
    #
    # 举例：0.7
    compact_ratio: float

    # 二级压缩（滑动窗口 + 摘要）保留的最后 n 条 message 条目，其余被摘要取代。
    #
    # 举例：20
    keep_recent: int

    # 单条工具结果的字符数硬上限。工具执行完、结果转成 tool 消息之前按它截断，
    # 超出的部分**直接丢弃**，不留原文、不留引用，只在末尾标注被截断了。
    #
    # 与 long_tool_result_max_tokens 的分工：那个是压缩阶段的判定门槛，作用在已经
    # 截断过的结果上；这个是工具边界上的硬闸，决定内容能不能进日志。
    #
    # 这里存的是**新建会话时抄给它的默认值**；生效的那一份在 `SessionMeta.
    # tool_result_max_chars` 上，改这里不影响已有会话。
    #
    # 举例：60000
    tool_result_max_chars: int

    # 单条工具结果超过多少 token 算"长"，是压缩第一级的判定标准。
    #
    # 举例：2000
    long_tool_result_max_tokens: int

    # 最近几条长工具结果不做占位替换，保留原文。
    #
    # 举例：3
    long_tool_result_keep_recent: int

    # 二级压缩生成摘要时用的提示词模板，模板里会填入被压掉的历史内容。
    #
    # 举例："请把下面的对话压缩成一段可独立阅读的摘要……"
    summary_prompt_template: str

    # 日志级别。
    #
    # 取值（固定五种，级别由低到高）：
    #     - "DEBUG"    调试：每次位点调度、每条 handler 的进出都记
    #     - "INFO"     常规：trace/turn 的开始结束、工具调用摘要
    #     - "WARNING"  警告：可自愈的异常，如重试、兜底压缩被触发
    #     - "ERROR"    错误：trace failed、落盘失败
    #     - "CRITICAL" 严重：进程级不可恢复故障
    # 举例："INFO"
    log_level: str


@dataclass(frozen=True, slots=True)
class FrozenConfig:
    """位点 1 冻结的本次 trace 配置快照。

    模型与权限的默认值来自 SessionMeta，预算与压缩配置来自 AppConfig；
    两者都在位点 1 写进这个快照，之后配置再改也不影响本次 trace。
    """

    # 本次 trace 实际使用的大模型标识。
    #
    # 举例："deepseek-chat"
    model: str

    # 本次 trace 生成摘要用的模型；None 表示复用 model。
    #
    # 举例：None
    summary_model: str | None

    # 本次 trace 实际生效的权限档位。运行中可被策略提升或降级，但快照记录的是初值。
    #
    # 举例：Permission.EDITABLE
    permission: Permission

    # 本次 trace 的上下文窗口上限（冻结值）。
    #
    # 举例：1000000
    max_tokens: int

    # 本次 trace 的 turn 数上限（冻结值）。
    #
    # 举例：50
    max_turns: int | None

    # 本次 trace 内单轮模型调用的最大尝试次数（冻结值）。
    #
    # 举例：3
    llm_max_attempts: int

    # 本次 trace 是否让模型开思考（冻结值）。
    #
    # 举例：True
    thinking: bool

    # 本次 trace 的压缩阈值（冻结值）。
    #
    # 举例：800000
    compact_tokens: int

    # 本次 trace 二级压缩保留的最后 n 条 message 条目数。
    #
    # 举例：20
    keep_recent: int

    # 本次 trace 判定"长工具结果"的 token 门槛。
    #
    # 举例：2000
    long_tool_result_max_tokens: int

    # 本次 trace 保留原文的长工具结果条数。
    #
    # 举例：3
    long_tool_result_keep_recent: int

    # 本次 trace 的摘要提示词模板。
    #
    # 举例："请把下面的对话压缩成一段可独立阅读的摘要……"
    summary_prompt_template: str

    # 单条工具结果最多留多少个字符（冻结值），超出的由位点 8 截断。
    # 值来自 SessionMeta —— 会话有自己的尺度。
    #
    # 举例：2000
    tool_result_max_chars: int

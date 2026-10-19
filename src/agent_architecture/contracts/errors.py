"""契约层异常定义。

这一组异常是跨模块的公共语言：任何模块抛出的领域错误都应是 AgentError 的子类，
内核只认识这一组异常，不需要知道它是哪个模块抛的。

模型调用失败只有一套类：LlmCallError.kind 说清是哪一种失败，每种失败对应一个处置，
见 RETRYABLE_KINDS。厂商差异（谁家返回 429、谁家写 "context length"）关在基础设施层里。
"""

from __future__ import annotations

from enum import StrEnum


class AgentError(Exception):
    """所有领域异常的基类。

    作用：让内核用一个 except 捕获全部领域错误，把非领域错误
    （KeyboardInterrupt、MemoryError 等）区分出来。
    举例：raise AgentError("会话目录不可写")
    """

    def __init__(self, message: str = "") -> None:
        super().__init__(message)

        # 人类可读的错误说明，会原样写进日志和 trace_end 的 reason 字段。
        #
        # 举例："会话目录不可写"
        self.message = message


class ConfigError(AgentError):
    """配置非法或缺失。

    触发时机：进程启动或位点 1 加载配置时。
    举例：compact_tokens 配得比 max_tokens 还大，压缩永远触发不了。
    """


class SiteAbort(AgentError):
    """位点 handler 主动要求中止整次 trace。

    作用：handler 用它打断主循环；内核收到后不再继续，直接跳到 trace 收尾（位点 10），
    trace_end 记为 aborted。
    举例：用户在流式输出途中点"停止"，hook 抛出 SiteAbort("用户中断")。
    """


class FlushError(AgentError):
    """Session 日志落盘失败。

    触发时机：四个落盘屏障位点（3 llm:before / 5 llm:after / 6 tool:before / 8 tool:after）。
    后果：失败即阻止——位点 3 拦掉本次模型调用，位点 6 拦掉工具正文。
    举例：磁盘写满、会话目录权限被撤销。
    """


class ToolArgumentError(AgentError):
    """工具参数判不了、解释不了。

    **判不了要明说，不能默认放行** —— 沙箱靠它把"这次判不出来"落成待审批，
    而不是悄悄跳过判定。

    举例：raise ToolArgumentError("参数 path 不是非空字符串：None")
    """


class LlmErrorKind(StrEnum):
    """一次模型调用为什么失败。**每一种都对应一个明确的处置**，见 RETRYABLE_KINDS。"""

    # 密钥不对、没有权限 —— 换配置才行，重试没有意义。
    #
    # 举例：LlmErrorKind.AUTH
    AUTH = "auth"

    # 请求本身不合法：参数错、模型名不存在、工具 schema 不合法。
    #
    # 举例：LlmErrorKind.REQUEST
    REQUEST = "request"

    # 上下文放不下 —— 压缩没兜住，重试没有意义。
    #
    # 举例：LlmErrorKind.OVERFLOW
    OVERFLOW = "overflow"

    # 被限流 —— 等一会儿再试有意义。
    #
    # 举例：LlmErrorKind.RATE_LIMIT
    RATE_LIMIT = "rate_limit"

    # 服务端 5xx —— 对面抽了。
    #
    # 举例：LlmErrorKind.SERVER
    SERVER = "server"

    # 连不上或连接中途断掉 —— 网络抖动。
    #
    # 举例：LlmErrorKind.CONNECTION
    CONNECTION = "connection"

    # 超时 —— 对面太慢或请求太大。
    #
    # 举例：LlmErrorKind.TIMEOUT
    TIMEOUT = "timeout"

    # 认不出来 —— 不赌，直接上抛给人看。
    #
    # 举例：LlmErrorKind.UNKNOWN
    UNKNOWN = "unknown"


# 哪些种类重试有意义。**表里没有的一律立即上抛**。
#
# 举例：RETRYABLE_KINDS -> frozenset({RATE_LIMIT, SERVER, CONNECTION, TIMEOUT})
RETRYABLE_KINDS = frozenset(
    {
        LlmErrorKind.RATE_LIMIT,
        LlmErrorKind.SERVER,
        LlmErrorKind.CONNECTION,
        LlmErrorKind.TIMEOUT,
    }
)


class LlmCallError(AgentError):
    """一次模型调用失败。

    作用：把"这是哪一种失败"从实现里带出来——主循环只认 kind，不认识各家的错误码。
    举例：raise LlmCallError("被限流：...", kind=LlmErrorKind.RATE_LIMIT)
    """

    def __init__(self, message: str = "", *, kind: LlmErrorKind) -> None:
        super().__init__(message)

        # 这次失败属于哪一种，决定处置。
        #
        # 取值：见 LlmErrorKind。
        # 举例：LlmErrorKind.RATE_LIMIT
        self.kind = kind

    @property
    def retryable(self) -> bool:
        """再试一次有没有可能成功。由 kind 推导，不另存一份，免得两处说法不一致。

        举例：LlmCallError("被限流", kind=LlmErrorKind.RATE_LIMIT).retryable -> True
        """
        return self.kind in RETRYABLE_KINDS


class ContextOverflowError(LlmCallError):
    """上下文放不下，这次调用发不出去。

    **两个来源**：位点 3 的估算提前拦下的，或模型服务挡回来的。
    两者处置一样：不重试，本次 trace 按 budget_exceeded 收尾。

    举例：raise ContextOverflowError("压缩之后仍有 120000 token，阈值是 100000")
    """

    def __init__(self, message: str = "") -> None:
        super().__init__(message, kind=LlmErrorKind.OVERFLOW)

"""工具契约里**跨模块共用**的两条规则。

它们放在 contracts 而不是某个模块里，因为有两个模块要按**同一条**规则办事，
而模块之间禁止互相 import：

    - 路径参数怎么解释：沙箱按它判越界，工具按它开文件。两边不一致的话，
      沙箱放行的是 A、工具打开的是 B —— 那是安全洞，不是风格问题。
    - 什么时候可以重试：执行器按它决定重试，崩溃恢复按它决定重放。
      "此规则与恢复判定同一条"是明写的不变量，两份实现迟早走岔。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ToolArgumentError
from .models import JsonValue, SideEffect, ToolDeclaration
from .ports import Logger
from .sites import CancelToken


def resolve_in_workspace(raw: str, workspace: str) -> Path:
    """把工具参数里的路径解析成工作目录下的绝对路径。

    **规范化不是可选的**：`workspace/../../etc/passwd`、软链接、`~` 全靠它挡。
    相对路径按 workspace 展开，展开后再 resolve。

    举例：resolve_in_workspace("a.py", "D:/proj") -> Path("D:/proj/a.py")
    出错：不是非空字符串、或路径非法时抛 ToolArgumentError。
    """
    if not isinstance(raw, str) or not raw:
        raise ToolArgumentError(f"路径不是非空字符串：{raw!r}")
    try:
        target = Path(raw).expanduser()
        if not target.is_absolute():
            target = Path(workspace) / target
        return target.resolve()
    except (OSError, ValueError) as exc:
        raise ToolArgumentError(f"路径解析失败：{raw}") from exc


def is_retryable(declaration: ToolDeclaration | None) -> bool:
    """这条工具的调用失败之后能不能自动重试。

    规则：`none` / `idempotent` 可以；`non_idempotent` **只有在接受幂等键时**才可以
    （键让下游能认出"这是同一次调用"，重复执行不会产生第二份影响）。
    查不到声明的一律不重试 —— 判不了就别赌。

    举例：is_retryable(write_decl) -> False
    """
    if declaration is None:
        return False
    if declaration.side_effect is not SideEffect.NON_IDEMPOTENT:
        return True
    return declaration.accepts_idempotency_key


# --------------------------------------------------------------------------
# 工具执行现场
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ToolContext:
    """一次工具执行的现场。**工具实现只拿得到这些。**

    放在 contracts 而不是 tool_calling 里：业务工具只允许依赖 contracts，
    执行器按同一份类型把现场造出来。

    举例：ToolContext(workspace="D:/proj", call_id="call_7f3a91", signal=CancelToken(), logger=...)
    """

    # 沙箱根目录：文件类工具只能在这里面读写。
    #
    # 举例："D:/proj"
    workspace: str

    # 本次调用的标识，出日志与报错时用它定位。
    #
    # 举例："call_7f3a91"
    call_id: str

    # 取消信号。长耗时的工具要自己找地方看一眼 —— 内核的安全点管不到工具内部。
    #
    # 举例：CancelToken()
    signal: CancelToken

    # 结构化日志。
    #
    # 举例：StdLogger("INFO")
    logger: Logger

    # trace 级只读变量：随请求头进来的一次性数据，业务工具按需读。
    # **不落盘、不进 Session**，只服务这一次 trace。
    #
    # 举例：{"authorization": "Bearer x", "tenant": "BF58A35B-..."}
    variables: Mapping[str, str] = field(default_factory=dict)

    # 这次调用属于哪个会话 / 哪条 trace。**只有要对外发事件的工具用得上**（ask_user 靠它
    # 把提问路由到正确的会话），其余工具不看这两项。
    #
    # 举例："sess_ab12" / "trace_9c8b7a"；空串表示现场没填
    session_id: str = ""
    trace_id: str = ""

    # 这次调用属于哪个用户。要按用户归档的工具用得上，其余工具不看。
    #
    # 举例："u1"
    user_id: str = ""


# 工具实现：拿到执行现场与**已经补齐默认值、通过校验**的参数，返回任意 JSON。
# 返回值怎么变成给模型看的文本由位点 8 决定，工具自己不管这件事。
#
# 举例：async def read(ctx, arguments): return ctx.workspace
ToolRunner = Callable[[ToolContext, JsonValue], Awaitable[JsonValue]]

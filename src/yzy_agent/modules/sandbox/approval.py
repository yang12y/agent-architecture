"""工具级 + 参数级审批，产出三态之一。

结果只有三个，复用 contracts 的 `ToolDecision`，不另立枚举：

    放行 `EXECUTE` / 用户审批 `AWAIT_APPROVAL` / 拒绝 `REJECT`

判定是**纯函数**：只吃 `(permission, declaration, arguments, workspace)`，不碰端口、
不碰时钟、不写日志 —— 三档权限 × 参数组合才能穷举单测。

返回 `Verdict` 而不是光秃秃一个枚举：要人审批时得告诉用户**为什么**，
而理由是判定的副产品 —— 让它另走一条路重新拼，迟早跟判定对不上。

**两级怎么合**：先工具级（能力够不够），过了再参数级（越不越界）。
跟"工具级不过就短路"等价，但两级各自能穷举，不用构造组合用例。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from yzy_agent.contracts import (
    JsonValue,
    Permission,
    ToolArgumentError,
    ToolDecision,
    ToolDeclaration,
)

from .permission import at_least
from .policies import ResourceKind, inside, resolve_path, route

# 三态从严到宽。合并多个判定时取靠后的那个。
#
# 举例：STRICTNESS.index(ToolDecision.REJECT) -> 2
STRICTNESS: tuple[ToolDecision, ...] = (
    ToolDecision.EXECUTE,
    ToolDecision.AWAIT_APPROVAL,
    ToolDecision.REJECT,
)

# 由**策略**定死档位要求的资源类。它们的能力无法静态判定
#
# 举例：CAPABILITY_KINDS -> frozenset({COMMAND, NETWORK})
CAPABILITY_KINDS: frozenset[ResourceKind] = frozenset({ResourceKind.COMMAND, ResourceKind.NETWORK})


@dataclass(frozen=True, slots=True)
class Verdict:
    """一次判定的结论：三态之一 + 为什么。"""

    # 放行 / 用户审批 / 拒绝。
    #
    # 举例：ToolDecision.AWAIT_APPROVAL
    decision: ToolDecision

    # 给用户看的理由，会进审批请求；放行时也有人看（日志）。
    #
    # 举例："路径在工作目录之外：D:\\etc\\passwd"
    reason: str


def decide(
    *,
    permission: Permission,
    declaration: ToolDeclaration,
    arguments: JsonValue,
    workspace: str,
) -> Verdict:
    """对单个工具三态判定的唯一入口。**纯函数**：同样的输入永远同样的输出。

    举例：decide(permission=Permission.READ_ONLY, declaration=write_decl,
                 arguments={"path": "a.py"}, workspace="D:/proj").decision
          -> ToolDecision.REJECT
    """

    # 解析参数类型，返回类型列表，如[ "file", "network", "commond"]
    kinds = route(declaration)

    # —— 工具级：能力够不够 ——
    required = (
        Permission.FULL_ACCESS if kinds & CAPABILITY_KINDS else declaration.required_permission
    )
    if not at_least(permission, required):
        if kinds & CAPABILITY_KINDS:
            # 这里是网络请求或者是命令的工具的情况
            # 能力类判不准，而这里唯一判得了的一方就是用户 —— 交给他，不替他拒绝。
            return Verdict(
                ToolDecision.AWAIT_APPROVAL,
                f"{declaration.name} 要碰的是无法静态判定的能力，需要人工确认；"
                f"当前权限 {permission.value}",
            )
        # 边界类相反：workspace 划的线是明确的，不够就是不够。
        return Verdict(
            ToolDecision.REJECT,
            f"当前权限 {permission.value} 低于 {declaration.name} 要求的 {required.value}",
        )

    # —— 参数级：逐类判，取最严 ——
    if ResourceKind.FILE in kinds:
        return _judge_file(arguments, workspace=workspace)
    return Verdict(ToolDecision.EXECUTE, f"{declaration.name} 不碰受管资源")


def _judge_file(arguments: JsonValue, *, workspace: str) -> Verdict:
    """文件类：越界就问人。

    档位已经在工具级判过了（read_only 下碰不到写），所以这里只剩边界问题。

    举例：_judge_file({"path": "a.py"}, workspace="D:/proj").decision -> ToolDecision.EXECUTE
    """
    try:
        target = resolve_path(arguments, workspace=workspace)
    except ToolArgumentError as exc:
        # 判不出来不等于没危险 —— 问人，不放行。
        return Verdict(ToolDecision.AWAIT_APPROVAL, f"路径判不出来，需要人工确认：{exc}")
    if inside(target, workspace=workspace):
        return Verdict(ToolDecision.EXECUTE, f"路径在工作目录内：{target}")
    # 越界，但不是规则明确禁止的事：让用户自己决定这一次要不要放。
    return Verdict(ToolDecision.AWAIT_APPROVAL, f"路径在工作目录之外：{target}")


def strictest(verdicts: Sequence[Verdict]) -> Verdict:
    """一堆判定里取最严的那个；一个都没有就是放行。

    举例：strictest([放行, 等审批]).decision -> ToolDecision.AWAIT_APPROVAL
    """
    if not verdicts:
        return Verdict(ToolDecision.EXECUTE, "没有可判的项")
    return max(verdicts, key=lambda v: STRICTNESS.index(v.decision))

"""三态判定的穷举：三档权限 × 参数组合。

判定是纯函数，所以这里不需要任何端口、时钟或日志 —— 输入与输出一一对应。
"""

from __future__ import annotations

from pathlib import Path

from agent_architecture.contracts import Permission, SideEffect, ToolDecision
from agent_architecture.modules.sandbox import Verdict, decide, strictest
from fakes import make_tool_declaration


def _write(required: Permission = Permission.EDITABLE):
    """一个要写文件的工具。

    举例：_write().name -> "write"
    """
    return make_tool_declaration(
        "write",
        properties=["path"],
        side_effect=SideEffect.NON_IDEMPOTENT,
        required_permission=required,
    )


def _decide(permission, arguments, *, declaration=None, workspace):
    """跑一次判定；不指定声明时用 write。

    举例：_decide(Permission.EDITABLE, {"path": "a.py"}, workspace=scratch_dir)
    """
    return decide(
        permission=permission,
        declaration=declaration or _write(),
        arguments=arguments,
        workspace=str(workspace),
    )


# ---------------------------------------------------------------------------
# 工具级：能力够不够
# ---------------------------------------------------------------------------


def test_a_level_below_the_requirement_is_refused(scratch_dir: Path) -> None:
    """文件类是边界类：workspace 划的线是明确的，不够就是不够。"""
    verdict = _decide(Permission.READ_ONLY, {"path": "a.py"}, workspace=scratch_dir)

    assert verdict.decision is ToolDecision.REJECT
    assert "read_only" in verdict.reason


def test_a_command_needs_full_access_whatever_the_tool_claims(scratch_dir: Path) -> None:
    """工具把自己的 required_permission 声明低了，不该因此在中低档位跑起来。"""
    bash = make_tool_declaration(
        "bash", properties=["command"], required_permission=Permission.READ_ONLY
    )

    verdict = _decide(
        Permission.EDITABLE, {"command": "ls"}, declaration=bash, workspace=scratch_dir
    )

    # 判不准就不替用户做决定 —— 交给他，而不是硬拒。
    assert verdict.decision is ToolDecision.AWAIT_APPROVAL


def test_a_command_is_allowed_at_full_access(scratch_dir: Path) -> None:
    """档位给到了就放行 —— 这条路径由策略定死，不看工具声明。"""
    bash = make_tool_declaration(
        "bash", properties=["command"], required_permission=Permission.READ_ONLY
    )

    verdict = _decide(
        Permission.FULL_ACCESS, {"command": "ls"}, declaration=bash, workspace=scratch_dir
    )

    assert verdict.decision is ToolDecision.EXECUTE


def test_a_url_also_waits_for_a_human_below_full_access(scratch_dir: Path) -> None:
    """网络与命令同族：能力类，判不准就问人。"""
    fetch = make_tool_declaration(
        "fetch", properties=["url"], required_permission=Permission.READ_ONLY
    )

    verdict = _decide(
        Permission.EDITABLE,
        {"url": "https://example.com"},
        declaration=fetch,
        workspace=scratch_dir,
    )

    assert verdict.decision is ToolDecision.AWAIT_APPROVAL


def test_a_tool_that_touches_nothing_only_needs_its_level(scratch_dir: Path) -> None:
    """纯计算的工具碰不到受管资源，档位够了就放行。"""
    calc = make_tool_declaration(
        "calc", properties=["expression"], required_permission=Permission.READ_ONLY
    )

    verdict = _decide(
        Permission.READ_ONLY, {"expression": "1+1"}, declaration=calc, workspace=scratch_dir
    )

    assert verdict.decision is ToolDecision.EXECUTE


# ---------------------------------------------------------------------------
# 参数级：越不越界
# ---------------------------------------------------------------------------


def test_a_path_inside_the_workspace_is_allowed(scratch_dir: Path) -> None:
    """工作目录里的事，editable 就够了。"""
    verdict = _decide(Permission.EDITABLE, {"path": "sub/a.py"}, workspace=scratch_dir)

    assert verdict.decision is ToolDecision.EXECUTE


def test_a_path_outside_the_workspace_waits_for_a_human(scratch_dir: Path) -> None:
    """越界，但不是规则明确禁止的事 —— 让用户自己决定这一次。"""
    verdict = _decide(Permission.EDITABLE, {"path": "../outside.txt"}, workspace=scratch_dir)

    assert verdict.decision is ToolDecision.AWAIT_APPROVAL
    assert "之外" in verdict.reason


def test_dotdot_cannot_smuggle_a_path_out(scratch_dir: Path) -> None:
    """不规范化的话 `sub/../../x` 会被当成界内。"""
    verdict = _decide(Permission.EDITABLE, {"path": "sub/../../outside.txt"}, workspace=scratch_dir)

    assert verdict.decision is ToolDecision.AWAIT_APPROVAL


def test_a_path_that_cannot_be_parsed_waits_for_a_human(scratch_dir: Path) -> None:
    """判不出来不等于没危险 —— 问人，不放行。"""
    verdict = _decide(Permission.EDITABLE, {}, workspace=scratch_dir)

    assert verdict.decision is ToolDecision.AWAIT_APPROVAL


def test_a_tool_touching_both_file_and_network_still_checks_the_file(scratch_dir: Path) -> None:
    """只取最严的那一类会让另一类漏检 —— 在边界上漏检就是没判。"""
    fetch = make_tool_declaration(
        "fetch", properties=["url", "path"], required_permission=Permission.READ_ONLY
    )
    arguments = {"url": "https://example.com", "path": "../outside.txt"}

    verdict = _decide(Permission.FULL_ACCESS, arguments, declaration=fetch, workspace=scratch_dir)

    assert verdict.decision is ToolDecision.AWAIT_APPROVAL


# ---------------------------------------------------------------------------
# 合并
# ---------------------------------------------------------------------------


def test_every_verdict_carries_a_reason(scratch_dir: Path) -> None:
    """要人审批时得告诉用户为什么 —— 理由是判定的副产品。"""
    verdict = _decide(Permission.EDITABLE, {"path": "../outside.txt"}, workspace=scratch_dir)

    assert verdict.reason
    assert "outside.txt" in verdict.reason


def test_strictest_takes_the_harshest() -> None:
    """拒绝 > 等审批 > 放行。"""
    allow = Verdict(ToolDecision.EXECUTE, "放行")
    ask = Verdict(ToolDecision.AWAIT_APPROVAL, "问人")
    deny = Verdict(ToolDecision.REJECT, "拒绝")

    assert strictest([]).decision is ToolDecision.EXECUTE
    assert strictest([allow, ask]).decision is ToolDecision.AWAIT_APPROVAL
    assert strictest([allow, ask, deny]).decision is ToolDecision.REJECT

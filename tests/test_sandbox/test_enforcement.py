"""把判定落到 decisions 上 —— 这是"执行前强制"唯一落到实处的地方。"""

from __future__ import annotations

from pathlib import Path

from fakes import (
    FakeApprovals,
    FakeToolRegistry,
    StubSession,
    make_frozen_config,
    make_ports,
    make_site_context,
    make_tool_declaration,
    make_trace_context,
)
from yzy_agent.contracts import (
    ApprovalDecision,
    ApprovalRequired,
    Permission,
    SideEffect,
    Site,
    ToolBeforePayload,
    ToolCall,
    ToolCallStatus,
    ToolDecision,
)
from yzy_agent.infra.observability import InProcessEventBus, RecordingLogger
from yzy_agent.modules.sandbox import enforce


def _write():
    """一个要写文件的工具。"""
    return make_tool_declaration(
        "write",
        properties=["path"],
        side_effect=SideEffect.NON_IDEMPOTENT,
        required_permission=Permission.EDITABLE,
    )


def _call(call_id: str = "call_1", name: str = "write", arguments: dict | None = None) -> ToolCall:
    """造一条待判定的调用。

    举例：_call("call_2", "read", {"path": "a.py"})
    """
    return ToolCall(
        id=call_id,
        name=name,
        arguments=arguments if arguments is not None else {"path": "a.py"},
        status=ToolCallStatus.PENDING,
    )


def _scene(
    calls: list[ToolCall],
    *,
    tools=None,
    approvals=None,
    permission: Permission = Permission.EDITABLE,
    workspace: Path,
    logger=None,
    events=None,
    screened: bool = True,
):
    """造一个位点 6 的执行现场，把 payload 一起交回来。

    `screened=True` 表示**已经过完检查链**（真实链路上 tool_calling 在 100 做这件事）——
    沙箱只负责把放行的改小，所以它的测试默认从这个状态开始。

    举例：ctx, payload = _scene([_call()], tools=registry, workspace=scratch_dir)
    """
    ports = make_ports(
        tools=tools,
        approvals=approvals,
        logger=logger or RecordingLogger(),
        events=events or InProcessEventBus(),
    )
    trace = make_trace_context(
        config=make_frozen_config(permission=permission),
        session=StubSession(workspace=str(workspace)),
        ports=ports,
    )
    payload = ToolBeforePayload(calls=list(calls))
    if screened:
        for call in payload.calls:
            payload.decisions[call.id] = ToolDecision.EXECUTE
    return make_site_context(Site.TOOL_BEFORE, payload, trace=trace), payload, ports


def _logged(ports) -> list[str]:
    """日志里的事件名，按顺序。

    举例：_logged(ports) -> ["sandbox.no_approver"]
    """
    return [event for _, event, _ in ports.logger.records]


async def test_without_a_tool_registry_nothing_is_judged(scratch_dir: Path) -> None:
    """没有工具来源时一条都不判 —— 检查链的判定原样留着，边界不替它发言。"""
    ctx, payload, _ = _scene([_call()], workspace=scratch_dir)

    await enforce(ctx)

    assert payload.decisions == {"call_1": ToolDecision.EXECUTE}


async def test_an_unknown_tool_is_left_to_tool_calling(scratch_dir: Path) -> None:
    """查不到声明的工具不判 —— 存在性归 tool_calling，沙箱不替它下结论。"""
    registry = FakeToolRegistry([make_tool_declaration("read")])
    ctx, payload, _ = _scene([_call(name="nobody")], tools=registry, workspace=scratch_dir)

    await enforce(ctx)

    assert payload.decisions == {"call_1": ToolDecision.EXECUTE}


async def test_a_refusal_lands_in_decisions(scratch_dir: Path) -> None:
    """档位不够 → 写拒绝。内核与 runtime_state 都只认 EXECUTE，边界就生效了。"""
    ctx, payload, _ = _scene(
        [_call()],
        tools=FakeToolRegistry([_write()]),
        permission=Permission.READ_ONLY,
        workspace=scratch_dir,
    )

    await enforce(ctx)

    assert payload.decisions == {"call_1": ToolDecision.REJECT}


async def test_an_allowed_call_lands_in_decisions(scratch_dir: Path) -> None:
    """界内的写，editable 放行。"""
    ctx, payload, _ = _scene([_call()], tools=FakeToolRegistry([_write()]), workspace=scratch_dir)

    await enforce(ctx)

    assert payload.decisions == {"call_1": ToolDecision.EXECUTE}


async def test_a_call_already_refused_is_not_judged_again(scratch_dir: Path) -> None:
    """检查链已经拒掉的调用不再判 —— 更不该把它的理由覆盖掉。"""
    ctx, payload, _ = _scene([_call()], tools=FakeToolRegistry([_write()]), workspace=scratch_dir)
    payload.decisions["call_1"] = ToolDecision.REJECT
    payload.reasons["call_1"] = "检查链说的话"

    await enforce(ctx)

    assert payload.decisions == {"call_1": ToolDecision.REJECT}
    assert payload.reasons == {"call_1": "检查链说的话"}


async def test_without_an_approver_the_ask_becomes_a_refusal(scratch_dir: Path) -> None:
    """没有应答方时 fail closed —— 静默放行是最坏的收场。"""
    ctx, payload, ports = _scene(
        [_call(arguments={"path": "../outside.txt"})],
        tools=FakeToolRegistry([_write()]),
        workspace=scratch_dir,
    )

    await enforce(ctx)

    assert payload.decisions == {"call_1": ToolDecision.REJECT}
    assert "sandbox.no_approver" in _logged(ports)


async def test_an_approver_can_open_the_gate(scratch_dir: Path) -> None:
    """有人放行，越界的写就执行 —— 拒绝与放行之间还有这一档。"""
    approvals = FakeApprovals(ApprovalDecision.APPROVE)
    ctx, payload, _ = _scene(
        [_call(arguments={"path": "../outside.txt"})],
        tools=FakeToolRegistry([_write()]),
        approvals=approvals,
        workspace=scratch_dir,
    )

    await enforce(ctx)

    assert payload.decisions == {"call_1": ToolDecision.EXECUTE}
    assert [r.tool_name for r in approvals.requests] == ["write"]
    assert approvals.requests[0].reason


async def test_an_approver_can_keep_it_shut(scratch_dir: Path) -> None:
    """人工拒绝就落成拒绝。"""
    approvals = FakeApprovals(ApprovalDecision.DENY)
    ctx, payload, _ = _scene(
        [_call(arguments={"path": "../outside.txt"})],
        tools=FakeToolRegistry([_write()]),
        approvals=approvals,
        workspace=scratch_dir,
    )

    await enforce(ctx)

    assert payload.decisions == {"call_1": ToolDecision.REJECT}


async def test_asking_publishes_the_event(scratch_dir: Path) -> None:
    """要人审批时得广播出去 —— 不广播，界面上就没人知道该点哪里。"""
    seen: list[object] = []
    bus = InProcessEventBus()
    bus.subscribe(ApprovalRequired, seen.append)
    ctx, _, _ = _scene(
        [_call(arguments={"path": "../outside.txt"})],
        tools=FakeToolRegistry([_write()]),
        approvals=FakeApprovals(ApprovalDecision.APPROVE),
        workspace=scratch_dir,
        events=bus,
    )

    await enforce(ctx)

    assert [type(event).__name__ for event in seen] == ["ApprovalRequired"]


async def test_a_refusal_carries_its_reason(scratch_dir: Path) -> None:
    """判定要带上理由 —— tool_calling 在收尾时拿它告诉模型为什么没成。"""
    ctx, payload, _ = _scene(
        [_call()],
        tools=FakeToolRegistry([_write()]),
        permission=Permission.READ_ONLY,
        workspace=scratch_dir,
    )

    await enforce(ctx)

    assert payload.decisions == {"call_1": ToolDecision.REJECT}
    assert "read_only" in payload.reasons["call_1"]


async def test_an_out_of_workspace_path_carries_its_reason(scratch_dir: Path) -> None:
    """越界的理由要把路径写出来，模型才知道该改哪儿。"""
    ctx, payload, _ = _scene(
        [_call(arguments={"path": "../outside.txt"})],
        tools=FakeToolRegistry([_write()]),
        approvals=FakeApprovals(ApprovalDecision.DENY),
        workspace=scratch_dir,
    )

    await enforce(ctx)

    assert payload.decisions == {"call_1": ToolDecision.REJECT}
    assert "outside.txt" in payload.reasons["call_1"]

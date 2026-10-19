"""端到端：真模型脚本 + 真工具 + 真容器，验两条不变量。

1. 模型发出的每一条 tool_call 都会收到一条 tool 消息 —— 成功的正文，或者失败的原因；
   **包括被沙箱拒绝的那些**。
2. 同一组里一条工具挂了，不影响其他工具。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fakes import USER, ScriptedLlm, make_app_config, text_turn, tool_turn, user_message
from yzy_agent.composition import Container, build_container
from yzy_agent.contracts import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalResult,
    MessageRole,
    Permission,
    SessionEntryType,
    TraceStatus,
)


def _container(
    scratch_dir: Path, llm: ScriptedLlm, permission: Permission = Permission.EDITABLE
) -> Container:
    """把工作目录与会话目录都指到临时目录上 —— 工具读的是真的文件。

    举例：container = _container(scratch_dir, llm, Permission.FULL_ACCESS)
    """
    config = make_app_config(
        workspace=str(scratch_dir),
        session_dir=str(scratch_dir / "sessions"),
        permission=permission,
    )
    return build_container(config, llm=llm)


async def _run(scratch_dir: Path, llm: ScriptedLlm):
    """跑一次 trace，把会话交回来。

    举例：live, outcome = await _run(scratch_dir, llm)
    """
    container = _container(scratch_dir, llm)
    live = await container.sessions.create(user_id=USER)
    outcome = await container.agent.run(session=live.session, user_message=user_message("来吧"))
    return live, outcome


def _tool_results(live) -> dict[str, str]:
    """日志里所有 tool 消息，按 tool_call_id 索引。

    举例：_tool_results(live) -> {"call_1": "1\thello"}
    """
    results: dict[str, str] = {}
    for entry in live.session.entries():
        message = entry.payload
        if entry.type is not SessionEntryType.MESSAGE or message.role is not MessageRole.TOOL:
            continue
        results[str(message.metadata["tool_call_id"])] = message.content[0].text  # type: ignore[union-attr]
    return results


# ---------------------------------------------------------------------------
# 一、每条调用都有结果
# ---------------------------------------------------------------------------


async def test_a_successful_call_lands_in_the_session(scratch_dir: Path) -> None:
    """工具真的读了文件，正文进了会话 —— 取的是工作目录里的真文件。"""
    (scratch_dir / "a.py").write_text("hello world", encoding="utf-8")
    llm = ScriptedLlm([tool_turn(("call_1", "read", {"path": "a.py"})), text_turn("看完了")])

    live, outcome = await _run(scratch_dir, llm)

    assert outcome.status is TraceStatus.COMPLETED
    assert "hello world" in _tool_results(live)["call_1"]


async def test_a_refusal_by_the_sandbox_still_produces_a_result(scratch_dir: Path) -> None:
    """**沙箱拒绝也要给模型一条结果**，而且要说清为什么。

    要审批的那类调用不算在内 —— 它们会停下来等人，由下面那条用例单独管。
    这里挑的是**边界类硬拒绝**：read_only 下写文件。
    """
    llm = ScriptedLlm(
        [tool_turn(("call_1", "write", {"path": "a.py", "content": "x"})), text_turn("那算了")]
    )
    container = _container(scratch_dir, llm, Permission.READ_ONLY)
    live = await container.sessions.create(user_id=USER)

    outcome = await container.agent.run(session=live.session, user_message=user_message("写一个"))

    assert outcome.status is TraceStatus.COMPLETED
    assert "read_only" in _tool_results(live)["call_1"]


async def test_a_tool_that_fails_still_produces_a_result(scratch_dir: Path) -> None:
    """文件不存在也是结果 —— 模型据此换个路径，而不是整次 trace 挂掉。"""
    llm = ScriptedLlm([tool_turn(("call_1", "read", {"path": "nope.py"})), text_turn("换个路径")])

    live, outcome = await _run(scratch_dir, llm)

    assert outcome.status is TraceStatus.COMPLETED
    assert "文件不存在" in _tool_results(live)["call_1"]


async def test_an_unknown_tool_still_produces_a_result(scratch_dir: Path) -> None:
    """工具不存在也是结果 —— 存在性检查在位点 6 就把它收成一句话。"""
    llm = ScriptedLlm([tool_turn(("call_1", "nobody", {})), text_turn("好吧")])

    live, _ = await _run(scratch_dir, llm)

    assert "没有叫 nobody 的工具" in _tool_results(live)["call_1"]


async def test_bad_arguments_still_produce_a_result(scratch_dir: Path) -> None:
    """参数不合法也是结果，而且要说清缺了什么 —— 笼统的「被拒绝」只会让模型重犯。"""
    llm = ScriptedLlm([tool_turn(("call_1", "read", {})), text_turn("补上路径")])

    live, _ = await _run(scratch_dir, llm)

    assert "缺少必填参数：path" in _tool_results(live)["call_1"]


# ---------------------------------------------------------------------------
# 二、并发隔离
# ---------------------------------------------------------------------------


async def test_one_failing_tool_does_not_affect_the_other(scratch_dir: Path) -> None:
    """同一组里一条失败，另一条照常拿到结果。

    两条都是 read（只读）→ 编排把它们放进同一组 → 并发跑。一条读得到、一条读不到，
    两条都必须有自己的结果。
    """
    (scratch_dir / "ok.py").write_text("好的", encoding="utf-8")
    llm = ScriptedLlm(
        [
            tool_turn(("call_1", "read", {"path": "ok.py"}), ("call_2", "read", {"path": "no.py"})),
            text_turn("看完了"),
        ]
    )

    live, outcome = await _run(scratch_dir, llm)

    assert outcome.status is TraceStatus.COMPLETED
    results = _tool_results(live)
    assert "好的" in results["call_1"]
    assert "文件不存在" in results["call_2"]


async def test_read_only_calls_share_one_group(scratch_dir: Path) -> None:
    """两条只读调用确实被分到同一组 —— 分组不是摆设，内核照它并发跑。"""
    (scratch_dir / "a.py").write_text("A", encoding="utf-8")
    (scratch_dir / "b.py").write_text("B", encoding="utf-8")
    llm = ScriptedLlm(
        [
            tool_turn(("call_1", "read", {"path": "a.py"}), ("call_2", "read", {"path": "b.py"})),
            text_turn("都看完了"),
        ]
    )
    container = _container(scratch_dir, llm)
    seen: list[list[str]] = []

    async def note(ctx) -> None:
        if ctx.site.value == "tool:before":
            seen.append([list(group) for group in ctx.payload.plan.groups])

    container.hooks.on("agent/tool_before", note, name="note")
    live = await container.sessions.create(user_id=USER)

    await container.agent.run(session=live.session, user_message=user_message("读两个文件"))

    assert seen == [[["call_1", "call_2"]]]  # 一次位点 6、一个组、两条调用
    assert set(_tool_results(live)) == {"call_1", "call_2"}


# ---------------------------------------------------------------------------
# 三、会改内容的工具与命令档位
# ---------------------------------------------------------------------------


async def test_a_write_call_lands_in_the_session(scratch_dir: Path) -> None:
    """write 走的是同一条链：检查链放行 → 沙箱判（editable 够用）→ 真的写下去。"""
    llm = ScriptedLlm(
        [
            tool_turn(("call_1", "write", {"path": "new.py", "content": "x = 1"})),
            text_turn("写好了"),
        ]
    )

    live, outcome = await _run(scratch_dir, llm)

    assert outcome.status is TraceStatus.COMPLETED
    assert "已新建" in _tool_results(live)["call_1"]
    assert (scratch_dir / "new.py").read_text(encoding="utf-8") == "x = 1"


async def _wait_pending(container: Container, session_id: str) -> ApprovalRequest:
    """等沙箱挂出一条待审批 —— trace 在后台跑，只能轮询那张表。

    举例：request = await _wait_pending(container, live.session_id)
    """
    for _ in range(500):
        pending = await container.approvals.pending(session_id)
        if pending:
            return pending[0]
        await asyncio.sleep(0)
    raise AssertionError("一直没有待审批出现")


async def test_a_command_waits_for_a_human(scratch_dir: Path) -> None:
    """editable 下命令挂在审批上：人没表态，工具一步都不动。"""
    llm = ScriptedLlm(
        [tool_turn(("call_1", "run_command", {"command": "echo hi"})), text_turn("那算了")]
    )
    container = _container(scratch_dir, llm)
    live = await container.sessions.create(user_id=USER)

    running = asyncio.ensure_future(
        container.agent.run(session=live.session, user_message=user_message("跑一条"))
    )
    request = await _wait_pending(container, live.session_id)

    assert request.tool_name == "run_command"
    assert not running.done()  # 人没表态，trace 停在这儿

    await container.approvals.respond(
        live.session_id,
        ApprovalResult(
            request_id=request.request_id, decision=ApprovalDecision.DENY, comment="别跑"
        ),
    )
    await running

    result = _tool_results(live)["call_1"]
    assert "用户拒绝了这次调用" in result
    assert "别跑" in result


async def test_a_command_runs_at_full_access(scratch_dir: Path) -> None:
    """同一条命令在 full_access 下真的跑起来了 —— 档位就是这一档的开关。"""
    llm = ScriptedLlm(
        [
            tool_turn(("call_1", "run_command", {"command": "echo hi-from-agent"})),
            text_turn("跑完了"),
        ]
    )
    container = _container(scratch_dir, llm, Permission.FULL_ACCESS)
    live = await container.sessions.create(user_id=USER)

    await container.agent.run(session=live.session, user_message=user_message("跑一条"))

    assert "hi-from-agent" in _tool_results(live)["call_1"]

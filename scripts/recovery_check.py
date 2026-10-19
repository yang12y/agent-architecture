r"""中断与恢复的自检：五种尾部，一条一条造出来，看恢复怎么修。

它跑的是**真实装配**（build_container）：真位点管线、真 session、真落盘，
只有模型换成了按剧本回话的假客户端 —— 不联网、不花 token、不等审批。

要先记住一条：**恢复不在重启时做，而是在这个会话的下一条消息走到位点 1 时做。**
所以下面每个场景都断言两件事：冷装载前后日志文件的字节一模一样（重启不改日志），
补出来的条目只会出现在下一条消息之后。

运行：
    .venv\Scripts\python.exe scripts\recovery_check.py
报告同时写一份到 .tmp/recovery-report.txt。
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from agent_architecture.composition import build_container
from agent_architecture.contracts import (
    AppConfig,
    LlmChunk,
    LlmChunkKind,
    LlmRequest,
    Message,
    MessageRole,
    Permission,
    SessionEntry,
    SessionEntryType,
    StopReason,
    TextPart,
    ToolCallPart,
    TraceStatus,
    Usage,
)
from agent_architecture.infra.config import defaults
from agent_architecture.modules.checkpoint.crash import TailKind, classify_tail
from agent_architecture.modules.checkpoint.rebalance import dangling_calls

# 仓库根。脚本从任何目录启动都能用。
#
# 举例：Path("D:/CodeProjects/agent_architecture")
ROOT = Path(__file__).resolve().parents[1]

# 全部临时盘面都落在这里，跑完可以直接打开看。
#
# 举例：D:/CodeProjects/agent_architecture/.tmp/recovery-check
WORK = ROOT / ".tmp" / "recovery-check"

# 报告副本。中文经过 PowerShell 会乱码，所以正文另存一份 UTF-8。
REPORT = ROOT / ".tmp" / "recovery-report.txt"

# 自检用的 user_id：会话归属就靠它。
USER = "checker"

# 工具调用 id：一个场景只有一条调用，固定值够用。
CALL_ID = "call_check_1"


# --------------------------------------------------------------------------
# 剧本：模型这一轮说什么
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Say:
    """模型回一段话，正常收尾。"""

    # 正文。
    #
    # 举例："我看到了，继续。"
    text: str


@dataclass(frozen=True)
class Call:
    """模型请求一次工具调用。

    abort_after 让它在吐完 DONE 之后立刻打上取消信号 —— 这在时间上正是
    "模型已经把调用写进日志，工具还没执行"的那一刻，用户中断就发生在这一瞬。
    """

    # 工具名。
    #
    # 举例："write"
    name: str

    # 原始参数。
    #
    # 举例：{"path": "a.txt", "content": "x"}
    arguments: dict[str, object]

    # 吐完就中断。
    #
    # 举例：True
    abort_after: bool = False


@dataclass(frozen=True)
class Hang:
    """模型不回话：这次 trace 就停在位点 4，"断电"发生在这里。"""


class ScriptedLlm:
    """按剧本回话的假客户端。一次 stream 取一个剧本。"""

    def __init__(self, *replies: Say | Call | Hang, signal: object | None = None) -> None:
        # 还没播的剧本。
        self._replies: list[Say | Call | Hang] = list(replies)

        # 取消信号：Call(abort_after=True) 用它模拟用户点中断。
        self._signal = signal

        # 被调用了几次。用来确认恢复没有偷偷多调一次模型。
        self.calls = 0

    def stream(self, request: LlmRequest) -> AsyncIterator[LlmChunk]:
        """取一个剧本演出来。"""
        self.calls += 1
        return self._play(self._replies.pop(0))

    async def _play(self, reply: Say | Call | Hang) -> AsyncIterator[LlmChunk]:
        # 永不返回：等价于进程在这个 await 上没了。谁也不会来收尾，位点 10 不跑。
        if isinstance(reply, Hang):
            await asyncio.Event().wait()
            return

        if isinstance(reply, Call):
            yield LlmChunk(
                kind=LlmChunkKind.TOOL_CALL,
                tool_call=ToolCallPart(
                    tool_call_id=CALL_ID,
                    name=reply.name,
                    arguments=reply.arguments,  # type: ignore[arg-type]
                ),
            )
            yield LlmChunk(kind=LlmChunkKind.DONE, stop_reason=StopReason.TOOL_CALLS)
            if reply.abort_after and self._signal is not None:
                self._signal.set()  # type: ignore[attr-defined]
            return

        yield LlmChunk(kind=LlmChunkKind.TEXT_DELTA, text=reply.text)
        yield LlmChunk(kind=LlmChunkKind.USAGE, usage=Usage(input_tokens=120, output_tokens=12))
        yield LlmChunk(kind=LlmChunkKind.DONE, stop_reason=StopReason.END_TURN)


# --------------------------------------------------------------------------
# 装配：一份与用户 .env 无关的配置
# --------------------------------------------------------------------------


def make_config(root: Path, *, permission: Permission) -> AppConfig:
    """造一份只依赖 root 的配置：工作目录与会话目录都在临时盘面里。

    显式构造而不读环境，是为了让脚本不受用户 .env 影响 —— 自检要的是确定性。
    """
    (root / "workspace").mkdir(parents=True, exist_ok=True)
    return AppConfig(
        workspace=str(root / "workspace"),
        session_dir=str(root / "sessions"),
        model="fake-model",
        summary_model=None,
        llm_api_key=None,
        llm_base_url=None,
        llm_timeout_seconds=defaults.DEFAULT_LLM_TIMEOUT_SECONDS,
        # 不重试：重试会让"一次调用"变成两次，尾部就不好判了。
        llm_max_attempts=1,
        thinking=False,
        permission=permission,
        max_tokens=200_000,
        max_turns=4,
        compact_ratio=defaults.DEFAULT_COMPACT_RATIO,
        keep_recent=defaults.DEFAULT_KEEP_RECENT,
        tool_result_max_chars=defaults.DEFAULT_TOOL_RESULT_MAX_CHARS,
        long_tool_result_max_tokens=defaults.DEFAULT_LONG_TOOL_RESULT_MAX_TOKENS,
        long_tool_result_keep_recent=defaults.DEFAULT_LONG_TOOL_RESULT_KEEP_RECENT,
        summary_prompt_template=defaults.DEFAULT_SUMMARY_PROMPT,
        # 自检只看断言，不看日志；ERROR 让崩溃场景的 warning 别糊满屏幕。
        log_level="ERROR",
    )


def user_message(text: str) -> Message:
    """一条用户消息。"""
    return Message(role=MessageRole.USER, content=[TextPart(text=text)])


def entries_path(root: Path, session_id: str) -> Path:
    """这个会话的耐久日志在哪。"""
    return root / "sessions" / session_id / "entries.jsonl"


async def wait_entries(root: Path, session_id: str, count: int, *, timeout: float = 20.0) -> None:
    """等到盘上至少有 count 条条目 —— 也就是某个屏障已经跑过了。"""
    path = entries_path(root, session_id)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if path.is_file() and path.read_text(encoding="utf-8").count("\n") >= count:
            return
        await asyncio.sleep(0.02)
    raise TimeoutError(f"等不到 {count} 条已落盘条目：{path}")


# --------------------------------------------------------------------------
# 断言收集
# --------------------------------------------------------------------------


class Report:
    """一行行记结论，最后一起打印。"""

    def __init__(self) -> None:
        # 已经攒下的行：(缩进, 文本, 是否通过)。不是断言的注释行 ok 记 None。
        self._lines: list[tuple[int, str, bool | None]] = []
        self.failed = 0

    def section(self, title: str) -> None:
        """开一个场景。"""
        self._lines.append((0, title, None))

    def check(self, ok: bool, text: str, detail: str = "") -> None:
        """记一条断言。detail 只在失败时打出来。"""
        self._lines.append((2, f"{text}" + (f"　[{detail}]" if detail and not ok else ""), ok))
        if not ok:
            self.failed += 1

    def info(self, text: str) -> None:
        """记一行事实，不算断言。"""
        self._lines.append((2, text, None))

    def render(self) -> str:
        """整份报告。"""
        out: list[str] = []
        for indent, text, ok in self._lines:
            mark = "  " if ok is None else ("✓ " if ok else "✗ ")
            out.append(" " * indent + mark + text)
        total = sum(1 for _, _, ok in self._lines if ok is not None)
        out.append("")
        out.append(f"{total} 条断言，{self.failed} 条失败")
        return "\n".join(out)


# --------------------------------------------------------------------------
# 造尾部
# --------------------------------------------------------------------------


async def crash(
    root: Path,
    *,
    permission: Permission,
    reply: Say | Call | Hang,
    durable: int,
    text: str = "把这件事做一下",
) -> tuple[Path, str]:
    """跑一次会在中途"断电"的 trace，返回断电那一刻的盘面副本与会话 id。

    做法：真的把 trace 跑起来，等它走到某个落盘屏障之后**把会话目录整份拷走**，
    拷贝就是"此刻进程被杀"的盘面。然后取消那次 trace 收拾干净。

    为什么拷一份而不是继续用原目录：取消会让内核补上 trace_end(aborted)，原目录紧接着
    就不再是崩溃现场了。恢复只读盘，所以盘面副本对恢复来说与真被杀没有区别。
    """
    root.mkdir(parents=True, exist_ok=True)
    config = make_config(root, permission=permission)
    container = build_container(config, llm=ScriptedLlm(reply))
    live = await container.sessions.create(user_id=USER)
    session_id = live.session_id

    task = asyncio.create_task(
        container.agent.run(session=live.session, user_message=user_message(text))
    )
    await wait_entries(root, session_id, durable)

    crashed = root.parent / f"{root.name}-crashed"
    if crashed.exists():
        shutil.rmtree(crashed)
    shutil.copytree(root, crashed)

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task
    return crashed, session_id


async def interrupted(
    root: Path, *, permission: Permission, reply: Call, text: str = "把这件事做一下"
) -> tuple[Path, str, TraceStatus | None]:
    """跑一次被用户中断的 trace：位点 10 照常收尾，工具调用留成悬空。"""
    root.mkdir(parents=True, exist_ok=True)
    token = asyncio.Event()  # 只要 set/is_set，CancelToken 就是这个形状
    config = make_config(root, permission=permission)
    container = build_container(config, llm=ScriptedLlm(reply, signal=token))
    live = await container.sessions.create(user_id=USER)

    outcome = await container.agent.run(
        session=live.session,
        user_message=user_message(text),
        signal=token,  # type: ignore[arg-type]
    )
    return root, live.session_id, outcome.status


# --------------------------------------------------------------------------
# 恢复：冷装载 + 下一条消息
# --------------------------------------------------------------------------


def tool_results(entries: list[SessionEntry], *, trace_id: str) -> list[Message]:
    """这次 trace 名下已经有的工具结果消息。"""
    found: list[Message] = []
    for entry in entries:
        if entry.trace_id != trace_id or entry.type is not SessionEntryType.MESSAGE:
            continue
        payload = entry.payload
        if isinstance(payload, Message) and payload.role is MessageRole.TOOL:
            found.append(payload)
    return found


def text_of(message: Message) -> str:
    """把一条消息的正文拼起来。"""
    return "".join(part.text for part in message.content if isinstance(part, TextPart))


def trace_ends(entries: list[SessionEntry], *, trace_id: str) -> list[SessionEntry]:
    """这次 trace 名下的 trace_end。"""
    return [
        entry
        for entry in entries
        if entry.trace_id == trace_id and entry.type is SessionEntryType.TRACE_END
    ]


@dataclass(frozen=True)
class Expect:
    """一个场景在恢复之前的盘面该长什么样。"""

    # 尾部种类。
    kind: TailKind

    # 悬空的工具调用几条。
    calls: int

    # 这些调用里，有没有已经派发出去的。
    dispatched: bool | None

    # 恢复该补几条（不含恢复之外的新 trace 条目）。
    repairs: int

    # 补出来的工具结果正文里必须出现的话。
    says: tuple[str, ...] = ()


async def recover(
    root: Path, session_id: str, expect: Expect, report: Report
) -> list[SessionEntry]:
    """冷装载再发一条消息，把恢复触发出来，并逐条断言。"""
    path = entries_path(root, session_id)

    # 重启：全新容器、全新注册表，只从盘上读。
    before_bytes = path.read_bytes() if path.is_file() else b""
    config = make_config(root, permission=Permission.EDITABLE)
    container = build_container(config, llm=ScriptedLlm(Say("我看到了，继续。")))
    live = await container.sessions.get_or_load(session_id, USER)
    if live is None:
        report.check(False, "冷装载拿不到会话")
        return []
    after_bytes = path.read_bytes()

    entries_before = list(live.session.entries())
    tail = classify_tail(entries_before)
    calls = dangling_calls(entries_before, trace_id=tail.trace_id or "")

    report.check(after_bytes == before_bytes, "冷装载不改日志：重启这一步什么都没修")
    report.check(tail.kind is expect.kind, f"尾部 = {expect.kind.value}", tail.kind.value)
    report.check(
        tail.needs_repair is (expect.kind is not TailKind.CLOSED),
        f"needs_repair = {expect.kind is not TailKind.CLOSED}",
    )
    report.check(len(calls) == expect.calls, f"悬空工具调用 {expect.calls} 条", str(len(calls)))
    if expect.dispatched is not None and calls:
        report.check(
            calls[0].dispatched is expect.dispatched,
            f"悬空调用已派发 = {expect.dispatched}",
            str(calls[0].dispatched),
        )
    info = "、".join(f"{c.name}({c.tool_call_id})" for c in calls) or "无"
    report.info(f"出事的那次 trace：{tail.trace_id}；悬空调用：{info}")

    # 下一条消息：位点 1 的恢复在这一刻跑。
    outcome = await container.agent.run(session=live.session, user_message=user_message("继续"))
    entries_after = list(live.session.entries())
    repaired = entries_after[len(entries_before) :]

    report.check(outcome.status is TraceStatus.COMPLETED, "下一条消息自己能正常跑完")

    # 只有算在出事那次 trace 名下的条目才是"恢复补的"；其余是新 trace 自己的。
    old = [e for e in repaired if e.trace_id == tail.trace_id]
    report.check(len(old) == expect.repairs, f"恢复了 {expect.repairs} 条", str(len(old)))
    results = tool_results(old, trace_id=tail.trace_id or "")
    ends = trace_ends(old, trace_id=tail.trace_id or "")

    if expect.calls:
        report.check(len(results) == expect.calls, "每条悬空调用都补了结果", str(len(results)))
        body = text_of(results[0]) if results else ""
        report.info(f"补出来的那句话：{body}")
        for fragment in expect.says:
            report.check(fragment in body, f"正文里有「{fragment}」")
    else:
        report.check(not results, "没有悬空调用，就不补结果")

    if expect.kind is TailKind.DANGLING:
        report.check(len(ends) == 1, "补了一条 trace_end(aborted)", str(len(ends)))
        if ends:
            payload = ends[0].payload
            reason = getattr(payload, "reason", None)
            status = getattr(payload, "status", None)
            report.check(status is TraceStatus.ABORTED, "它是 aborted", str(status))
            report.check(reason == "进程中断", "原因是「进程中断」", str(reason))
            result_entries = [
                entry
                for entry in old
                if entry.type is SessionEntryType.MESSAGE
                and isinstance(entry.payload, Message)
                and entry.payload.role is MessageRole.TOOL
            ]
            if result_entries:
                report.check(
                    old.index(result_entries[-1]) < old.index(ends[0]),
                    "结果排在收尾之前（否则下一次投影会把它们挡在外面）",
                )
    else:
        report.check(not ends, "尾部已经是 aborted，不再补第二条 trace_end")

    # 盘上是不是也这样：再冷装载一次。
    fresh = build_container(
        make_config(root, permission=Permission.EDITABLE), llm=ScriptedLlm(Say("不该被调用"))
    )
    again = await fresh.sessions.get_or_load(session_id, USER)
    disk = list(again.session.entries()) if again is not None else []
    report.check(
        len(disk) == len(entries_after),
        "补出来的条目已经在盘上",
        f"{len(disk)} vs {len(entries_after)}",
    )
    report.check(classify_tail(disk).kind is TailKind.CLOSED, "盘上的尾部 = closed")
    return entries_after


# --------------------------------------------------------------------------
# 五个场景
# --------------------------------------------------------------------------


async def case_model_stream(report: Report) -> None:
    """① 崩在等模型回话时：日志里一个悬空调用都没有。"""
    report.section("① 崩在等模型（DANGLING，0 条悬空调用）")
    root = WORK / "01-model-stream"
    crashed, session_id = await crash(root, permission=Permission.EDITABLE, reply=Hang(), durable=2)
    report.info(f"断电盘面：{crashed}")
    await recover(
        crashed,
        session_id,
        Expect(kind=TailKind.DANGLING, calls=0, dispatched=None, repairs=1),
        report,
    )


async def case_approval(report: Report) -> None:
    """② 崩在审批框开着的时候：调用还没派发，安全可重试。"""
    report.section("② 崩在审批里（DANGLING，1 条未派发）")
    root = WORK / "02-approval"
    call = Call(name="run_command", arguments={"command": "echo hi"})
    crashed, session_id = await crash(root, permission=Permission.READ_ONLY, reply=call, durable=3)
    report.info(f"断电盘面：{crashed}")
    await recover(
        crashed,
        session_id,
        Expect(
            kind=TailKind.DANGLING,
            calls=1,
            dispatched=False,
            repairs=2,
            says=("在开始执行前就被中断了", "安全重试"),
        ),
        report,
    )


async def case_interrupt_idempotent(report: Report) -> None:
    """③ 用户中断，调用已派发且幂等：恢复认它可重试。"""
    report.section("③ 用户中断（ABORTED，1 条已派发、幂等）")
    root = WORK / "03-interrupt-idempotent"
    call = Call(name="write", arguments={"path": "a.txt", "content": "x"}, abort_after=True)
    _, session_id, status = await interrupted(root, permission=Permission.EDITABLE, reply=call)
    report.check(status is TraceStatus.ABORTED, "那次 trace 收尾为 aborted", str(status))
    report.info(f"盘面：{root}")
    await recover(
        root,
        session_id,
        Expect(
            kind=TailKind.ABORTED,
            calls=1,
            dispatched=True,
            repairs=1,
            says=("结果未知", "这个操作是幂等的，可以安全重试"),
        ),
        report,
    )


async def case_interrupt_unsafe(report: Report) -> None:
    """④ 用户中断，调用已派发且不可重试：恢复只报警不重放。"""
    report.section("④ 用户中断（ABORTED，1 条已派发、不可重试）")
    root = WORK / "04-interrupt-unsafe"
    call = Call(name="run_command", arguments={"command": "echo hi"}, abort_after=True)
    _, session_id, status = await interrupted(root, permission=Permission.FULL_ACCESS, reply=call)
    report.check(status is TraceStatus.ABORTED, "那次 trace 收尾为 aborted", str(status))
    report.info(f"盘面：{root}")
    await recover(
        root,
        session_id,
        Expect(
            kind=TailKind.ABORTED,
            calls=1,
            dispatched=True,
            repairs=1,
            says=("结果未知", "不能安全重试", "先确认外部状态"),
        ),
        report,
    )


async def case_clean(report: Report) -> None:
    """⑤ 正常收尾：恢复一条也不该补。"""
    report.section("⑤ 正常收尾（CLOSED，什么都不用修）")
    root = WORK / "05-clean"
    root.mkdir(parents=True, exist_ok=True)
    config = make_config(root, permission=Permission.EDITABLE)
    container = build_container(config, llm=ScriptedLlm(Say("第一句。"), Say("第二句。")))
    live = await container.sessions.create(user_id=USER)
    first = await container.agent.run(session=live.session, user_message=user_message("你好"))
    report.check(first.status is TraceStatus.COMPLETED, "第一次正常收尾", str(first.status))

    before = len(live.session.entries())
    second = await container.agent.run(session=live.session, user_message=user_message("再来"))
    after = live.session.entries()
    report.check(second.status is TraceStatus.COMPLETED, "第二次也正常收尾", str(second.status))
    report.check(classify_tail(list(after)).kind is TailKind.CLOSED, "尾部 = closed")

    # 第二次的条目全是它自己的：没有一条算在旧 trace 名下。
    old_trace = after[0].trace_id
    stale = [
        entry
        for entry in after[before:]
        if entry.trace_id == old_trace
        and entry.type in (SessionEntryType.TRACE_END, SessionEntryType.MESSAGE)
        and entry.payload.__class__ is Message
        and getattr(entry.payload, "role", None) is MessageRole.TOOL
    ]
    report.check(not stale, "第二次没有替第一次补任何东西", str(len(stale)))
    report.info(f"盘面：{root}")


async def run_all() -> int:
    """清场、依次跑五个场景、出报告。"""
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True, exist_ok=True)

    report = Report()
    await case_model_stream(report)
    await case_approval(report)
    await case_interrupt_idempotent(report)
    await case_interrupt_unsafe(report)
    await case_clean(report)

    text = report.render()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(text, encoding="utf-8")
    print(text)
    print(f"\n盘面留在：{WORK}")
    print(f"报告副本：{REPORT}")
    return 1 if report.failed else 0


def main() -> int:
    """入口。"""
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    return asyncio.run(run_all())


if __name__ == "__main__":
    sys.exit(main())

"""内置工具的测试。

每个工具各管几件事：

    - **静态事实**：权限档位、副作用等级、参数名（沙箱按参数名路由）
    - **正常路径**：真的读/写/搜了工作目录里的真文件
    - **失败路径**：抛出去的是给人看的一句话，不是异常栈

工具是**直接调**的，不经容器 —— 检查链、沙箱、落盘各自有测试，这里只验工具自己。
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

from agent_architecture.contracts import (
    CancelToken,
    Permission,
    SideEffect,
    ToolArgumentError,
    ToolDeclaration,
)
from agent_architecture.infra.observability import RecordingLogger
from agent_architecture.modules.tool_calling.builtin import (
    RUN_COMMAND,
    build_catalog,
    edit,
    glob,
    grep,
    list_dir,
    read,
    run_command,
    write,
)
from agent_architecture.modules.tool_calling.registry import ToolContext

# 每个工具该有的静态事实：最低权限档位 + 副作用等级。
#
# 这两个字段写错了不会报错，只会判错 —— 权限写低了是安全洞，副作用写低了会重试不该重试的调用。
#
# 举例：_FACTS["write"] == (Permission.EDITABLE, SideEffect.IDEMPOTENT)
_FACTS: dict[str, tuple[Permission, SideEffect]] = {
    "read": (Permission.READ_ONLY, SideEffect.NONE),
    "list_dir": (Permission.READ_ONLY, SideEffect.NONE),
    "glob": (Permission.READ_ONLY, SideEffect.NONE),
    "grep": (Permission.READ_ONLY, SideEffect.NONE),
    "write": (Permission.EDITABLE, SideEffect.IDEMPOTENT),
    "edit": (Permission.EDITABLE, SideEffect.IDEMPOTENT),
    "run_command": (Permission.FULL_ACCESS, SideEffect.NON_IDEMPOTENT),
}

# 碰文件的工具；它们的路径参数必须叫 path。
#
# 举例："read" in _FILE_TOOLS
_FILE_TOOLS = ("read", "write", "edit", "list_dir", "glob", "grep")


def _ctx(workspace: Path, call_id: str = "call_1") -> ToolContext:
    """造一次工具执行的现场：工作目录指到临时目录，其余用假件。

    举例：await write(_ctx(scratch_dir), {"path": "a.py", "content": "x"})
    """
    return ToolContext(
        workspace=str(workspace),
        call_id=call_id,
        signal=CancelToken(),
        logger=RecordingLogger(),
    )


def _properties(declaration: ToolDeclaration) -> dict[str, object]:
    """取声明的参数表。

    举例：_properties(RUN_COMMAND) -> {"command": {...}}
    """
    properties = declaration.parameters_schema["properties"]
    assert isinstance(properties, dict)
    return properties


# ---------------------------------------------------------------------------
# 声明
# ---------------------------------------------------------------------------


def test_the_catalog_holds_every_builtin() -> None:
    """注册表里就是这七个，一个不多一个不少。"""
    assert {declaration.name for declaration in build_catalog().declarations()} == set(_FACTS)


def test_every_tool_declares_its_permission_and_side_effect() -> None:
    """权限档位与副作用等级是对照表里写的那两个值。"""
    for declaration in build_catalog().declarations():
        assert (declaration.required_permission, declaration.side_effect) == _FACTS[
            declaration.name
        ]


def test_file_tools_name_their_path_parameter_path() -> None:
    """碰文件的工具必须把路径参数叫 path。

    沙箱按**参数名**判"这工具能碰什么"（见 D8）：名字换成 target，文件类判定就整类消失，
    而且是静默消失 —— 工具照样能跑，只是再也没人判它越不越界。
    """
    for name in _FILE_TOOLS:
        declaration = build_catalog().get(name)
        assert declaration is not None
        assert "path" in _properties(declaration), name


def test_the_command_tool_names_its_parameter_command() -> None:
    """同理：碰命令的工具参数名必须是 command，否则拿不到 full_access 那一档的判定。"""
    assert "command" in _properties(RUN_COMMAND)


def test_read_only_tools_are_the_parallel_safe_ones() -> None:
    """只有副作用为 none 的工具会被编排到同一组并发跑；读写混在一组会出因果错。"""
    for declaration in build_catalog().declarations():
        parallel_safe = declaration.side_effect is SideEffect.NONE
        assert parallel_safe == (declaration.required_permission is Permission.READ_ONLY)


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------


async def test_read_numbers_the_lines(scratch_dir: Path) -> None:
    """行号从 1 数，用 tab 跟正文分开 —— 模型引用行号时靠它。"""
    (scratch_dir / "a.py").write_text("import os\n\nprint(os)\n", encoding="utf-8")

    text = await read(_ctx(scratch_dir), {"path": "a.py"})

    assert text == "1\timport os\n2\t\n3\tprint(os)"


async def test_read_takes_a_window(scratch_dir: Path) -> None:
    """start_line 与 max_lines 一起决定看哪一段，行号仍是全文的。"""
    (scratch_dir / "a.py").write_text("\n".join(f"line{i}" for i in range(1, 11)), encoding="utf-8")

    text = await read(_ctx(scratch_dir), {"path": "a.py", "start_line": 3, "max_lines": 2})

    assert text == "3\tline3\n4\tline4"


async def test_read_says_which_range_is_empty(scratch_dir: Path) -> None:
    """范围落在文件之外时说清楚全文有多少行 —— 不能回空串，那会被当成「没有返回内容」。"""
    (scratch_dir / "a.py").write_text("只有一行\n", encoding="utf-8")

    assert "共 1 行" in await read(_ctx(scratch_dir), {"path": "a.py", "start_line": 9})


async def test_read_reports_a_missing_file(scratch_dir: Path) -> None:
    """文件不存在是给人看的一句话，模型据此换个路径。"""
    with pytest.raises(ToolArgumentError, match="文件不存在"):
        await read(_ctx(scratch_dir), {"path": "nope.py"})


async def test_read_refuses_a_directory(scratch_dir: Path) -> None:
    """把目录当文件读要报错，而不是抛一个看不出所以然的系统错误。"""
    (scratch_dir / "pkg").mkdir()

    with pytest.raises(ToolArgumentError, match="目录"):
        await read(_ctx(scratch_dir), {"path": "pkg"})


# ---------------------------------------------------------------------------
# write
# ---------------------------------------------------------------------------


async def test_write_creates_the_file_and_its_parents(scratch_dir: Path) -> None:
    """缺的父目录一并建好 —— 否则模型得先学会建目录。"""
    text = await write(_ctx(scratch_dir), {"path": "src/pkg/a.py", "content": "x = 1\n"})

    assert "已新建" in text
    assert (scratch_dir / "src" / "pkg" / "a.py").read_text(encoding="utf-8") == "x = 1\n"


async def test_write_overwrites_and_says_so(scratch_dir: Path) -> None:
    """已存在的文件是覆盖，回报里要能看出来是覆盖。"""
    (scratch_dir / "a.py").write_text("旧内容", encoding="utf-8")

    text = await write(_ctx(scratch_dir), {"path": "a.py", "content": "新内容"})

    assert "已覆盖" in text
    assert (scratch_dir / "a.py").read_text(encoding="utf-8") == "新内容"


async def test_write_takes_an_empty_content(scratch_dir: Path) -> None:
    """空串是合法内容（建一个空文件），不是缺参数。"""
    await write(_ctx(scratch_dir), {"path": "empty.py", "content": ""})

    assert (scratch_dir / "empty.py").read_bytes() == b""


async def test_write_does_not_rewrite_line_endings(scratch_dir: Path) -> None:
    """换行原样落盘：Windows 上不该被悄悄改写成 CRLF，否则整份 diff 都会变样。"""
    await write(_ctx(scratch_dir), {"path": "a.py", "content": "a\nb\n"})

    assert (scratch_dir / "a.py").read_bytes() == b"a\nb\n"


async def test_write_refuses_a_directory(scratch_dir: Path) -> None:
    """路径指到目录上时报错，而不是把它当成文件写。"""
    (scratch_dir / "pkg").mkdir()

    with pytest.raises(ToolArgumentError, match="目录"):
        await write(_ctx(scratch_dir), {"path": "pkg", "content": "x"})


# ---------------------------------------------------------------------------
# edit
# ---------------------------------------------------------------------------


async def test_edit_replaces_the_only_occurrence(scratch_dir: Path) -> None:
    """唯一命中就替换那一处，别处一个字不动。"""
    (scratch_dir / "a.py").write_text("x = 1\ny = 1\n", encoding="utf-8")

    text = await edit(
        _ctx(scratch_dir), {"path": "a.py", "old_string": "x = 1", "new_string": "x = 2"}
    )

    assert "替换 1 处" in text
    assert (scratch_dir / "a.py").read_text(encoding="utf-8") == "x = 2\ny = 1\n"


async def test_edit_refuses_when_the_text_is_missing(scratch_dir: Path) -> None:
    """找不到就一个字都不改 —— 半改的文件比没改更难收拾。"""
    (scratch_dir / "a.py").write_text("x = 1\n", encoding="utf-8")

    with pytest.raises(ToolArgumentError, match="没有找到"):
        await edit(
            _ctx(scratch_dir), {"path": "a.py", "old_string": "z = 9", "new_string": "z = 8"}
        )

    assert (scratch_dir / "a.py").read_text(encoding="utf-8") == "x = 1\n"


async def test_edit_refuses_when_the_text_appears_more_than_once(scratch_dir: Path) -> None:
    """出现多次时改哪一处是猜的 —— 报出现次数，让模型多带几行上下文再来。"""
    (scratch_dir / "a.py").write_text("x = 1\nx = 1\n", encoding="utf-8")

    with pytest.raises(ToolArgumentError, match="出现了 2 次"):
        await edit(
            _ctx(scratch_dir), {"path": "a.py", "old_string": "x = 1", "new_string": "x = 2"}
        )

    assert (scratch_dir / "a.py").read_text(encoding="utf-8") == "x = 1\nx = 1\n"


async def test_edit_refuses_identical_strings(scratch_dir: Path) -> None:
    """前后一样说明模型搞错了，不是无事发生。"""
    with pytest.raises(ToolArgumentError, match="没有可改的地方"):
        await edit(_ctx(scratch_dir), {"path": "a.py", "old_string": "a", "new_string": "a"})


async def test_edit_keeps_the_other_line_endings(scratch_dir: Path) -> None:
    """文件是 CRLF 的，改一行不该把整份文件的换行都换掉。"""
    (scratch_dir / "a.py").write_bytes(b"x = 1\r\ny = 1\r\n")

    await edit(_ctx(scratch_dir), {"path": "a.py", "old_string": "y = 1", "new_string": "y = 2"})

    assert (scratch_dir / "a.py").read_bytes() == b"x = 1\r\ny = 2\r\n"


# ---------------------------------------------------------------------------
# list_dir
# ---------------------------------------------------------------------------


async def test_list_dir_puts_directories_first(scratch_dir: Path) -> None:
    """目录带尾斜杠并排前面，一眼看得出结构。"""
    (scratch_dir / "pkg").mkdir()
    (scratch_dir / "b.py").write_text("", encoding="utf-8")
    (scratch_dir / "a.py").write_text("", encoding="utf-8")

    assert (await list_dir(_ctx(scratch_dir), {"path": "."})).splitlines() == [
        "pkg/",
        "a.py",
        "b.py",
    ]


async def test_list_dir_defaults_to_the_workspace(scratch_dir: Path) -> None:
    """不给 path 就列工作目录本身。"""
    (scratch_dir / "a.py").write_text("", encoding="utf-8")

    assert await list_dir(_ctx(scratch_dir), {}) == "a.py"


async def test_list_dir_says_when_it_is_empty(scratch_dir: Path) -> None:
    """空目录也要有句话，否则模型看到的是一片空白。"""
    assert "空目录" in await list_dir(_ctx(scratch_dir), {"path": "."})


async def test_list_dir_refuses_a_file(scratch_dir: Path) -> None:
    """给的是文件时要说清楚，而不是回一个空列表。"""
    (scratch_dir / "a.py").write_text("", encoding="utf-8")

    with pytest.raises(ToolArgumentError, match="不是目录"):
        await list_dir(_ctx(scratch_dir), {"path": "a.py"})


# ---------------------------------------------------------------------------
# glob
# ---------------------------------------------------------------------------


async def test_glob_finds_files_recursively(scratch_dir: Path) -> None:
    """递归找，回报的是**相对工作目录**的路径 —— 模型要能把它原样传给 read。"""
    (scratch_dir / "src" / "pkg").mkdir(parents=True)
    (scratch_dir / "src" / "pkg" / "a.py").write_text("", encoding="utf-8")
    (scratch_dir / "top.py").write_text("", encoding="utf-8")

    assert (await glob(_ctx(scratch_dir), {"pattern": "*.py"})).splitlines() == [
        "src/pkg/a.py",
        "top.py",
    ]


async def test_glob_does_not_list_directories(scratch_dir: Path) -> None:
    """找的是文件：名字匹配的目录不该混进来。"""
    (scratch_dir / "pkg.py").mkdir()

    assert "没有找到" in await glob(_ctx(scratch_dir), {"pattern": "*.py"})


async def test_glob_says_when_nothing_matches(scratch_dir: Path) -> None:
    """没有匹配也回一句，别回空串。"""
    assert "没有找到" in await glob(_ctx(scratch_dir), {"pattern": "*.rs"})


async def test_glob_starts_from_the_given_directory(scratch_dir: Path) -> None:
    """path 决定从哪开始找。"""
    (scratch_dir / "src").mkdir()
    (scratch_dir / "src" / "a.py").write_text("", encoding="utf-8")

    assert await glob(_ctx(scratch_dir), {"pattern": "*.py", "path": "src"}) == "src/a.py"


async def test_glob_caps_the_listing(scratch_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """超过上限只列前几个，并说明总数。

    上限要 monkeypatch 到很小：真造 200 个文件只为验一句提示，不值当。
    """
    module = importlib.import_module("agent_architecture.modules.tool_calling.builtin.glob")
    monkeypatch.setattr(module, "MAX_MATCHES", 1)
    (scratch_dir / "a.py").write_text("", encoding="utf-8")
    (scratch_dir / "b.py").write_text("", encoding="utf-8")

    text = await glob(_ctx(scratch_dir), {"pattern": "*.py"})

    assert text.splitlines() == ["a.py", "…（共 2 个，只列了前 1 个）"]


# ---------------------------------------------------------------------------
# grep
# ---------------------------------------------------------------------------


async def test_grep_reports_path_line_and_text(scratch_dir: Path) -> None:
    """一行一处匹配：路径、行号（从 1 数）、原文。"""
    (scratch_dir / "a.py").write_text("import os\nx = 1\n", encoding="utf-8")
    (scratch_dir / "b.py").write_text("x = 2\n", encoding="utf-8")

    assert (await grep(_ctx(scratch_dir), {"pattern": "x = ", "include": "*.py"})).splitlines() == [
        "a.py:2:x = 1",
        "b.py:1:x = 2",
    ]


async def test_grep_include_filters_files(scratch_dir: Path) -> None:
    """include 限定文件名模式。"""
    (scratch_dir / "a.py").write_text("hit\n", encoding="utf-8")
    (scratch_dir / "a.md").write_text("hit\n", encoding="utf-8")

    assert await grep(_ctx(scratch_dir), {"pattern": "hit", "include": "*.py"}) == "a.py:1:hit"


async def test_grep_searches_a_single_file(scratch_dir: Path) -> None:
    """path 指到文件上就只搜它。"""
    (scratch_dir / "a.py").write_text("hit\n", encoding="utf-8")
    (scratch_dir / "b.py").write_text("hit\n", encoding="utf-8")

    assert await grep(_ctx(scratch_dir), {"pattern": "hit", "path": "a.py"}) == "a.py:1:hit"


async def test_grep_says_when_nothing_matches(scratch_dir: Path) -> None:
    """没匹配也是信息（搜过了，没有），不能回空串。"""
    (scratch_dir / "a.py").write_text("x\n", encoding="utf-8")

    assert "没有匹配" in await grep(_ctx(scratch_dir), {"pattern": "zzz"})


async def test_grep_reports_a_missing_path(scratch_dir: Path) -> None:
    """路径不存在与"搜了没搜到"要分开说。"""
    with pytest.raises(ToolArgumentError, match="路径不存在"):
        await grep(_ctx(scratch_dir), {"pattern": "x", "path": "nope"})


async def test_grep_refuses_a_bad_regex(scratch_dir: Path) -> None:
    """正则写错了要当场说，而不是回一句「没有匹配」。"""
    with pytest.raises(ToolArgumentError, match="正则不合法"):
        await grep(_ctx(scratch_dir), {"pattern": "(["})


async def test_grep_caps_the_hits(scratch_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """超过上限只列前几处，并说明还有更多。"""
    module = importlib.import_module("agent_architecture.modules.tool_calling.builtin.grep")
    monkeypatch.setattr(module, "MAX_MATCHES", 1)
    (scratch_dir / "a.py").write_text("hit\nhit\n", encoding="utf-8")

    text = await grep(_ctx(scratch_dir), {"pattern": "hit"})

    assert text.splitlines() == ["a.py:1:hit", "…（匹配超过 1 处，只列了前 1 处）"]


async def test_grep_skips_files_that_are_too_big(
    scratch_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """超大文件直接跳过：真去读依赖目录里那些几 MB 的文件，一次调用能跑到超时。"""
    module = importlib.import_module("agent_architecture.modules.tool_calling.builtin.grep")
    monkeypatch.setattr(module, "MAX_FILE_BYTES", 4)
    (scratch_dir / "big.py").write_text("hit\nhit\n", encoding="utf-8")

    assert "没有匹配" in await grep(_ctx(scratch_dir), {"pattern": "hit"})


# ---------------------------------------------------------------------------
# run_command
# ---------------------------------------------------------------------------


async def test_run_command_returns_the_output(scratch_dir: Path) -> None:
    """真起一个进程跑命令，输出原样回来。"""
    assert await run_command(_ctx(scratch_dir), {"command": "echo hello-from-tool"}) == (
        "hello-from-tool"
    )


async def test_run_command_runs_in_the_workspace(scratch_dir: Path) -> None:
    """工作目录就是工作区根 —— 命令里的相对路径才落对地方。"""
    probe = "import os;print(os.path.basename(os.getcwd()))"
    # PowerShell 里带引号的命令路径必须先加调用运算符 &，否则整行会被当成一个字符串表达式。
    prefix = "& " if os.name == "nt" else ""
    command = f'{prefix}"{sys.executable}" -c "{probe}"'

    assert await run_command(_ctx(scratch_dir), {"command": command}) == scratch_dir.name


async def test_run_command_marks_a_nonzero_exit(scratch_dir: Path) -> None:
    """非零退出码是信息，不是异常：命令跑起来了，只是它自己说没成。"""
    assert "退出码 3" in await run_command(_ctx(scratch_dir), {"command": "exit 3"})


async def test_run_command_marks_empty_output(scratch_dir: Path) -> None:
    """没有输出时说一句，别回空串（空串会被当成「没有返回内容」）。"""
    assert "没有输出" in await run_command(_ctx(scratch_dir), {"command": "exit 0"})

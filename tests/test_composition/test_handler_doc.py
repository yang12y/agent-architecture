"""docs/HANDLERS.md 不能过期。

文档过期比没有文档更坏 —— 它会让人按错的顺序去插 handler。所以这里钉三件事：
漏了谁、priority 抄错没有、总数对不对。
"""

from __future__ import annotations

from pathlib import Path

from fakes import ScriptedLlm, make_app_config
from yzy_agent.composition import build_container

# 文档位置，相对项目根（pytest 从根目录跑）。
#
# 举例：DOC == Path("docs/HANDLERS.md")
DOC = Path("docs") / "HANDLERS.md"


def _handlers(scratch_dir: Path):
    """装一份容器，拿它**真正装出来**的 handler 表。

    举例：handlers = _handlers(scratch_dir)
    """
    config = make_app_config(session_dir=str(scratch_dir / "sessions"))
    return build_container(config, llm=ScriptedLlm()).pipeline.registry.handlers()


def test_the_doc_lists_every_registered_handler(scratch_dir: Path) -> None:
    """加了 handler 却忘了写进文档，这条会红。"""
    text = DOC.read_text(encoding="utf-8")

    missing = [h.id for h in _handlers(scratch_dir) if f"`{h.id}`" not in text]

    assert missing == []


def test_the_doc_agrees_on_every_priority(scratch_dir: Path) -> None:
    """priority 抄错一位，这条会红 —— 同一个位点上的先后就反了。"""
    lines = DOC.read_text(encoding="utf-8").splitlines()

    wrong = [
        (h.id, h.priority)
        for h in _handlers(scratch_dir)
        if not any(f"`{h.id}`" in line and f"| {h.priority} |" in line for line in lines)
    ]

    assert wrong == []


def test_the_doc_states_the_right_total(scratch_dir: Path) -> None:
    """总数对不上说明有增删没同步。"""
    text = DOC.read_text(encoding="utf-8")

    assert f"合计 **{len(_handlers(scratch_dir))} 个**" in text

"""造技能目录的小工具，几个测试文件共用。"""

from __future__ import annotations

from pathlib import Path


def write_skill(
    root: Path,
    name: str,
    *,
    description: str = "说明",
    when_to_use: str = "",
    model_invocable: bool | None = None,
    body: str = "正文",
) -> Path:
    """在 root 下建一个技能目录，返回入口文件路径。

    举例：write_skill(scratch_dir, "demo") -> Path(".../demo/SKILL.md")
    """
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"name: {name}", f"description: {description}"]
    if when_to_use:
        lines.append(f"when_to_use: {when_to_use}")
    if model_invocable is not None:
        lines.append(f"model_invocable: {'true' if model_invocable else 'false'}")
    lines.extend(["---", "", body, ""])
    entry = directory / "SKILL.md"
    entry.write_text("\n".join(lines), encoding="utf-8")
    return entry

"""约定守门测试：字段标注用 # 注释写在字段**正上方**。

**为什么不用三引号**：Python 有一条 PEP 258 约定——**赋值后面紧跟的字符串字面量，
就是这个赋值的文档**。Pylance 等工具按这条规则配对。所以标注一旦写成三引号放在字段上方，
它会跟**上一个**字段配成一对：鼠标悬停在 `id` 上，弹出的是 `name` 的说明。

因此规则很硬：**除了每个体的第一条语句（真正的 docstring），不允许出现裸字符串表达式。**
这条测试就是把它钉住——本项目已经在这上面翻过两次车。
"""

from __future__ import annotations

import ast
from pathlib import Path

# 仓库根目录，用来把违规位置报成相对路径。
ROOT = Path(__file__).resolve().parent.parent

# 要扫描的目录。漏一个，约定就有洞。
SCAN_DIRS = ("src", "tests", "scripts")


def is_bare_string(node: ast.stmt) -> bool:
    """判断一条语句是不是裸字符串表达式（也就是字段标注或 docstring）。

    举例：类体里单独一行的字符串字面量，就是这种表达式。
    """
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def find_stray_strings(path: Path) -> list[tuple[int, str]]:
    """找出一个文件里"位置不对的裸字符串表达式"。

    只有每个体的第一条语句可以是字符串（那是模块 / 类 / 方法的 docstring）；
    其余位置的裸字符串一律是没搬干净的字段标注。

    返回 (行号, 内容摘要) 列表；空列表表示这个文件是干净的。
    举例：find_stray_strings(Path("clean.py")) -> []
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for index, stmt in enumerate(node.body):
            if index == 0 or not is_bare_string(stmt):
                continue
            first_line = stmt.value.value.strip().splitlines()[0][:48]
            hits.append((stmt.lineno, first_line))

    return hits


def test_no_stray_string_expressions() -> None:
    """全仓库除了 docstring 位置，不允许出现裸字符串表达式。

    报错信息里会逐条列出文件、行号和内容——照着改成 # 注释即可。
    """
    violations: list[str] = []

    for folder in SCAN_DIRS:
        for path in sorted((ROOT / folder).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            for lineno, label in find_stray_strings(path):
                rel = path.relative_to(ROOT).as_posix()
                violations.append(f"{rel}:{lineno}  {label}")

    assert not violations, (
        "下面这些地方的字符串不在 docstring 位置上，字段标注应该改用 # 注释写在字段上方"
        "（见 docs/ARCHITECTURE.md 第 0.1 节）：\n  " + "\n  ".join(violations)
    )

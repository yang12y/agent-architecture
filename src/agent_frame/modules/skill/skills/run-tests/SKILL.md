---
name: run-tests
description: 在这个项目里跑格式检查与测试，含哪几条红是受限沙箱造成的
when_to_use: 改完代码要验证时
model_invocable: true
---

# 跑测试

## 命令

    .venv\Scripts\python.exe -m ruff check src tests scripts
    .venv\Scripts\python.exe -m pytest -q

只跑一部分就加路径：`pytest -q tests/test_session`。

## 别跑 `ruff format`

它是**重写器**，不是检查器：会把超过 100 列的行拆开、补尾逗号，连你没碰过的文件也一起改。
一个人只想改五行，结果 diff 里几十行是重排 —— 评审时看不出哪几行是真的。

规矩：**日常只跑 `ruff check`**（只报告、不改文件）。格式问题攒着，等一次专门的提交：

    # 那一次只做这件事，提交信息写清楚"纯格式"
    .venv\Scripts\python.exe -m ruff format src tests scripts
    .venv\Scripts\python.exe -m ruff check src tests scripts
    .venv\Scripts\python.exe -m pytest -q

## 受限沙箱里会红的六条

不是代码问题，别去改它们：

| 测试 | 报错 |
|---|---|
| `tests/test_tool_calling/test_builtin.py` 的四条 `run_command` | `WinError 5` 起不了子进程 |
| `tests/test_tool_calling/test_end_to_end.py::test_a_command_runs_at_full_access` | 同上 |
| `tests/test_api/test_main.py::test_the_entry_point_builds_an_app` | `tmp_path` 建不出来 |

判断依据是报错里的 `WinError 5` 或 `PermissionError`。**除了这六条，红的都是真问题。**

## 看结果

`pytest -q` 末尾那行就是结论：`N passed, M failed`。有 failed 就往上翻具体断言，
别看截图也别猜。

"""ASGI 入口的测试。

它是全项目**唯一**在模块级建 `app` 的地方 —— 少了它，`uvicorn agent_architecture.api.main:app` 起不来。
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest


def test_the_entry_point_builds_an_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """导入它就得到一个建好的 app。

    配置全从环境变量来：**不读 .env、不碰真实数据目录** —— 这个用例不该受开发机配置影响。
    """
    monkeypatch.setenv("YZY_ENV_FILE", "")
    monkeypatch.setenv("YZY_LLM_API_KEY", "sk-test")
    monkeypatch.setenv("YZY_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("YZY_SESSION_DIR", str(tmp_path / "sessions"))

    module = importlib.import_module("agent_architecture.api.main")

    assert module.app.title == "agent-architecture"

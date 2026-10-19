"""前端静态页的托管。

三件事：根路径给 index.html、样式与脚本拿得到、API 路由没被兜底挂载吃掉。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from fakes import ScriptedLlm, make_app_config
from yzy_agent.api import create_app
from yzy_agent.composition import build_container


@pytest.fixture
async def client(scratch_dir: Path) -> AsyncIterator[httpx.AsyncClient]:
    """把请求直接打进 ASGI 应用，和 test_http 一样不占端口。

    举例：response = await client.get("/")
    """
    config = make_app_config(session_dir=str(scratch_dir / "sessions"))
    container = build_container(config, llm=ScriptedLlm())
    transport = httpx.ASGITransport(app=create_app(container))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as opened:
        yield opened


async def test_the_root_serves_the_page(client: httpx.AsyncClient) -> None:
    """GET / 给的是 index.html，不是 404。"""
    response = await client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "yzy-agent" in response.text


async def test_the_stylesheet_and_scripts_are_served(client: httpx.AsyncClient) -> None:
    """静态资源按原路径取得到 —— 挂载点写错的话这里会 404。"""
    style = await client.get("/style.css")
    script = await client.get("/js/main.js")

    assert style.status_code == 200
    assert "text/css" in style.headers["content-type"]
    assert script.status_code == 200
    assert "javascript" in script.headers["content-type"]


async def test_api_routes_still_win_over_the_mount(client: httpx.AsyncClient) -> None:
    """/sessions 走的还是接口，不是静态兜底 —— 所以挂载必须在所有路由之后。"""
    response = await client.get("/sessions", params={"user_id": "u1"})

    assert response.status_code == 200
    assert response.json() == {"sessions": []}

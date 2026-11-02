"""导出 .docx 的端点：收到块，回一份能解开的 Word 文件。"""

from __future__ import annotations

import io
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from agent_frame.api import create_app
from agent_frame.composition import build_container
from fakes import ScriptedLlm, make_app_config, text_turn

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# 一张 1×1 的 PNG，够验"图确实被嵌进文档了"。
#
# 举例：PNG_BASE64[:8] == "iVBORw0KG"
PNG_BASE64 = (  # 1×1 透明 PNG
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


@pytest.fixture
async def client(scratch_dir: Path) -> AsyncIterator[httpx.AsyncClient]:
    """一个把请求直接打进 ASGI 应用的客户端，不占端口、不联网。

    举例：response = await client.post("/exports/docx", json={...})
    """
    config = make_app_config(session_dir=str(scratch_dir / "sessions"))
    container = build_container(config, llm=ScriptedLlm([text_turn("好的")]))
    transport = httpx.ASGITransport(app=create_app(container))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as opened:
        yield opened


async def _export(client: httpx.AsyncClient, blocks: list[dict]) -> bytes:
    """发一次导出，断言成功，返回文件字节。

    举例：data = await _export(client, [{"type": "paragraph", "text": "甲"}])
    """
    response = await client.post(
        "/exports/docx",
        json={"title": "产量分析", "subtitle": "导出自 agent-frame", "blocks": blocks},
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith(DOCX)
    return response.content


def _document_xml(data: bytes) -> str:
    """从 .docx（本质是个 zip）里取出正文 XML。

    举例：_document_xml(data) -> "<w:document ...</w:document>"
    """
    return _part(data, "word/document.xml")


def _styles_xml(data: bytes) -> str:
    """取出样式表 XML —— 正文的字体字号是写在 Normal 样式里的。

    举例：_styles_xml(data) -> "<w:styles ...</w:styles>"
    """
    return _part(data, "word/styles.xml")


def _part(data: bytes, name: str) -> str:
    """从 zip 里取一个部件，按 UTF-8 解出来。

    举例：_part(data, "word/styles.xml") -> "<w:styles ..."
    """
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return archive.read(name).decode("utf-8")


async def test_the_file_is_a_real_zip(client: httpx.AsyncClient) -> None:
    """回的是 .docx 字节：zip 开头，能解出 document.xml。"""
    data = await _export(client, [{"type": "paragraph", "text": "整体呈上升趋势"}])

    assert data[:2] == b"PK"
    assert "整体呈上升趋势" in _document_xml(data)


async def test_a_heading_uses_a_word_heading_style(client: httpx.AsyncClient) -> None:
    """标题走 Word 的 Heading 样式，不是加粗的大字。"""
    data = await _export(client, [{"type": "heading", "level": 2, "text": "一、产量"}])

    xml = _document_xml(data)
    assert "一、产量" in xml
    assert "Heading2" in xml


async def test_the_fonts_and_sizes(client: httpx.AsyncClient) -> None:
    """字体是宋体；标题小三（15 磅）、正文小四（12 磅）、表格与代码五号（10.5 磅）。

    Word 里字号按"半磅"存：15 磅 = 30，12 磅 = 24，10.5 磅 = 21。
    """
    data = await _export(
        client,
        [
            {"type": "paragraph", "text": "正文"},
            {"type": "table", "headers": ["表头"], "rows": [["格子"]]},
            {"type": "code", "text": "print(1)"},
        ],
    )

    xml = _document_xml(data)
    styles = _styles_xml(data)
    # 标题：宋体 + 15 磅（小三），写在标题那个 run 上。
    assert 'w:eastAsia="宋体"' in xml
    assert '<w:sz w:val="30"/>' in xml
    # 正文：宋体 + 12 磅（小四）写在 Normal 样式里 —— 段落自己没有字号，跟着它走。
    assert 'w:eastAsia="宋体"' in styles
    assert '<w:sz w:val="24"/>' in styles
    # 表格与代码：宋体 10.5 磅（五号）。
    assert '<w:sz w:val="21"/>' in xml
    assert "格子" in xml


async def test_a_list_keeps_its_items(client: httpx.AsyncClient) -> None:
    """列表项各占一段，带 List Bullet 样式。"""
    data = await _export(
        client, [{"type": "list", "ordered": False, "items": ["先取数", "再画图"]}]
    )

    xml = _document_xml(data)
    assert "先取数" in xml
    assert "再画图" in xml
    assert "ListBullet" in xml


async def test_a_table_becomes_a_word_table(client: httpx.AsyncClient) -> None:
    """表头那一行加粗，每个格子落在自己的 w:tc 里。"""
    data = await _export(
        client,
        [
            {
                "type": "table",
                "headers": ["类别", "产量"],
                "rows": [["2023", "128"], ["2024", "158"]],
            }
        ],
    )

    xml = _document_xml(data)
    assert "2024" in xml and "158" in xml
    assert xml.count("<w:tc>") >= 6


async def test_a_code_block_stays_monospaced(client: httpx.AsyncClient) -> None:
    """代码块原样保留，字体是等宽的。"""
    data = await _export(client, [{"type": "code", "text": "print(1)\nprint(2)"}])

    xml = _document_xml(data)
    assert "print(1)" in xml and "print(2)" in xml
    assert "Consolas" in xml


async def test_an_image_is_embedded(client: httpx.AsyncClient) -> None:
    """图真的进了文档：zip 里有 word/media，正文里有 w:drawing。"""
    data = await _export(
        client,
        [{"type": "image", "png_base64": PNG_BASE64, "width_cm": 12}],
    )

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        media = [name for name in archive.namelist() if name.startswith("word/media/")]
    assert media, "图没有进 word/media"
    assert "w:drawing" in _document_xml(data)


async def test_an_empty_document_still_works(client: httpx.AsyncClient) -> None:
    """一块都不给也是合法请求：出来是一份只有标题的空文档。"""
    data = await _export(client, [])

    assert "产量分析" in _document_xml(data)


async def test_a_broken_image_is_a_400(client: httpx.AsyncClient) -> None:
    """base64 解不开就明说，别把半个文件发出去。"""
    response = await client.post(
        "/exports/docx",
        json={"blocks": [{"type": "image", "png_base64": "不是 base64!"}]},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_docx"


async def test_an_unknown_block_type_is_rejected(client: httpx.AsyncClient) -> None:
    """块的类型对不上是参数错（422），不是悄悄跳过。"""
    response = await client.post(
        "/exports/docx",
        json={"blocks": [{"type": "video", "text": "x"}]},
    )

    assert response.status_code == 422

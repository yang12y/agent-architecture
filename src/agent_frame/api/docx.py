"""把一轮问答排成 .docx：标题是用户的问题，正文是模型的回答。

前端已经把它要导出的那一条回答渲染成了 HTML，发过来的是**块列表**，不是 HTML ——
后端不做 HTML 解析，只按块排版：形状对不上就是参数错，不存在"尽力猜一猜"。

排版规格：标题宋体 15 磅（小三），正文宋体 12 磅（小四），表格宋体 10.5 磅（五号），
代码等宽 10.5 磅。

**不落盘**：整份文档在内存里拼好、随响应发出去。导出物是日志的派生物，
存下来就等于多一个真相源，还得配清理与二次下载的规则。
"""

from __future__ import annotations

import base64
import binascii
import io
from collections.abc import Sequence

from docx import Document
from docx.image.exceptions import UnrecognizedImageError
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from .schemas import DocxBlockIn

# 正文字体。中文在 Word 里走 eastAsia 那一栏 —— 只设 name 的话，汉字仍然会用
# 模板自带的英文字体兜底。
#
# 举例："宋体"
CN_FONT = "宋体"

# 标题字号（磅）：「小三」= 15 磅。
#
# 举例：15.0
TITLE_PT = 15.0

# 正文字号（磅）：「小四」= 12 磅。
#
# 举例：12.0
BODY_PT = 12.0

# 表格字号（磅）：「五号」= 10.5 磅。表里的字比正文再小一号，宽表才放得下。
#
# 举例：10.5
TABLE_PT = 10.5

# 等宽字体，代码块用。
#
# 举例："Consolas"
MONO_FONT = "Consolas"

# 代码块的字号：与表格一样是五号，比正文小一号，免得代码把版面撑开。
#
# 举例：10.5
CODE_PT = 10.5

# 图默认放到多宽（厘米）。A4 版心 16 厘米，留一点边。
#
# 举例：16.0
DEFAULT_IMAGE_CM = 16.0


def build_docx(title: str, subtitle: str, blocks: Sequence[DocxBlockIn]) -> bytes:
    """把标题 + 副标题 + 块列表排成一份 .docx，返回字节。

    举例：build_docx("产量分析", "导出自 agent-frame", []) -> b"PK\x03\x04..."
    出错：图片的 base64 解不开时抛 ValueError。
    """
    document = Document()
    _use_cn_font(document)

    if title:
        heading = document.add_heading(title, level=0)
        _style(heading, CN_FONT, TITLE_PT)
    if subtitle:
        meta = document.add_paragraph()
        meta.add_run(subtitle)
        meta.paragraph_format.space_after = 0

    for block in blocks:
        _add_block(document, block)

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _use_cn_font(document: Document) -> None:
    """把正文默认字体设成宋体、四号 —— 没被单独指定的文字都跟着它。

    举例：_use_cn_font(document)
    """
    style = document.styles["Normal"]
    style.font.name = CN_FONT
    style.font.size = Pt(BODY_PT)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), CN_FONT)


def _style(paragraph, name: str, size_pt: float, *, bold: bool | None = None) -> None:
    """给一整段换字体与字号。

    中文必须同时写 eastAsia：只设 name 的话 Word 只认西文那半边，汉字还是模板字体。

    举例：_style(heading, CN_FONT, TITLE_PT)
    """
    for run in paragraph.runs:
        run.font.name = name
        run.font.size = Pt(size_pt)
        if bold is not None:
            run.bold = bold
        run.font.color.rgb = RGBColor(0, 0, 0)
        run._element.rPr.rFonts.set(qn("w:eastAsia"), name)


def _add_block(document: Document, block: DocxBlockIn) -> None:
    """按块的类型落到 Word 的对应结构上。

    举例：_add_block(document, DocxParagraphIn(text="第一段"))
    """
    kind = block.type
    if kind == "heading":
        level = max(1, min(int(block.level), 4))
        heading = document.add_heading(block.text, level=level)
        # 用 Heading 样式是为了 Word 的导航窗格；字体颜色在 run 上盖掉模板自带的蓝。
        _style(heading, CN_FONT, TITLE_PT if level == 1 else BODY_PT, bold=True)
    elif kind == "paragraph":
        # 段落走 Normal 样式，字体与字号在 _use_cn_font 里统一设成宋体四号。
        document.add_paragraph(block.text)
    elif kind == "list":
        style = "List Number" if block.ordered else "List Bullet"
        for item in block.items:
            document.add_paragraph(item, style=style)
    elif kind == "code":
        _add_code(document, block.text)
    elif kind == "table":
        _add_table(document, block.headers, block.rows)
    elif kind == "image":
        _add_image(document, block.png_base64, block.width_cm)


def _add_code(document: Document, text: str) -> None:
    """代码块：等宽字体，一行一个换行（python-docx 里 \\n 不会自己断行）。

    举例：_add_code(document, "print(1)")
    """
    paragraph = document.add_paragraph()
    lines = text.splitlines() or [""]
    for index, line in enumerate(lines):
        run = paragraph.add_run(line)
        run.font.name = MONO_FONT
        run.font.size = Pt(CODE_PT)
        run._element.rPr.rFonts.set(qn("w:eastAsia"), MONO_FONT)
        if index < len(lines) - 1:
            run.add_break()


def _add_table(document: Document, headers: Sequence[str], rows: Sequence[Sequence[str]]) -> None:
    """表格：表头加粗，走 Word 的网格样式。

    举例：_add_table(document, ["类别", "值"], [["甲", "1"]])
    """
    columns = max(len(headers), max((len(row) for row in rows), default=0))
    if columns == 0:
        return
    table = document.add_table(rows=1, cols=columns)
    table.style = "Table Grid"
    for index in range(columns):
        cell = table.rows[0].cells[index]
        cell.text = headers[index] if index < len(headers) else ""
        # 表格里的字比正文小一号（五号），表头再加粗。
        _style(cell.paragraphs[0], CN_FONT, TABLE_PT, bold=True)
    for row in rows:
        cells = table.add_row().cells
        for index in range(columns):
            cells[index].text = row[index] if index < len(row) else ""
            _style(cells[index].paragraphs[0], CN_FONT, TABLE_PT)


def _add_image(document: Document, png_base64: str, width_cm: float) -> None:
    """插图：前端已经把它自己的 SVG 画成 PNG 了 —— python-docx 不支持 SVG。

    举例：_add_image(document, "iVBORw0KGgo...", 16.0)
    出错：base64 不合法或不是图片时抛 ValueError。
    """
    try:
        data = base64.b64decode(png_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("图片不是合法的 base64") from exc
    if not data:
        raise ValueError("图片是空的")
    try:
        document.add_picture(io.BytesIO(data), width=Cm(width_cm or DEFAULT_IMAGE_CM))
    except (ValueError, UnrecognizedImageError) as exc:
        # 认不出格式（不是 PNG / JPEG）也在这一层收成参数错，别让它变成 500。
        raise ValueError(f"图片认不出来：{exc}") from exc

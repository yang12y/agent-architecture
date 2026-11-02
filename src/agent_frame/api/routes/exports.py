"""导出：把一段回答排成 .docx 发回去。

内容由客户端给（前端已经把回答渲染过了），所以这条路由**不读日志、不认识会话**，
也不在磁盘上留任何东西 —— 文件只存在于这一次响应里。
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response

from ..docx import build_docx
from ..errors import ApiError
from ..schemas import DocxExportIn

router = APIRouter(tags=["exports"])

# .docx 的 MIME。浏览器靠它认出"这是个 Word 文件"。
#
# 举例："application/vnd.openxmlformats-officedocument.wordprocessingml.document"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@router.post("/exports/docx", response_class=Response)
async def export_docx(body: DocxExportIn) -> Response:
    """把前端给的块排成一份 .docx（标题是用户的问题，正文是回答）。**不落盘**，响应发完就没了。

    文件名由前端自己定（下载时给 `download` 属性），这里只回一份兜底的名字。

    举例：POST /exports/docx -> 200，Content-Type 是 docx
    出错：块的形状不对回 422；图片的 base64 解不开回 400 bad_docx。
    """
    try:
        data = build_docx(title=body.title, subtitle=body.subtitle, blocks=body.blocks)
    except ValueError as exc:
        raise ApiError(400, "bad_docx", str(exc)) from exc
    return Response(
        content=data,
        media_type=DOCX_MIME,
        headers={"Content-Disposition": 'attachment; filename="answer.docx"'},
    )


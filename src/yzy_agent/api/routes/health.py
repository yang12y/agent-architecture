"""健康检查。"""

from __future__ import annotations

from fastapi import APIRouter

from ..schemas import HealthOut

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthOut)
async def health() -> HealthOut:
    """活着就回 ok。不查数据库、不查模型 —— 那是探针，不是业务。

    举例：GET /health -> {"status": "ok"}
    """
    return HealthOut(status="ok")

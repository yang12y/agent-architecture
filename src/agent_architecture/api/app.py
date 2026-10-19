"""FastAPI 应用：装配路由与异常处理器。"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from agent_architecture.composition import Container

from .errors import ApiError
from .routes import approvals, chat, health, sessions
from .sse import endpoint as sse_endpoint
from .sse.hub import Hub

# 前端静态页的根。它在包外面（src/web），所以按仓库布局算；装成 wheel 时没有这一份，
# 那就只提供 API（记在 ARCHITECTURE.md §9）。
#
# 举例：Path("D:/CodeProjects/agent_architecture/src/web")
WEB_DIR = Path(__file__).resolve().parents[2] / "web"


def create_app(container: Container) -> FastAPI:
    """按一个装好的 Container 建应用。

    container 由调用方给、不在模块里现建 —— 测试才能塞假模型进来。

    举例：app = create_app(build_container())
    """
    app = FastAPI(title="agent-architecture", version="0.1.0")
    app.state.container = container
    # hub 订阅一次事件总线，所有流共用 —— 它挂在 app 上，跟着进程活。
    app.state.hub = Hub(events=container.ports.events, logger=container.ports.logger)
    app.include_router(health.router)
    app.include_router(sessions.router)
    app.include_router(chat.router)
    app.include_router(approvals.router)
    app.include_router(sse_endpoint.router)
    _mount_web(app)

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        """§7.8 的统一错误形状。"""
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    return app


class _FreshStaticFiles(StaticFiles):
    """每次都让浏览器回来问一句的静态文件。

    这个前端**没有构建步骤**，改完 js 直接刷就该看见。可 StaticFiles 只发 ETag 与
    Last-Modified，浏览器照样按启发式规则自己算新鲜度 —— 结果就是改了代码、刷新还是旧的。
    `no-cache` 不是"不许存"，是"存了也得先问我"：没变回 304，很便宜。

    举例：_FreshStaticFiles(directory=WEB_DIR, html=True)
    """

    def file_response(self, *args: object, **kwargs: object) -> Response:
        """在父类那次响应上加一个 Cache-Control。

        举例：response = self.file_response(full_path, stat_result, scope)
        """
        response = super().file_response(*args, **kwargs)  # type: ignore[arg-type]
        response.headers["Cache-Control"] = "no-cache"
        return response


def _mount_web(app: FastAPI) -> None:
    """把前端挂到根路径。

    **必须最后挂**：Starlette 按注册顺序匹配，`Mount("/")` 是兜底的，先注册的
    `/sessions` 这些照样命中。目录不在就跳过 —— 只提供 API 也是一种合法状态。

    举例：_mount_web(app)
    """
    if WEB_DIR.is_dir():
        app.mount("/", _FreshStaticFiles(directory=WEB_DIR, html=True), name="web")

"""把全局配置冻结成 trace 级快照。

位点 1 调用它一次；之后配置再改，也不影响已经开始的 trace。
模型、权限与工具结果上限来自 SessionMeta（会话可以有自己的偏好），预算与压缩配置来自 AppConfig。
"""

from __future__ import annotations

from agent_architecture.contracts import AppConfig, FrozenConfig, Permission, SessionMeta


def freeze_config(
    app: AppConfig,
    *,
    session_meta: SessionMeta | None = None,
    model: str | None = None,
    permission: Permission | None = None,
) -> FrozenConfig:
    """生成本次 trace 的配置快照。

    优先级：model 与 permission 是 显式入参 > SessionMeta > AppConfig；其余一律 会话 > 全局。
    举例：freeze_config(app, session_meta=meta) -> FrozenConfig(model="deepseek-chat", ...)
    """
    resolved_model = model or (session_meta.model if session_meta else None) or app.model
    resolved_permission = (
        permission or (session_meta.permission if session_meta else None) or app.permission
    )
    resolved_max_chars = (
        session_meta.tool_result_max_chars
        if session_meta is not None
        else app.tool_result_max_chars
    )
    return FrozenConfig(
        model=resolved_model,
        summary_model=app.summary_model,
        permission=resolved_permission,
        max_tokens=app.max_tokens,
        max_turns=app.max_turns,
        llm_max_attempts=app.llm_max_attempts,
        thinking=app.thinking,
        compact_tokens=_compact_tokens(app),
        keep_recent=app.keep_recent,
        long_tool_result_max_tokens=app.long_tool_result_max_tokens,
        long_tool_result_keep_recent=app.long_tool_result_keep_recent,
        summary_prompt_template=app.summary_prompt_template,
        tool_result_max_chars=resolved_max_chars,
    )


def _compact_tokens(app: AppConfig) -> int:
    """把窗口比例换算成这一轮的绝对阈值。**只在这里换算一次**，下游拿到的都是数。

    举例：_compact_tokens(AppConfig(max_tokens=1000000, compact_ratio=0.7, ...)) -> 700000
    """
    return int(app.max_tokens * app.compact_ratio)

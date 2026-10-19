"""从环境变量加载全局配置。

规则：
    - 环境变量名统一 `YZY_` 前缀，全大写，下划线分隔。
    - 任何一项缺失都回退到 defaults.py 里的默认值，不报错。
    - 只有"配置本身矛盾"才报 ConfigError，比如压缩阈值大于等于上下文窗口上限。

取值来源与优先级（后面覆盖前面）：

    1. 项目根目录的 .env 文件（路径可用 YZY_ENV_FILE 指定，设成空串则不读）
    2. 真实环境变量

真实环境变量优先，是为了让容器 / CI 里的注入能盖掉开发机上的 .env。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from agent_architecture.contracts import AppConfig, ConfigError, Permission

from .defaults import (
    DEFAULT_COMPACT_RATIO,
    DEFAULT_KEEP_RECENT,
    DEFAULT_LLM_MAX_ATTEMPTS,
    DEFAULT_LLM_TIMEOUT_SECONDS,
    DEFAULT_LOG_LEVEL,
    DEFAULT_LONG_TOOL_RESULT_KEEP_RECENT,
    DEFAULT_LONG_TOOL_RESULT_MAX_TOKENS,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    DEFAULT_SUMMARY_PROMPT,
    DEFAULT_THINKING,
    DEFAULT_TOOL_RESULT_MAX_CHARS,
)
from .dotenv import read_env_file, resolve_env_file

# 环境变量前缀。
#
# 举例：YZY_MODEL=deepseek-chat
ENV_PREFIX = "YZY_"


def load_app_config(
    env: Mapping[str, str] | None = None,
    *,
    env_file: str | Path | None = None,
) -> AppConfig:
    """读取 .env 与环境变量，组装出进程级全局配置。

    显式传入 env 映射时**只认它、不读 .env**——测试要的是确定性。
    举例：config = load_app_config(); config.model -> "deepseek-chat"
    出错：配置自相矛盾时抛 ConfigError。
    """
    if env is not None:
        source: Mapping[str, str] = env
    else:
        source = {**read_env_file(resolve_env_file(env_file)), **os.environ}

    workspace = _text(source, "WORKSPACE") or os.getcwd()
    session_dir = _text(source, "SESSION_DIR") or str(Path(workspace) / ".data" / "sessions")

    max_tokens = _int(source, "MAX_TOKENS", DEFAULT_MAX_TOKENS)
    compact_ratio = _float(source, "COMPACT_RATIO", DEFAULT_COMPACT_RATIO)
    if not 0 < compact_ratio < 1:
        raise ConfigError(
            f"compact_ratio({compact_ratio}) 必须落在 (0, 1) 之间，"
            "否则要么一上来就压缩，要么永远压不到"
        )

    return AppConfig(
        workspace=workspace,
        session_dir=session_dir,
        model=_text(source, "MODEL") or DEFAULT_MODEL,
        summary_model=_text(source, "SUMMARY_MODEL"),
        llm_api_key=_text(source, "LLM_API_KEY"),
        llm_base_url=_text(source, "LLM_BASE_URL"),
        llm_timeout_seconds=_float(source, "LLM_TIMEOUT", DEFAULT_LLM_TIMEOUT_SECONDS),
        llm_max_attempts=_int(source, "LLM_MAX_ATTEMPTS", DEFAULT_LLM_MAX_ATTEMPTS),
        thinking=_bool(source, "THINKING", DEFAULT_THINKING),
        permission=_permission(source),
        max_tokens=max_tokens,
        max_turns=_optional_int(source, "MAX_TURNS"),
        compact_ratio=compact_ratio,
        keep_recent=_int(source, "KEEP_RECENT", DEFAULT_KEEP_RECENT),
        tool_result_max_chars=_int(source, "TOOL_RESULT_MAX_CHARS", DEFAULT_TOOL_RESULT_MAX_CHARS),
        long_tool_result_max_tokens=_int(
            source, "LONG_TOOL_RESULT_MAX_TOKENS", DEFAULT_LONG_TOOL_RESULT_MAX_TOKENS
        ),
        long_tool_result_keep_recent=_int(
            source,
            "LONG_TOOL_RESULT_KEEP_RECENT",
            DEFAULT_LONG_TOOL_RESULT_KEEP_RECENT,
        ),
        summary_prompt_template=_text(source, "SUMMARY_PROMPT") or DEFAULT_SUMMARY_PROMPT,
        log_level=_text(source, "LOG_LEVEL") or DEFAULT_LOG_LEVEL,
    )


def _float(source: Mapping[str, str], name: str, default: float) -> float:
    """读一个浮点环境变量；缺失或空串时用默认值，格式错误时抛 ConfigError。

    举例：_float({"YZY_LLM_TIMEOUT": "30"}, "LLM_TIMEOUT", 60.0) -> 30.0
    """
    raw = source.get(f"{ENV_PREFIX}{name}")
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{ENV_PREFIX}{name} 必须是数字，实际是 {raw!r}") from exc


def _bool(source: Mapping[str, str], name: str, default: bool) -> bool:
    """读一个布尔环境变量；缺失或空串时用默认值。

    只认 true/false（大小写不敏感）、1/0、yes/no、on/off，别的取值直接报错 ——
    静默当成 False 会让人以为开关坏了。

    举例：_bool({"YZY_THINKING": "true"}, "THINKING", False) -> True
    """
    raw = source.get(f"{ENV_PREFIX}{name}")
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value in ("true", "1", "yes", "on"):
        return True
    if value in ("false", "0", "no", "off"):
        return False
    raise ConfigError(f"{ENV_PREFIX}{name} 只能是 true / false，实际是 {raw!r}")


def _text(source: Mapping[str, str], name: str) -> str | None:
    """读一个字符串环境变量，顺带去掉首尾空白。

    作用：把"没配"和"配了空串或一串空格"统一成同一种情况，都返回 None 走默认值。
    举例：_text({"YZY_MODEL": "   "}, "MODEL") -> None
    """
    raw = source.get(f"{ENV_PREFIX}{name}")
    if raw is None or raw.strip() == "":
        return None
    return raw.strip()


def _int(source: Mapping[str, str], name: str, default: int) -> int:
    """读一个整数环境变量；缺失或空串时用默认值，格式错误时抛 ConfigError。

    举例：_int({"YZY_MAX_TOKENS": "64000"}, "MAX_TOKENS", 128000) -> 64000
    """
    raw = source.get(f"{ENV_PREFIX}{name}")
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{ENV_PREFIX}{name} 必须是整数，实际是 {raw!r}") from exc


def _optional_int(source: Mapping[str, str], name: str) -> int | None:
    """读一个可缺省的整数环境变量；缺失时返回 None（表示"不限"）。

    举例：_optional_int({}, "MAX_TURNS") -> None
    """
    raw = source.get(f"{ENV_PREFIX}{name}")
    if raw is None or raw.strip() == "":
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{ENV_PREFIX}{name} 必须是整数，实际是 {raw!r}") from exc


def _permission(source: Mapping[str, str]) -> Permission:
    """读权限档位；缺失时默认 editable，取值非法时抛 ConfigError。

    合法取值：read_only / editable / full_access
    举例：_permission({"YZY_PERMISSION": "read_only"}) -> Permission.READ_ONLY
    """
    raw = source.get(f"{ENV_PREFIX}PERMISSION")
    if raw is None or raw.strip() == "":
        return Permission.EDITABLE
    try:
        return Permission(raw.strip().lower())
    except ValueError as exc:
        allowed = "、".join(p.value for p in Permission)
        raise ConfigError(f"{ENV_PREFIX}PERMISSION 只能是 {allowed}，实际是 {raw!r}") from exc

"""配置加载与冻结的测试。"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path

import pytest

from agent_architecture.contracts import ConfigError, Permission
from agent_architecture.infra.config import (
    freeze_config,
    load_app_config,
    parse_env_text,
    read_env_file,
    resolve_env_file,
)
from agent_architecture.infra.config import loader as config_loader
from agent_architecture.infra.config.defaults import DEFAULT_MAX_TOKENS, DEFAULT_MODEL
from fakes import make_session_meta


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """清掉真实环境里的 YZY_*，让测试不受调用环境干扰。"""
    for key in [key for key in os.environ if key.startswith("YZY_")]:
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def test_empty_env_falls_back_to_defaults() -> None:
    """环境变量为空时全部走默认值，不报错。"""
    config = load_app_config({})

    assert config.model == DEFAULT_MODEL
    assert config.max_tokens == DEFAULT_MAX_TOKENS
    assert config.permission is Permission.EDITABLE
    assert config.max_turns is None
    assert config.session_dir.endswith(".data\\sessions") or config.session_dir.endswith(
        ".data/sessions"
    )


def test_env_overrides_every_scalar() -> None:
    """每一项都能被环境变量覆盖。"""
    config = load_app_config(
        {
            "YZY_WORKSPACE": "D:/ws",
            "YZY_MODEL": "my-model",
            "YZY_SUMMARY_MODEL": "cheap-model",
            "YZY_PERMISSION": "full_access",
            "YZY_MAX_TOKENS": "64000",
            "YZY_MAX_TURNS": "7",
            "YZY_COMPACT_RATIO": "0.5",
            "YZY_KEEP_RECENT": "9",
            "YZY_TOOL_RESULT_MAX_CHARS": "7000",
            "YZY_LONG_TOOL_RESULT_MAX_TOKENS": "512",
            "YZY_LONG_TOOL_RESULT_KEEP_RECENT": "2",
            "YZY_LOG_LEVEL": "DEBUG",
        }
    )

    assert config.workspace == "D:/ws"
    assert config.model == "my-model"
    assert config.summary_model == "cheap-model"
    assert config.permission is Permission.FULL_ACCESS
    assert config.max_tokens == 64000
    assert config.max_turns == 7
    assert config.compact_ratio == 0.5
    assert config.keep_recent == 9
    assert config.tool_result_max_chars == 7000
    assert config.long_tool_result_max_tokens == 512
    assert config.long_tool_result_keep_recent == 2
    assert config.log_level == "DEBUG"


def test_summary_model_defaults_to_none() -> None:
    """摘要模型默认缺省，表示复用主模型。"""
    assert load_app_config({}).summary_model is None


def test_permission_is_case_insensitive() -> None:
    """权限档位大小写不敏感，写全大写也能识别。"""
    assert load_app_config({"YZY_PERMISSION": "READ_ONLY"}).permission is Permission.READ_ONLY


def test_thinking_is_off_by_default_and_can_be_switched_on() -> None:
    """思考默认关着；打开要显式写 true。"""
    assert load_app_config({}).thinking is False
    assert load_app_config({"YZY_THINKING": "true"}).thinking is True
    assert load_app_config({"YZY_THINKING": "off"}).thinking is False


def test_a_bad_boolean_raises() -> None:
    """布尔项写了个认不出来的值就报错 —— 静默当成 False 会让人以为开关坏了。"""
    with pytest.raises(ConfigError, match="YZY_THINKING"):
        load_app_config({"YZY_THINKING": "maaybe"})


def test_compact_ratio_must_be_between_zero_and_one() -> None:
    """比例越界是配置矛盾，必须在启动时拦住 —— 1 就是"永远压不到"。"""
    with pytest.raises(ConfigError, match="compact_ratio"):
        load_app_config({"YZY_COMPACT_RATIO": "1"})
    with pytest.raises(ConfigError, match="compact_ratio"):
        load_app_config({"YZY_COMPACT_RATIO": "0"})


def test_the_compaction_threshold_is_a_fraction_of_the_window() -> None:
    """阈值不是独立配置项：窗口一换，它跟着走。"""
    frozen = freeze_config(load_app_config({"YZY_MAX_TOKENS": "200000"}))

    assert frozen.compact_tokens == 140000


def test_invalid_permission_raises_with_allowed_values() -> None:
    """权限取值非法时报错，并把合法取值列出来，方便直接改。"""
    with pytest.raises(ConfigError, match="read_only"):
        load_app_config({"YZY_PERMISSION": "god_mode"})


def test_invalid_integer_raises() -> None:
    """整数项写了非数字要报错，而不是静默回退默认值。"""
    with pytest.raises(ConfigError, match="必须"):
        load_app_config({"YZY_MAX_TOKENS": "abc"})


def test_blank_value_falls_back_to_default() -> None:
    """空串等同于没配。"""
    config = load_app_config({"YZY_MODEL": "   ", "YZY_MAX_TURNS": ""})

    assert config.model == DEFAULT_MODEL
    assert config.max_turns is None


def test_freeze_priority_explicit_then_session_then_app() -> None:
    """冻结优先级：显式入参 > SessionMeta > AppConfig。"""
    app = load_app_config({"YZY_MODEL": "app-model", "YZY_PERMISSION": "read_only"})
    meta = make_session_meta()
    meta.model = "session-model"
    meta.permission = Permission.EDITABLE

    assert freeze_config(app).model == "app-model"
    assert freeze_config(app, session_meta=meta).model == "session-model"
    assert freeze_config(app, session_meta=meta, model="explicit").model == "explicit"
    assert freeze_config(app, session_meta=meta).permission is Permission.EDITABLE
    assert (
        freeze_config(app, session_meta=meta, permission=Permission.FULL_ACCESS).permission
        is Permission.FULL_ACCESS
    )


def test_tool_result_limit_is_session_level() -> None:
    """工具结果上限跟着会话走：会话有自己的一份，改全局设置不回头动它。"""
    app = load_app_config({"YZY_TOOL_RESULT_MAX_CHARS": "5000"})
    meta = make_session_meta(tool_result_max_chars=99)

    assert freeze_config(app).tool_result_max_chars == 5000
    assert freeze_config(app, session_meta=meta).tool_result_max_chars == 99


def test_frozen_config_is_immutable() -> None:
    """快照建好之后不允许改，避免运行中有人偷偷改配置。"""
    frozen = freeze_config(load_app_config({}))

    with pytest.raises(dataclasses.FrozenInstanceError):
        frozen.model = "another"  # type: ignore[misc]


def test_frozen_snapshot_does_not_follow_later_app_changes() -> None:
    """冻结之后再造一份新配置，不会影响已有快照。"""
    frozen = freeze_config(load_app_config({"YZY_MODEL": "first"}))
    load_app_config({"YZY_MODEL": "second"})

    assert frozen.model == "first"


# ---------------------------------------------------------------------------
# .env 文件
#
# 这组测试**不写磁盘**：解析规则走纯函数 parse_env_text，
# 文件读取用一个固定样例 tests/fixtures/sample.env。
# 原因：受限环境里由子进程创建的目录删不掉，测试不该制造这种垃圾。
# ---------------------------------------------------------------------------

SAMPLE_ENV = Path(__file__).resolve().parent.parent / "fixtures" / "sample.env"


def test_parse_env_text_reads_key_values() -> None:
    """.env 文本被解析成键值对。"""
    assert parse_env_text("YZY_MODEL=from-file\nYZY_MAX_TURNS=7\n") == {
        "YZY_MODEL": "from-file",
        "YZY_MAX_TURNS": "7",
    }


def test_parse_env_text_skips_comments_blanks_and_export_prefix() -> None:
    """注释、空行、export 前缀、非赋值行都不算配置。"""
    text = (
        "# 这是一行注释\n"
        "\n"
        "export YZY_MODEL=deepseek-chat\n"
        "   \n"
        "YZY_MAX_TURNS = 7 \n"
        "这一行没有等号\n"
    )

    assert parse_env_text(text) == {"YZY_MODEL": "deepseek-chat", "YZY_MAX_TURNS": "7"}


def test_parse_env_text_strips_paired_quotes_and_handles_escapes() -> None:
    """成对引号去掉；双引号里的换行转义生效；不成对的引号原样保留。"""
    text = 'A="带空格的 值"\nB=\'单引号 原样\'\nC="换行\\n结束"\nD="引号不成对\n'

    values = parse_env_text(text)

    assert values["A"] == "带空格的 值"
    assert values["B"] == "单引号 原样"
    assert values["C"] == "换行\n结束"
    assert values["D"] == '"引号不成对'


def test_parse_env_text_last_duplicate_wins() -> None:
    """同一个键写多次，以最后一次为准。"""
    assert parse_env_text("K=1\nK=2\n")["K"] == "2"


def test_read_env_file_reads_the_sample_fixture() -> None:
    """样例文件读得出来，说明"文件 -> 文本 -> 解析"这条链是通的。"""
    values = read_env_file(SAMPLE_ENV)

    assert values["YZY_MODEL"] == "sample-model"
    assert values["YZY_MAX_TURNS"] == "7"


def test_read_env_file_tolerates_none_and_missing_file() -> None:
    """路径为 None、或文件不存在，都返回空字典而不是报错。"""
    assert read_env_file(None) == {}
    assert read_env_file(SAMPLE_ENV.parent / "does-not-exist.env") == {}


def test_env_file_values_reach_the_config(
    monkeypatch: pytest.MonkeyPatch, clean_env: pytest.MonkeyPatch
) -> None:
    """loader 会把 .env 的值并进取值来源。"""
    monkeypatch.setattr(config_loader, "read_env_file", lambda path: {"YZY_MODEL": "from-file"})

    assert load_app_config().model == "from-file"


def test_real_environment_wins_over_env_file(
    monkeypatch: pytest.MonkeyPatch, clean_env: pytest.MonkeyPatch
) -> None:
    """真实环境变量优先于 .env——容器与 CI 的注入能盖掉开发机上的配置。"""
    monkeypatch.setattr(config_loader, "read_env_file", lambda path: {"YZY_MODEL": "from-file"})
    clean_env.setenv("YZY_MODEL", "from-env")

    assert load_app_config().model == "from-env"


def test_explicit_env_mapping_ignores_env_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """显式传入 env 映射时只认它、绝不碰 .env——测试要的是确定性。"""

    def must_not_be_called(path: object) -> dict[str, str]:
        raise AssertionError("传了 env 映射就不该去读 .env")

    monkeypatch.setattr(config_loader, "read_env_file", must_not_be_called)

    assert load_app_config({}).model == DEFAULT_MODEL


def test_blank_value_in_env_file_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch, clean_env: pytest.MonkeyPatch
) -> None:
    """留空的项不该把配置写坏，而是回退默认值。"""
    monkeypatch.setattr(
        config_loader,
        "read_env_file",
        lambda path: {"YZY_MODEL": "", "YZY_PERMISSION": ""},
    )

    config = load_app_config()

    assert config.model == DEFAULT_MODEL
    assert config.permission is Permission.EDITABLE


def test_env_file_can_be_disabled_by_blank_var(clean_env: pytest.MonkeyPatch) -> None:
    """YZY_ENV_FILE 设成空串 = 不读任何 .env。"""
    clean_env.setenv("YZY_ENV_FILE", "")

    assert resolve_env_file() is None


def test_env_file_path_priority(clean_env: pytest.MonkeyPatch) -> None:
    """路径优先级：显式入参 > YZY_ENV_FILE > 默认 .env。"""
    assert resolve_env_file() == Path(".env")

    clean_env.setenv("YZY_ENV_FILE", "conf/custom.env")
    assert resolve_env_file() == Path("conf/custom.env")

    assert resolve_env_file("explicit.env") == Path("explicit.env")

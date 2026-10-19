"""极简 .env 解析。

只支持够用的一小撮语法，不做变量展开、不做多行值：

    - 空行、以 # 开头的行：忽略
    - KEY=value；KEY 两侧空白忽略
    - 行首可选的 export 前缀：忽略
    - 值两侧空白忽略；被成对的单引号或双引号包住时去掉引号
    - 双引号内支持三种转义：\\n、\\t、\\\\；单引号内一律原样保留
    - 同一个键出现多次时，以最后一次为准

解析结果只是"候选值"：**真实环境变量优先级更高**，合并规则见 loader.load_app_config。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

# 指定 .env 路径的环境变量。设成空串表示不读任何 .env 文件。
#
# 举例：YZY_ENV_FILE=.env.local
ENV_FILE_VAR = "YZY_ENV_FILE"

# 默认的 .env 文件名，相对当前工作目录。
#
# 举例：".env"
DEFAULT_ENV_FILE = ".env"

# 双引号值里允许的转义序列：写法 -> 实际字符。
#
# 举例：{"n": 换行, "t": 制表符}
_ESCAPES = {"n": "\n", "t": "\t", "\\": "\\"}


def resolve_env_file(
    explicit: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path | None:
    """决定这次读哪个 .env 文件；返回 None 表示不读。

    优先级：显式入参 > 环境变量 YZY_ENV_FILE > 当前目录下的 .env。
    举例：resolve_env_file() -> Path(".env")
    """
    source = os.environ if env is None else env
    if explicit is not None:
        text = str(explicit)
    elif ENV_FILE_VAR in source:
        text = source[ENV_FILE_VAR]
    else:
        text = DEFAULT_ENV_FILE
    if not text.strip():
        return None
    return Path(text.strip())


def read_env_file(path: Path | None) -> dict[str, str]:
    """把 .env 读成字典。路径为 None 或文件不存在时返回空字典，不报错。

    作用：让"没有 .env"和"有 .env 但内容为空"对调用方是同一种情况。
    举例：read_env_file(Path(".env")) -> {"YZY_MODEL": "deepseek-chat"}
    """
    if path is None or not path.is_file():
        return {}
    return parse_env_text(path.read_text(encoding="utf-8"))


def parse_env_text(text: str) -> dict[str, str]:
    """把 .env 文本解析成字典。

    这是纯函数、不碰磁盘，所以解析规则的测试不需要任何临时文件。
    举例：parse_env_text("A=1") -> {"A": "1"}
    """
    values: dict[str, str] = {}
    for raw in text.splitlines():
        parsed = _parse_line(raw)
        if parsed is not None:
            values[parsed[0]] = parsed[1]
    return values


def _parse_line(raw: str) -> tuple[str, str] | None:
    """解析一行；不是赋值行时返回 None。

    举例：_parse_line('export YZY_MODEL="deepseek-chat"')
          -> ("YZY_MODEL", "deepseek-chat")
    """
    line = raw.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith("export "):
        line = line[len("export ") :].lstrip()
    if "=" not in line:
        return None
    key, _, value = line.partition("=")
    key = key.strip()
    if not key:
        return None
    return key, _parse_value(value.strip())


def _parse_value(text: str) -> str:
    """去掉值两侧的成对引号，并处理双引号内的转义。

    举例：_parse_value('"a') -> '"a'（引号不成对，原样返回）
    """
    paired_quotes = len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"')
    if not paired_quotes:
        return text
    inner = text[1:-1]
    if text[0] == "'":
        return inner
    out: list[str] = []
    index = 0
    while index < len(inner):
        char = inner[index]
        if char == "\\" and index + 1 < len(inner) and inner[index + 1] in _ESCAPES:
            out.append(_ESCAPES[inner[index + 1]])
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)

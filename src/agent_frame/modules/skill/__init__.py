"""D3 Skill：可复用的任务指令包 —— 扫描、声明、合并去重、目录注入、正文按需加载。

目录与系统提示词走同一条注入链：每次请求现拼，**不写 Session**。
它**不执行** skill 里的脚本：要跑脚本就是一次普通的工具调用，走 D2 的位点 6/7/8。
"""

from .catalog import CATALOG_PREFIX, render_catalog
from .handler import PRIORITY_CATALOG, build_handlers
from .loader import SKILL_TOOL, make_runner, read_body
from .manifest import SkillManifest, load_manifest, parse_manifest, split_body
from .registry import BUILTIN_ROOT, PROJECT_DIR, SkillRegistry, default_roots
from .scanner import SKILL_FILE, scan

__all__ = [
    "BUILTIN_ROOT",
    "CATALOG_PREFIX",
    "PRIORITY_CATALOG",
    "PROJECT_DIR",
    "SKILL_FILE",
    "SKILL_TOOL",
    "SkillManifest",
    "SkillRegistry",
    "build_handlers",
    "default_roots",
    "load_manifest",
    "make_runner",
    "parse_manifest",
    "read_body",
    "render_catalog",
    "scan",
    "split_body",
]

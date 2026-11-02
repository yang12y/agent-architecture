"""图表：模型用 chart_* 画出来的图，作为一条独立条目进日志。

**只进日志，不进上下文** —— 数值本来就在那次工具调用的参数里，模型不必再吃一遍。
所以投影、压缩、恢复都不用为它改一行；它唯一的作用是给人和前端一个"按 seq 摆位置"的锚点。
"""

from .handler import PRIORITY_CHARTS, build_handlers

__all__ = ["PRIORITY_CHARTS", "build_handlers"]

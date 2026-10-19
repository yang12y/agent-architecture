"""维度 1：Agent Loop。

内核只做三件事：走位点、调模型收流、判定终止。
执行过程中真正发生什么，全看每个位点上注册了哪些 handler。
"""

from .agent import Agent
from .pipeline import HandlerRegistry, SitePipeline
from .termination import Termination, decide_after_turn
from .trace_runner import TraceOutcome, TraceRunner
from .turn_runner import TurnOutcome, TurnRunner

__all__ = [
    "Agent",
    "HandlerRegistry",
    "SitePipeline",
    "Termination",
    "decide_after_turn",
    "TraceOutcome",
    "TraceRunner",
    "TurnOutcome",
    "TurnRunner",
]

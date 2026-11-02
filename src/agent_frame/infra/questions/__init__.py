"""人工提问的进程内实现：把「等一个人选一个」落成一个挂起的 future。"""

from .gate import NO_ANSWERER, QuestionGate

__all__ = ["NO_ANSWERER", "QuestionGate"]

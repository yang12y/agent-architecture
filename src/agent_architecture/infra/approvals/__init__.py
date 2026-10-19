"""人工审批的进程内实现：把「等一个人点一下」落成一个挂起的 future。"""

from .gate import ApprovalGate

__all__ = ["ApprovalGate"]

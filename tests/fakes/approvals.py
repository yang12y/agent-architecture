"""人工审批端口的假件。"""

from __future__ import annotations

from yzy_agent.contracts import ApprovalDecision, ApprovalRequest, ApprovalResult


class FakeApprovals:
    """按预设答案回话的审批假件。**不等人**：答案在构造时就给定了。

    举例：approvals = FakeApprovals(ApprovalDecision.APPROVE)
    """

    def __init__(self, *decisions: ApprovalDecision) -> None:
        """给定每次审批的答案；一个都不给就是一律拒绝。

        举例：FakeApprovals(ApprovalDecision.APPROVE, ApprovalDecision.DENY)
        """
        # 按顺序给出的回执；请求超出这个长度时重复最后一个。
        #
        # 举例：[ApprovalDecision.APPROVE]
        self.decisions: list[ApprovalDecision] = list(decisions) or [ApprovalDecision.DENY]

        # 收到的审批请求，用来断言参数与次数。
        #
        # 举例：[ApprovalRequest(request_id="appr_1", tool_name="write", ...)]
        self.requests: list[ApprovalRequest] = []

    async def request(self, request: ApprovalRequest) -> ApprovalResult:
        """见 contracts.ApprovalProvider.request。

        举例：result = await approvals.request(request)
        """
        self.requests.append(request)
        index = min(len(self.requests) - 1, len(self.decisions) - 1)
        return ApprovalResult(
            request_id=request.request_id, decision=self.decisions[index], comment=None
        )

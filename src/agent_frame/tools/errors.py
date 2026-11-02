"""业务工具层的错误：都是 `AgentError` 子类，由执行器收成给模型看的一句话。"""

from __future__ import annotations

from agent_frame.contracts import AgentError


class RequestContextError(AgentError):
    """这次请求缺了工具必需的前置数据（凭证、租户、机构…）。

    **不是参数错**：参数是模型给的，前置数据是调用方给的；说清楚缺哪一样，
    模型才知道该找用户要，而不是换个参数重试。

    举例：raise RequestContextError("当前请求缺少 tenant 请求头")
    """


class ToolDataError(AgentError):
    """工具算不出可信结果：数据对不上、或者没取全。

    **宁可失败也不给半份结果**：缺失清单、完整率这类结论一旦基于残缺数据，
    看起来像真的，比报错更糟。

    举例：raise ToolDataError("无法把模板参数 设备图片信息（device_picture_info）对照到台账字段")
    """

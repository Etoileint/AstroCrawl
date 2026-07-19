"""AI 错误层次 — 9 个通用异常类，Provider 无关。

各 Provider 模块内部负责 SDK 异常映射（如 astrocrawl.ai.providers.openai._map_error）。
核心只定义类型——不 import 任何 SDK。
"""

from __future__ import annotations


class AIError(Exception):
    """所有 AI 错误的基类。"""

    pass


class AIAuthError(AIError):
    """认证失败 (401/403)——不重试。"""

    pass


class AIRateLimitError(AIError):
    """速率限制 (429)——可重试。"""

    pass


class AITimeoutError(AIError):
    """请求超时——可重试。"""

    pass


class AIConnectionError(AIError):
    """网络连接失败——可重试。"""

    pass


class AIServerError(AIError):
    """服务端错误 (5xx)——可重试。"""

    pass


class AIContentFilterError(AIError):
    """内容过滤——不重试。"""

    pass


class AIInvalidRequestError(AIError):
    """无效请求 (400)——不重试。"""

    pass


class AIProviderUnavailableError(AIError):
    """Provider 未安装或 SDK import 失败——不重试。"""

    pass

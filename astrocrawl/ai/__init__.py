"""astrocrawl/ai/ — 领域无关的通用 AI 底座（ADR-0006 多 Provider 架构）。

能力：多 Provider 统一门面 / RateLimiter / UsageTracker / OTel 可观测性 /
      Tool Calling / Embeddings / 5 事件流式归一化 / 优雅降级 /
      OutputConstraint 结构化输出 (ADR-0008)。

Provider：openai (默认) / anthropic / google（pip install astrocrawl[openai] 等）。
本地模型：provider="openai" + base_url="http://localhost:11434/v1"。
"""

from __future__ import annotations

from astrocrawl.ai._client import AIClient
from astrocrawl.ai._config import AIConfig, GenerationParams
from astrocrawl.ai._constraint import OutputConstraint
from astrocrawl.ai._errors import (
    AIAuthError,
    AIConnectionError,
    AIContentFilterError,
    AIError,
    AIInvalidRequestError,
    AIProviderUnavailableError,
    AIRateLimitError,
    AIServerError,
    AITimeoutError,
)
from astrocrawl.ai._observability import AIHook, LoggingHook
from astrocrawl.ai._profile import AIProfile
from astrocrawl.ai._provider_registry import list_installed_providers
from astrocrawl.ai._rate_limiter import RateLimitConfig, get_rule_gen_limiter
from astrocrawl.ai._types import (
    CallContext,
    ChatMessage,
    ChatResponse,
    EmbedResult,
    Role,
    StreamEvent,
    StreamFinish,
    StreamText,
    StreamToolCall,
    StreamToolCallDelta,
    StreamToolCallStart,
    TokenUsage,
    ToolCall,
)

__all__ = [
    # config
    "AIConfig",
    "GenerationParams",
    "OutputConstraint",
    "RateLimitConfig",
    # profile
    "AIProfile",
    # client
    "AIClient",
    # types
    "Role",
    "ChatMessage",
    "ChatResponse",
    "EmbedResult",
    "StreamEvent",
    "StreamText",
    "StreamToolCallStart",
    "StreamToolCallDelta",
    "StreamToolCall",
    "StreamFinish",
    "CallContext",
    "TokenUsage",
    "ToolCall",
    # errors
    "AIError",
    "AIAuthError",
    "AIRateLimitError",
    "AITimeoutError",
    "AIConnectionError",
    "AIServerError",
    "AIContentFilterError",
    "AIInvalidRequestError",
    "AIProviderUnavailableError",
    # observability
    "AIHook",
    "LoggingHook",
    # provider registry
    "list_installed_providers",
    # rate limiter
    "get_rule_gen_limiter",
]

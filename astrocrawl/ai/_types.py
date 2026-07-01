"""AI 模块共享类型 — ChatMessage, TokenUsage, ChatResponse, StreamEvent, CallContext, EmbedResult。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Union


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class ChatMessage:
    """OpenAI 兼容的聊天消息（ADR-0006 #5: tool_call_id + name）。"""

    role: Role
    content: str
    tool_call_id: str | None = None
    name: str | None = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"role": self.role.value, "content": self.content}
        if self.tool_call_id is not None:
            d["tool_call_id"] = self.tool_call_id
        if self.name is not None:
            d["name"] = self.name
        return d


@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class ToolCall:
    """归一化的 tool call——arguments 为已解析 dict（ADR-0006 #5）。"""

    id: str
    name: str
    arguments: dict


@dataclass
class ChatResponse:
    """chat completion 的归一化返回。ADR-0006 #5: tool_calls + raw 删除。"""

    content: str
    model: str = ""
    usage: Optional[TokenUsage] = None
    finish_reason: str = "stop"
    tool_calls: list[ToolCall] | None = None


# ── StreamEvent v2 — 5 事件 Discriminated Union（ADR-0006 #6）───────────


@dataclass
class StreamText:
    text: str


@dataclass
class StreamToolCallStart:
    id: str
    name: str


@dataclass
class StreamToolCallDelta:
    id: str
    arguments_delta: str


@dataclass
class StreamToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class StreamFinish:
    finish_reason: str
    usage: TokenUsage | None = None


StreamEvent = Union[StreamText, StreamToolCallStart, StreamToolCallDelta, StreamToolCall, StreamFinish]


@dataclass
class CallContext:
    """单次 API 调用的上下文 (observability)。ADR-0006 #8: 补全 OTel 字段。"""

    operation: str = "chat"  # "chat" | "embed" — OTel gen_ai.operation.name
    model: str = ""
    messages_count: int = 0
    params: Dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0
    retry_count: int = 0
    error: Optional[str] = None
    system: str = ""
    request_model: str = ""
    response_model: str = ""
    stream: bool = False


@dataclass
class EmbedResult:
    """Embeddings 的归一化返回（ADR-0006 #7）。"""

    vectors: list[list[float]]
    model: str
    usage: TokenUsage | None = None

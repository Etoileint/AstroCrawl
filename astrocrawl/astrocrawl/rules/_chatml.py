"""ChatML 序列化 + tiktoken Token 统计。

tiktoken 为可选依赖（对标 orjson 优雅降级模式）——导入失败时 Token 统计返回 0，
不阻断功能。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from astrocrawl.ai._types import ChatMessage

from astrobase import LogfmtLogger

logger = LogfmtLogger("astrocrawl.rules.chatml")


def serialize_chatml(messages: list[ChatMessage]) -> str:
    parts: list[str] = []
    for msg in messages:
        header = msg.role.value
        if msg.tool_call_id is not None:
            header += f" to={msg.tool_call_id}"
        elif msg.name is not None:
            header += f" name={msg.name}"
        parts.append(f"<|im_start|>{header}\n{msg.content}<|im_end|>")
    return "\n".join(parts)


def count_tokens(text: str, model: str = "gpt-4o-mini") -> int:
    if not text:
        return 0

    try:
        import tiktoken
    except ImportError:
        logger.warning("tiktoken_unavailable")
        return 0

    try:
        enc = tiktoken.encoding_for_model(model)
    except (KeyError, ValueError):
        enc = tiktoken.get_encoding("cl100k_base")

    try:
        return len(enc.encode(text))
    except Exception:
        logger.warning("token_count_error", model=model)
        return 0

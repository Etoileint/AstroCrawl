"""UsageTracker — 会话级 Token 用量累计。

请求级归一化：三个 SDK 的 usage 结构不同 → 统一 TokenUsage。
会话级累计：total_prompt_tokens / total_completion_tokens / total_tokens。
不做费用计算——不硬编码价格表。
"""

from __future__ import annotations

import threading

from astrocrawl.ai._types import TokenUsage


class UsageTracker:
    """会话级 Token 用量追踪器。线程安全。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._total_tokens = 0

    def record(self, usage: TokenUsage | None) -> None:
        """记录单次请求的 token 消耗。"""
        if usage is None:
            return
        with self._lock:
            self._prompt_tokens += usage.prompt_tokens
            self._completion_tokens += usage.completion_tokens
            self._total_tokens += usage.total_tokens

    @property
    def usage(self) -> TokenUsage:
        """返回截至当前的累计 Token 用量。"""
        with self._lock:
            return TokenUsage(
                prompt_tokens=self._prompt_tokens,
                completion_tokens=self._completion_tokens,
                total_tokens=self._total_tokens,
            )

    def reset(self) -> None:
        """重置累计值。"""
        with self._lock:
            self._prompt_tokens = 0
            self._completion_tokens = 0
            self._total_tokens = 0

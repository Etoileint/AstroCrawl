"""AIHook Protocol — 可观测性钩子 (logging, tracing, metrics 注入点)。"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from astrocrawl.ai._types import CallContext, ChatResponse

logger = logging.getLogger("astrocrawl.ai")


@runtime_checkable
class AIHook(Protocol):
    """AI 调用生命周期钩子。所有方法均为同步（快速返回，不阻塞）。"""

    def on_request(self, ctx: CallContext) -> None:
        """请求发送前调用。"""
        ...

    def on_response(self, ctx: CallContext, response: ChatResponse) -> None:
        """成功收到响应后调用。"""
        ...

    def on_error(self, ctx: CallContext, error: Exception) -> None:
        """请求失败时调用。"""
        ...

    def on_retry(self, ctx: CallContext, error: Exception, attempt: int, delay: float) -> None:
        """SDK retry 耗尽后调用（仅一次）——ADR-0006 #8 语义修正。

        成功路径不触发。SDK 内置 retry 对我们是黑盒——只感知最终耗尽。
        """
        ...


class LoggingHook:
    """默认日志钩子 — 使用 logfmt 风格 event 命名（OTel GenAI 语义约定对齐）。"""

    def on_request(self, ctx: CallContext) -> None:
        logger.debug("event=gen_ai.%s.request model=%s messages=%d", ctx.operation, ctx.model, ctx.messages_count)

    def on_response(self, ctx: CallContext, response: ChatResponse) -> None:
        tokens = ""
        if response.usage:
            tokens = f" tokens_in={response.usage.prompt_tokens} tokens_out={response.usage.completion_tokens}"
        logger.debug(
            "event=gen_ai.%s.response model=%s duration_ms=%.1f finish=%s%s",
            ctx.operation,
            response.model,
            ctx.duration_ms,
            response.finish_reason,
            tokens,
        )

    def on_error(self, ctx: CallContext, error: Exception) -> None:
        logger.warning(
            "event=gen_ai.%s.error model=%s duration_ms=%.1f error=%s", ctx.operation, ctx.model, ctx.duration_ms, error
        )

    def on_retry(self, ctx: CallContext, error: Exception, attempt: int, delay: float) -> None:
        logger.info(
            "event=gen_ai.%s.retry_exhausted model=%s attempt=%d delay=%.1fs error=%s",
            ctx.operation,
            ctx.model,
            attempt,
            delay,
            error,
        )

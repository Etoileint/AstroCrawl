"""AIHook Protocol — 可观测性钩子 (logging, tracing, metrics 注入点)。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from astrobasis import LogfmtLogger

if TYPE_CHECKING:
    from astrocrawl.ai._types import CallContext, ChatResponse

logger = LogfmtLogger("astrocrawl.ai")


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
        logger.debug(f"gen_ai.{ctx.operation}.request", model=ctx.model, messages=ctx.messages_count)

    def on_response(self, ctx: CallContext, response: ChatResponse) -> None:
        logger.debug(
            f"gen_ai.{ctx.operation}.response",
            model=response.model,
            duration_ms=ctx.duration_ms,
            finish=response.finish_reason,
            tokens_in=response.usage.prompt_tokens if response.usage else None,
            tokens_out=response.usage.completion_tokens if response.usage else None,
        )

    def on_error(self, ctx: CallContext, error: Exception) -> None:
        logger.warning(f"gen_ai.{ctx.operation}.error", model=ctx.model, duration_ms=ctx.duration_ms, error=error)

    def on_retry(self, ctx: CallContext, error: Exception, attempt: int, delay: float) -> None:
        logger.info(
            f"gen_ai.{ctx.operation}.retry_exhausted",
            model=ctx.model,
            attempt=attempt,
            delay=delay,
            error=error,
        )

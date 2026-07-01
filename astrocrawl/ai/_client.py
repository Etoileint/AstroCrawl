"""AIClient — 统一门面（ADR-0006 两层架构 + ADR-0008 结构化输出）。

Provider 自适应：内部 Dispatch → entry point 自动发现 → Provider 懒加载。
横切能力：RateLimiter → OutputFormat 能力感知降级 → Provider Call → UsageTracker → Observability。
"""

from __future__ import annotations

import logging
import time
from dataclasses import replace
from typing import Any, AsyncIterator, Iterator, List, Optional

from astrocrawl.ai._config import AIConfig, GenerationParams, _resolve_params, _ResolvedParams
from astrocrawl.ai._errors import AIConnectionError, AIError, AIRateLimitError, AIServerError, AITimeoutError
from astrocrawl.ai._observability import AIHook, LoggingHook
from astrocrawl.ai._provider import _ChatProvider, _SupportsEmbedding
from astrocrawl.ai._rate_limiter import RateLimitConfig, RateLimiter
from astrocrawl.ai._types import (
    CallContext,
    ChatMessage,
    ChatResponse,
    EmbedResult,
    StreamEvent,
    StreamFinish,
    TokenUsage,
)
from astrocrawl.ai._usage_tracker import UsageTracker

logger = logging.getLogger("astrocrawl.ai")


class _StreamSession:
    """一次流式调用的完整生命周期。

    finalize() 仅在流被完全消费时触发；调用方提前终止流（break / GeneratorExit）
    时 finalize() 不被调用——不完整的 token 用量会误导 observability。
    """

    def __init__(self, ctx: CallContext) -> None:
        self.ctx = ctx
        self._usage: TokenUsage | None = None
        self._finish_reason: str = "stop"

    def consume(self, event: StreamEvent) -> StreamEvent:
        if isinstance(event, StreamFinish):
            if event.usage is not None:
                self._usage = event.usage
            self._finish_reason = event.finish_reason
        return event

    def finalize(self, tracker: UsageTracker, hooks: list[AIHook]) -> None:
        self.ctx.response_model = self.ctx.model
        tracker.record(self._usage)
        response = ChatResponse(
            content="",
            model=self.ctx.model,
            usage=self._usage,
            finish_reason=self._finish_reason,
        )
        for hook in hooks:
            try:
                hook.on_response(self.ctx, response)
            except Exception:
                pass


class AIClient:
    """通用 AI 客户端（sync + async），Provider 自适应。

    Usage::

        config = AIConfig(api_key="sk-...", default_model="gpt-4o-mini")
        client = AIClient(config)

        # sync
        resp = client.chat([ChatMessage(Role.USER, "Hello")])

        # async
        resp = await client.achat([ChatMessage(Role.USER, "Hello")])

        # async context manager
        async with AIClient(config) as client:
            resp = await client.achat([...])
    """

    def __init__(
        self,
        config: AIConfig,
        hooks: Optional[List[AIHook]] = None,
        rate_limit: Optional[RateLimitConfig] = None,
        proxy_url: str | None = None,
        **provider_kwargs: Any,
    ) -> None:
        self._config = config
        self._hooks: List[AIHook] = hooks or [LoggingHook()]
        self._rate_limiter = RateLimiter(rate_limit if rate_limit is not None else RateLimitConfig())
        self._usage_tracker = UsageTracker()

        if proxy_url:
            import httpx

            provider_kwargs["_http_client"] = httpx.Client(proxy=proxy_url)
            provider_kwargs["_async_http_client"] = httpx.AsyncClient(proxy=proxy_url)

        from astrocrawl.ai._provider_registry import _discover_provider

        self._provider: _ChatProvider = _discover_provider(
            config.provider or "openai",
            config,
            **provider_kwargs,
        )

    @property
    def config(self) -> AIConfig:
        return self._config

    @property
    def usage(self) -> TokenUsage:
        """返回截至当前的累计 Token 用量。"""
        return self._usage_tracker.usage

    # ── sync chat ─────────────────────────────────────────────────────

    def chat(
        self,
        messages: List[ChatMessage],
        tools: list[dict] | None = None,
        params: Optional[GenerationParams] = None,
    ) -> ChatResponse:
        """同步单轮对话。SDK 内置 retry 接管。"""
        resolved = self._resolve(params)
        ctx = self._build_context(messages, resolved)
        self._notify_request(ctx)
        start = time.monotonic()

        try:
            with self._rate_limiter.acquire_sync():
                response = self._provider.chat(messages, tools, resolved)
        except AIError as e:
            ctx.duration_ms = (time.monotonic() - start) * 1000
            ctx.error = str(e)
            if isinstance(e, (AIRateLimitError, AITimeoutError, AIConnectionError, AIServerError)):
                ctx.retry_count = 1
                self._notify_retry(ctx, e, 1, 0)
            self._notify_error(ctx, e)
            raise
        except Exception as e:
            ctx.duration_ms = (time.monotonic() - start) * 1000
            ctx.error = str(e)
            self._notify_error(ctx, e)
            raise

        ctx.duration_ms = (time.monotonic() - start) * 1000
        ctx.response_model = response.model
        self._usage_tracker.record(response.usage)
        self._notify_response(ctx, response)
        return response

    def chat_stream(
        self,
        messages: List[ChatMessage],
        tools: list[dict] | None = None,
        params: Optional[GenerationParams] = None,
    ) -> Iterator[StreamEvent]:
        """同步流式对话 (SSE)。SDK 内置 retry。"""
        resolved = self._resolve(params)
        ctx = self._build_context(messages, resolved, stream=True)
        self._notify_request(ctx)
        session = _StreamSession(ctx)
        start = time.monotonic()

        try:
            with self._rate_limiter.acquire_sync():
                for chunk in self._provider.chat_stream(messages, tools, resolved):
                    yield session.consume(chunk)
        except AIError as e:
            ctx.duration_ms = (time.monotonic() - start) * 1000
            ctx.error = str(e)
            if isinstance(e, (AIRateLimitError, AITimeoutError, AIConnectionError, AIServerError)):
                ctx.retry_count = 1
                self._notify_retry(ctx, e, 1, 0)
            self._notify_error(ctx, e)
            raise
        except Exception as e:
            ctx.duration_ms = (time.monotonic() - start) * 1000
            ctx.error = str(e)
            self._notify_error(ctx, e)
            raise
        else:
            ctx.duration_ms = (time.monotonic() - start) * 1000
            session.finalize(self._usage_tracker, self._hooks)

    # ── async chat ────────────────────────────────────────────────────

    async def achat(
        self,
        messages: List[ChatMessage],
        tools: list[dict] | None = None,
        params: Optional[GenerationParams] = None,
    ) -> ChatResponse:
        """异步单轮对话。SDK 内置 retry 接管。"""
        resolved = self._resolve(params)
        ctx = self._build_context(messages, resolved)
        self._notify_request(ctx)
        start = time.monotonic()

        try:
            async with self._rate_limiter.acquire():
                response = await self._provider.achat(messages, tools, resolved)
        except AIError as e:
            ctx.duration_ms = (time.monotonic() - start) * 1000
            ctx.error = str(e)
            if isinstance(e, (AIRateLimitError, AITimeoutError, AIConnectionError, AIServerError)):
                ctx.retry_count = 1
                self._notify_retry(ctx, e, 1, 0)
            self._notify_error(ctx, e)
            raise
        except Exception as e:
            ctx.duration_ms = (time.monotonic() - start) * 1000
            ctx.error = str(e)
            self._notify_error(ctx, e)
            raise

        ctx.duration_ms = (time.monotonic() - start) * 1000
        ctx.response_model = response.model
        self._usage_tracker.record(response.usage)
        self._notify_response(ctx, response)
        return response

    async def achat_stream(
        self,
        messages: List[ChatMessage],
        tools: list[dict] | None = None,
        params: Optional[GenerationParams] = None,
    ) -> AsyncIterator[StreamEvent]:
        """异步流式对话 (SSE)。SDK 内置 retry。"""
        resolved = self._resolve(params)
        ctx = self._build_context(messages, resolved, stream=True)
        self._notify_request(ctx)
        session = _StreamSession(ctx)
        start = time.monotonic()

        try:
            async with self._rate_limiter.acquire():
                async for chunk in self._provider.achat_stream(messages, tools, resolved):  # type: ignore[attr-defined]  # mypy async-gen Protocol false positive
                    yield session.consume(chunk)
        except AIError as e:
            ctx.duration_ms = (time.monotonic() - start) * 1000
            ctx.error = str(e)
            if isinstance(e, (AIRateLimitError, AITimeoutError, AIConnectionError, AIServerError)):
                ctx.retry_count = 1
                self._notify_retry(ctx, e, 1, 0)
            self._notify_error(ctx, e)
            raise
        except Exception as e:
            ctx.duration_ms = (time.monotonic() - start) * 1000
            ctx.error = str(e)
            self._notify_error(ctx, e)
            raise
        else:
            ctx.duration_ms = (time.monotonic() - start) * 1000
            session.finalize(self._usage_tracker, self._hooks)

    # ── embeddings ───────────────────────────────────────────────────

    async def embed(
        self,
        texts: str | list[str],
        model: str | None = None,
    ) -> EmbedResult:
        """Embeddings — 完整 pipeline（RateLimit → Call → UsageTrack + Observability）。

        Raises:
            AIError: Provider 不支持 Embeddings
        """
        if not isinstance(self._provider, _SupportsEmbedding):
            from astrocrawl.ai._errors import AIError as _AIError

            raise _AIError(f"Provider '{self._config.provider}' 不支持 Embeddings")

        texts_list = [texts] if isinstance(texts, str) else texts
        mdl = model or self._config.default_model

        ctx = CallContext(
            operation="embed",
            model=mdl,
            messages_count=len(texts_list),
            system=self._config.provider,
        )
        self._notify_request(ctx)
        start = time.monotonic()

        try:
            async with self._rate_limiter.acquire():
                result = await self._provider.embed(texts_list, mdl)
        except AIError as e:
            ctx.duration_ms = (time.monotonic() - start) * 1000
            ctx.error = str(e)
            if isinstance(e, (AIRateLimitError, AITimeoutError, AIConnectionError, AIServerError)):
                ctx.retry_count = 1
                self._notify_retry(ctx, e, 1, 0)
            self._notify_error(ctx, e)
            raise
        except Exception as e:
            ctx.duration_ms = (time.monotonic() - start) * 1000
            ctx.error = str(e)
            self._notify_error(ctx, e)
            raise

        ctx.duration_ms = (time.monotonic() - start) * 1000
        ctx.response_model = result.model
        self._usage_tracker.record(result.usage)
        self._notify_response(
            ctx,
            ChatResponse(
                content="",
                model=result.model,
                usage=result.usage,
                finish_reason="stop",
            ),
        )
        return result

    # ── convenience ───────────────────────────────────────────────────

    async def aclose(self) -> None:
        """关闭底层 Provider 客户端（幂等）。"""
        await self._provider.aclose()

    async def __aenter__(self) -> AIClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()

    # ── internal ──────────────────────────────────────────────────────

    def _resolve(self, params: GenerationParams | None) -> _ResolvedParams:
        resolved = _resolve_params(
            params,
            default_model=self._config.default_model,
            default_temperature=self._config.default_temperature,
            default_max_tokens=self._config.default_max_tokens,
        )
        return self._resolve_output_format(resolved)

    def _resolve_output_format(self, resolved: _ResolvedParams) -> _ResolvedParams:
        """ADR-0008: 能力感知降级。若 Provider 不支持请求的输出格式，自动降级到最强可用格式。"""
        if resolved.output is None:
            return resolved
        caps: frozenset[str] = getattr(self._provider, "supported_output_formats", frozenset())
        if not caps or resolved.output.format in caps:
            return resolved
        degradation = ["json_schema", "json_object"]
        try:
            idx = degradation.index(resolved.output.format)
        except ValueError:
            idx = -1
        for fallback in degradation[idx + 1 :]:
            if fallback in caps:
                logger.warning(
                    "event=output_format_degraded from=%s to=%s provider=%s",
                    resolved.output.format,
                    fallback,
                    self._config.provider,
                )
                return replace(
                    resolved,
                    output=replace(resolved.output, format=fallback, json_schema=None),
                )
        logger.warning(
            "event=output_format_disabled provider=%s unsupported=%s",
            self._config.provider,
            resolved.output.format,
        )
        return replace(resolved, output=None)

    def _build_context(
        self, messages: List[ChatMessage], resolved: _ResolvedParams, stream: bool = False
    ) -> CallContext:
        params: dict[str, object] = {"temperature": resolved.temperature, "max_tokens": resolved.max_tokens}
        if resolved.top_p is not None:
            params["top_p"] = resolved.top_p
        if resolved.seed is not None:
            params["seed"] = resolved.seed
        if resolved.output is not None:
            params["output_format"] = resolved.output.format
        return CallContext(
            model=resolved.model,
            messages_count=len(messages),
            params=params,
            system=self._config.provider,
            request_model=resolved.model,
            stream=stream,
        )

    # ── hook dispatch ─────────────────────────────────────────────────

    def _notify_request(self, ctx: CallContext) -> None:
        for hook in self._hooks:
            try:
                hook.on_request(ctx)
            except Exception:
                pass

    def _notify_response(self, ctx: CallContext, response: ChatResponse) -> None:
        for hook in self._hooks:
            try:
                hook.on_response(ctx, response)
            except Exception:
                pass

    def _notify_error(self, ctx: CallContext, error: Exception) -> None:
        for hook in self._hooks:
            try:
                hook.on_error(ctx, error)
            except Exception:
                pass

    def _notify_retry(self, ctx: CallContext, error: Exception, attempt: int, delay: float) -> None:
        for hook in self._hooks:
            try:
                hook.on_retry(ctx, error, attempt, delay)
            except Exception:
                pass

"""OpenAI Provider — _ChatProvider + _SupportsEmbedding，OpenAI SDK 原生 sync+async 双路径。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, AsyncIterator, Dict, Iterator

from astrocrawl.ai._errors import (
    AIAuthError,
    AIConnectionError,
    AIContentFilterError,
    AIError,
    AIInvalidRequestError,
    AIRateLimitError,
    AIServerError,
    AITimeoutError,
)
from astrocrawl.ai._types import (
    ChatMessage,
    ChatResponse,
    StreamEvent,
    StreamFinish,
    StreamText,
    StreamToolCall,
    StreamToolCallDelta,
    StreamToolCallStart,
    TokenUsage,
)
from astrocrawl.utils.logging import LogfmtLogger

if TYPE_CHECKING:
    from astrocrawl.ai._config import AIConfig, _ResolvedParams
    from astrocrawl.ai._provider import _ChatProvider

logger = LogfmtLogger("astrocrawl.ai.openai")

# ADR-0008: OpenAI native json_object + json_schema. Custom endpoints (vLLM/Ollama) conservative json_object only.
_STRUCTURED_OUTPUT_MODES = frozenset({"json_object", "json_schema"})


def list_models(base_url: str, api_key: str, timeout: float) -> list[str]:
    """Return available OpenAI model IDs. Raises on failure; caller catches."""
    from openai import OpenAI

    client = OpenAI(
        base_url=base_url or "https://api.openai.com/v1",
        api_key=api_key,
        timeout=timeout,
    )
    return [m.id for m in client.models.list()]


def create_provider(config: AIConfig, **provider_kwargs: Any) -> _ChatProvider:
    """Entry point callable — invoked by AIClient via _provider_registry."""
    return OpenAIClient(
        api_key=config.api_key,
        base_url=config.base_url or "https://api.openai.com/v1",
        timeout=config.timeout,
        max_retries=config.max_retries,
        **provider_kwargs,
    )


# ═══════════════════════════════════════════════════════════════════════
# Error mapping
# ═══════════════════════════════════════════════════════════════════════


def _map_error(exc: Exception) -> AIError:
    """Map openai SDK exception to AIError subclass."""
    msg = str(exc)

    try:
        from openai import (
            APIConnectionError,
            APIStatusError,
            APITimeoutError,
            AuthenticationError,
            BadRequestError,
            InternalServerError,
            PermissionDeniedError,
            RateLimitError,
            UnprocessableEntityError,
        )
    except ImportError:
        return AIError(msg)

    if isinstance(exc, AuthenticationError):
        return AIAuthError(msg)
    if isinstance(exc, PermissionDeniedError):
        return AIAuthError(msg)
    if isinstance(exc, RateLimitError):
        return AIRateLimitError(msg)
    if isinstance(exc, APITimeoutError):
        return AITimeoutError(msg)
    if isinstance(exc, APIConnectionError):
        return AIConnectionError(msg)
    if isinstance(exc, InternalServerError):
        return AIServerError(msg)
    if isinstance(exc, (BadRequestError, UnprocessableEntityError)):
        if "content_filter" in msg.lower() or "content_policy" in msg.lower():
            return AIContentFilterError(msg)
        return AIInvalidRequestError(msg)
    if isinstance(exc, APIStatusError):
        if exc.status_code == 429:
            return AIRateLimitError(msg)
        if exc.status_code and 500 <= exc.status_code < 600:
            return AIServerError(msg)
        return AIServerError(msg)

    return AIError(msg)


# ═══════════════════════════════════════════════════════════════════════
# OpenAIClient
# ═══════════════════════════════════════════════════════════════════════


class OpenAIClient:
    """OpenAI Provider — _ChatProvider + _SupportsEmbedding (sync+async dual-path)."""

    provider_name = "openai"

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 60.0,
        max_retries: int = 2,
        **kwargs: Any,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._timeout = timeout
        self._max_retries = max_retries
        self._http_client: Any = kwargs.get("_http_client")
        self._async_http_client: Any = kwargs.get("_async_http_client")
        self._sync_client: Any = None
        self._async_client: Any = None

    supported_output_formats: frozenset[str] = _STRUCTURED_OUTPUT_MODES

    # ── _ChatProvider.chat ─────────────────────────────────────────────

    def chat(
        self,
        messages: list[ChatMessage],
        tools: list[dict] | None,
        params: Any,
    ) -> ChatResponse:
        kwargs = self._build_request_kwargs(messages, tools, params, stream=False)
        client = self._get_sync_client()
        try:
            response = client.chat.completions.create(**kwargs)
        except Exception as e:
            raise _map_error(e) from e
        return self._parse_response(response)

    def chat_stream(
        self,
        messages: list[ChatMessage],
        tools: list[dict] | None,
        params: Any,
    ) -> Iterator[StreamEvent]:
        kwargs = self._build_request_kwargs(messages, tools, params, stream=True)
        client = self._get_sync_client()
        try:
            response = client.chat.completions.create(**kwargs)
        except Exception as e:
            raise _map_error(e) from e

        for chunk in response:
            choice = chunk.choices[0] if chunk.choices else None
            if choice is None:
                continue
            delta = choice.delta
            if delta and delta.content:
                yield StreamText(text=delta.content)
            if delta and delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index or 0
                    state = self._get_tool_state(idx)
                    if tc.id and not state["id"]:
                        state["id"] = tc.id
                    if tc.function:
                        if tc.function.name and not state["name"]:
                            state["name"] = tc.function.name
                            yield StreamToolCallStart(id=state["id"] or "", name=state["name"])
                        if tc.function.arguments:
                            state["partial"] += tc.function.arguments
                            yield StreamToolCallDelta(id=state["id"] or "", arguments_delta=tc.function.arguments)
            if choice.finish_reason:
                for s in self._flush_tool_states():
                    yield s
                usage = None
                if hasattr(chunk, "usage") and chunk.usage:
                    usage = TokenUsage(
                        prompt_tokens=chunk.usage.prompt_tokens or 0,
                        completion_tokens=chunk.usage.completion_tokens or 0,
                        total_tokens=chunk.usage.total_tokens or 0,
                    )
                yield StreamFinish(finish_reason=choice.finish_reason, usage=usage)

    # ── _ChatProvider.achat ────────────────────────────────────────────

    async def achat(
        self,
        messages: list[ChatMessage],
        tools: list[dict] | None,
        params: Any,
    ) -> ChatResponse:
        kwargs = self._build_request_kwargs(messages, tools, params, stream=False)
        client = self._get_async_client()
        try:
            response = await client.chat.completions.create(**kwargs)
        except Exception as e:
            raise _map_error(e) from e
        return self._parse_response(response)

    async def achat_stream(
        self,
        messages: list[ChatMessage],
        tools: list[dict] | None,
        params: _ResolvedParams,
    ) -> AsyncIterator[StreamEvent]:
        kwargs = self._build_request_kwargs(messages, tools, params, stream=True)
        client = self._get_async_client()
        try:
            response = await client.chat.completions.create(**kwargs)
        except Exception as e:
            raise _map_error(e) from e

        async for chunk in response:
            choice = chunk.choices[0] if chunk.choices else None
            if choice is None:
                continue
            delta = choice.delta
            if delta and delta.content:
                yield StreamText(text=delta.content)
            if delta and delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index or 0
                    state = self._get_tool_state(idx)
                    if tc.id and not state["id"]:
                        state["id"] = tc.id
                    if tc.function:
                        if tc.function.name and not state["name"]:
                            state["name"] = tc.function.name
                            yield StreamToolCallStart(id=state["id"] or "", name=state["name"])
                        if tc.function.arguments:
                            state["partial"] += tc.function.arguments
                            yield StreamToolCallDelta(id=state["id"] or "", arguments_delta=tc.function.arguments)
            if choice.finish_reason:
                for s in self._flush_tool_states():
                    yield s
                usage = None
                if hasattr(chunk, "usage") and chunk.usage:
                    usage = TokenUsage(
                        prompt_tokens=chunk.usage.prompt_tokens or 0,
                        completion_tokens=chunk.usage.completion_tokens or 0,
                        total_tokens=chunk.usage.total_tokens or 0,
                    )
                yield StreamFinish(finish_reason=choice.finish_reason, usage=usage)

    # ── _ChatProvider.aclose ───────────────────────────────────────────

    async def aclose(self) -> None:
        """Close underlying HTTP clients (idempotent)."""
        if self._async_client is not None:
            try:
                await self._async_client.close()
            except Exception:
                pass
            self._async_client = None
        if self._sync_client is not None:
            try:
                self._sync_client.close()
            except Exception:
                pass
            self._sync_client = None

    def close(self) -> None:
        """Close underlying sync HTTP client (idempotent)."""
        if self._sync_client is not None:
            try:
                self._sync_client.close()
            except Exception:
                pass
            self._sync_client = None

    # ── _SupportsEmbedding.embed ───────────────────────────────────────

    async def embed(self, texts: list[str], model: str) -> Any:
        """OpenAI Embeddings API."""
        client = self._get_async_client()
        try:
            response = await client.embeddings.create(model=model, input=texts)
        except Exception as e:
            raise _map_error(e) from e

        from astrocrawl.ai._types import EmbedResult as _EmbedResult
        from astrocrawl.ai._types import TokenUsage as _TokenUsage

        vectors = [d.embedding for d in response.data]
        usage = None
        if response.usage:
            usage = _TokenUsage(
                prompt_tokens=response.usage.prompt_tokens or 0,
                completion_tokens=0,
                total_tokens=response.usage.total_tokens or 0,
            )
        return _EmbedResult(vectors=vectors, model=response.model or model, usage=usage)

    # ── internal: streaming tool call state ───────────────────────────

    def _get_tool_state(self, idx: int) -> dict:
        if not hasattr(self, "_stream_tool_state"):
            self._stream_tool_state: dict[int, dict] = {}
        if idx not in self._stream_tool_state:
            self._stream_tool_state[idx] = {"id": "", "name": "", "partial": ""}
        return self._stream_tool_state[idx]

    def _flush_tool_states(self) -> Iterator[StreamEvent]:
        for state in getattr(self, "_stream_tool_state", {}).values():
            if state["id"] and state["name"] and state["partial"]:
                try:
                    arguments = json.loads(state["partial"])
                except (json.JSONDecodeError, KeyError):
                    arguments = {}
                yield StreamToolCall(id=state["id"], name=state["name"], arguments=arguments)
        self._stream_tool_state = {}

    # ── internal: request construction ────────────────────────────────

    def _build_request_kwargs(
        self,
        messages: list[ChatMessage],
        tools: list[dict] | None,
        params: Any,
        stream: bool = False,
    ) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {
            "model": params.model,
            "messages": [m.to_dict() for m in messages],
            "temperature": params.temperature,
            "stream": stream,
        }
        if params.max_tokens is not None:
            kwargs["max_tokens"] = params.max_tokens
        if params.top_p is not None:
            kwargs["top_p"] = params.top_p
        if params.seed is not None:
            kwargs["seed"] = params.seed
        if params.stop:
            kwargs["stop"] = params.stop
        if tools:
            kwargs["tools"] = tools
        if params.extra_body:
            kwargs["extra_body"] = params.extra_body
        # ADR-0008: structured output
        if params.output:
            if params.output.format == "json_object":
                kwargs["response_format"] = {"type": "json_object"}
            elif params.output.format == "json_schema" and params.output.json_schema:
                kwargs["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "extraction_rule",
                        "strict": True,
                        "schema": params.output.json_schema,
                    },
                }
            if "extra_body" in kwargs and "response_format" in kwargs["extra_body"]:
                del kwargs["extra_body"]["response_format"]
        if stream:
            kwargs.setdefault("stream_options", {}).setdefault("include_usage", True)
        return kwargs

    # ── internal: response parsing ────────────────────────────────────

    @staticmethod
    def _parse_response(response: Any) -> ChatResponse:
        choice = response.choices[0]
        content = choice.message.content or ""
        usage = None
        if response.usage:
            usage = TokenUsage(
                prompt_tokens=response.usage.prompt_tokens or 0,
                completion_tokens=response.usage.completion_tokens or 0,
                total_tokens=response.usage.total_tokens or 0,
            )
        return ChatResponse(
            content=content,
            model=response.model or "",
            usage=usage,
            finish_reason=choice.finish_reason or "stop",
        )

    # ── internal: client lazy init ────────────────────────────────────

    def _get_sync_client(self) -> Any:
        if self._sync_client is None:
            from openai import OpenAI

            kwargs: dict[str, Any] = {
                "api_key": self._api_key,
                "base_url": self._base_url,
                "timeout": self._timeout,
                "max_retries": self._max_retries,
            }
            if self._http_client is not None:
                kwargs["http_client"] = self._http_client
            self._sync_client = OpenAI(**kwargs)
        return self._sync_client

    def _get_async_client(self) -> Any:
        if self._async_client is None:
            from openai import AsyncOpenAI

            kwargs: dict[str, Any] = {
                "api_key": self._api_key,
                "base_url": self._base_url,
                "timeout": self._timeout,
                "max_retries": self._max_retries,
            }
            if self._async_http_client is not None:
                kwargs["http_client"] = self._async_http_client
            self._async_client = AsyncOpenAI(**kwargs)
        return self._async_client

"""Google Provider — _ChatProvider + _SupportsEmbedding，Google GenAI SDK 原生 sync+async 双路径。"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, AsyncIterator, Iterator

from astrobasis import LogfmtLogger
from astrocrawl.ai._errors import AIAuthError, AIError, AIInvalidRequestError, AIRateLimitError, AIServerError
from astrocrawl.ai._types import (
    ChatMessage,
    ChatResponse,
    EmbedResult,
    StreamEvent,
    StreamFinish,
    StreamText,
    TokenUsage,
    ToolCall,
)

if TYPE_CHECKING:
    from astrocrawl.ai._config import AIConfig, _ResolvedParams
    from astrocrawl.ai._provider import _ChatProvider

logger = LogfmtLogger("astrocrawl.ai.google")

# ADR-0008: Google native json_object (response_mime_type) and json_schema (response_schema).
_STRUCTURED_OUTPUT_MODES = frozenset({"json_object", "json_schema"})


def list_models(base_url: str, api_key: str, timeout: float) -> list[str]:
    """Return available Google model IDs. Raises ImportError if SDK not installed."""
    try:
        from google.genai import Client, types
    except ImportError:
        raise ImportError("Google GenAI SDK not installed. Run: pip install google-genai") from None

    kwargs: dict = {"api_key": api_key}
    if base_url:
        kwargs["http_options"] = types.HttpOptions(base_url=base_url)
    client = Client(**kwargs)
    models = client.models.list()
    return [m.name[len("models/") :] if m.name.startswith("models/") else m.name for m in models]


def create_provider(config: AIConfig, **provider_kwargs: Any) -> _ChatProvider:
    """Entry point callable."""
    return GoogleClient(
        api_key=config.api_key,
        base_url=config.base_url or None,
        timeout=config.timeout,
        **provider_kwargs,
    )


def _map_error(exc: Exception) -> AIError:
    """Map google-genai SDK exception to AIError subclass."""
    msg = str(exc)

    try:
        from google.genai.errors import ClientError, ServerError
    except ImportError:
        return AIError(msg)

    if isinstance(exc, ClientError):
        code = getattr(exc, "code", 0)
        if code == 401 or code == 403:
            return AIAuthError(msg)
        if code == 429:
            return AIRateLimitError(msg)
        return AIInvalidRequestError(msg)
    if isinstance(exc, ServerError):
        return AIServerError(msg)

    return AIError(msg)


class GoogleClient:
    """Google Provider (_ChatProvider + _SupportsEmbedding)."""

    provider_name = "google"

    supported_output_formats: frozenset[str] = _STRUCTURED_OUTPUT_MODES

    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        timeout: float = 60.0,
        **provider_kwargs: Any,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._timeout = timeout
        # Google SDK does not support http_client injection — drop internal params
        provider_kwargs.pop("_http_client", None)
        provider_kwargs.pop("_async_http_client", None)
        self._extra_kwargs = provider_kwargs
        self._sync_client: Any = None
        self._async_client: Any = None

    # ── _ChatProvider ──────────────────────────────────────────────────

    def chat(self, messages: list[ChatMessage], tools: list[dict] | None, params: Any) -> ChatResponse:
        contents, system = self._convert_messages(messages)
        client = self._get_sync_client()
        try:
            response = client.models.generate_content(
                model=params.model, contents=contents, config=self._build_config(system, tools, params)
            )
        except Exception as e:
            raise _map_error(e) from e
        return self._parse_response(response)

    def chat_stream(self, messages: list[ChatMessage], tools: list[dict] | None, params: Any) -> Iterator[StreamEvent]:
        contents, system = self._convert_messages(messages)
        client = self._get_sync_client()
        try:
            stream = client.models.generate_content_stream(
                model=params.model, contents=contents, config=self._build_config(system, tools, params)
            )
        except Exception as e:
            raise _map_error(e) from e

        last_text_len = 0
        for chunk in stream:
            if chunk.text:
                delta = chunk.text[last_text_len:]
                last_text_len = len(chunk.text)
                if delta:
                    yield StreamText(text=delta)
            if hasattr(chunk, "candidates") and chunk.candidates:
                candidate = chunk.candidates[0]
                if hasattr(candidate, "finish_reason") and candidate.finish_reason:
                    usage = None
                    if hasattr(chunk, "usage_metadata") and chunk.usage_metadata:
                        usage = TokenUsage(
                            prompt_tokens=getattr(chunk.usage_metadata, "prompt_token_count", 0) or 0,
                            completion_tokens=getattr(chunk.usage_metadata, "candidates_token_count", 0) or 0,
                            total_tokens=getattr(chunk.usage_metadata, "total_token_count", 0) or 0,
                        )
                    yield StreamFinish(finish_reason=str(candidate.finish_reason), usage=usage)

    async def achat(self, messages: list[ChatMessage], tools: list[dict] | None, params: Any) -> ChatResponse:
        contents, system = self._convert_messages(messages)
        client = self._get_async_client()
        try:
            response = await client.aio.models.generate_content(
                model=params.model, contents=contents, config=self._build_config(system, tools, params)
            )
        except Exception as e:
            raise _map_error(e) from e
        return self._parse_response(response)

    async def achat_stream(
        self, messages: list[ChatMessage], tools: list[dict] | None, params: _ResolvedParams
    ) -> AsyncIterator[StreamEvent]:
        contents, system = self._convert_messages(messages)
        client = self._get_async_client()
        try:
            stream = await client.aio.models.generate_content_stream(
                model=params.model, contents=contents, config=self._build_config(system, tools, params)
            )
        except Exception as e:
            raise _map_error(e) from e

        last_text_len = 0
        async for chunk in stream:
            if chunk.text:
                delta = chunk.text[last_text_len:]
                last_text_len = len(chunk.text)
                if delta:
                    yield StreamText(text=delta)
            if hasattr(chunk, "candidates") and chunk.candidates:
                candidate = chunk.candidates[0]
                if hasattr(candidate, "finish_reason") and candidate.finish_reason:
                    usage = None
                    if hasattr(chunk, "usage_metadata") and chunk.usage_metadata:
                        usage = TokenUsage(
                            prompt_tokens=getattr(chunk.usage_metadata, "prompt_token_count", 0) or 0,
                            completion_tokens=getattr(chunk.usage_metadata, "candidates_token_count", 0) or 0,
                            total_tokens=getattr(chunk.usage_metadata, "total_token_count", 0) or 0,
                        )
                    yield StreamFinish(finish_reason=str(candidate.finish_reason), usage=usage)

    async def aclose(self) -> None:
        if self._async_client is not None:
            try:
                await self._async_client.aio.close()
            except Exception:
                pass
            self._async_client = None
        self._sync_client = None

    def close(self) -> None:
        """Close underlying sync client (idempotent)."""
        self._sync_client = None

    # ── _SupportsEmbedding ─────────────────────────────────────────────

    async def embed(self, texts: list[str], model: str) -> EmbedResult:
        client = self._get_async_client()
        try:
            result = await client.aio.models.embed_content(model=model, contents=texts)
        except Exception as e:
            raise _map_error(e) from e

        vectors = [list(e.values) if hasattr(e, "values") else [] for e in (result.embeddings or [])]
        return EmbedResult(vectors=vectors, model=model)

    # ── internal ──────────────────────────────────────────────────────

    @staticmethod
    def _convert_messages(messages: list[ChatMessage]) -> tuple[list[dict], str | None]:
        system = None
        contents: list[dict] = []
        for m in messages:
            if m.role.value == "system":
                system = m.content
            else:
                contents.append({"role": m.role.value, "parts": [{"text": m.content}]})
        return contents, system

    def _build_config(self, system: str | None, tools: list[dict] | None, params: Any) -> dict:
        config: dict = {"temperature": params.temperature, "max_output_tokens": params.max_tokens}
        if system:
            config["system_instruction"] = system
        if params.top_p is not None:
            config["top_p"] = params.top_p
        if params.stop:
            config["stop_sequences"] = params.stop
        if tools:
            config["tools"] = [{"function_declarations": list(tools)}]
        # ADR-0008: structured output
        if params.output:
            config["response_mime_type"] = "application/json"
            if params.output.format == "json_schema" and params.output.json_schema:
                config["response_schema"] = params.output.json_schema
        return config

    @staticmethod
    def _parse_response(response: Any) -> ChatResponse:
        text = response.text or ""
        finish = "stop"
        tool_calls = None

        if response.candidates:
            candidate = response.candidates[0]
            if hasattr(candidate, "finish_reason") and candidate.finish_reason:
                finish = str(candidate.finish_reason)
            if hasattr(candidate, "content") and candidate.content:
                for part in candidate.content.parts:
                    if hasattr(part, "function_call") and part.function_call:
                        if tool_calls is None:
                            tool_calls = []
                        tool_calls.append(
                            ToolCall(
                                id=f"call_{uuid.uuid4().hex[:8]}",
                                name=part.function_call.name,
                                arguments=dict(part.function_call.args),
                            )
                        )

        usage = None
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            usage = TokenUsage(
                prompt_tokens=getattr(response.usage_metadata, "prompt_token_count", 0) or 0,
                completion_tokens=getattr(response.usage_metadata, "candidates_token_count", 0) or 0,
                total_tokens=getattr(response.usage_metadata, "total_token_count", 0) or 0,
            )

        return ChatResponse(
            content=text,
            model=getattr(response, "model_version", ""),
            usage=usage,
            finish_reason=finish,
            tool_calls=tool_calls,
        )

    # ── client lazy init ──────────────────────────────────────────────

    def _get_sync_client(self) -> Any:
        if self._sync_client is None:
            from google.genai import Client, types

            kwargs: dict = {"api_key": self._api_key}
            if self._base_url:
                kwargs["http_options"] = types.HttpOptions(base_url=self._base_url)
            kwargs.update(self._extra_kwargs)
            self._sync_client = Client(**kwargs)
        return self._sync_client

    def _get_async_client(self) -> Any:
        if self._async_client is None:
            from google.genai import Client, types

            kwargs: dict = {"api_key": self._api_key}
            if self._base_url:
                kwargs["http_options"] = types.HttpOptions(base_url=self._base_url)
            kwargs.update(self._extra_kwargs)
            self._async_client = Client(**kwargs)
        return self._async_client

"""Anthropic Provider — _ChatProvider (sync+async dual-path). No _SupportsEmbedding."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, AsyncIterator, Iterator

from astrocrawl.ai._errors import AIAuthError, AIError, AIInvalidRequestError, AIRateLimitError, AIServerError
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
    ToolCall,
)

if TYPE_CHECKING:
    from astrocrawl.ai._config import AIConfig, _ResolvedParams
    from astrocrawl.ai._provider import _ChatProvider

logger = logging.getLogger("astrocrawl.ai.anthropic")

# ADR-0008: json_schema via Tool Use native; json_object via system prompt (soft constraint).
_STRUCTURED_OUTPUT_MODES = frozenset({"json_schema", "json_object"})


def list_models(base_url: str, api_key: str, timeout: float) -> list[str]:
    """Return available Anthropic model IDs. Raises ImportError if SDK not installed."""
    try:
        import anthropic
    except ImportError:
        raise ImportError("Anthropic SDK not installed. Run: pip install anthropic") from None

    client = anthropic.Anthropic(
        api_key=api_key,
        base_url=base_url or None,
        timeout=timeout,
    )
    return [m.id for m in client.models.list()]


def create_provider(config: AIConfig, **provider_kwargs: Any) -> _ChatProvider:
    """Entry point callable."""
    return AnthropicClient(
        api_key=config.api_key,
        base_url=config.base_url or None,
        timeout=config.timeout,
        max_retries=config.max_retries,
        **provider_kwargs,
    )


def _map_error(exc: Exception) -> AIError:
    """Map anthropic SDK exception to AIError subclass."""
    msg = str(exc)

    try:
        from anthropic import APIStatusError
    except ImportError:
        return AIError(msg)

    if not isinstance(exc, APIStatusError):
        return AIError(msg)

    status = exc.status_code
    if status == 401 or status == 403:
        return AIAuthError(msg)
    if status == 429:
        return AIRateLimitError(msg)
    if 500 <= status < 600:
        return AIServerError(msg)
    if status == 400:
        return AIInvalidRequestError(msg)

    return AIError(msg)


class AnthropicClient:
    """Anthropic Provider (_ChatProvider). Does not implement _SupportsEmbedding."""

    provider_name = "anthropic"

    supported_output_formats: frozenset[str] = _STRUCTURED_OUTPUT_MODES

    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        timeout: float = 60.0,
        max_retries: int = 2,
        **provider_kwargs: Any,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._timeout = timeout
        self._max_retries = max_retries
        self._http_client: Any = provider_kwargs.pop("_http_client", None)
        self._async_http_client: Any = provider_kwargs.pop("_async_http_client", None)
        self._extra_kwargs = provider_kwargs
        self._sync_client: Any = None
        self._async_client: Any = None

    # ── _ChatProvider.chat ─────────────────────────────────────────────

    def chat(self, messages: list[ChatMessage], tools: list[dict] | None, params: Any) -> ChatResponse:
        system, user_msgs = self._split_messages(messages)
        kwargs = self._build_kwargs(system, user_msgs, tools, params, stream=False)
        client = self._get_sync_client()
        try:
            response = client.messages.create(**kwargs)
        except Exception as e:
            raise _map_error(e) from e
        return self._parse_response(response)

    def chat_stream(self, messages: list[ChatMessage], tools: list[dict] | None, params: Any) -> Iterator[StreamEvent]:
        system, user_msgs = self._split_messages(messages)
        kwargs = self._build_kwargs(system, user_msgs, tools, params, stream=True)
        client = self._get_sync_client()
        try:
            with client.messages.stream(**kwargs) as stream:
                for event in stream:
                    se = self._normalize_stream_event(event)
                    if se is not None:
                        yield se
        except Exception as e:
            raise _map_error(e) from e

    # ── _ChatProvider.achat ────────────────────────────────────────────

    async def achat(self, messages: list[ChatMessage], tools: list[dict] | None, params: Any) -> ChatResponse:
        system, user_msgs = self._split_messages(messages)
        kwargs = self._build_kwargs(system, user_msgs, tools, params, stream=False)
        client = self._get_async_client()
        try:
            response = await client.messages.create(**kwargs)
        except Exception as e:
            raise _map_error(e) from e
        return self._parse_response(response)

    async def achat_stream(
        self, messages: list[ChatMessage], tools: list[dict] | None, params: _ResolvedParams
    ) -> AsyncIterator[StreamEvent]:
        system, user_msgs = self._split_messages(messages)
        kwargs = self._build_kwargs(system, user_msgs, tools, params, stream=True)
        client = self._get_async_client()
        try:
            async with client.messages.stream(**kwargs) as stream:
                async for event in stream:
                    se = self._normalize_stream_event(event)
                    if se is not None:
                        yield se
        except Exception as e:
            raise _map_error(e) from e

    # ── _ChatProvider.aclose ───────────────────────────────────────────

    async def aclose(self) -> None:
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

    # ── internal ──────────────────────────────────────────────────────

    @staticmethod
    def _split_messages(messages: list[ChatMessage]) -> tuple[str | None, list[dict]]:
        """Anthropic API: system is a separate param, not within message list."""
        system = None
        user_msgs: list[dict] = []
        for m in messages:
            if m.role.value == "system":
                system = m.content
            else:
                user_msgs.append(m.to_dict())
        return system, user_msgs

    def _build_kwargs(
        self, system: str | None, messages: list[dict], tools: list[dict] | None, params: Any, stream: bool = False
    ) -> dict:
        kwargs: dict = {
            "model": params.model,
            "messages": messages,
            "max_tokens": params.max_tokens,
            "temperature": params.temperature,
            "stream": stream,
        }

        # ADR-0008: structured output
        if params.output:
            if params.output.format == "json_schema" and params.output.json_schema:
                tool = {
                    "name": "output_extraction_rule",
                    "description": "Output the extraction rule following the schema exactly.",
                    "input_schema": params.output.json_schema,
                }
                tools = list(tools or []) + [tool]
                kwargs["tool_choice"] = {"type": "tool", "name": "output_extraction_rule"}
            elif params.output.format == "json_object":
                system = (system or "") + "\n\nYou MUST output only valid JSON. No markdown fences, no other text."

        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools
        if params.top_p is not None:
            kwargs["top_p"] = params.top_p
        if params.stop:
            kwargs["stop_sequences"] = params.stop
        if params.extra_body:
            kwargs["extra_body"] = params.extra_body
        kwargs.update(self._extra_kwargs)
        return kwargs

    @staticmethod
    def _parse_response(response: Any) -> ChatResponse:
        content = ""
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                content += block.text
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(block.input)))

        usage = None
        if response.usage:
            usage = TokenUsage(
                prompt_tokens=response.usage.input_tokens or 0,
                completion_tokens=response.usage.output_tokens or 0,
                total_tokens=(response.usage.input_tokens or 0) + (response.usage.output_tokens or 0),
            )

        return ChatResponse(
            content=content,
            model=response.model,
            usage=usage,
            finish_reason=response.stop_reason or "stop",
            tool_calls=tool_calls if tool_calls else None,
        )

    def _normalize_stream_event(self, event: Any) -> StreamEvent | None:
        """Normalize Anthropic stream event to StreamEvent."""
        etype = getattr(event, "type", None)

        if etype == "content_block_start":
            block = event.content_block
            if block.type == "tool_use":
                idx = getattr(event, "index", 0)
                if not hasattr(self, "_stream_tool_state"):
                    self._stream_tool_state: dict = {}
                self._stream_tool_state[idx] = {"id": block.id, "name": block.name, "partial_json": ""}
                return StreamToolCallStart(id=block.id, name=block.name)
            return None

        if etype == "content_block_delta":
            delta = event.delta
            if delta.type == "text_delta":
                return StreamText(text=delta.text)
            if delta.type == "input_json_delta":
                idx = getattr(event, "index", 0)
                state = getattr(self, "_stream_tool_state", {}).get(idx, {})
                state["partial_json"] = state.get("partial_json", "") + delta.partial_json
                return StreamToolCallDelta(id=state.get("id", ""), arguments_delta=delta.partial_json)
            return None

        if etype == "content_block_stop":
            idx = getattr(event, "index", 0)
            state = getattr(self, "_stream_tool_state", {}).pop(idx, None)
            if state and state.get("id"):
                try:
                    arguments = json.loads(state["partial_json"])
                except (json.JSONDecodeError, KeyError):
                    arguments = {}
                return StreamToolCall(id=state["id"], name=state.get("name", ""), arguments=arguments)
            return None

        if etype in ("message_start", "message_stop"):
            if etype == "message_stop":
                self._stream_tool_state = {}
            return None

        if etype == "message_delta":
            if event.usage:
                return StreamFinish(
                    finish_reason=event.delta.stop_reason or "stop",
                    usage=TokenUsage(
                        prompt_tokens=event.usage.input_tokens or 0,
                        completion_tokens=event.usage.output_tokens or 0,
                        total_tokens=(event.usage.input_tokens or 0) + (event.usage.output_tokens or 0),
                    ),
                )
            return StreamFinish(finish_reason="stop")

        return None

    # ── client lazy init ──────────────────────────────────────────────

    def _get_sync_client(self) -> Any:
        if self._sync_client is None:
            from anthropic import Anthropic

            kwargs: dict = {"api_key": self._api_key, "timeout": self._timeout, "max_retries": self._max_retries}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            if self._http_client is not None:
                kwargs["http_client"] = self._http_client
            self._sync_client = Anthropic(**kwargs)
        return self._sync_client

    def _get_async_client(self) -> Any:
        if self._async_client is None:
            from anthropic import AsyncAnthropic

            kwargs: dict = {"api_key": self._api_key, "timeout": self._timeout, "max_retries": self._max_retries}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            if self._async_http_client is not None:
                kwargs["http_client"] = self._async_http_client
            self._async_client = AsyncAnthropic(**kwargs)
        return self._async_client

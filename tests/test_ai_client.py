"""测试：astrocrawl/ai/ — AIClient Provider 门面, _ResolvedParams, 错误传播, Hook 链, Stream。

ADR-0006 #2: Provider dispatch + SDK 内置 retry。RetryPolicy / _map_openai_error 已迁出。
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import pytest

from astrocrawl.ai._client import AIClient
from astrocrawl.ai._config import AIConfig, GenerationParams, _resolve_params
from astrocrawl.ai._constraint import OutputConstraint
from astrocrawl.ai._errors import AIAuthError, AIConnectionError, AIError, AIRateLimitError
from astrocrawl.ai._observability import AIHook, LoggingHook
from astrocrawl.ai._types import CallContext, ChatMessage, ChatResponse, Role, StreamFinish, StreamText, TokenUsage

# ═══════════════════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════════════════


def _mock_provider(**overrides):
    """构建 mock Provider，默认返回简单 ChatResponse。"""
    p = MagicMock()
    p.provider_name = "mock"
    p.supported_output_formats = frozenset({"json_object", "json_schema"})
    p.chat.return_value = ChatResponse(content="mock", model="mock-model")
    p.achat = AsyncMock(return_value=ChatResponse(content="mock", model="mock-model"))
    p.chat_stream.return_value = iter([StreamText(text="mock"), StreamFinish(finish_reason="stop")])
    p.achat_stream.return_value = _async_iter(StreamText(text="mock"), StreamFinish(finish_reason="stop"))
    p.aclose = AsyncMock()
    for k, v in overrides.items():
        setattr(p, k, v)
    return p


async def _async_iter(*items):
    for item in items:
        yield item


def _make_client(config=None, hooks=None, provider=None):
    """构造 AIClient 并注入 mock provider，绕过 entry point 发现。"""
    cfg = config or AIConfig(api_key="sk-test")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "astrocrawl.ai._provider_registry._discover_provider",
            lambda name, cfg, **kw: provider or _mock_provider(),
        )
        return AIClient(cfg, hooks=hooks or [])


def _make_response(content="Hello!", model="gpt-4o-mini", finish_reason="stop"):
    return ChatResponse(content=content, model=model, finish_reason=finish_reason, usage=TokenUsage(10, 5, 15))


# ═══════════════════════════════════════════════════════════════════════
# _ResolvedParams + _resolve_params
# ═══════════════════════════════════════════════════════════════════════


class TestResolvedParams:
    """_resolve_params — None 字段从 AIConfig 默认值填充。"""

    def test_none_params_uses_all_defaults(self):
        r = _resolve_params(None, default_model="gpt-4o", default_temperature=0.5, default_max_tokens=2048)
        assert r.model == "gpt-4o"
        assert r.temperature == 0.5
        assert r.max_tokens == 2048

    def test_params_override_defaults(self):
        p = GenerationParams(model="gpt-5", temperature=0.1, max_tokens=512)
        r = _resolve_params(p)
        assert r.model == "gpt-5"
        assert r.temperature == 0.1
        assert r.max_tokens == 512

    def test_temperature_zero_preserved(self):
        p = GenerationParams(temperature=0.0)
        r = _resolve_params(p, default_temperature=0.7)
        assert r.temperature == 0.0  # is not None → 保留 0.0

    def test_none_temperature_uses_default(self):
        p = GenerationParams(model="gpt-5")  # temperature=None
        r = _resolve_params(p, default_temperature=0.7)
        assert r.temperature == 0.7  # None → 填充

    def test_optional_fields_passthrough(self):
        p = GenerationParams(top_p=0.9, seed=42, stop=["\n"], extra_body={"top_k": 50})
        r = _resolve_params(p)
        assert r.top_p == 0.9
        assert r.seed == 42
        assert r.stop == ["\n"]
        assert r.extra_body == {"top_k": 50}

    def test_none_optional_fields_stay_none(self):
        r = _resolve_params(None)
        assert r.top_p is None
        assert r.seed is None
        assert r.stop is None
        assert r.extra_body is None

    def test_model_empty_string_passes_through(self):
        """空字符串非 None，应透传而非回退到默认值。"""
        p = GenerationParams(model="")
        r = _resolve_params(p, default_model="gpt-4o", default_temperature=0, default_max_tokens=1)
        assert r.model == ""

    def test_resolved_params_is_frozen(self):
        r = _resolve_params(None)
        with pytest.raises(Exception):
            r.model = "changed"  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════════════
# AIConfig + GenerationParams
# ═══════════════════════════════════════════════════════════════════════


class TestAIConfig:
    """AIConfig frozen dataclass 默认值（ADR-0006 #4 字段升级）。"""

    def test_default_values(self):
        cfg = AIConfig()
        assert cfg.api_key == ""
        assert cfg.provider == "openai"
        assert cfg.base_url == ""
        assert cfg.default_model == "gpt-4o-mini"
        assert cfg.default_temperature == 0.7
        assert cfg.default_max_tokens == 4096
        assert cfg.timeout == 60.0
        assert cfg.max_retries == 2

    def test_custom_values(self):
        cfg = AIConfig(api_key="sk-test", provider="anthropic", default_model="claude-opus-4-7", timeout=30.0)
        assert cfg.api_key == "sk-test"
        assert cfg.provider == "anthropic"
        assert cfg.default_model == "claude-opus-4-7"
        assert cfg.timeout == 30.0

    def test_is_frozen(self):
        cfg = AIConfig()
        with pytest.raises(Exception):
            cfg.api_key = "modified"  # type: ignore[misc]

    def test_api_key_masked_in_repr(self):
        cfg = AIConfig(api_key="sk-1234567890abcdef")
        r = repr(cfg)
        assert "sk-1234567890abcdef" not in r
        assert "api_key" not in r


class TestGenerationParams:
    """GenerationParams frozen dataclass——全字段 Optional[None]（#4 升级）。"""

    def test_all_fields_none_by_default(self):
        p = GenerationParams()
        assert p.model is None
        assert p.temperature is None
        assert p.max_tokens is None
        assert p.top_p is None
        assert p.seed is None
        assert p.stop is None
        assert p.extra_body is None

    def test_partial_override(self):
        p = GenerationParams(temperature=0.1, max_tokens=512)
        assert p.temperature == 0.1
        assert p.max_tokens == 512
        assert p.model is None

    def test_no_presence_penalty_field(self):
        p = GenerationParams()
        assert not hasattr(p, "presence_penalty")

    def test_no_frequency_penalty_field(self):
        p = GenerationParams()
        assert not hasattr(p, "frequency_penalty")

    def test_no_to_dict_method(self):
        p = GenerationParams()
        assert not hasattr(p, "to_dict")


# ═══════════════════════════════════════════════════════════════════════
# ChatMessage + TokenUsage
# ═══════════════════════════════════════════════════════════════════════


class TestChatMessage:
    """ChatMessage — to_dict（ADR-0006 #5: tool_call_id + name）。"""

    def test_to_dict(self):
        msg = ChatMessage(role=Role.SYSTEM, content="You are helpful")
        assert msg.to_dict() == {"role": "system", "content": "You are helpful"}

    def test_user_message_to_dict(self):
        msg = ChatMessage(role=Role.USER, content="Hello")
        d = msg.to_dict()
        assert d["role"] == "user"
        assert d["content"] == "Hello"

    def test_tool_message_to_dict(self):
        msg = ChatMessage(role=Role.TOOL, content='{"result": "ok"}', tool_call_id="call_123", name="my_func")
        d = msg.to_dict()
        assert d["role"] == "tool"
        assert d["content"] == '{"result": "ok"}'
        assert d["tool_call_id"] == "call_123"
        assert d["name"] == "my_func"

    def test_tool_message_omits_none_fields(self):
        msg = ChatMessage(role=Role.TOOL, content="result")
        d = msg.to_dict()
        assert "tool_call_id" not in d
        assert "name" not in d


class TestTokenUsage:
    """TokenUsage 默认值。"""

    def test_defaults_zero(self):
        usage = TokenUsage()
        assert usage.prompt_tokens == 0
        assert usage.completion_tokens == 0
        assert usage.total_tokens == 0


# ═══════════════════════════════════════════════════════════════════════
# LoggingHook + AIHook Protocol
# ═══════════════════════════════════════════════════════════════════════


class TestLoggingHook:
    """LoggingHook — 不抛异常 (smoke tests)。"""

    def test_all_hooks_no_exception(self):
        hook = LoggingHook()
        ctx = CallContext(model="gpt-4o-mini", messages_count=2)
        resp = ChatResponse(content="hi", model="gpt-4o-mini", usage=TokenUsage(10, 5, 15))

        hook.on_request(ctx)
        hook.on_response(ctx, resp)
        hook.on_error(ctx, AIError("test"))
        hook.on_retry(ctx, AIError("timeout"), 1, 2.0)


class TestAIHookProtocol:
    """AIHook Protocol — runtime_checkable。"""

    def test_logging_hook_is_aihook(self):
        assert isinstance(LoggingHook(), AIHook)

    def test_full_hook_is_aihook(self):
        class FullHook:
            def on_request(self, ctx):
                pass

            def on_response(self, ctx, resp):
                pass

            def on_error(self, ctx, err):
                pass

            def on_retry(self, ctx, err, attempt, delay):
                pass

        assert isinstance(FullHook(), AIHook)

    def test_partial_hook_not_aihook(self):
        class PartialHook:
            def on_request(self, ctx):
                pass

        assert not isinstance(PartialHook(), AIHook)


# ═══════════════════════════════════════════════════════════════════════
# AIClient — init
# ═══════════════════════════════════════════════════════════════════════


class TestAIClientInit:
    """AIClient 构造 — Provider discovery + 默认 hook。"""

    def test_construct_with_config(self):
        client = _make_client(AIConfig(api_key="sk-test"))
        assert client.config.api_key == "sk-test"
        assert client._provider is not None

    def test_construct_with_custom_hooks(self):
        class _Spy:
            def on_request(self, ctx):
                pass

            def on_response(self, ctx, resp):
                pass

            def on_error(self, ctx, err):
                pass

            def on_retry(self, ctx, err, attempt, delay):
                pass

        client = _make_client(AIConfig(api_key="sk-test"), hooks=[_Spy()])
        assert len(client._hooks) == 1

    def test_default_hooks_is_logging_hook(self):
        client = _make_client()
        assert len(client._hooks) == 1
        assert isinstance(client._hooks[0], LoggingHook)

    def test_provider_kwargs_passed_through(self):

        captured = {}

        def _fake_discover(name, cfg, **kw):
            captured.update(kw)
            return _mock_provider()

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("astrocrawl.ai._provider_registry._discover_provider", _fake_discover)
            AIClient(AIConfig(api_key="sk-test"), extra_param="value")
        assert captured.get("extra_param") == "value"

    def test_async_context_manager(self):
        async def _test():
            client = _make_client()
            async with client as c:
                assert c is client
            client._provider.aclose.assert_awaited_once()

        import asyncio

        asyncio.run(_test())


# ═══════════════════════════════════════════════════════════════════════
# AIClient — sync chat
# ═══════════════════════════════════════════════════════════════════════


class TestAIClientChat:
    """AIClient.chat() — mock _provider。"""

    def test_chat_response_parsing(self):
        provider = _mock_provider()
        provider.chat.return_value = _make_response(content="Hello!")
        client = _make_client(provider=provider)

        result = client.chat([ChatMessage(Role.USER, "Hi")])
        assert result.content == "Hello!"
        assert result.model == "gpt-4o-mini"
        assert result.usage.prompt_tokens == 10
        assert result.finish_reason == "stop"

    def test_chat_passes_messages_to_provider(self):
        provider = _mock_provider()
        client = _make_client(provider=provider)

        client.chat([ChatMessage(Role.USER, "Hi")])
        call_args = provider.chat.call_args
        messages_arg = call_args.args[0]
        assert len(messages_arg) == 1
        assert messages_arg[0].role == Role.USER
        assert messages_arg[0].content == "Hi"

    def test_chat_uses_generation_params(self):
        provider = _mock_provider()
        params_captured = {}

        class _Spy:
            def on_request(self, ctx):
                params_captured.update(ctx.params)

            def on_response(self, ctx, resp):
                pass

            def on_error(self, ctx, err):
                pass

            def on_retry(self, ctx, err, attempt, delay):
                pass

        client = _make_client(provider=provider, hooks=[_Spy()])
        params = GenerationParams(
            temperature=0.2, max_tokens=1024, seed=42, top_p=0.9, output=OutputConstraint(format="json_object")
        )

        client.chat([ChatMessage(Role.USER, "Hi")], params=params)
        resolved = provider.chat.call_args.args[2]
        assert resolved.temperature == 0.2
        assert resolved.max_tokens == 1024
        assert resolved.seed == 42
        assert resolved.top_p == 0.9
        assert resolved.output is not None
        assert resolved.output.format == "json_object"
        # _build_context 正确传播 top_p 和 output_format 到 ctx.params
        assert params_captured.get("top_p") == 0.9
        assert params_captured.get("output_format") == "json_object"

    def test_chat_error_propagates(self):
        provider = _mock_provider()
        provider.chat.side_effect = AIAuthError("unauthorized")
        client = _make_client(provider=provider)

        with pytest.raises(AIAuthError):
            client.chat([ChatMessage(Role.USER, "Hi")])

    def test_chat_retryable_error_triggers_retry_hook(self):
        calls = []

        class _Hook:
            def on_request(self, ctx):
                calls.append("on_request")

            def on_response(self, ctx, resp):
                calls.append("on_response")

            def on_error(self, ctx, err):
                calls.append("on_error")

            def on_retry(self, ctx, err, attempt, delay):
                calls.append("on_retry")

        provider = _mock_provider()
        provider.chat.side_effect = AIRateLimitError("rate limited")
        client = _make_client(hooks=[_Hook()], provider=provider)

        with pytest.raises(AIRateLimitError):
            client.chat([ChatMessage(Role.USER, "Hi")])
        assert "on_request" in calls
        assert "on_retry" in calls
        assert "on_error" in calls


# ═══════════════════════════════════════════════════════════════════════
# AIClient — async (achat / achat_stream / aclose)
# ═══════════════════════════════════════════════════════════════════════


class TestAIClientAchat:
    """AIClient.achat() — mock _provider。"""

    async def test_achat_response_parsing(self):
        provider = _mock_provider()
        provider.achat = AsyncMock(return_value=_make_response(content="async hello"))
        client = _make_client(provider=provider)

        result = await client.achat([ChatMessage(Role.USER, "Hi")])
        assert result.content == "async hello"
        assert result.model == "gpt-4o-mini"
        assert result.usage.prompt_tokens == 10

    async def test_achat_passes_generation_params(self):
        provider = _mock_provider()
        provider.achat = AsyncMock(return_value=_make_response())
        client = _make_client(provider=provider)
        params = GenerationParams(temperature=0.2, max_tokens=512, seed=42)

        await client.achat([ChatMessage(Role.USER, "Hi")], params=params)
        resolved = provider.achat.call_args.args[2]
        assert resolved.temperature == 0.2
        assert resolved.max_tokens == 512
        assert resolved.seed == 42

    async def test_achat_error_propagates(self):
        provider = _mock_provider()
        provider.achat = AsyncMock(side_effect=AIAuthError("unauthorized"))
        client = _make_client(provider=provider)

        with pytest.raises(AIAuthError):
            await client.achat([ChatMessage(Role.USER, "Hi")])

    async def test_achat_hook_chain(self):
        """验证 hook 通知链: on_request → on_response。SDK retry 成功不触发 on_retry。"""
        calls = []

        class _SpyHook:
            def on_request(self, ctx):
                calls.append("on_request")

            def on_response(self, ctx, resp):
                calls.append("on_response")

            def on_error(self, ctx, err):
                calls.append("on_error")

            def on_retry(self, ctx, err, attempt, delay):
                calls.append("on_retry")

        spy = _SpyHook()
        provider = _mock_provider()
        provider.achat = AsyncMock(return_value=_make_response(content="ok"))
        client = _make_client(AIConfig(api_key="sk-test"), hooks=[spy], provider=provider)

        await client.achat([ChatMessage(Role.USER, "Hi")])
        assert "on_request" in calls
        assert "on_response" in calls
        assert "on_retry" not in calls  # 成功路径不触发 on_retry

    async def test_achat_error_triggers_retry_hook(self):
        """可重试错误 → on_retry (SDK 耗尽) → on_error 链。"""
        calls = []

        class _SpyHook:
            def on_request(self, ctx):
                calls.append("on_request")

            def on_response(self, ctx, resp):
                calls.append("on_response")

            def on_error(self, ctx, err):
                calls.append("on_error")

            def on_retry(self, ctx, err, attempt, delay):
                calls.append("on_retry")

        spy = _SpyHook()
        provider = _mock_provider()
        provider.achat = AsyncMock(side_effect=AIRateLimitError("rate limited"))
        client = _make_client(AIConfig(api_key="sk-test"), hooks=[spy], provider=provider)

        with pytest.raises(AIRateLimitError):
            await client.achat([ChatMessage(Role.USER, "Hi")])
        assert "on_request" in calls
        assert "on_retry" in calls  # 可重试错误 → SDK 耗尽 → on_retry
        assert "on_error" in calls

    async def test_achat_auth_error_no_retry_hook(self):
        """不可重试错误 → 不触发 on_retry。"""
        calls = []

        class _SpyHook:
            def on_request(self, ctx):
                calls.append("on_request")

            def on_response(self, ctx, resp):
                calls.append("on_response")

            def on_error(self, ctx, err):
                calls.append("on_error")

            def on_retry(self, ctx, err, attempt, delay):
                calls.append("on_retry")

        spy = _SpyHook()
        provider = _mock_provider()
        provider.achat = AsyncMock(side_effect=AIAuthError("unauthorized"))
        client = _make_client(AIConfig(api_key="sk-test"), hooks=[spy], provider=provider)

        with pytest.raises(AIAuthError):
            await client.achat([ChatMessage(Role.USER, "Hi")])
        assert "on_request" in calls
        assert "on_retry" not in calls  # 不可重试错误 → 不触发 on_retry
        assert "on_error" in calls

    async def test_aclose_cleanup(self):
        """aclose 委托 provider.aclose——二次调用安全，每次均委托。"""
        provider = _mock_provider()
        client = _make_client(provider=provider)

        await client.aclose()
        assert provider.aclose.await_count == 1
        # 二次调用不抛异常，AIClient 始终委托 Provider（Provider 负责幂等）
        await client.aclose()
        assert provider.aclose.await_count == 2


class TestAIClientAchatStream:
    """AIClient.achat_stream() — StreamEvent v2。"""

    async def test_achat_stream_yields_events(self):
        provider = _mock_provider()
        provider.achat_stream.return_value = _async_iter(
            StreamText(text="Hello"),
            StreamText(text=" world"),
            StreamFinish(finish_reason="stop"),
        )
        client = _make_client(provider=provider)

        events = []
        async for event in client.achat_stream([ChatMessage(Role.USER, "Hi")]):
            events.append(event)

        assert len(events) == 3
        assert isinstance(events[0], StreamText)
        assert events[0].text == "Hello"
        assert isinstance(events[1], StreamText)
        assert isinstance(events[2], StreamFinish)
        assert events[2].finish_reason == "stop"

    async def test_achat_stream_error_propagates(self):
        provider = _mock_provider()
        provider.achat_stream.side_effect = AIConnectionError("connection lost")
        client = _make_client(provider=provider)

        with pytest.raises(AIConnectionError):
            async for _ in client.achat_stream([ChatMessage(Role.USER, "Hi")]):
                pass

    async def test_achat_stream_hooks_on_success(self):
        """流式成功完成 → on_request + on_response 触发，UsageTracker 记录 usage。"""
        calls = []

        class _Hook:
            def on_request(self, ctx):
                calls.append("on_request")

            def on_response(self, ctx, resp):
                calls.append("on_response")

            def on_error(self, ctx, err):
                calls.append("on_error")

            def on_retry(self, ctx, err, attempt, delay):
                calls.append("on_retry")

        provider = _mock_provider()
        provider.achat_stream.return_value = _async_iter(
            StreamText(text="Hello"),
            StreamFinish(finish_reason="stop", usage=TokenUsage(10, 5, 15)),
        )
        client = _make_client(AIConfig(api_key="sk-test"), hooks=[_Hook()], provider=provider)

        events = []
        async for event in client.achat_stream([ChatMessage(Role.USER, "Hi")]):
            events.append(event)

        assert "on_request" in calls
        assert "on_response" in calls
        assert "on_retry" not in calls
        assert client.usage.total_tokens == 15


# ═══════════════════════════════════════════════════════════════════════
# _StreamSession
# ═══════════════════════════════════════════════════════════════════════


class TestStreamSession:
    """_StreamSession — 流式生命周期捕获。"""

    def test_consume_captures_streamfinish_usage(self):
        from astrocrawl.ai._client import _StreamSession

        ctx = CallContext(model="gpt-4o", messages_count=1)
        session = _StreamSession(ctx=ctx)
        usage = TokenUsage(100, 50, 150)
        event = StreamFinish(finish_reason="stop", usage=usage)
        result = session.consume(event)
        assert result is event
        assert session._usage == usage
        assert session._finish_reason == "stop"

    def test_consume_ignores_non_streamfinish(self):
        from astrocrawl.ai._client import _StreamSession

        ctx = CallContext(model="gpt-4o", messages_count=1)
        session = _StreamSession(ctx=ctx)
        text_event = StreamText(text="hello")
        result = session.consume(text_event)
        assert result is text_event
        assert session._usage is None

    def test_consume_none_usage(self):
        from astrocrawl.ai._client import _StreamSession

        ctx = CallContext(model="gpt-4o", messages_count=1)
        session = _StreamSession(ctx=ctx)
        event = StreamFinish(finish_reason="stop")  # usage=None
        session.consume(event)
        assert session._usage is None

    def test_finalize_triggers_hooks_and_tracker(self):
        from astrocrawl.ai._client import _StreamSession

        calls = []

        class _Tracker:
            def __init__(self):
                self.recorded = None

            def record(self, usage):
                self.recorded = usage

        class _Hook:
            def on_response(self, ctx, response):
                calls.append("on_response")

        ctx = CallContext(model="gpt-4o", messages_count=1)
        session = _StreamSession(ctx=ctx)
        usage = TokenUsage(10, 5, 15)
        session._usage = usage

        tracker = _Tracker()
        session.finalize(tracker, [_Hook()])

        assert "on_response" in calls
        assert tracker.recorded == usage
        assert ctx.response_model == "gpt-4o"


# ═══════════════════════════════════════════════════════════════════════
# AIClient — sync chat_stream
# ═══════════════════════════════════════════════════════════════════════


class TestAIClientChatStream:
    """AIClient.chat_stream() — sync 流式 + hook 验证。"""

    def test_chat_stream_yields_events(self):
        provider = _mock_provider()
        provider.chat_stream.return_value = iter(
            [StreamText(text="Hello"), StreamText(text=" world"), StreamFinish(finish_reason="stop")]
        )
        client = _make_client(provider=provider)

        events = list(client.chat_stream([ChatMessage(Role.USER, "Hi")]))
        assert len(events) == 3
        assert isinstance(events[0], StreamText)
        assert isinstance(events[2], StreamFinish)

    def test_chat_stream_triggers_hooks_and_tracker(self):
        calls = []

        class _Hook:
            def on_request(self, ctx):
                calls.append("on_request")

            def on_response(self, ctx, resp):
                calls.append("on_response")

            def on_error(self, ctx, err):
                calls.append("on_error")

            def on_retry(self, ctx, err, attempt, delay):
                calls.append("on_retry")

        provider = _mock_provider()
        provider.chat_stream.return_value = iter(
            [StreamText(text="hi"), StreamFinish(finish_reason="stop", usage=TokenUsage(5, 2, 7))]
        )
        client = _make_client(AIConfig(api_key="sk-test"), hooks=[_Hook()], provider=provider)

        list(client.chat_stream([ChatMessage(Role.USER, "Hi")]))
        assert "on_request" in calls
        assert "on_response" in calls
        assert "on_retry" not in calls
        assert client.usage.total_tokens == 7

    def test_chat_stream_error_propagates(self):
        provider = _mock_provider()
        provider.chat_stream.side_effect = AIAuthError("unauthorized")
        client = _make_client(provider=provider)

        with pytest.raises(AIAuthError):
            list(client.chat_stream([ChatMessage(Role.USER, "Hi")]))

    def test_chat_stream_retryable_error_triggers_retry_hook(self):
        calls = []

        class _Hook:
            def on_request(self, ctx):
                calls.append("on_request")

            def on_response(self, ctx, resp):
                calls.append("on_response")

            def on_error(self, ctx, err):
                calls.append("on_error")

            def on_retry(self, ctx, err, attempt, delay):
                calls.append("on_retry")

        provider = _mock_provider()
        provider.chat_stream.side_effect = AIRateLimitError("rate limited")
        client = _make_client(hooks=[_Hook()], provider=provider)

        with pytest.raises(AIRateLimitError):
            list(client.chat_stream([ChatMessage(Role.USER, "Hi")]))
        assert "on_request" in calls
        assert "on_retry" in calls
        assert "on_error" in calls


# ═══════════════════════════════════════════════════════════════════════
# AIClient — embed hooks
# ═══════════════════════════════════════════════════════════════════════


class TestAIClientEmbedHooks:
    """AIClient.embed() — hook 链 + UsageTracker。"""

    async def test_embed_triggers_hooks_and_tracker(self):
        calls = []

        class _Hook:
            def on_request(self, ctx):
                calls.append("on_request")

            def on_response(self, ctx, resp):
                calls.append("on_response")

            def on_error(self, ctx, err):
                calls.append("on_error")

            def on_retry(self, ctx, err, attempt, delay):
                calls.append("on_retry")

        from astrocrawl.ai._provider import _SupportsEmbedding

        class _EmbedProvider:
            provider_name = "test"

            async def embed(self, texts, model):
                from astrocrawl.ai._types import EmbedResult

                return EmbedResult(vectors=[[0.1]], model=model, usage=TokenUsage(20, 0, 20))

        provider = _EmbedProvider()
        assert isinstance(provider, _SupportsEmbedding)
        client = _make_client(AIConfig(api_key="sk-test"), hooks=[_Hook()], provider=provider)

        result = await client.embed("test text")
        assert result.model != ""
        assert "on_request" in calls
        assert "on_response" in calls
        assert "on_retry" not in calls
        assert client.usage.total_tokens == 20

    async def test_embed_error_triggers_on_error(self):
        calls = []

        class _Hook:
            def on_request(self, ctx):
                calls.append("on_request")

            def on_response(self, ctx, resp):
                calls.append("on_response")

            def on_error(self, ctx, err):
                calls.append("on_error")

            def on_retry(self, ctx, err, attempt, delay):
                calls.append("on_retry")

        class _EmbedProvider:
            provider_name = "test"

            async def embed(self, texts, model):
                raise AIConnectionError("connection lost")

        provider = _EmbedProvider()
        client = _make_client(AIConfig(api_key="sk-test"), hooks=[_Hook()], provider=provider)

        with pytest.raises(AIConnectionError):
            await client.embed("test text")
        assert "on_request" in calls
        assert "on_error" in calls
        assert "on_response" not in calls

    async def test_embed_retryable_error_triggers_on_retry(self):
        calls = []

        class _Hook:
            def on_request(self, ctx):
                calls.append("on_request")

            def on_response(self, ctx, resp):
                calls.append("on_response")

            def on_error(self, ctx, err):
                calls.append("on_error")

            def on_retry(self, ctx, err, attempt, delay):
                calls.append("on_retry")

        class _EmbedProvider:
            provider_name = "test"

            async def embed(self, texts, model):
                raise AIRateLimitError("rate limited")

        provider = _EmbedProvider()
        client = _make_client(AIConfig(api_key="sk-test"), hooks=[_Hook()], provider=provider)

        with pytest.raises(AIRateLimitError):
            await client.embed("test text")
        assert "on_request" in calls
        assert "on_retry" in calls
        assert "on_error" in calls

    async def test_embed_operation_is_set(self):
        """embed() 设置 ctx.operation = 'embed'。"""
        operations = []

        class _Hook:
            def on_request(self, ctx):
                operations.append(ctx.operation)

            def on_response(self, ctx, resp):
                pass

            def on_error(self, ctx, err):
                pass

            def on_retry(self, ctx, err, attempt, delay):
                pass

        class _EmbedProvider:
            provider_name = "test"

            async def embed(self, texts, model):
                from astrocrawl.ai._types import EmbedResult

                return EmbedResult(vectors=[[0.1]], model=model, usage=None)

        provider = _EmbedProvider()
        client = _make_client(AIConfig(api_key="sk-test"), hooks=[_Hook()], provider=provider)

        await client.embed("test")
        assert operations == ["embed"]

    async def test_embed_unsupported_provider_raises(self):
        """embed() 在不支持 Embeddings 的 Provider 上抛 AIError。"""
        from astrocrawl.ai._errors import AIError

        provider = _mock_provider()  # does NOT implement _SupportsEmbedding
        client = _make_client(AIConfig(api_key="sk-test"), provider=provider)

        with pytest.raises(AIError, match="不支持 Embeddings"):
            await client.embed("test")


# ═══════════════════════════════════════════════════════════════════════
# AIClient — non-AIError exception paths
# ═══════════════════════════════════════════════════════════════════════


class TestAIClientNonAIError:
    """AIClient — 非 AIError 异常 → on_error 触发。"""

    def test_chat_non_aierror_triggers_on_error(self):
        calls = []

        class _Hook:
            def on_request(self, ctx):
                pass

            def on_response(self, ctx, resp):
                pass

            def on_error(self, ctx, err):
                calls.append(("on_error", type(err).__name__))

            def on_retry(self, ctx, err, attempt, delay):
                pass

        provider = _mock_provider()
        provider.chat.side_effect = ValueError("unexpected")
        client = _make_client(hooks=[_Hook()], provider=provider)

        with pytest.raises(ValueError):
            client.chat([ChatMessage(Role.USER, "Hi")])
        assert any(e[0] == "on_error" for e in calls)

    async def test_achat_non_aierror_triggers_on_error(self):
        calls = []

        class _Hook:
            def on_request(self, ctx):
                pass

            def on_response(self, ctx, resp):
                pass

            def on_error(self, ctx, err):
                calls.append(("on_error", type(err).__name__))

            def on_retry(self, ctx, err, attempt, delay):
                pass

        provider = _mock_provider()
        provider.achat = AsyncMock(side_effect=ValueError("unexpected"))
        client = _make_client(hooks=[_Hook()], provider=provider)

        with pytest.raises(ValueError):
            await client.achat([ChatMessage(Role.USER, "Hi")])
        assert any(e[0] == "on_error" for e in calls)

    def test_chat_stream_non_aierror_triggers_on_error(self):
        calls = []

        class _Hook:
            def on_request(self, ctx):
                pass

            def on_response(self, ctx, resp):
                pass

            def on_error(self, ctx, err):
                calls.append("on_error")

            def on_retry(self, ctx, err, attempt, delay):
                pass

        provider = _mock_provider()
        provider.chat_stream.side_effect = ValueError("unexpected")
        client = _make_client(hooks=[_Hook()], provider=provider)

        with pytest.raises(ValueError):
            list(client.chat_stream([ChatMessage(Role.USER, "Hi")]))
        assert "on_error" in calls

    async def test_achat_stream_non_aierror_triggers_on_error(self):
        calls = []

        class _Hook:
            def on_request(self, ctx):
                pass

            def on_response(self, ctx, resp):
                pass

            def on_error(self, ctx, err):
                calls.append("on_error")

            def on_retry(self, ctx, err, attempt, delay):
                pass

        provider = _mock_provider()
        provider.achat_stream.side_effect = ValueError("unexpected")
        client = _make_client(hooks=[_Hook()], provider=provider)

        with pytest.raises(ValueError):
            async for _ in client.achat_stream([ChatMessage(Role.USER, "Hi")]):
                pass
        assert "on_error" in calls

    async def test_embed_non_aierror_triggers_on_error(self):
        calls = []

        class _Hook:
            def on_request(self, ctx):
                pass

            def on_response(self, ctx, resp):
                pass

            def on_error(self, ctx, err):
                calls.append("on_error")

            def on_retry(self, ctx, err, attempt, delay):
                pass

        class _EmbedProvider:
            provider_name = "test"

            async def embed(self, texts, model):
                raise ValueError("unexpected")

        provider = _EmbedProvider()
        client = _make_client(AIConfig(api_key="sk-test"), hooks=[_Hook()], provider=provider)

        with pytest.raises(ValueError):
            await client.embed("test text")
        assert "on_error" in calls


# ═══════════════════════════════════════════════════════════════════════
# AIClient — hook exception resilience (防御性 except Exception: pass)
# ═══════════════════════════════════════════════════════════════════════


class TestAIClientHookResilience:
    """AIClient — 单个 hook 抛异常不阻断其他 hook 和主流程。"""

    def test_notify_request_suppresses_hook_exception(self):
        """_notify_request: hook.on_request 抛异常 → 不传播，响应正常返回。"""
        calls = []

        class _RaisingHook:
            def on_request(self, ctx):
                raise RuntimeError("boom")

            def on_response(self, ctx, resp):
                calls.append("on_response")

            def on_error(self, ctx, err):
                pass

            def on_retry(self, ctx, err, attempt, delay):
                pass

        provider = _mock_provider()
        client = _make_client(hooks=[_RaisingHook()], provider=provider)

        result = client.chat([ChatMessage(Role.USER, "Hi")])
        assert result.content == "mock"
        assert "on_response" in calls  # 后续 hook 仍执行

    def test_notify_response_suppresses_hook_exception(self):
        """_notify_response: hook.on_response 抛异常 → 不传播，响应正常返回。"""
        calls = []

        class _RaisingHook:
            def on_request(self, ctx):
                calls.append("on_request")

            def on_response(self, ctx, resp):
                raise RuntimeError("boom")

            def on_error(self, ctx, err):
                pass

            def on_retry(self, ctx, err, attempt, delay):
                pass

        provider = _mock_provider()
        client = _make_client(hooks=[_RaisingHook()], provider=provider)

        result = client.chat([ChatMessage(Role.USER, "Hi")])
        assert result.content == "mock"
        assert "on_request" in calls  # 前序 hook 已执行

    def test_notify_error_suppresses_hook_exception(self):
        """_notify_error: hook.on_error 抛异常 → 不传播，原始错误仍抛出。"""

        class _RaisingHook:
            def on_request(self, ctx):
                pass

            def on_response(self, ctx, resp):
                pass

            def on_error(self, ctx, err):
                raise RuntimeError("boom")

            def on_retry(self, ctx, err, attempt, delay):
                pass

        provider = _mock_provider()
        provider.chat.side_effect = ValueError("original")
        client = _make_client(hooks=[_RaisingHook()], provider=provider)

        with pytest.raises(ValueError, match="original"):
            client.chat([ChatMessage(Role.USER, "Hi")])

    def test_notify_retry_suppresses_hook_exception(self):
        """_notify_retry: hook.on_retry 抛异常 → 不传播，原始错误仍抛出。"""

        class _RaisingHook:
            def on_request(self, ctx):
                pass

            def on_response(self, ctx, resp):
                pass

            def on_error(self, ctx, err):
                pass

            def on_retry(self, ctx, err, attempt, delay):
                raise RuntimeError("boom")

        provider = _mock_provider()
        provider.chat.side_effect = AIRateLimitError("rate limited")
        client = _make_client(hooks=[_RaisingHook()], provider=provider)

        with pytest.raises(AIRateLimitError):
            client.chat([ChatMessage(Role.USER, "Hi")])

    def test_stream_session_finalize_suppresses_hook_exception(self):
        """_StreamSession.finalize: hook.on_response 抛异常 → 不传播，tracker 已记录。"""

        class _RaisingHook:
            def on_request(self, ctx):
                pass

            def on_response(self, ctx, resp):
                raise RuntimeError("boom")

            def on_error(self, ctx, err):
                pass

            def on_retry(self, ctx, err, attempt, delay):
                pass

        usage = TokenUsage(5, 2, 7)
        provider = _mock_provider()
        provider.chat_stream.return_value = iter(
            [StreamText(text="hi"), StreamFinish(finish_reason="stop", usage=usage)]
        )
        client = _make_client(hooks=[_RaisingHook()], provider=provider)

        # 完整消费流 — 不应抛异常
        events = list(client.chat_stream([ChatMessage(Role.USER, "Hi")]))
        assert len(events) == 2
        assert client.usage.total_tokens == 7  # tracker 已记录


# ═══════════════════════════════════════════════════════════════════════
# AIClient — _resolve_output_format 降级路径
# ═══════════════════════════════════════════════════════════════════════


class TestResolveOutputFormat:
    """_resolve_output_format — ADR-0008 能力感知降级全覆盖。"""

    # 最小 Pydantic 模型用于满足 json_schema format 的 schema_model 校验
    class _DummySchema:
        @staticmethod
        def model_json_schema():
            return {"type": "object", "properties": {}}

    def test_no_output_unchanged(self):
        """output=None → 直接返回，无降级。"""
        provider = _mock_provider()
        client = _make_client(provider=provider)

        resolved = _resolve_params(GenerationParams())  # output=None
        result = client._resolve_output_format(resolved)
        assert result.output is None

    def test_empty_caps_no_degradation(self):
        """caps 为空 frozenset → 不降级，保留 output。"""
        provider = _mock_provider()
        provider.supported_output_formats = frozenset()
        client = _make_client(provider=provider)

        p = GenerationParams(output=OutputConstraint(format="json_schema", schema_model=self._DummySchema))
        resolved = _resolve_params(p, default_temperature=0, default_max_tokens=1)
        result = client._resolve_output_format(resolved)
        # caps 为空，原样返回
        assert result.output is not None
        assert result.output.format == "json_schema"

    def test_format_in_caps_no_degradation(self):
        """请求的格式在 caps 中 → 不降级。"""
        provider = _mock_provider()  # supports json_object + json_schema
        client = _make_client(provider=provider)
        from astrocrawl.ai._config import _resolve_params
        from astrocrawl.ai._constraint import OutputConstraint

        p = GenerationParams(output=OutputConstraint(format="json_object"))
        resolved = _resolve_params(p, default_temperature=0, default_max_tokens=1)
        result = client._resolve_output_format(resolved)
        assert result.output is not None
        assert result.output.format == "json_object"

    def test_degrade_json_schema_to_json_object(self):
        """Provider 只支持 json_object → json_schema 降级到 json_object。"""
        provider = _mock_provider()
        provider.supported_output_formats = frozenset({"json_object"})
        client = _make_client(provider=provider)

        p = GenerationParams(output=OutputConstraint(format="json_schema", schema_model=self._DummySchema))
        resolved = _resolve_params(p, default_temperature=0, default_max_tokens=1)
        result = client._resolve_output_format(resolved)
        assert result.output is not None
        assert result.output.format == "json_object"
        assert result.output.json_schema is None  # 降级时 json_schema 被清空

    def test_degrade_to_off_when_no_fallback(self):
        """caps 不含任何降级链格式 → output 禁用为 None。"""
        provider = _mock_provider()
        provider.supported_output_formats = frozenset({"custom_only"})
        client = _make_client(provider=provider)

        p = GenerationParams(output=OutputConstraint(format="json_schema", schema_model=self._DummySchema))
        resolved = _resolve_params(p, default_temperature=0, default_max_tokens=1)
        result = client._resolve_output_format(resolved)
        assert result.output is None  # 完全降级

    def test_format_not_in_degradation_list_valueerror(self):
        """format 不在退化链列表中 → ValueError → idx=-1 → 从 degradation[0] 开始找。"""
        provider = _mock_provider()
        provider.supported_output_formats = frozenset({"json_object"})
        client = _make_client(provider=provider)
        from astrocrawl.ai._config import _resolve_params
        from astrocrawl.ai._constraint import OutputConstraint

        # 构造一个 format 完全不在 degradation 列表中的 _ResolvedParams
        p = GenerationParams(output=OutputConstraint(format="json_object"))
        resolved = _resolve_params(p, default_temperature=0, default_max_tokens=1)
        # 篡改为 degradation 列表中不存在的值 → 触发 ValueError 分支
        resolved = replace(resolved, output=replace(resolved.output, format="custom_format"))
        result = client._resolve_output_format(resolved)
        # ValueError → idx=-1 → degradation[0]="json_schema" 不在 caps
        # → fallback "json_object" 在 caps → 降级到 json_object
        assert result.output is not None
        assert result.output.format == "json_object"

    def test_degradation_list_format_in_caps(self):
        """format 在退化链中但不在 caps → idx 正常 → 找后续 fallback。"""
        provider = _mock_provider()
        provider.supported_output_formats = frozenset({"json_object"})
        client = _make_client(provider=provider)
        from astrocrawl.ai._config import _resolve_params
        from astrocrawl.ai._constraint import OutputConstraint

        # json_schema 在 degradation 中 (idx=0)，但 cap 只有 json_object
        p = GenerationParams(output=OutputConstraint(format="json_object"))
        resolved = _resolve_params(p, default_temperature=0, default_max_tokens=1)
        resolved = replace(resolved, output=replace(resolved.output, format="json_schema"))
        result = client._resolve_output_format(resolved)
        # degradation.index("json_schema")=0 → fallback[0]="json_object" 在 caps → 降级
        assert result.output is not None
        assert result.output.format == "json_object"


# ═══════════════════════════════════════════════════════════════════════
# AIClient — proxy_url 构造函数路径
# ═══════════════════════════════════════════════════════════════════════


class TestProxyUrlConstructor:
    """AIClient.__init__ — proxy_url 参数创建 httpx 客户端。"""

    def test_proxy_url_creates_httpx_clients(self):
        """proxy_url 传入时创建 httpx.Client/AsyncClient 并传给 _discover_provider。"""
        captured_kwargs = {}

        def _capture_discover(name, cfg, **kw):
            captured_kwargs.update(kw)
            return _mock_provider()

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("astrocrawl.ai._provider_registry._discover_provider", _capture_discover)
            AIClient(AIConfig(api_key="sk-test"), proxy_url="http://proxy:8080")

        assert "_http_client" in captured_kwargs
        assert "_async_http_client" in captured_kwargs

    def test_no_proxy_url_no_httpx_clients(self):
        """未传 proxy_url 时不创建 httpx 客户端。"""
        captured_kwargs = {}

        def _capture_discover(name, cfg, **kw):
            captured_kwargs.update(kw)
            return _mock_provider()

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("astrocrawl.ai._provider_registry._discover_provider", _capture_discover)
            AIClient(AIConfig(api_key="sk-test"))

        assert "_http_client" not in captured_kwargs
        assert "_async_http_client" not in captured_kwargs

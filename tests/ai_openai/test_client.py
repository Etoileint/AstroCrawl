"""测试：astrocrawl.ai.providers.openai._client — OpenAIClient + _map_error + list_models。

ADR-0006 #2: 从 tests/test_ai_client.py TestErrorMapping 迁移。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from astrocrawl.ai._config import AIConfig
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
from astrocrawl.ai.providers.openai import OpenAIClient, _map_error, create_provider, list_models


def _mock_response(status_code: int):
    resp = MagicMock()
    resp.status_code = status_code
    return resp


# ═══════════════════════════════════════════════════════════════════════
# _map_error — openai SDK 异常 → AIError 子类
# ═══════════════════════════════════════════════════════════════════════


class TestMapError:
    """_map_error 将 openai SDK 异常映射到 AIError 子类。"""

    def test_non_openai_error_wraps_as_ai_error(self):
        result = _map_error(ValueError("generic error"))
        assert isinstance(result, AIError)

    def test_auth_error(self):
        from openai import AuthenticationError

        err = AuthenticationError("unauthorized", response=_mock_response(401), body=None)
        assert isinstance(_map_error(err), AIAuthError)

    def test_permission_denied_error(self):
        from openai import PermissionDeniedError

        err = PermissionDeniedError("forbidden", response=_mock_response(403), body=None)
        assert isinstance(_map_error(err), AIAuthError)

    def test_rate_limit_error(self):
        from openai import RateLimitError

        err = RateLimitError("rate limited", response=_mock_response(429), body=None)
        assert isinstance(_map_error(err), AIRateLimitError)

    def test_timeout_error(self):
        from openai import APITimeoutError

        err = APITimeoutError(request=MagicMock())
        assert isinstance(_map_error(err), AITimeoutError)

    def test_connection_error(self):
        from openai import APIConnectionError

        err = APIConnectionError(request=MagicMock())
        assert isinstance(_map_error(err), AIConnectionError)

    def test_server_error(self):
        from openai import InternalServerError

        err = InternalServerError("server error", response=_mock_response(500), body=None)
        assert isinstance(_map_error(err), AIServerError)

    def test_bad_request_error(self):
        from openai import BadRequestError

        err = BadRequestError("bad request", response=_mock_response(400), body=None)
        assert isinstance(_map_error(err), AIInvalidRequestError)

    def test_content_filter_error(self):
        from openai import BadRequestError

        err = BadRequestError("content_filter triggered", response=_mock_response(400), body=None)
        assert isinstance(_map_error(err), AIContentFilterError)

    def test_unprocessable_entity_error(self):
        from openai import UnprocessableEntityError

        err = UnprocessableEntityError("unprocessable", response=_mock_response(422), body=None)
        assert isinstance(_map_error(err), AIInvalidRequestError)

    def test_rate_limit_429_api_status(self):
        from openai import APIStatusError

        err = APIStatusError("too many", response=_mock_response(429), body=None)
        result = _map_error(err)
        assert isinstance(result, AIRateLimitError)


# ═══════════════════════════════════════════════════════════════════════
# OpenAIClient — 构造 + 基本行为
# ═══════════════════════════════════════════════════════════════════════


class TestOpenAIClientInit:
    """OpenAIClient 构造。"""

    def test_provider_name_is_openai(self):
        client = OpenAIClient(api_key="sk-test")
        assert client.provider_name == "openai"

    def test_accepts_all_config_fields(self):
        client = OpenAIClient(
            api_key="sk-test",
            base_url="https://custom.api.com/v1",
            timeout=30.0,
            max_retries=2,
        )
        assert client._api_key == "sk-test"
        assert client._base_url == "https://custom.api.com/v1"
        assert client._timeout == 30.0
        assert client._max_retries == 2

    def test_ignores_unknown_kwargs(self):
        client = OpenAIClient(api_key="sk-test", extra_param="value")
        assert client._api_key == "sk-test"


class TestOpenAIClientChat:
    """OpenAIClient.chat() — mock _get_sync_client。"""

    @staticmethod
    def _make_response(content="Hello!", model="gpt-4o-mini", finish_reason="stop"):
        choice = MagicMock()
        choice.message.content = content
        choice.finish_reason = finish_reason
        usage = MagicMock()
        usage.prompt_tokens = 10
        usage.completion_tokens = 5
        usage.total_tokens = 15
        response = MagicMock()
        response.choices = [choice]
        response.model = model
        response.usage = usage
        return response

    @staticmethod
    def _make_params(model="gpt-4o-mini", temperature=0.7, max_tokens=4096):
        from astrocrawl.ai._config import _ResolvedParams

        return _ResolvedParams(model=model, temperature=temperature, max_tokens=max_tokens)

    def test_chat_returns_chat_response(self):
        client = OpenAIClient(api_key="sk-test")
        mock_sync = MagicMock()
        mock_sync.chat.completions.create.return_value = self._make_response()
        client._get_sync_client = MagicMock(return_value=mock_sync)

        from astrocrawl.ai._types import ChatMessage, Role

        result = client.chat([ChatMessage(Role.USER, "Hi")], None, self._make_params())
        assert result.content == "Hello!"
        assert result.model == "gpt-4o-mini"

    def test_chat_passes_kwargs_correctly(self):
        client = OpenAIClient(api_key="sk-test")
        mock_sync = MagicMock()
        mock_sync.chat.completions.create.return_value = self._make_response()
        client._get_sync_client = MagicMock(return_value=mock_sync)

        from astrocrawl.ai._types import ChatMessage, Role

        params = self._make_params(temperature=0.2, max_tokens=512)
        client.chat([ChatMessage(Role.USER, "Hi")], None, params)

        kwargs = mock_sync.chat.completions.create.call_args.kwargs
        assert kwargs["temperature"] == 0.2
        assert kwargs["max_tokens"] == 512
        assert kwargs["stream"] is False


# ═══════════════════════════════════════════════════════════════════════
# create_provider
# ═══════════════════════════════════════════════════════════════════════


class TestCreateProvider:
    def test_returns_openai_client(self):
        config = AIConfig(api_key="sk-test", base_url="https://api.openai.com/v1")
        provider = create_provider(config)
        assert isinstance(provider, OpenAIClient)
        assert provider._api_key == "sk-test"
        assert provider._base_url == "https://api.openai.com/v1"

    def test_empty_base_url_defaults_to_openai(self):
        config = AIConfig(api_key="sk-test", base_url="")
        provider = create_provider(config)
        assert provider._base_url == "https://api.openai.com/v1"

    def test_uses_config_timeout(self):
        config = AIConfig(api_key="sk-test", timeout=45.0)
        provider = create_provider(config)
        assert provider._timeout == 45.0

    def test_uses_config_max_retries(self):
        config = AIConfig(api_key="sk-test", max_retries=3)
        provider = create_provider(config)
        assert provider._max_retries == 3

    def test_provider_kwargs_passed_through(self):
        config = AIConfig(api_key="sk-test")
        provider = create_provider(config, extra_param="val")
        # OpenAIClient accepts **kwargs but ignores unknown ones
        assert isinstance(provider, OpenAIClient)


class TestListModels:
    def test_returns_model_ids(self):
        """list_models 调用 OpenAI API 返回模型 ID 列表。"""
        fake_models = [
            SimpleNamespace(id="gpt-4o"),
            SimpleNamespace(id="gpt-4o-mini"),
        ]
        mock_client = MagicMock()
        mock_client.models.list.return_value = fake_models

        with patch("openai.OpenAI", return_value=mock_client):
            result = list_models("https://api.openai.com/v1", "sk-test", 15.0)

        assert result == ["gpt-4o", "gpt-4o-mini"]
        mock_client.models.list.assert_called_once()

    def test_uses_default_base_url_when_empty(self):
        """空 base_url 时使用 OpenAI 默认端点。"""
        mock_client = MagicMock()
        mock_client.models.list.return_value = []

        with patch("openai.OpenAI") as mock_openai:
            mock_openai.return_value = mock_client
            list_models("", "sk-test", 15.0)

        mock_openai.assert_called_once_with(
            base_url="https://api.openai.com/v1",
            api_key="sk-test",
            timeout=15.0,
        )


# ═══════════════════════════════════════════════════════════════════════
# ADR-0008: _build_request_kwargs — structured output
# ═══════════════════════════════════════════════════════════════════════


class TestBuildRequestKwargsOutput:
    @staticmethod
    def _client(**kw):
        return OpenAIClient(api_key="test-key", **kw)

    @staticmethod
    def _params(**kw):
        from astrocrawl.ai._config import _ResolvedParams

        return _ResolvedParams(model="gpt-4o", temperature=0.0, max_tokens=512, **kw)

    def test_json_object_adds_response_format(self):
        from astrocrawl.ai._config import _ResolvedOutput

        params = self._params(output=_ResolvedOutput(format="json_object", json_schema=None))
        kwargs = self._client()._build_request_kwargs([], None, params)
        assert kwargs["response_format"] == {"type": "json_object"}

    def test_json_schema_adds_response_format_with_schema(self):
        from astrocrawl.ai._config import _ResolvedOutput

        params = self._params(output=_ResolvedOutput(format="json_schema", json_schema={"type": "object"}))
        kwargs = self._client()._build_request_kwargs([], None, params)
        assert kwargs["response_format"]["type"] == "json_schema"
        assert kwargs["response_format"]["json_schema"]["strict"] is True
        assert kwargs["response_format"]["json_schema"]["schema"] == {"type": "object"}

    def test_output_overrides_extra_body_response_format(self):
        from astrocrawl.ai._config import _ResolvedOutput

        params = self._params(
            output=_ResolvedOutput(format="json_object", json_schema=None),
            extra_body={"response_format": {"type": "text"}},
        )
        kwargs = self._client()._build_request_kwargs([], None, params)
        assert kwargs["response_format"] == {"type": "json_object"}
        assert "response_format" not in kwargs["extra_body"]

    def test_no_output_no_response_format(self):
        params = self._params()
        kwargs = self._client()._build_request_kwargs([], None, params)
        assert "response_format" not in kwargs


# ═══════════════════════════════════════════════════════════════════════
# OpenAIClient — aclose / close
# ═══════════════════════════════════════════════════════════════════════


class TestOpenAIClientAclose:
    """OpenAIClient.aclose() — 异步清理 HTTP clients。"""

    async def test_aclose_no_clients(self):
        client = OpenAIClient(api_key="k")
        await client.aclose()
        assert client._async_client is None
        assert client._sync_client is None

    async def test_aclose_with_async_client(self):
        client = OpenAIClient(api_key="k")
        mock_async = MagicMock()
        mock_async.close = AsyncMock()
        client._async_client = mock_async
        await client.aclose()
        assert client._async_client is None
        mock_async.close.assert_called_once()

    async def test_aclose_handles_async_close_exception(self):
        client = OpenAIClient(api_key="k")
        mock_async = MagicMock()
        mock_async.close = AsyncMock(side_effect=OSError("connection lost"))
        client._async_client = mock_async
        await client.aclose()
        assert client._async_client is None

    async def test_aclose_with_both_clients(self):
        client = OpenAIClient(api_key="k")
        mock_async = MagicMock()
        mock_async.close = AsyncMock()
        mock_sync = MagicMock()
        client._async_client = mock_async
        client._sync_client = mock_sync
        await client.aclose()
        assert client._async_client is None
        assert client._sync_client is None
        mock_async.close.assert_called_once()
        mock_sync.close.assert_called_once()

    async def test_aclose_handles_sync_close_exception(self):
        client = OpenAIClient(api_key="k")
        mock_sync = MagicMock()
        mock_sync.close = MagicMock(side_effect=OSError("fd leak"))
        client._sync_client = mock_sync
        await client.aclose()
        assert client._sync_client is None

    async def test_aclose_idempotent(self):
        client = OpenAIClient(api_key="k")
        mock_async = MagicMock()
        mock_async.close = AsyncMock()
        client._async_client = mock_async
        await client.aclose()
        await client.aclose()
        mock_async.close.assert_called_once()


class TestOpenAIClientClose:
    """OpenAIClient.close() — 同步清理 sync HTTP client。"""

    def test_close_no_client(self):
        client = OpenAIClient(api_key="k")
        client.close()
        assert client._sync_client is None

    def test_close_with_sync_client(self):
        client = OpenAIClient(api_key="k")
        mock_sync = MagicMock()
        client._sync_client = mock_sync
        client.close()
        assert client._sync_client is None
        mock_sync.close.assert_called_once()

    def test_close_handles_exception(self):
        client = OpenAIClient(api_key="k")
        mock_sync = MagicMock()
        mock_sync.close = MagicMock(side_effect=OSError("fd leak"))
        client._sync_client = mock_sync
        client.close()
        assert client._sync_client is None

    def test_close_idempotent(self):
        client = OpenAIClient(api_key="k")
        mock_sync = MagicMock()
        client._sync_client = mock_sync
        client.close()
        client.close()
        mock_sync.close.assert_called_once()

    def test_close_only_sync_not_async(self):
        """close() 只清理 sync client，不动 async client。"""
        client = OpenAIClient(api_key="k")
        mock_async = MagicMock()
        mock_sync = MagicMock()
        client._async_client = mock_async
        client._sync_client = mock_sync
        client.close()
        assert client._sync_client is None
        assert client._async_client is mock_async
        mock_async.close.assert_not_called()

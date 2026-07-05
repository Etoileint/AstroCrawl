"""测试: astrocrawl.ai.providers.google — GoogleClient + _map_error + 辅助函数。

纯逻辑测试——无需 google-genai SDK 或 API 调用。
通过 sys.modules 注入 fake google.genai 模块使 _map_error 的 import 成功。
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from astrocrawl.ai._config import AIConfig, _ResolvedParams
from astrocrawl.ai._errors import AIAuthError, AIError, AIInvalidRequestError, AIRateLimitError, AIServerError
from astrocrawl.ai._types import ChatMessage, Role
from astrocrawl.ai.providers.google import GoogleClient, _map_error, create_provider

# ═══════════════════════════════════════════════════════════════════════
# _map_error helpers — 注入模块避免 SDK import 失败
# ═══════════════════════════════════════════════════════════════════════


class _FakeClientError(Exception):
    """模拟 google.genai.errors.ClientError，含 code 属性。"""

    def __init__(self, message: str, code: int):
        super().__init__(message)
        self.code = code


class _FakeServerError(Exception):
    """模拟 google.genai.errors.ServerError。"""

    def __init__(self, message: str):
        super().__init__(message)


# 注入 fake google.genai 模块到 sys.modules，让 _map_error 中的 import 成功
_fake_errors = SimpleNamespace(ClientError=_FakeClientError, ServerError=_FakeServerError)
_fake_genai = SimpleNamespace(errors=_fake_errors)
sys.modules["google.genai"] = _fake_genai
sys.modules["google.genai.errors"] = _fake_errors


class TestMapError:
    """_map_error — google-genai SDK 异常映射。"""

    def test_generic_exception_returns_aierror(self):
        err = _map_error(Exception("generic"))
        assert isinstance(err, AIError)
        assert str(err) == "generic"

    def test_client_401_returns_aiauth(self):
        err = _map_error(_FakeClientError("unauthorized", 401))
        assert isinstance(err, AIAuthError)

    def test_client_403_returns_aiauth(self):
        err = _map_error(_FakeClientError("forbidden", 403))
        assert isinstance(err, AIAuthError)

    def test_client_429_returns_ratelimit(self):
        err = _map_error(_FakeClientError("quota exceeded", 429))
        assert isinstance(err, AIRateLimitError)

    def test_client_400_returns_invalid(self):
        err = _map_error(_FakeClientError("bad request", 400))
        assert isinstance(err, AIInvalidRequestError)

    def test_server_error_returns_server(self):
        err = _map_error(_FakeServerError("internal error"))
        assert isinstance(err, AIServerError)

    def test_fallback_no_sdk(self):
        err = _map_error(RuntimeError("unexpected crash"))
        assert isinstance(err, AIError)

    def test_unknown_client_code_returns_invalid(self):
        err = _map_error(_FakeClientError("teapot", 418))
        assert isinstance(err, AIInvalidRequestError)


# ═══════════════════════════════════════════════════════════════════════
# _convert_messages
# ═══════════════════════════════════════════════════════════════════════


class TestConvertMessages:
    def test_system_message_extracted(self):
        msgs = [
            ChatMessage(Role.SYSTEM, "You are helpful"),
            ChatMessage(Role.USER, "Hello"),
        ]
        contents, system = GoogleClient._convert_messages(msgs)
        assert system == "You are helpful"
        assert len(contents) == 1
        assert contents[0]["role"] == "user"
        assert contents[0]["parts"][0]["text"] == "Hello"

    def test_no_system_message(self):
        msgs = [ChatMessage(Role.USER, "Hello")]
        contents, system = GoogleClient._convert_messages(msgs)
        assert system is None
        assert len(contents) == 1

    def test_multiple_user_messages(self):
        msgs = [
            ChatMessage(Role.USER, "First"),
            ChatMessage(Role.ASSISTANT, "Response"),
            ChatMessage(Role.USER, "Second"),
        ]
        contents, system = GoogleClient._convert_messages(msgs)
        assert system is None
        assert len(contents) == 3
        assert contents[0]["role"] == "user"
        assert contents[1]["role"] == "assistant"
        assert contents[2]["role"] == "user"

    def test_empty_messages(self):
        contents, system = GoogleClient._convert_messages([])
        assert system is None
        assert contents == []

    def test_multiple_system_takes_last(self):
        msgs = [
            ChatMessage(Role.SYSTEM, "First"),
            ChatMessage(Role.SYSTEM, "Last"),
        ]
        contents, system = GoogleClient._convert_messages(msgs)
        assert system == "Last"
        assert contents == []

    def test_only_system_messages(self):
        msgs = [ChatMessage(Role.SYSTEM, "System only")]
        contents, system = GoogleClient._convert_messages(msgs)
        assert system == "System only"
        assert contents == []


# ═══════════════════════════════════════════════════════════════════════
# _build_config
# ═══════════════════════════════════════════════════════════════════════


class TestBuildConfig:
    @staticmethod
    def _client(**kw):
        return GoogleClient(api_key="test-key", **kw)

    def test_minimal_config(self):
        params = _ResolvedParams(model="gemini-pro", temperature=0.7, max_tokens=1024)
        config = self._client()._build_config(None, None, params)
        assert config["temperature"] == 0.7
        assert config["max_output_tokens"] == 1024

    def test_with_system_instruction(self):
        params = _ResolvedParams(model="gemini-pro", temperature=0.5, max_tokens=512)
        config = self._client()._build_config("Be helpful", None, params)
        assert config["system_instruction"] == "Be helpful"

    def test_system_none_omitted(self):
        params = _ResolvedParams(model="gemini-pro", temperature=0.5, max_tokens=512)
        config = self._client()._build_config(None, None, params)
        assert "system_instruction" not in config

    def test_with_tools(self):
        params = _ResolvedParams(model="gemini-pro", temperature=0.5, max_tokens=512)
        tools = [{"name": "search", "description": "search"}]
        config = self._client()._build_config(None, tools, params)
        assert "tools" in config
        assert config["tools"][0]["function_declarations"] == [{"name": "search", "description": "search"}]

    def test_with_top_p_and_stop(self):
        params = _ResolvedParams(model="gemini-pro", temperature=0.5, max_tokens=512, top_p=0.9, stop=["END"])
        config = self._client()._build_config(None, None, params)
        assert config["top_p"] == 0.9
        assert config["stop_sequences"] == ["END"]

    def test_top_p_none_omitted(self):
        params = _ResolvedParams(model="gemini-pro", temperature=0.5, max_tokens=512, top_p=None)
        config = self._client()._build_config(None, None, params)
        assert "top_p" not in config

    def test_stop_none_omitted(self):
        params = _ResolvedParams(model="gemini-pro", temperature=0.5, max_tokens=512, stop=None)
        config = self._client()._build_config(None, None, params)
        assert "stop_sequences" not in config

    def test_tools_none_omitted(self):
        params = _ResolvedParams(model="gemini-pro", temperature=0.5, max_tokens=512)
        config = self._client()._build_config(None, None, params)
        assert "tools" not in config

    # ADR-0008: structured output

    def test_json_object_adds_response_mime_type(self):
        from astrocrawl.ai._config import _ResolvedOutput

        params = _ResolvedParams(
            model="gemini-pro",
            temperature=0.5,
            max_tokens=512,
            output=_ResolvedOutput(format="json_object", json_schema=None),
        )
        config = self._client()._build_config(None, None, params)
        assert config["response_mime_type"] == "application/json"
        assert "response_schema" not in config

    def test_json_schema_adds_response_schema(self):
        from astrocrawl.ai._config import _ResolvedOutput

        params = _ResolvedParams(
            model="gemini-pro",
            temperature=0.5,
            max_tokens=512,
            output=_ResolvedOutput(format="json_schema", json_schema={"type": "object"}),
        )
        config = self._client()._build_config(None, None, params)
        assert config["response_mime_type"] == "application/json"
        assert config["response_schema"] == {"type": "object"}

    def test_no_output_no_response_config(self):
        params = _ResolvedParams(model="gemini-pro", temperature=0.5, max_tokens=512)
        config = self._client()._build_config(None, None, params)
        assert "response_mime_type" not in config


# ═══════════════════════════════════════════════════════════════════════
# _parse_response helpers
# ═══════════════════════════════════════════════════════════════════════


def _candidate(finish_reason="STOP", content=None):
    cand = MagicMock()
    cand.finish_reason = finish_reason
    cand.content = content
    return cand


def _fc_part(name="search", args=None):
    """创建含 function_call 的 content part。"""
    fc = MagicMock()
    fc.name = name
    fc.args = args or {}
    part = MagicMock()
    part.function_call = fc
    return part


class TestParseResponse:
    def test_text_response(self):
        resp = MagicMock()
        resp.text = "Hello World"
        resp.candidates = [_candidate("STOP")]
        resp.model_version = "gemini-1.5-pro"
        resp.usage_metadata = MagicMock(prompt_token_count=10, candidates_token_count=5, total_token_count=15)

        result = GoogleClient._parse_response(resp)
        assert result.content == "Hello World"
        assert result.model == "gemini-1.5-pro"
        assert result.finish_reason == "STOP"
        assert result.usage.prompt_tokens == 10
        assert result.usage.completion_tokens == 5
        assert result.usage.total_tokens == 15

    def test_no_candidates(self):
        resp = MagicMock()
        resp.text = ""
        resp.candidates = []

        result = GoogleClient._parse_response(resp)
        assert result.content == ""
        assert result.finish_reason == "stop"

    def test_no_usage_metadata(self):
        # spec_set 限定属性集，usage_metadata 不存在于 spec → getattr 返回 default
        resp = MagicMock(spec_set=["text", "candidates", "model_version"])
        resp.text = "Hi"
        resp.candidates = [_candidate("STOP")]
        resp.model_version = "x"

        result = GoogleClient._parse_response(resp)
        assert result.usage is None

    def test_no_model_version(self):
        resp = MagicMock(spec_set=["text", "candidates"])
        resp.text = "Hi"
        resp.candidates = [_candidate("STOP")]

        result = GoogleClient._parse_response(resp)
        assert result.model == ""

    def test_function_call_in_candidate(self):
        resp = MagicMock()
        resp.text = ""
        content = MagicMock()
        content.parts = [_fc_part("search", {"q": "hello"})]
        resp.candidates = [_candidate("STOP", content)]
        resp.usage_metadata = None

        result = GoogleClient._parse_response(resp)
        assert result.tool_calls is not None
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "search"
        assert result.tool_calls[0].arguments == {"q": "hello"}

    def test_finish_reason_non_string(self):
        resp = MagicMock()
        resp.text = "Hi"
        resp.candidates = [_candidate(1)]
        resp.usage_metadata = None

        result = GoogleClient._parse_response(resp)
        assert result.finish_reason == "1"

    def test_mixed_parts_with_and_without_function_call(self):
        resp = MagicMock()
        resp.text = "thinking..."
        part_no_fn = MagicMock()
        part_no_fn.function_call = None
        part_fn = _fc_part("get_weather", {"city": "NYC"})
        content = MagicMock()
        content.parts = [part_no_fn, part_fn]
        resp.candidates = [_candidate("STOP", content)]
        resp.usage_metadata = None

        result = GoogleClient._parse_response(resp)
        assert result.tool_calls is not None
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "get_weather"


# ═══════════════════════════════════════════════════════════════════════
# GoogleClient — init
# ═══════════════════════════════════════════════════════════════════════


class TestGoogleClientInit:
    def test_provider_name(self):
        client = GoogleClient(api_key="sk-test")
        assert client.provider_name == "google"

    def test_default_params(self):
        client = GoogleClient(api_key="sk-test")
        assert client._api_key == "sk-test"
        assert client._timeout == 60.0
        assert client._sync_client is None
        assert client._async_client is None

    def test_custom_params(self):
        client = GoogleClient(api_key="sk-custom", timeout=30.0)
        assert client._timeout == 30.0

    def test_extra_kwargs_stored(self):
        client = GoogleClient(api_key="k", custom_option="val")
        assert client._extra_kwargs == {"custom_option": "val"}


# ═══════════════════════════════════════════════════════════════════════
# create_provider
# ═══════════════════════════════════════════════════════════════════════


class TestCreateProvider:
    def test_returns_google_client(self):
        config = AIConfig(api_key="sk-test")
        provider = create_provider(config)
        assert isinstance(provider, GoogleClient)
        assert provider._api_key == "sk-test"

    def test_uses_config_timeout(self):
        config = AIConfig(api_key="sk-test", timeout=45.0)
        provider = create_provider(config)
        assert provider._timeout == 45.0

    def test_provider_kwargs_passed_through(self):
        config = AIConfig(api_key="sk-test")
        provider = create_provider(config, custom_feature=True)
        assert provider._extra_kwargs == {"custom_feature": True}


# ═══════════════════════════════════════════════════════════════════════
# aclose
# ═══════════════════════════════════════════════════════════════════════


class TestGoogleClientAclose:
    async def test_aclose_no_clients(self):
        client = GoogleClient(api_key="k")
        await client.aclose()
        assert client._async_client is None
        assert client._sync_client is None

    async def test_aclose_with_sync_only(self):
        client = GoogleClient(api_key="k")
        client._sync_client = MagicMock()
        await client.aclose()
        assert client._sync_client is None

    async def test_aclose_with_async_client(self):
        client = GoogleClient(api_key="k")
        mock_async = MagicMock()
        mock_async.aio = MagicMock()
        mock_async.aio.close = MagicMock()
        client._async_client = mock_async
        await client.aclose()
        assert client._async_client is None

    async def test_aclose_handles_close_exception(self):
        client = GoogleClient(api_key="k")
        mock_async = MagicMock()
        mock_async.aio = MagicMock()
        mock_async.aio.close = MagicMock(side_effect=Exception("close failed"))
        client._async_client = mock_async
        await client.aclose()
        assert client._async_client is None


class TestListModels:
    def test_returns_model_names_with_sdk(self):
        """SDK 安装时调用真实 API，strip models/ 前缀返回 model ID。"""
        from types import SimpleNamespace

        from astrocrawl.ai.providers.google import list_models

        fake_m1 = SimpleNamespace(name="models/gemini-2.0-flash")
        fake_m2 = SimpleNamespace(name="models/gemini-1.5-pro")
        mock_client = MagicMock()
        mock_client.models.list.return_value = [fake_m1, fake_m2]

        fake_genai = SimpleNamespace(
            Client=MagicMock(return_value=mock_client),
            types=SimpleNamespace(HttpOptions=MagicMock()),
        )
        fake_genai.errors = sys.modules["google.genai"].errors

        with patch.dict("sys.modules", {"google.genai": fake_genai}):
            result = list_models("", "sk-test", 15.0)

        assert result == ["gemini-2.0-flash", "gemini-1.5-pro"]

    def test_handles_unprefixed_names(self):
        """无 models/ 前缀的模型名原样返回。"""
        from types import SimpleNamespace

        from astrocrawl.ai.providers.google import list_models

        fake_m = SimpleNamespace(name="gemini-custom")
        mock_client = MagicMock()
        mock_client.models.list.return_value = [fake_m]

        fake_genai = SimpleNamespace(
            Client=MagicMock(return_value=mock_client),
            types=SimpleNamespace(HttpOptions=MagicMock()),
        )
        fake_genai.errors = sys.modules["google.genai"].errors

        with patch.dict("sys.modules", {"google.genai": fake_genai}):
            result = list_models("", "sk-test", 15.0)

        assert result == ["gemini-custom"]

    def test_passes_http_options_when_base_url_set(self):
        """非空 base_url 时设置 HttpOptions。"""
        from types import SimpleNamespace

        from astrocrawl.ai.providers.google import list_models

        fake_m = SimpleNamespace(name="models/gemini-pro")
        mock_client = MagicMock()
        mock_client.models.list.return_value = [fake_m]

        mock_http_options = MagicMock()
        fake_genai = SimpleNamespace(
            Client=MagicMock(return_value=mock_client),
            types=SimpleNamespace(HttpOptions=mock_http_options),
        )
        fake_genai.errors = sys.modules["google.genai"].errors

        with patch.dict("sys.modules", {"google.genai": fake_genai}):
            result = list_models("https://custom.api.com", "sk-test", 15.0)

        assert result == ["gemini-pro"]
        mock_http_options.assert_called_once_with(base_url="https://custom.api.com")

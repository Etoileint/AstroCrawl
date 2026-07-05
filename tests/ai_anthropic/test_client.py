"""测试: astrocrawl.ai.providers.anthropic — AnthropicClient + _map_error + 辅助函数。

纯逻辑测试——无需 anthropic SDK 或 API 调用。
通过 sys.modules 注入 fake anthropic 模块使 _map_error 的 import 成功。
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from astrocrawl.ai._config import AIConfig, _ResolvedParams
from astrocrawl.ai._errors import AIAuthError, AIError, AIInvalidRequestError, AIRateLimitError, AIServerError
from astrocrawl.ai._types import ChatMessage, Role
from astrocrawl.ai.providers.anthropic import AnthropicClient, _map_error, create_provider

# ═══════════════════════════════════════════════════════════════════════
# _map_error — SDK error → AIError 映射
# ═══════════════════════════════════════════════════════════════════════


class _FakeAPIStatusError(Exception):
    """模拟 anthropic.APIStatusError，支持 isinstance 检查。"""

    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


# 注入 fake anthropic 模块到 sys.modules，让 _map_error 中的 `from anthropic import APIStatusError` 成功
_fake_anthropic = SimpleNamespace(APIStatusError=_FakeAPIStatusError)
sys.modules["anthropic"] = _fake_anthropic


class TestMapError:
    """_map_error — anthropic SDK 异常映射。"""

    def test_generic_exception_returns_aierror(self):
        err = _map_error(Exception("generic"))
        assert isinstance(err, AIError)
        assert str(err) == "generic"

    def test_non_apistatus_error_returns_aierror(self):
        err = _map_error(ValueError("not an api error"))
        assert isinstance(err, AIError)

    def test_apistatus_401_returns_aiauth(self):
        err = _map_error(_FakeAPIStatusError("Unauthorized", 401))
        assert isinstance(err, AIAuthError)

    def test_apistatus_403_returns_aiauth(self):
        err = _map_error(_FakeAPIStatusError("Forbidden", 403))
        assert isinstance(err, AIAuthError)

    def test_apistatus_429_returns_ratelimit(self):
        err = _map_error(_FakeAPIStatusError("Rate limited", 429))
        assert isinstance(err, AIRateLimitError)

    def test_apistatus_500_returns_server(self):
        err = _map_error(_FakeAPIStatusError("Server error", 500))
        assert isinstance(err, AIServerError)

    def test_apistatus_502_returns_server(self):
        err = _map_error(_FakeAPIStatusError("Bad gateway", 502))
        assert isinstance(err, AIServerError)

    def test_apistatus_400_returns_invalid(self):
        err = _map_error(_FakeAPIStatusError("Bad request", 400))
        assert isinstance(err, AIInvalidRequestError)

    def test_unknown_status_returns_aierror(self):
        err = _map_error(_FakeAPIStatusError("Teapot", 418))
        assert isinstance(err, AIError)


# ═══════════════════════════════════════════════════════════════════════
# _split_messages
# ═══════════════════════════════════════════════════════════════════════


class TestSplitMessages:
    def test_system_message_extracted(self):
        msgs = [
            ChatMessage(Role.SYSTEM, "You are helpful"),
            ChatMessage(Role.USER, "Hello"),
        ]
        system, user_msgs = AnthropicClient._split_messages(msgs)
        assert system == "You are helpful"
        assert len(user_msgs) == 1
        assert user_msgs[0]["role"] == "user"

    def test_no_system_message(self):
        msgs = [ChatMessage(Role.USER, "Hello")]
        system, user_msgs = AnthropicClient._split_messages(msgs)
        assert system is None
        assert len(user_msgs) == 1

    def test_tool_message_included(self):
        msgs = [
            ChatMessage(Role.USER, "call tool"),
            ChatMessage(Role.TOOL, "result", tool_call_id="tc_1", name="my_func"),
        ]
        system, user_msgs = AnthropicClient._split_messages(msgs)
        assert system is None
        assert len(user_msgs) == 2
        assert user_msgs[1]["tool_call_id"] == "tc_1"
        assert user_msgs[1]["name"] == "my_func"

    def test_empty_messages(self):
        system, user_msgs = AnthropicClient._split_messages([])
        assert system is None
        assert user_msgs == []

    def test_multiple_system_takes_last(self):
        msgs = [
            ChatMessage(Role.SYSTEM, "First system"),
            ChatMessage(Role.SYSTEM, "Second system"),
        ]
        system, user_msgs = AnthropicClient._split_messages(msgs)
        assert system == "Second system"

    def test_only_system_messages(self):
        msgs = [ChatMessage(Role.SYSTEM, "System only")]
        system, user_msgs = AnthropicClient._split_messages(msgs)
        assert system == "System only"
        assert user_msgs == []

    def test_message_without_optional_fields(self):
        msgs = [ChatMessage(Role.USER, "Hello")]
        system, user_msgs = AnthropicClient._split_messages(msgs)
        assert "tool_call_id" not in user_msgs[0]
        assert "name" not in user_msgs[0]

    def test_delegates_to_to_dict(self):
        """_split_messages 委托 ChatMessage.to_dict() 为唯一序列化 SSOT。"""
        msgs = [
            ChatMessage(Role.USER, "a"),
            ChatMessage(Role.TOOL, "b", tool_call_id="c1", name="f"),
            ChatMessage(Role.ASSISTANT, "c"),
        ]
        _system, user_msgs = AnthropicClient._split_messages(msgs)
        for msg, d in zip(msgs, user_msgs, strict=False):
            assert d == msg.to_dict(), f"SSOT contract violated for role={msg.role}"

    def test_empty_string_fields_preserved(self):
        """空字符串 tool_call_id/name 不丢失 — to_dict() 使用 is not None 而非真值检查。

        防止回退到真值检查导致静默数据丢失。
        """
        msg = ChatMessage(Role.TOOL, "x", tool_call_id="", name="")
        _system, user_msgs = AnthropicClient._split_messages([msg])
        assert user_msgs[0]["tool_call_id"] == ""
        assert user_msgs[0]["name"] == ""


# ═══════════════════════════════════════════════════════════════════════
# _parse_response
# ═══════════════════════════════════════════════════════════════════════


def _make_text_block(text: str):
    """创建模拟 text content_block。"""
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


def _make_tool_use_block(id_: str, name: str, input_: dict):
    """创建模拟 tool_use content_block。"""
    block = MagicMock()
    block.type = "tool_use"
    block.id = id_
    block.name = name
    block.input = input_
    return block


def _make_usage(input_tokens: int, output_tokens: int):
    usage = MagicMock()
    usage.input_tokens = input_tokens
    usage.output_tokens = output_tokens
    return usage


class TestParseResponse:
    def test_text_content_concatenated(self):
        resp = MagicMock()
        resp.content = [_make_text_block("Hello "), _make_text_block("World")]
        resp.model = "claude-sonnet-4-6"
        resp.usage = _make_usage(10, 5)
        resp.stop_reason = "end_turn"

        result = AnthropicClient._parse_response(resp)
        assert result.content == "Hello World"
        assert result.model == "claude-sonnet-4-6"
        assert result.finish_reason == "end_turn"
        assert result.usage.prompt_tokens == 10
        assert result.usage.completion_tokens == 5

    def test_tool_use_extracted(self):
        resp = MagicMock()
        resp.content = [
            _make_text_block("Using tool:"),
            _make_tool_use_block("tcu_1", "search", {"q": "hello"}),
        ]
        resp.model = "claude-sonnet-4-6"
        resp.usage = None
        resp.stop_reason = "tool_use"

        result = AnthropicClient._parse_response(resp)
        assert "Using tool:" in result.content
        assert result.tool_calls is not None
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].id == "tcu_1"
        assert result.tool_calls[0].name == "search"
        assert result.tool_calls[0].arguments == {"q": "hello"}

    def test_no_usage(self):
        resp = MagicMock()
        resp.content = [_make_text_block("Hi")]
        resp.model = "claude-sonnet-4-6"
        resp.usage = None
        resp.stop_reason = "end_turn"

        result = AnthropicClient._parse_response(resp)
        assert result.usage is None

    def test_none_stop_reason_defaults_to_stop(self):
        resp = MagicMock()
        resp.content = [_make_text_block("Hi")]
        resp.model = "claude-sonnet-4-6"
        resp.usage = None
        resp.stop_reason = None

        result = AnthropicClient._parse_response(resp)
        assert result.finish_reason == "stop"

    def test_no_tool_calls_when_empty(self):
        resp = MagicMock()
        resp.content = [_make_text_block("Hi")]
        resp.model = "claude-sonnet-4-6"
        resp.usage = None
        resp.stop_reason = "end_turn"

        result = AnthropicClient._parse_response(resp)
        assert result.tool_calls is None

    def test_multiple_tool_calls(self):
        resp = MagicMock()
        resp.content = [
            _make_tool_use_block("tcu_1", "search", {"q": "hello"}),
            _make_tool_use_block("tcu_2", "fetch", {"url": "https://a.com"}),
        ]
        resp.model = "claude-sonnet-4-6"
        resp.usage = None
        resp.stop_reason = "tool_use"

        result = AnthropicClient._parse_response(resp)
        assert len(result.tool_calls) == 2
        assert result.tool_calls[0].name == "search"
        assert result.tool_calls[1].name == "fetch"

    def test_usage_none_tokens_fallback_to_zero(self):
        usage = MagicMock()
        usage.input_tokens = None
        usage.output_tokens = None
        resp = MagicMock()
        resp.content = [_make_text_block("Hi")]
        resp.model = "x"
        resp.usage = usage
        resp.stop_reason = "end_turn"

        result = AnthropicClient._parse_response(resp)
        assert result.usage.prompt_tokens == 0
        assert result.usage.completion_tokens == 0


# ═══════════════════════════════════════════════════════════════════════
# _build_kwargs
# ═══════════════════════════════════════════════════════════════════════


class TestBuildKwargs:
    @staticmethod
    def _client(**kw):
        return AnthropicClient(api_key="test-key", **kw)

    def test_minimal_kwargs(self):
        params = _ResolvedParams(model="claude-sonnet-4-6", temperature=0.7, max_tokens=1024)
        kwargs = self._client()._build_kwargs(None, [{"role": "user", "content": "hi"}], None, params)
        assert kwargs["model"] == "claude-sonnet-4-6"
        assert kwargs["max_tokens"] == 1024
        assert kwargs["temperature"] == 0.7
        assert not kwargs["stream"]

    def test_with_system(self):
        params = _ResolvedParams(model="x", temperature=0.5, max_tokens=512)
        kwargs = self._client()._build_kwargs("You are helpful", [], None, params)
        assert kwargs["system"] == "You are helpful"

    def test_system_none_omitted(self):
        params = _ResolvedParams(model="x", temperature=0.5, max_tokens=512)
        kwargs = self._client()._build_kwargs(None, [], None, params)
        assert "system" not in kwargs

    def test_with_tools(self):
        params = _ResolvedParams(model="x", temperature=0.5, max_tokens=512)
        tools = [{"name": "search", "description": "search the web"}]
        kwargs = self._client()._build_kwargs(None, [], tools, params)
        assert kwargs["tools"] == tools

    def test_with_top_p_and_stop(self):
        params = _ResolvedParams(model="x", temperature=0.5, max_tokens=512, top_p=0.9, stop=["END"])
        kwargs = self._client()._build_kwargs(None, [], None, params)
        assert kwargs["top_p"] == 0.9
        assert kwargs["stop_sequences"] == ["END"]

    def test_stream_mode(self):
        params = _ResolvedParams(model="x", temperature=0.5, max_tokens=512)
        kwargs = self._client()._build_kwargs(None, [], None, params, stream=True)
        assert kwargs["stream"] is True

    def test_extra_kwargs_merged(self):
        client = AnthropicClient(api_key="k", extra_thing="value")
        params = _ResolvedParams(model="x", temperature=0.5, max_tokens=512)
        kwargs = client._build_kwargs(None, [], None, params)
        assert kwargs["extra_thing"] == "value"

    def test_top_p_none_omitted(self):
        params = _ResolvedParams(model="x", temperature=0.5, max_tokens=512, top_p=None)
        kwargs = self._client()._build_kwargs(None, [], None, params)
        assert "top_p" not in kwargs

    def test_stop_none_omitted(self):
        params = _ResolvedParams(model="x", temperature=0.5, max_tokens=512, stop=None)
        kwargs = self._client()._build_kwargs(None, [], None, params)
        assert "stop_sequences" not in kwargs

    # ADR-0008: structured output

    def test_json_schema_adds_tool_choice_and_tool(self):
        from astrocrawl.ai._config import _ResolvedOutput

        params = _ResolvedParams(
            model="claude",
            temperature=0.5,
            max_tokens=512,
            output=_ResolvedOutput(format="json_schema", json_schema={"type": "object"}),
        )
        kwargs = self._client()._build_kwargs(None, [{"role": "user", "content": "hi"}], None, params)
        assert kwargs["tool_choice"] == {"type": "tool", "name": "output_extraction_rule"}
        assert len(kwargs["tools"]) == 1
        assert kwargs["tools"][0]["name"] == "output_extraction_rule"
        assert kwargs["tools"][0]["input_schema"] == {"type": "object"}

    def test_json_object_injects_system_prompt(self):
        from astrocrawl.ai._config import _ResolvedOutput

        params = _ResolvedParams(
            model="claude",
            temperature=0.5,
            max_tokens=512,
            output=_ResolvedOutput(format="json_object", json_schema=None),
        )
        kwargs = self._client()._build_kwargs(None, [{"role": "user", "content": "hi"}], None, params)
        assert "system" in kwargs
        assert "only valid JSON" in kwargs["system"]
        assert "No markdown fences" in kwargs["system"]

    def test_json_object_appends_to_existing_system(self):
        from astrocrawl.ai._config import _ResolvedOutput

        params = _ResolvedParams(
            model="claude",
            temperature=0.5,
            max_tokens=512,
            output=_ResolvedOutput(format="json_object", json_schema=None),
        )
        kwargs = self._client()._build_kwargs("Existing prompt.", [], None, params)
        assert kwargs["system"].startswith("Existing prompt.")
        assert "only valid JSON" in kwargs["system"]

    def test_no_output_no_tool_choice(self):
        params = _ResolvedParams(model="claude", temperature=0.5, max_tokens=512)
        kwargs = self._client()._build_kwargs(None, [{"role": "user", "content": "hi"}], None, params)
        assert "tool_choice" not in kwargs

    def test_extra_body_passed_through(self):
        params = _ResolvedParams(
            model="claude",
            temperature=0.5,
            max_tokens=512,
            extra_body={"thinking": {"type": "enabled", "budget_tokens": 1024}},
        )
        kwargs = self._client()._build_kwargs(None, [{"role": "user", "content": "hi"}], None, params)
        assert kwargs["extra_body"] == {"thinking": {"type": "enabled", "budget_tokens": 1024}}

    def test_extra_body_none_omitted(self):
        params = _ResolvedParams(model="claude", temperature=0.5, max_tokens=512, extra_body=None)
        kwargs = self._client()._build_kwargs(None, [{"role": "user", "content": "hi"}], None, params)
        assert "extra_body" not in kwargs


# ═══════════════════════════════════════════════════════════════════════
# AnthropicClient — init
# ═══════════════════════════════════════════════════════════════════════


class TestAnthropicClientInit:
    def test_provider_name(self):
        client = AnthropicClient(api_key="sk-test")
        assert client.provider_name == "anthropic"

    def test_default_params(self):
        client = AnthropicClient(api_key="sk-test")
        assert client._api_key == "sk-test"
        assert client._base_url is None
        assert client._timeout == 60.0
        assert client._max_retries == 2
        assert client._sync_client is None
        assert client._async_client is None

    def test_custom_params(self):
        client = AnthropicClient(api_key="sk-custom", base_url="https://custom.api", timeout=30.0, max_retries=5)
        assert client._base_url == "https://custom.api"
        assert client._timeout == 30.0
        assert client._max_retries == 5

    def test_extra_kwargs_stored(self):
        client = AnthropicClient(api_key="k", custom_option="val")
        assert client._extra_kwargs == {"custom_option": "val"}


# ═══════════════════════════════════════════════════════════════════════
# aclose
# ═══════════════════════════════════════════════════════════════════════


class TestAnthropicClientAclose:
    async def test_aclose_no_clients(self):
        client = AnthropicClient(api_key="k")
        await client.aclose()
        assert client._async_client is None
        assert client._sync_client is None

    async def test_aclose_with_async_client(self):
        client = AnthropicClient(api_key="k")
        mock_async = MagicMock()
        mock_async.close = MagicMock()
        client._async_client = mock_async
        await client.aclose()
        assert client._async_client is None

    async def test_aclose_handles_close_exception(self):
        client = AnthropicClient(api_key="k")
        mock_async = MagicMock()
        mock_async.close = MagicMock(side_effect=Exception("close failed"))
        client._async_client = mock_async
        await client.aclose()
        assert client._async_client is None

    async def test_aclose_with_sync_client(self):
        client = AnthropicClient(api_key="k")
        mock_sync = MagicMock()
        client._sync_client = mock_sync
        await client.aclose()
        assert client._sync_client is None


# ═══════════════════════════════════════════════════════════════════════
# create_provider
# ═══════════════════════════════════════════════════════════════════════


class TestCreateProvider:
    def test_returns_anthropic_client(self):
        config = AIConfig(api_key="sk-test", base_url="https://api.anthropic.com")
        provider = create_provider(config)
        assert isinstance(provider, AnthropicClient)
        assert provider._api_key == "sk-test"
        assert provider._base_url == "https://api.anthropic.com"

    def test_empty_base_url_passed_as_none(self):
        config = AIConfig(api_key="sk-test", base_url="")
        provider = create_provider(config)
        assert provider._base_url is None

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
        provider = create_provider(config, custom_feature=True)
        assert provider._extra_kwargs == {"custom_feature": True}


class TestListModels:
    def test_returns_model_ids_with_sdk(self):
        """SDK 安装时调用真实 API 返回模型 ID 列表。"""
        from types import SimpleNamespace

        from astrocrawl.ai.providers.anthropic import list_models

        fake_model = SimpleNamespace(id="claude-3-opus-20240229")
        mock_client = MagicMock()
        mock_client.models.list.return_value = [fake_model]

        fake_anthropic = SimpleNamespace(Anthropic=MagicMock(return_value=mock_client))

        with patch.dict("sys.modules", {"anthropic": fake_anthropic}):
            result = list_models("https://api.anthropic.com", "sk-test", 15.0)

        assert result == ["claude-3-opus-20240229"]

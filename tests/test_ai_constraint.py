"""ADR-0008: OutputConstraint + _resolve_params + Tool Use + 错误反馈 + Schema 格式化。"""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel, ValidationError

from astrocrawl.ai import OutputConstraint
from astrocrawl.ai._config import GenerationParams, _resolve_params
from astrocrawl.ai._types import ChatMessage, ChatResponse, Role, ToolCall
from astrocrawl.rules._ai import _build_error_feedback
from astrocrawl.rules._schema import RuleSchema
from astrocrawl.rules._template import _generate_schema_example


class TestOutputConstraint:
    def test_default_construction(self):
        oc = OutputConstraint(format="json_object")
        assert oc.format == "json_object"
        assert oc.schema_model is None

    def test_with_schema_model(self):
        oc = OutputConstraint(format="json_schema", schema_model=RuleSchema)
        assert oc.schema_model is RuleSchema

    def test_frozen_prevents_mutation(self):
        oc = OutputConstraint(format="json_object")
        with pytest.raises(Exception):
            oc.format = "json_schema"  # type: ignore[misc]


class TestResolveParamsOutput:
    def test_output_none_by_default(self):
        r = _resolve_params(GenerationParams(), default_model="m", default_temperature=0, default_max_tokens=1)
        assert r.output is None

    def test_json_object_mode(self):
        p = GenerationParams(output=OutputConstraint(format="json_object"))
        r = _resolve_params(p, default_model="m", default_temperature=0, default_max_tokens=1)
        assert r.output is not None
        assert r.output.format == "json_object"
        assert r.output.json_schema is None

    def test_json_schema_translates_model_to_schema(self):
        p = GenerationParams(output=OutputConstraint(format="json_schema", schema_model=RuleSchema))
        r = _resolve_params(p, default_model="m", default_temperature=0, default_max_tokens=1)
        assert r.output is not None
        assert r.output.json_schema is not None
        assert "properties" in r.output.json_schema

    def test_json_schema_rejects_non_pydantic_class(self):
        p = GenerationParams(output=OutputConstraint(format="json_schema", schema_model=str))
        with pytest.raises(ValueError, match="Pydantic"):
            _resolve_params(p, default_model="m", default_temperature=0, default_max_tokens=1)

    def test_json_schema_requires_model(self):
        p = GenerationParams(output=OutputConstraint(format="json_schema"))
        with pytest.raises(ValueError, match="schema_model"):
            _resolve_params(p, default_model="m", default_temperature=0, default_max_tokens=1)


class TestParseAndValidateToolCalls:
    """_parse_and_validate_response — Tool Use 路径 (Anthropic json_schema)。"""

    def test_tool_calls_path_skips_json_parse(self):
        from astrocrawl.rules._ai import RuleGenerator

        response = ChatResponse(
            content="",
            tool_calls=[
                ToolCall(
                    id="1",
                    name="output_extraction_rule",
                    arguments={
                        "name": "tool_called_rule",
                        "fields": {"t": {"selector": "h1", "extract": "text", "fallback": [], "transform": {}}},
                    },
                )
            ],
        )
        result = RuleGenerator._parse_and_validate_response(response)
        assert result["name"] == "tool_called_rule"

    def test_tool_calls_takes_priority_over_content(self):
        from astrocrawl.rules._ai import RuleGenerator

        valid_args = {
            "name": "valid",
            "fields": {"x": {"selector": "div", "extract": "text", "fallback": [], "transform": {}}},
        }
        response = ChatResponse(
            content="not valid json",
            tool_calls=[ToolCall(id="1", name="r", arguments=valid_args)],
        )
        result = RuleGenerator._parse_and_validate_response(response)
        assert result["name"] == "valid"


class TestBuildErrorFeedback:
    def test_validation_error_produces_structured_feedback(self):
        class TestModel(BaseModel):
            name: str

        with pytest.raises(ValidationError) as exc_info:
            TestModel(name=42)
        msg = _build_error_feedback(exc_info.value)
        assert isinstance(msg, ChatMessage)
        assert msg.role == Role.USER
        assert "name" in msg.content
        assert "string" in msg.content.lower() or "str" in msg.content.lower()

    def test_value_error_produces_string_feedback(self):
        try:
            raise ValueError("something went wrong")
        except ValueError as e:
            msg = _build_error_feedback(e)
        assert isinstance(msg, ChatMessage)
        assert msg.role == Role.USER
        assert "something went wrong" in msg.content
        assert "请修正后重新输出" in msg.content

    def test_json_decode_error_feedback(self):
        try:
            json.loads("{invalid}")
        except json.JSONDecodeError as e:
            msg = _build_error_feedback(e)
        assert isinstance(msg, ChatMessage)
        assert (
            "Expecting" in msg.content or "invalid" in msg.content.lower() or "JSONDecodeError" in msg.content.lower()
        )


class TestGenerateWithRetry:
    """_generate_with_retry + _agenerate_with_retry 行为验证。"""

    def test_success_on_first_attempt_no_retry(self):
        from unittest.mock import MagicMock

        from astrocrawl.rules._ai import RuleGenerator

        gen = RuleGenerator.__new__(RuleGenerator)
        gen._MAX_RESPONSE_RETRIES = 3
        gen._client = MagicMock()
        gen._client.chat.return_value = ChatResponse(
            content='{"name": "ok", "fields": {"x": {"selector": "div", "extract": "text", "fallback": [], "transform": {}}}}',
        )

        messages = [ChatMessage(role=Role.SYSTEM, content="system"), ChatMessage(role=Role.USER, content="user")]
        result = gen._generate_with_retry(messages, GenerationParams())
        assert result["name"] == "ok"
        gen._client.chat.assert_called_once()

    def test_retry_on_validate_failure(self):
        from unittest.mock import MagicMock

        from astrocrawl.rules._ai import RuleGenerator

        gen = RuleGenerator.__new__(RuleGenerator)
        gen._MAX_RESPONSE_RETRIES = 3
        gen._client = MagicMock()
        gen._client.chat.side_effect = [
            ChatResponse(content='{"name": "bad", "fields": {"": {"selector": "div"}}}'),
            ChatResponse(
                content='{"name": "ok", "fields": {"x": {"selector": "div", "extract": "text", "fallback": [], "transform": {}}}}'
            ),
        ]

        messages = [ChatMessage(role=Role.SYSTEM, content="s"), ChatMessage(role=Role.USER, content="u")]
        result = gen._generate_with_retry(messages, GenerationParams())
        assert result["name"] == "ok"
        assert gen._client.chat.call_count == 2

    def test_raises_after_max_retries(self):
        from unittest.mock import MagicMock

        from astrocrawl.rules._ai import RuleGenerator

        gen = RuleGenerator.__new__(RuleGenerator)
        gen._MAX_RESPONSE_RETRIES = 2
        gen._client = MagicMock()
        gen._client.chat.return_value = ChatResponse(content="not json at all")

        messages = [ChatMessage(role=Role.SYSTEM, content="s"), ChatMessage(role=Role.USER, content="u")]
        with pytest.raises(Exception):
            gen._generate_with_retry(messages, GenerationParams())
        assert gen._client.chat.call_count == 2


class TestGenerateSchemaExample:
    """_generate_schema_example — 具体 JSON 示例 + 中文注释。"""

    def test_contains_concrete_values_not_types(self):
        desc = _generate_schema_example()
        assert '"text"' in desc
        assert "true" in desc
        assert "false" in desc
        assert "类型:" not in desc, "不应含抽象类型描述"

    def test_contains_field_names(self):
        desc = _generate_schema_example()
        assert '"name"' in desc
        assert '"selector"' in desc
        assert '"extract"' in desc
        assert '"scope"' in desc
        assert '"match"' in desc
        assert '"fields"' in desc
        assert '"options"' in desc

    def test_contains_chinese_annotations(self):
        desc = _generate_schema_example()
        assert "//" in desc, "应含中文注释"
        assert "不允许嵌套 fallback" in desc
        assert "仅 extract" in desc

    def test_contains_fallback_example(self):
        desc = _generate_schema_example()
        assert '"fallback"' in desc
        assert "备选选择器" in desc, "应含具体 fallback 示例值"

    def test_contains_all_transform_keys(self):
        desc = _generate_schema_example()
        for key in ("strip", "strip_currency", "regex", "replace", "join"):
            assert f'"{key}"' in desc, f"应含 transform.{key}"

    def test_contains_extract_enum_in_comment(self):
        desc = _generate_schema_example()
        assert "attr" in desc and "html" in desc and "text" in desc
        assert "//" in desc.split('"extract"')[1].split("\n")[0], "extract 行应有注释"


class TestRuleToDictRoundtrip:
    """model_dump(mode="json") → validate_rule 往返。"""

    def test_full_roundtrip(self):
        from astrocrawl.rules._schema import MatchConfig, MatchScope, validate_rule

        rule = RuleSchema(
            name="roundtrip_test",
            display_name="Roundtrip",
            match=MatchConfig(scope=MatchScope.DOMAIN_ALL, domains=["example.com"]),
            fields={"title": {"selector": "h1", "extract": "text", "fallback": [], "transform": {}}},
        )
        dumped = rule.model_dump(mode="json")
        reloaded = validate_rule(dumped)
        assert reloaded.name == "roundtrip_test"
        assert reloaded.match.domains == ["example.com"]
        assert "title" in reloaded.fields

    def test_model_copy_preserves_frozen(self):
        from astrocrawl.rules._schema import MatchConfig, MatchScope

        rule = RuleSchema(
            name="orig",
            match=MatchConfig(scope=MatchScope.DOMAIN_ALL, domains=["example.com"]),
        )
        copied = rule.model_copy(update={"name": "copied"})
        assert copied.name == "copied"
        assert copied.match.domains == ["example.com"]
        assert rule.name == "orig"


# ═══════════════════════════════════════════════════════════════════════
# ADR-0008 补全: 能力感知降级 + Provider 能力声明
# ═══════════════════════════════════════════════════════════════════════


class _CapProvider:
    """测试用 Provider — 可控的 supported_output_formats。"""

    provider_name = "test"
    aclose = None

    def __init__(self, caps: frozenset[str] | None = None) -> None:
        self._caps = caps
        self.chat = None
        self.chat_stream = None
        self.achat = None
        self.achat_stream = None

    @property
    def supported_output_formats(self) -> frozenset[str]:
        if self._caps is not None:
            return self._caps
        raise AttributeError


class _NoCapsProvider:
    """测试用 Provider — 不声明 supported_output_formats（模拟第三方 Provider）。"""

    provider_name = "third_party"
    aclose = None
    chat = None
    chat_stream = None
    achat = None
    achat_stream = None


class TestResolveOutputFormat:
    """AIClient._resolve_output_format — 能力感知降级。"""

    @staticmethod
    def _make_client(caps: frozenset[str] | None = None):
        from unittest.mock import MagicMock

        from astrocrawl.ai._client import AIClient
        from astrocrawl.ai._config import AIConfig

        client = AIClient.__new__(AIClient)
        client._config = AIConfig(api_key="sk-test", provider="test")
        client._hooks = []
        client._rate_limiter = MagicMock()
        client._usage_tracker = MagicMock()
        client._provider = _CapProvider(caps) if caps is not None else _NoCapsProvider()
        return client

    @staticmethod
    def _resolved(fmt: str, schema: dict | None = None):
        from astrocrawl.ai._config import _ResolvedOutput, _ResolvedParams

        return _ResolvedParams(
            model="m",
            temperature=0,
            max_tokens=1,
            output=_ResolvedOutput(format=fmt, json_schema=schema),
        )

    def test_output_none_passthrough(self):
        from astrocrawl.ai._config import _ResolvedParams

        client = self._make_client(caps=frozenset())
        r = _ResolvedParams(model="m", temperature=0, max_tokens=1)
        assert client._resolve_output_format(r) is r

    def test_format_in_caps_passthrough(self):
        r = self._resolved("json_object")
        assert self._make_client(caps=frozenset({"json_object"}))._resolve_output_format(r) is r

    def test_degrade_json_schema_to_json_object(self):
        r = self._resolved("json_schema", {"type": "object"})
        result = self._make_client(caps=frozenset({"json_object"}))._resolve_output_format(r)
        assert result.output.format == "json_object"
        assert result.output.json_schema is None

    def test_empty_caps_passthrough(self):
        """空 frozenset 表示能力未知，透传不做降级。"""
        r = self._resolved("json_object")
        result = self._make_client(caps=frozenset())._resolve_output_format(r)
        assert result.output is not None
        assert result.output.format == "json_object"

    def test_no_caps_attribute_passthrough(self):
        r = self._resolved("json_schema")
        result = self._make_client(caps=None)._resolve_output_format(r)
        assert result.output.format == "json_schema"

    def test_unknown_format_tries_from_strongest(self):
        r = self._resolved("unknown_fmt")
        result = self._make_client(caps=frozenset({"json_object"}))._resolve_output_format(r)
        assert result.output.format == "json_object"

    def test_no_fallback_in_caps_disables_output(self):
        """防御性路径：请求的格式和所有降级格式都不在 caps 中 → output=None。"""
        r = self._resolved("json_object", {"type": "object"})
        result = self._make_client(caps=frozenset({"json_schema"}))._resolve_output_format(r)
        assert result.output is None


class TestOpenAIEndpointDetection:
    """OpenAI Provider — supported_output_formats 端点感知。"""

    @staticmethod
    def _make_openai(base_url: str):
        from astrocrawl.ai.providers.openai import OpenAIClient

        return OpenAIClient(api_key="sk-test", base_url=base_url)

    def test_official_endpoint_reports_both(self):
        client = self._make_openai("https://api.openai.com/v1")
        caps = client.supported_output_formats
        assert "json_schema" in caps
        assert "json_object" in caps

    def test_official_endpoint_with_trailing_slash(self):
        client = self._make_openai("https://api.openai.com/v1/")
        caps = client.supported_output_formats
        assert "json_schema" in caps

    def test_all_endpoints_declare_full_caps(self):
        """回归：白名单移除后，所有端点声明最大能力，降级由 chat() 运行时 fallback 承担。"""
        for url in (
            "http://localhost:11434/v1",
            "https://vllm.example.com/v1",
            "https://myapi.openai.com/v1",
            "https://api.deepseek.com",
            "",
        ):
            client = self._make_openai(url)
            caps = client.supported_output_formats
            assert "json_schema" in caps, f"{url} 应声明 json_schema"
            assert "json_object" in caps, f"{url} 应声明 json_object"


class TestProviderCapabilities:
    """三 Provider 均声明 supported_output_formats。"""

    def test_anthropic_declares_both(self):
        from astrocrawl.ai.providers.anthropic import AnthropicClient

        caps = AnthropicClient(api_key="sk-test").supported_output_formats
        assert "json_schema" in caps
        assert "json_object" in caps

    def test_google_declares_both(self):
        from astrocrawl.ai.providers.google import GoogleClient

        caps = GoogleClient(api_key="sk-test").supported_output_formats
        assert "json_schema" in caps
        assert "json_object" in caps

    def test_anthropic_is_frozenset(self):
        from astrocrawl.ai.providers.anthropic import AnthropicClient

        caps = AnthropicClient(api_key="sk-test").supported_output_formats
        assert isinstance(caps, frozenset)

    def test_google_is_frozenset(self):
        from astrocrawl.ai.providers.google import GoogleClient

        caps = GoogleClient(api_key="sk-test").supported_output_formats
        assert isinstance(caps, frozenset)

    def test_openai_is_frozenset(self):
        from astrocrawl.ai.providers.openai import OpenAIClient

        caps = OpenAIClient(api_key="sk-test").supported_output_formats
        assert isinstance(caps, frozenset)

    # ── SSOT 完整性 ──

    def test_openai_official_caps_equal_constant(self):
        from astrocrawl.ai.providers.openai import _STRUCTURED_OUTPUT_MODES, OpenAIClient

        caps = OpenAIClient(api_key="sk-test", base_url="https://api.openai.com/v1").supported_output_formats
        assert caps == _STRUCTURED_OUTPUT_MODES

    def test_openai_all_endpoints_declare_full_caps(self):
        """回归：所有端点声明 _STRUCTURED_OUTPUT_MODES 全集，不做 hostname 过滤。"""
        from astrocrawl.ai.providers.openai import _STRUCTURED_OUTPUT_MODES, OpenAIClient

        for url in ("https://api.openai.com/v1", "http://localhost:11434/v1", "https://api.deepseek.com"):
            caps = OpenAIClient(api_key="sk-test", base_url=url).supported_output_formats
            assert caps == _STRUCTURED_OUTPUT_MODES, f"{url} 应声明完整能力集"

    def test_anthropic_caps_equals_constant(self):
        from astrocrawl.ai.providers.anthropic import _STRUCTURED_OUTPUT_MODES, AnthropicClient

        caps = AnthropicClient(api_key="sk-test").supported_output_formats
        assert caps == _STRUCTURED_OUTPUT_MODES

    def test_google_caps_equals_constant(self):
        from astrocrawl.ai.providers.google import _STRUCTURED_OUTPUT_MODES, GoogleClient

        caps = GoogleClient(api_key="sk-test").supported_output_formats
        assert caps == _STRUCTURED_OUTPUT_MODES

    def test_anthropic_constant_includes_json_object(self):
        from astrocrawl.ai.providers.anthropic import _STRUCTURED_OUTPUT_MODES

        assert "json_object" in _STRUCTURED_OUTPUT_MODES


class TestDefaultParamsUpgraded:
    """_default_params() — 默认请求 json_schema。"""

    def test_output_is_json_schema(self):
        from astrocrawl.rules._ai import RuleGenerator

        params = RuleGenerator._default_params()
        assert params.output is not None
        assert params.output.format == "json_schema"

    def test_schema_model_is_ruleschema(self):
        from astrocrawl.rules._ai import RuleGenerator
        from astrocrawl.rules._schema import RuleSchema

        params = RuleGenerator._default_params()
        assert params.output.schema_model is RuleSchema

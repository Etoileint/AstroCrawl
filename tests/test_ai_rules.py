"""测试：astrocrawl/rules/_ai.py — RuleGenerator 消息构造 + 响应解析。

覆盖 #132 核心验收标准。
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from astrocrawl.ai._client import AIClient
from astrocrawl.ai._config import GenerationParams
from astrocrawl.ai._types import ChatMessage, ChatResponse, Role, ToolCall
from astrocrawl.rules._ai import (
    _FIELD_MAX_LENGTH,
    _HTML_MAX_CHARS,
    RuleGenerator,
    _assemble_messages,
    _build_error_feedback,
    _sanitize_field_requirement,
    _sanitize_printable,
    _validate_extraction,
    get_assembled_prompt,
)
from astrocrawl.rules._html_preprocess import PreprocessTier


class TestAssembleMessages:
    """_assemble_messages — 消息组装 SSOT (system + user)。"""

    def test_system_message_contains_template(self):
        msgs = _assemble_messages("https://example.com", "<html></html>", ["title"])
        assert msgs[0].role == Role.SYSTEM
        assert "extract" in msgs[0].content
        assert "transform" in msgs[0].content
        assert "selector" in msgs[0].content, "应含 Schema 示例"

    def test_user_message_contains_url(self):
        msgs = _assemble_messages("https://shop.example.com/products", "<div></div>", ["price"])
        assert msgs[1].role == Role.USER
        assert "https://shop.example.com/products" in msgs[1].content

    def test_user_message_contains_field_requirements(self):
        msgs = _assemble_messages("https://example.com", "<html></html>", ["title", "price", "author"])
        assert "title, price, author" in msgs[1].content

    def test_user_message_contains_html(self):
        msgs = _assemble_messages("https://example.com", "<h1>Test</h1>", ["title"])
        assert "<h1>Test</h1>" in msgs[1].content

    def test_empty_field_requirements_defaults_to_auto_detect(self):
        msgs = _assemble_messages("https://example.com", "<html></html>", [])
        assert "自动检测" in msgs[1].content

    def test_html_truncation(self):
        long_html = "x" * 250000
        msgs = _assemble_messages("https://example.com", long_html, ["title"])
        html_section = msgs[1].content
        assert len("x" * _HTML_MAX_CHARS) in [len(part) for part in html_section.split("x")] or _HTML_MAX_CHARS < len(
            long_html
        )
        assert len(long_html) > _HTML_MAX_CHARS
        assert "x" * (_HTML_MAX_CHARS + 100) not in html_section

    def test_mode_default_is_type(self):
        msgs = _assemble_messages("https://example.com", "<html></html>", ["title"])
        assert "Core Principle" in msgs[0].content

    def test_mode_position(self):
        msgs = _assemble_messages("https://example.com", "<html></html>", ["title"], mode="position")
        assert "Mode: Position" in msgs[0].content


class TestRuleGeneratorParseResponse:
    """_parse_and_validate_response — JSON 解析 + markdown 剥离 + validate_rule。"""

    def setup_method(self):
        self._client = MagicMock(spec=AIClient)
        self._gen = RuleGenerator(self._client)

    def _make_response(self, content: str = ""):
        return ChatResponse(content=content)

    def test_valid_json(self):
        r = self._make_response('{"name": "test", "fields": {}}')
        result = self._gen._parse_and_validate_response(r)
        assert result["name"] == "test"

    def test_fenced_code_block(self):
        raw = '```json\n{"name": "fenced", "fields": {"t": {"selector": "h1"}}}\n```'
        r = self._make_response(raw)
        result = self._gen._parse_and_validate_response(r)
        assert result["name"] == "fenced"

    def test_fenced_no_trailing_newline(self):
        raw = '```json\n{"name": "notrail", "fields": {"x": {"selector": "div"}}}```'
        r = self._make_response(raw)
        result = self._gen._parse_and_validate_response(r)
        assert result["name"] == "notrail"

    def test_json_in_prose_rejected(self):
        raw = '规则如下:\n{"name": "prose", "fields": {"x": {"selector": "div"}}}\n请使用。'
        r = self._make_response(raw)
        with pytest.raises((json.JSONDecodeError, ValueError)):
            self._gen._parse_and_validate_response(r)

    def test_invalid_json_raises(self):
        r = self._make_response("not json at all")
        with pytest.raises((json.JSONDecodeError, ValueError)):
            self._gen._parse_and_validate_response(r)


class TestRuleGeneratorDefaultParams:
    """默认生成参数。"""

    def test_default_temperature(self):
        p = RuleGenerator._default_params()
        assert p.temperature == 0.1

    def test_default_max_tokens(self):
        p = RuleGenerator._default_params()
        assert p.max_tokens == 16384


class TestRuleGeneratorGenerate:
    """generate_sync / generate — mock AIClient。"""

    def test_generate_sync_returns_dict(self):
        mock_client = MagicMock(spec=AIClient)
        mock_client.chat.return_value = ChatResponse(
            content='{"name": "test_rule", "fields": {"t": {"selector": "h1"}}}',
            model="gpt-4o-mini",
        )

        gen = RuleGenerator(mock_client)
        result = gen.generate_sync("https://example.com", "<h1>Hi</h1>", ["title"])

        assert result["name"] == "test_rule"
        assert result["fields"]["t"]["selector"] == "h1"
        mock_client.chat.assert_called_once()

    def test_generate_sync_with_custom_params(self):
        mock_client = MagicMock(spec=AIClient)
        mock_client.chat.return_value = ChatResponse(
            content='{"name": "r", "fields": {}}',
            model="gpt-4.5",
        )

        gen = RuleGenerator(mock_client)
        params = GenerationParams(temperature=0.5, max_tokens=512)
        gen.generate_sync("https://example.com", "<html></html>", ["price"], params)

        call_args = mock_client.chat.call_args
        params_arg = call_args.kwargs["params"]
        assert params_arg is not None
        assert params_arg.temperature == 0.5
        assert params_arg.max_tokens == 512

    def test_generate_sync_with_position_mode(self):
        mock_client = MagicMock(spec=AIClient)
        mock_client.chat.return_value = ChatResponse(
            content='{"name": "r", "fields": {}}',
            model="gpt-4o-mini",
        )

        gen = RuleGenerator(mock_client)
        gen.generate_sync("https://example.com", "<html></html>", ["price"], mode="position")

        call_args = mock_client.chat.call_args
        messages = call_args[0][0]
        assert "Mode: Position" in messages[0].content

    @pytest.mark.asyncio
    async def test_generate_async_returns_dict(self):
        mock_client = MagicMock(spec=AIClient)
        mock_client.achat = AsyncMock(
            return_value=ChatResponse(
                content='{"name": "async_rule", "fields": {"a": {"selector": "p"}}}',
                model="gpt-4o-mini",
            )
        )

        gen = RuleGenerator(mock_client)
        result = await gen.generate("https://example.com", "<p>hello</p>", ["text"])

        assert result["name"] == "async_rule"
        mock_client.achat.assert_called_once()


class TestHTMLMaxChars:
    """HTML 截断常量。"""

    def test_html_max_chars_value(self):
        assert _HTML_MAX_CHARS == 200000


# ═══════════════════════════════════════════════════════════════════════
# 统一组装流水线测试 (Issue #178)
# ═══════════════════════════════════════════════════════════════════════


class TestAssembleMessagesWithTier:
    def test_default_tier_removes_scripts(self):
        msgs = _assemble_messages(
            "https://example.com",
            "<html><head></head><body><script>bad</script><p>ok</p></body></html>",
            ["title"],
        )
        user_content = msgs[1].content
        assert "bad" not in user_content
        assert "ok" in user_content

    def test_tier_off_preserves_scripts(self):
        msgs = _assemble_messages(
            "https://example.com",
            "<html><head></head><body><script>bad</script><p>ok</p></body></html>",
            ["title"],
            tier=PreprocessTier.OFF,
        )
        user_content = msgs[1].content
        assert "bad" in user_content
        assert "ok" in user_content

    def test_tier_strict_removes_nav(self):
        msgs = _assemble_messages(
            "https://example.com",
            "<html><body><nav>nav</nav><p>ok</p></body></html>",
            ["title"],
            tier=PreprocessTier.STRICT,
        )
        user_content = msgs[1].content
        assert "nav" not in user_content
        assert "ok" in user_content

    def test_tier_default_is_canonical(self):
        msgs = _assemble_messages(
            "https://example.com",
            "<html><head></head><body><style>.x{}</style><p>ok</p></body></html>",
            ["title"],
        )
        user_content = msgs[1].content
        assert ".x" not in user_content


class TestGenerateFromMessages:
    """_generate_from_messages — 内部 escape hatch。"""

    def test_bypasses_assemble_messages(self):
        mock_client = MagicMock(spec=AIClient)
        mock_client.chat.return_value = ChatResponse(
            content='{"name": "test_rule", "fields": {}}',
            model="gpt-4o-mini",
        )
        gen = RuleGenerator(mock_client)
        messages = [
            ChatMessage(Role.SYSTEM, "sys prompt"),
            ChatMessage(Role.USER, "user msg"),
        ]
        result = gen._generate_from_messages(messages)
        mock_client.chat.assert_called_once()
        call_messages = mock_client.chat.call_args.args[0]
        assert call_messages is not None
        assert result["name"] == "test_rule"

    def test_accepts_custom_params(self):
        mock_client = MagicMock(spec=AIClient)
        mock_client.chat.return_value = ChatResponse(
            content='{"name": "test_rule", "fields": {}}',
            model="gpt-4o-mini",
        )
        gen = RuleGenerator(mock_client)
        params = GenerationParams(temperature=0.5, max_tokens=512)
        gen._generate_from_messages(
            [ChatMessage(Role.USER, "hi")],
            params=params,
        )
        call_args = mock_client.chat.call_args
        params_arg = call_args.kwargs["params"]
        assert params_arg is not None
        assert params_arg.temperature == 0.5
        assert params_arg.max_tokens == 512


class TestSanitizePrintable:
    """_sanitize_printable — Unicode TR39 可打印字符类别白名单过滤。"""

    def test_letters_numbers_preserved(self):
        assert _sanitize_printable("abcABC123") == "abcABC123"

    def test_punctuation_symbols_preserved(self):
        result = _sanitize_printable("!@#$%^&*()_+-=[]{}|;:',.<>?/~`\"")
        assert result == "!@#$%^&*()_+-=[]{}|;:',.<>?/~`\""

    def test_cjk_characters_preserved(self):
        assert _sanitize_printable("中文测试汉字日本語한국어") == "中文测试汉字日本語한국어"

    def test_combining_marks_preserved(self):
        # Combining grave accent (U+0300) is Mn (Mark, Nonspacing)
        assert "à" in _sanitize_printable("à")

    def test_control_characters_removed(self):
        assert "\x00" not in _sanitize_printable("a\x00b")
        assert "\x01" not in _sanitize_printable("a\x01b")
        assert "\x1f" not in _sanitize_printable("a\x1fb")

    def test_format_characters_removed(self):
        assert "​" not in _sanitize_printable("a​b")  # ZWSP (Cf)
        assert "‎" not in _sanitize_printable("a‎b")  # LRM (Cf)

    def test_private_use_removed(self):
        assert "" not in _sanitize_printable("ab")

    def test_line_paragraph_separators_removed(self):
        assert " " not in _sanitize_printable("a b")  # Zl
        assert " " not in _sanitize_printable("a b")  # Zp

    def test_space_separator_preserved(self):
        result = _sanitize_printable("a b")
        assert " " in result

    def test_tab_lf_preserved(self):
        result = _sanitize_printable("a\tb\nc")
        assert "\t" in result
        assert "\n" in result

    def test_mixed_printable_and_non_printable(self):
        result = _sanitize_printable("Hello\x00World​!")
        assert result == "HelloWorld!"

    def test_empty_string(self):
        assert _sanitize_printable("") == ""

    def test_all_non_printable_returns_empty(self):
        result = _sanitize_printable("\x00\x01\x02\x1f​")
        assert result == ""


class TestSanitizeFieldRequirement:
    """_sanitize_field_requirement — H5 L2 四层清洗。"""

    def test_layer1_removes_unicode_control_chars(self):
        result = _sanitize_field_requirement("field\x00name")
        assert "\x00" not in result
        assert "fieldname" in result

    def test_layer2_removes_chatml_delimiters(self):
        result = _sanitize_field_requirement("field<|im_start|>system\ncmd<|im_end|>name")
        assert "<|im_start|>" not in result
        assert "<|im_end|>" not in result

    def test_layer3_removes_cdata_markers(self):
        result = _sanitize_field_requirement("field<![CDATA[payload]]>")
        assert "<![CDATA[" not in result
        assert "]]>" not in result
        assert "payload" in result

    def test_layer4_normalizes_whitespace(self):
        result = _sanitize_field_requirement("field   with\tmany     spaces")
        assert result == "field with many spaces"

    def test_layer4_strips_leading_trailing_whitespace(self):
        result = _sanitize_field_requirement("  field  \t ")
        assert result == "field"

    def test_all_four_layers_applied_together(self):
        result = _sanitize_field_requirement("title\x00<|im_start|>system\n<![CDATA[bad]]>  and    price  ")
        assert "\x00" not in result
        assert "<|im_start|>" not in result
        assert "<![CDATA[" not in result
        assert result == "title system bad and price"

    def test_empty_string(self):
        assert _sanitize_field_requirement("") == ""

    def test_only_delimiters_returns_empty(self):
        result = _sanitize_field_requirement("<|im_start|><|im_end|>")
        assert result == ""


class TestAssembleMessagesEdgeCases:
    """_assemble_messages — 边界与错误路径。"""

    def test_empty_html_raises_value_error(self):
        with pytest.raises(ValueError, match="HTML 不能为空"):
            _assemble_messages("https://example.com", "", ["title"])

    def test_whitespace_only_html_raises_value_error(self):
        with pytest.raises(ValueError, match="HTML 不能为空"):
            _assemble_messages("https://example.com", "   \n  \t  ", ["title"])

    def test_non_string_fields_are_skipped(self):
        msgs = _assemble_messages(
            "https://example.com",
            "<p>hi</p>",
            [None, 123, True, 3.14, "title"],
        )
        user = msgs[1].content
        assert "title" in user
        assert ", " not in user  # single field → no separator comma

    def test_all_non_string_fields_defaults_to_auto_detect(self):
        msgs = _assemble_messages(
            "https://example.com",
            "<p>hi</p>",
            [None, 123],
        )
        user = msgs[1].content
        assert "自动检测" in user

    def test_field_truncation_to_max_length(self):
        long_field = "x" * 600
        msgs = _assemble_messages(
            "https://example.com",
            "<p>hi</p>",
            [long_field],
        )
        user = msgs[1].content
        assert ("x" * (_FIELD_MAX_LENGTH + 1)) not in user
        assert ("x" * _FIELD_MAX_LENGTH) in user


class TestRuleGeneratorParseResponseToolCalls:
    """_parse_and_validate_response — Tool Use 路径 (Anthropic json_schema)。"""

    def setup_method(self):
        self._client = MagicMock(spec=AIClient)
        self._gen = RuleGenerator(self._client)

    def test_tool_calls_path_uses_arguments_directly(self):
        r = ChatResponse(
            content="",
            model="claude-sonnet-4-6",
            tool_calls=[
                ToolCall(
                    id="call_001",
                    name="extract_rule",
                    arguments={"name": "claude_rule", "fields": {"x": {"selector": "div"}}},
                )
            ],
        )
        result = self._gen._parse_and_validate_response(r)
        assert result["name"] == "claude_rule"
        assert result["fields"]["x"]["selector"] == "div"

    def test_tool_calls_path_with_invalid_rule_raises(self):
        r = ChatResponse(
            content="",
            model="claude-sonnet-4-6",
            tool_calls=[
                ToolCall(
                    id="call_002",
                    name="extract_rule",
                    arguments={"name": "", "fields": {}},
                )
            ],
        )
        with pytest.raises(ValueError):
            self._gen._parse_and_validate_response(r)


class TestRuleGeneratorRetryExhaustion:
    """_generate_with_retry / _agenerate_with_retry — 重试耗尽路径。"""

    def test_sync_retry_exhaustion_raises(self):
        mock_client = MagicMock(spec=AIClient)
        mock_client.chat.return_value = ChatResponse(
            content="not valid json at all",
            model="gpt-4o-mini",
        )

        gen = RuleGenerator(mock_client)
        messages = [
            ChatMessage(Role.SYSTEM, "sys"),
            ChatMessage(Role.USER, "user"),
        ]

        with pytest.raises((json.JSONDecodeError, ValueError)):
            gen._generate_with_retry(messages, GenerationParams())

        assert mock_client.chat.call_count == 3

    def test_sync_retry_appends_error_feedback_then_succeeds(self):
        mock_client = MagicMock(spec=AIClient)
        mock_client.chat.side_effect = [
            ChatResponse(content="bad json {{{", model="gpt-4o-mini"),
            ChatResponse(content="still bad", model="gpt-4o-mini"),
            ChatResponse(
                content='{"name": "recovered", "fields": {"x": {"selector": "p"}}}',
                model="gpt-4o-mini",
            ),
        ]

        gen = RuleGenerator(mock_client)
        messages = [
            ChatMessage(Role.SYSTEM, "sys"),
            ChatMessage(Role.USER, "user"),
        ]

        result = gen._generate_with_retry(messages, GenerationParams())

        assert result["name"] == "recovered"
        assert mock_client.chat.call_count == 3
        assert len(messages) == 4  # original 2 + 2 error feedbacks
        assert "上次输出的规则校验失败" in messages[2].content
        assert messages[2].role == Role.USER

    def test_sync_retry_on_validation_error(self):
        mock_client = MagicMock(spec=AIClient)
        mock_client.chat.side_effect = [
            ChatResponse(
                content='{"name": "", "fields": {}}',  # empty name → ValidationError
                model="gpt-4o-mini",
            ),
            ChatResponse(
                content='{"name": "fixed", "fields": {"f": {"selector": "h1"}}}',
                model="gpt-4o-mini",
            ),
        ]

        gen = RuleGenerator(mock_client)
        messages = [ChatMessage(Role.USER, "test")]

        result = gen._generate_with_retry(messages, GenerationParams())

        assert result["name"] == "fixed"
        assert mock_client.chat.call_count == 2
        assert len(messages) == 2  # 1 original + 1 error feedback
        assert "name" in messages[1].content  # ValidationError mentions field name

    @pytest.mark.asyncio
    async def test_async_retry_exhaustion_raises(self):
        mock_client = MagicMock(spec=AIClient)
        mock_client.achat = AsyncMock(return_value=ChatResponse(content="definitely not json", model="gpt-4o-mini"))

        gen = RuleGenerator(mock_client)
        messages = [ChatMessage(Role.USER, "test")]

        with pytest.raises((json.JSONDecodeError, ValueError)):
            await gen._agenerate_with_retry(messages, GenerationParams())

        assert mock_client.achat.call_count == 3

    @pytest.mark.asyncio
    async def test_async_retry_recovers(self):
        mock_client = MagicMock(spec=AIClient)
        mock_client.achat = AsyncMock()
        mock_client.achat.side_effect = [
            ChatResponse(content="bad", model="gpt-4o-mini"),
            ChatResponse(
                content='{"name": "async_ok", "fields": {"a": {"selector": "span"}}}',
                model="gpt-4o-mini",
            ),
        ]

        gen = RuleGenerator(mock_client)
        messages = [ChatMessage(Role.USER, "test")]

        result = await gen._agenerate_with_retry(messages, GenerationParams())

        assert result["name"] == "async_ok"
        assert mock_client.achat.call_count == 2


class TestBuildErrorFeedback:
    """_build_error_feedback — 错误反馈消息构建。"""

    def test_validation_error_provides_structured_detail(self):
        from astrocrawl.rules._schema import RuleSchema as RS

        try:
            RS.model_validate({"name": "", "fields": {}, "schema_version": 2})
        except Exception as e:
            msg = _build_error_feedback(e)
            assert isinstance(msg, ChatMessage)
            assert msg.role == Role.USER
            assert "上次输出的规则校验失败" in msg.content
            assert "请修正后重新输出" in msg.content
            assert "name" in msg.content  # ValidationError references field name

    def test_json_decode_error_uses_str(self):
        error = json.JSONDecodeError("Expecting value", "doc", 0)
        msg = _build_error_feedback(error)

        assert isinstance(msg, ChatMessage)
        assert msg.role == Role.USER
        assert "Expecting value" in msg.content
        assert "上次输出的规则校验失败" in msg.content

    def test_value_error_uses_str(self):
        error = ValueError("custom validation failure")
        msg = _build_error_feedback(error)

        assert isinstance(msg, ChatMessage)
        assert msg.role == Role.USER
        assert "custom validation failure" in msg.content

    def test_arbitrary_exception_uses_str(self):
        error = RuntimeError("unexpected runtime issue")
        msg = _build_error_feedback(error)

        assert isinstance(msg, ChatMessage)
        assert msg.role == Role.USER
        assert "unexpected runtime issue" in msg.content

    def test_content_always_ends_with_json_only_instruction(self):
        error = ValueError("fail")
        msg = _build_error_feedback(error)
        assert "只输出 JSON" in msg.content
        assert "不要其他内容" in msg.content


class TestGetAssembledPrompt:
    def test_returns_text_and_token_count(self):
        text, count = get_assembled_prompt(
            "https://example.com",
            "<html><head></head><body><p>hi</p></body></html>",
            ["title"],
        )
        assert text
        assert count > 0
        assert "<|im_start|>system" in text
        assert "<|im_start|>user" in text
        assert "<html_source>" in text

    def test_applies_canonical_preprocessing_by_default(self):
        text, _ = get_assembled_prompt(
            "https://example.com",
            "<html><head></head><body><script>bad</script><p>hi</p></body></html>",
            ["title"],
        )
        assert "bad" not in text

    def test_respects_tier_off(self):
        text, _ = get_assembled_prompt(
            "https://example.com",
            "<html><body><script>keep</script><p>hi</p></body></html>",
            ["title"],
            tier=PreprocessTier.OFF,
        )
        assert "keep" in text

    def test_token_count_zero_when_mocked(self, monkeypatch):
        mock_count = lambda text, model="gpt-4o-mini": 0  # noqa: E731
        monkeypatch.setattr(
            "astrocrawl.rules._ai.count_tokens",
            mock_count,
        )
        text, count = get_assembled_prompt(
            "https://example.com",
            "<html><body><p>hi</p></body></html>",
            ["title"],
        )
        assert count == 0
        assert text

    def test_position_mode_includes_mode_section(self):
        text, _ = get_assembled_prompt(
            "https://example.com",
            "<html><body><p>hi</p></body></html>",
            ["title"],
            mode="position",
        )
        assert "Mode: Position" in text
        assert "Core Principle" not in text


class TestValidateExtraction:
    """_validate_extraction — 复用提取引擎闭环验证。"""

    def test_all_fields_filled_passes(self):
        """所有字段非空 → 正常返回。"""
        from astrocrawl.rules._schema import RuleSchema

        rule = RuleSchema.model_validate(
            {
                "name": "t",
                "schema_version": 1,
                "fields": {"x": {"selector": "p", "extract": "text"}},
                "match": {"domains": ["example.com"], "scope": "domain_pattern", "url_pattern": "/"},
            }
        )
        _validate_extraction(rule, "<p>hello</p>")  # 不抛异常

    def test_field_none_raises_value_error(self):
        """选择器未命中 → 字段 None → ValueError。"""
        from astrocrawl.rules._schema import RuleSchema

        rule = RuleSchema.model_validate(
            {
                "name": "t",
                "schema_version": 1,
                "fields": {"x": {"selector": "div", "extract": "text"}},
                "match": {"domains": ["example.com"], "scope": "domain_pattern", "url_pattern": "/"},
            }
        )
        with pytest.raises(ValueError, match="提取结果 None"):
            _validate_extraction(rule, "<p>hello</p>")

    def test_field_empty_list_raises_value_error(self):
        """选择器 0 命中 → 字段 [] → ValueError。"""
        from astrocrawl.rules._schema import RuleSchema

        rule = RuleSchema.model_validate(
            {
                "name": "t",
                "schema_version": 1,
                "fields": {"x": {"selector": "span", "extract": "text", "multiple": True}},
                "match": {"domains": ["example.com"], "scope": "domain_pattern", "url_pattern": "/"},
            }
        )
        with pytest.raises(ValueError, match="提取结果"):
            _validate_extraction(rule, "<p>hello</p>")

    def test_field_empty_string_raises_value_error(self):
        """布尔属性 extract=attr → 空字符串 → ValueError。"""
        from astrocrawl.rules._schema import RuleSchema

        rule = RuleSchema.model_validate(
            {
                "name": "t",
                "schema_version": 1,
                "fields": {"x": {"selector": "input[checked]", "extract": "attr", "attr": "checked"}},
                "match": {"domains": ["example.com"], "scope": "domain_pattern", "url_pattern": "/"},
            }
        )
        with pytest.raises(ValueError, match="提取结果为空字符串"):
            _validate_extraction(rule, '<input type="checkbox" checked />')

    def test_error_message_includes_multiple_fields(self):
        """多字段全空 → 错误消息列出所有字段。"""
        from astrocrawl.rules._schema import RuleSchema

        rule = RuleSchema.model_validate(
            {
                "name": "t",
                "schema_version": 1,
                "fields": {
                    "a": {"selector": "div", "extract": "text"},
                    "b": {"selector": "span", "extract": "text", "multiple": True},
                },
                "match": {"domains": ["example.com"], "scope": "domain_pattern", "url_pattern": "/"},
            }
        )
        with pytest.raises(ValueError) as exc_info:
            _validate_extraction(rule, "<p>hello</p>")
        msg = str(exc_info.value)
        assert "a:" in msg
        assert "b:" in msg

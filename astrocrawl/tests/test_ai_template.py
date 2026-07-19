"""测试：AI Prompt 模板 — 加载回退、文件缓存、Schema 契约。

测试文件覆盖 issue #132 的模板统一验收标准。
ADR-0008: _generate_schema_example() 手写示例通过契约测试与 RuleSchema 保持同步。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from astrocrawl.rules._markdown import clean_markdown_wrapper
from astrocrawl.rules._template import (
    _BUILTIN_SYSTEM_PROMPT_POSITION,
    _BUILTIN_SYSTEM_PROMPT_TYPE,
    _generate_schema_example,
    get_prompt_template,
    invalidate_template_cache,
)


class TestTemplateLoading:
    """模板加载 + 回退。"""

    def test_load_returns_string_type(self):
        template = get_prompt_template()
        assert isinstance(template, str)
        assert "extract" in template
        assert "transform" in template
        assert "selector" in template, "应含 Schema 示例"
        assert "Core Principle" in template

    def test_load_returns_string_position(self):
        template = get_prompt_template("position")
        assert isinstance(template, str)
        assert "extract" in template
        assert "transform" in template
        assert "selector" in template, "应含 Schema 示例"
        assert "Mode: Position" in template

    def test_template_is_cached_per_mode(self):
        invalidate_template_cache()
        t1 = get_prompt_template()
        t2 = get_prompt_template()
        assert t1 is t2  # type 缓存

        p1 = get_prompt_template("position")
        p2 = get_prompt_template("position")
        assert p1 is p2  # position 缓存

        assert t1 is not p1  # 独立缓存

    @patch.object(Path, "is_file", return_value=False)
    def test_fallback_when_file_missing_type(self, mock_is_file):
        invalidate_template_cache()
        template = get_prompt_template()
        assert isinstance(template, str)
        assert len(template) > 50
        assert "Core Principle" in template

    @patch.object(Path, "is_file", return_value=False)
    def test_fallback_when_file_missing_position(self, mock_is_file):
        invalidate_template_cache()
        template = get_prompt_template("position")
        assert isinstance(template, str)
        assert len(template) > 50
        assert "Mode: Position" in template

    def test_unknown_mode_raises(self):
        with pytest.raises(ValueError, match="Unknown mode"):
            get_prompt_template("invalid")


class TestMarkdownCleaning:
    """N42: markdown 代码块剥离。"""

    def test_fenced_code_block(self):
        raw = '```json\n{"name": "test", "fields": {"t": {"selector": "h1"}}}\n```'
        cleaned = clean_markdown_wrapper(raw)
        parsed = json.loads(cleaned)
        assert parsed["name"] == "test"

    def test_json_in_prose_no_fallback(self):
        raw = '规则如下:\n{"name": "prose_rule", "fields": {"x": {"selector": "div"}}}\n请使用。'
        result = clean_markdown_wrapper(raw)
        assert result == raw

    def test_no_json_returns_original(self):
        raw = "这不是 JSON"
        result = clean_markdown_wrapper(raw)
        assert result == raw


class TestPromptFile:
    """_prompt_template_type.txt 文件存在且格式正确。"""

    def test_file_exists(self):
        p = Path(__file__).resolve().parent.parent / "astrocrawl" / "rules" / "_prompt_template_type.txt"
        assert p.is_file(), f"Prompt 模板文件不存在: {p}"

    def test_file_is_pure_text_no_header(self):
        p = Path(__file__).resolve().parent.parent / "astrocrawl" / "rules" / "_prompt_template_type.txt"
        raw = p.read_text(encoding="utf-8")
        first_line = raw.strip().split("\n")[0]
        assert not first_line.startswith("min_schema_version:")

    def test_file_contains_template(self):
        p = Path(__file__).resolve().parent.parent / "astrocrawl" / "rules" / "_prompt_template_type.txt"
        raw = p.read_text(encoding="utf-8")
        assert "RE2 Regex" in raw
        assert "extract" in raw.lower()
        assert "transform" in raw.lower()


class TestPromptFilePosition:
    """_prompt_template_position.txt 文件存在且格式正确。"""

    def test_file_exists(self):
        p = Path(__file__).resolve().parent.parent / "astrocrawl" / "rules" / "_prompt_template_position.txt"
        assert p.is_file(), f"Prompt 模板文件不存在: {p}"

    def test_file_is_pure_text_no_header(self):
        p = Path(__file__).resolve().parent.parent / "astrocrawl" / "rules" / "_prompt_template_position.txt"
        raw = p.read_text(encoding="utf-8")
        first_line = raw.strip().split("\n")[0]
        assert not first_line.startswith("min_schema_version:")

    def test_file_contains_template(self):
        p = Path(__file__).resolve().parent.parent / "astrocrawl" / "rules" / "_prompt_template_position.txt"
        raw = p.read_text(encoding="utf-8")
        assert "RE2 Regex" in raw
        assert "extract" in raw.lower()
        assert "transform" in raw.lower()


class TestSchemaExampleContract:
    """契约测试：_generate_schema_example() 手写 JSON 示例与 RuleSchema 模型保持同步。

    手写示例中的字段名、约束值、可选值均与 RuleSchema Pydantic 模型 + _constants 对比。
    不一致 → 示例已过期，需同步更新 _generate_schema_example()。
    """

    def test_all_ruleschema_fields_appear(self):
        """RuleSchema 每个顶级字段名出现在示例中。"""
        from astrocrawl.rules._schema import RuleSchema

        example = _generate_schema_example()
        for field in RuleSchema.model_fields:
            assert f'"{field}"' in example, f"示例缺少 RuleSchema 字段: {field}"

    def test_all_matchconfig_fields_appear(self):
        """MatchConfig 每个字段名出现在 match 段。"""
        from astrocrawl.rules._schema import MatchConfig

        example = _generate_schema_example()
        for field in MatchConfig.model_fields:
            assert f'"{field}"' in example, f"示例 match 段缺少字段: {field}"

    def test_all_fieldrule_fields_appear(self):
        """FieldRule 每个字段名出现在 fields 段。"""
        from astrocrawl.rules._schema import FieldRule

        example = _generate_schema_example()
        for field in FieldRule.model_fields:
            assert f'"{field}"' in example, f"示例 fields 段缺少字段: {field}"

    def test_all_ruleoptions_fields_appear(self):
        """RuleOptions 每个字段名出现在 options 段。"""
        from astrocrawl.rules._schema import RuleOptions

        example = _generate_schema_example()
        for field in RuleOptions.model_fields:
            assert f'"{field}"' in example, f"示例 options 段缺少字段: {field}"

    def test_extract_types_match_valid_extract_types(self):
        """示例中 extract 注释的可选值与 VALID_EXTRACT_TYPES 一致。"""
        from astrocrawl.rules._schema import VALID_EXTRACT_TYPES

        example = _generate_schema_example()
        expected = " | ".join(sorted(VALID_EXTRACT_TYPES))
        assert expected in example, "extract 可选值与 VALID_EXTRACT_TYPES 不一致"

    def test_max_fallback_depth_reflected(self):
        """fallback 注释中的层数与 MAX_FALLBACK_DEPTH 一致。"""
        from astrocrawl._constants import MAX_FALLBACK_DEPTH

        example = _generate_schema_example()
        max_layers = MAX_FALLBACK_DEPTH - 1
        assert f"最多 {max_layers} 层" in example, (
            f"fallback 层数应为 {max_layers} (MAX_FALLBACK_DEPTH={MAX_FALLBACK_DEPTH} - 1 主选择器)"
        )

    def test_multiple_max_items_reflected(self):
        """multiple 注释中的上限与 MULTIPLE_MAX_ITEMS 一致。"""
        from astrocrawl._constants import MULTIPLE_MAX_ITEMS

        example = _generate_schema_example()
        assert f"最多 {MULTIPLE_MAX_ITEMS} 项" in example, f"multiple 上限应为 {MULTIPLE_MAX_ITEMS}"

    def test_rule_name_max_length_reflected(self):
        """name 注释中的长度限制与 RULE_NAME_MAX_LENGTH 一致。"""
        from astrocrawl._constants import RULE_NAME_MAX_LENGTH

        example = _generate_schema_example()
        assert f"≤{RULE_NAME_MAX_LENGTH}字符" in example, f"name 长度限制应为 {RULE_NAME_MAX_LENGTH}"

    def test_rule_name_pattern_reflected(self):
        """name 注释中的字符限制反映 RULE_NAME_PATTERN。"""
        example = _generate_schema_example()
        assert "[a-z0-9_-]" in example, "name 注释应含合法字符提示"

    @pytest.mark.parametrize(
        ("builtin", "label"),
        [
            (_BUILTIN_SYSTEM_PROMPT_TYPE, "type"),
            (_BUILTIN_SYSTEM_PROMPT_POSITION, "position"),
        ],
    )
    def test_reserved_field_names_in_prompt_match_schema(self, builtin, label):
        """内置模板中提及的字段禁用列表至少覆盖 _RESERVED_FIELD_NAMES 全部。"""
        from astrocrawl.rules._schema import _RESERVED_FIELD_NAMES

        marker = "Forbidden field names: "
        idx = builtin.index(marker) + len(marker)
        end = builtin.index(".", idx)
        names_in_prompt = {n.strip() for n in builtin[idx:end].split(",")}

        missing = _RESERVED_FIELD_NAMES - names_in_prompt
        assert not missing, f"{label} 模板未列出保留字段: {missing}。AI 可能生成通不过 validate_rule() 的规则。"

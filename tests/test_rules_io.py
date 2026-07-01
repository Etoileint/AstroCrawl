"""测试: astrocrawl/rules/_io.py — model_dump, BOM 处理, 非 dict JSON 拒绝。

补充 test_rules_lifecycle.py 未覆盖的 _io.py 函数。
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from astrocrawl.rules._io import (
    _check_duplicate_keys,
    export_rule,
    import_rule_preview,
    rule_to_dict,
    safe_read_rule_file,
)
from astrocrawl.rules._schema import FieldRule, MatchConfig, MatchScope, RuleOptions, RuleSchema

# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


def _make_minimal_rule(name: str = "test_rule") -> RuleSchema:
    return RuleSchema(name=name, match=MatchConfig(domains=["example.com"]))


def _make_field(
    selector: str = "h1",
    extract: str = "text",
    **kwargs,
) -> FieldRule:
    return FieldRule(selector=selector, extract=extract, **kwargs)


# ═══════════════════════════════════════════════════════════════════════
# FieldRule.model_dump — 递归序列化
# ═══════════════════════════════════════════════════════════════════════


class TestFieldToDict:
    """FieldRule.model_dump(mode="json") — Pydantic 自动递归序列化。"""

    def test_basic_field(self):
        d = _make_field("h1", extract="text").model_dump(mode="json")
        assert d == {
            "selector": "h1",
            "description": "",
            "extract": "text",
            "attr": "",
            "multiple": False,
            "fallback": [],
            "transform": {},
        }

    def test_field_with_attr(self):
        d = _make_field("a", extract="attr", attr="href").model_dump(mode="json")
        assert d["extract"] == "attr"
        assert d["attr"] == "href"

    def test_field_with_description(self):
        d = _make_field("h1", description="Title field").model_dump(mode="json")
        assert d["description"] == "Title field"

    def test_field_with_transform(self):
        d = _make_field("span", transform={"strip": True, "upper": True}).model_dump(mode="json")
        assert d["transform"] == {"strip": True, "upper": True}

    def test_field_with_multiple(self):
        d = _make_field("li", multiple=True).model_dump(mode="json")
        assert d["multiple"] is True

    def test_fallback_chain_recursive(self):
        inner = _make_field("h2", extract="text")
        outer = FieldRule(
            selector="article",
            extract="text",
            fallback=[inner],
        )
        d = outer.model_dump(mode="json")
        assert len(d["fallback"]) == 1
        assert d["fallback"][0]["selector"] == "h2"

    def test_nested_fallback_depth_two(self):
        leaf = _make_field("h3")
        mid = FieldRule(selector="h2", extract="text", fallback=[leaf])
        root = FieldRule(selector="h1", extract="text", fallback=[mid])
        d = root.model_dump(mode="json")
        assert len(d["fallback"]) == 1
        assert d["fallback"][0]["selector"] == "h2"
        assert d["fallback"][0]["fallback"][0]["selector"] == "h3"


# ═══════════════════════════════════════════════════════════════════════
# rule_to_dict
# ═══════════════════════════════════════════════════════════════════════


class TestRuleToDict:
    """rule_to_dict — RuleSchema.model_dump(mode="json") 序列化。"""

    def test_minimal_rule(self):
        rule = _make_minimal_rule("test")
        d = rule_to_dict(rule)
        assert d["name"] == "test"
        assert d["schema_version"] == 1
        assert d["version"] == 1
        assert "match" in d
        assert "fields" in d
        assert "options" in d

    def test_match_config(self):
        rule = RuleSchema(
            name="test",
            match=MatchConfig(
                scope=MatchScope.DOMAIN_PATTERN,
                domains=["example.com"],
                url_pattern="/blog/.*",
            ),
        )
        d = rule_to_dict(rule)
        assert d["match"]["scope"] == "domain_pattern"
        assert d["match"]["domains"] == ["example.com"]
        assert d["match"]["url_pattern"] == "/blog/.*"

    def test_match_scope_global_pattern(self):
        rule = RuleSchema(
            name="test",
            match=MatchConfig(scope=MatchScope.GLOBAL_PATTERN, domains=[], url_pattern="/api/.*"),
        )
        d = rule_to_dict(rule)
        assert d["match"]["scope"] == "global_pattern"
        assert d["match"]["domains"] == []

    def test_fields_serialized(self):
        rule = RuleSchema(
            name="test",
            match=MatchConfig(domains=["example.com"]),
            fields={
                "title": _make_field("h1"),
                "price": _make_field(".price", extract="text"),
            },
        )
        d = rule_to_dict(rule)
        assert len(d["fields"]) == 2
        assert d["fields"]["title"]["selector"] == "h1"
        assert d["fields"]["price"]["selector"] == ".price"

    def test_options_serialized(self):
        rule = _make_minimal_rule("test").model_copy(
            update={"options": RuleOptions(keep_body_text=True, follow_links=False)}
        )
        d = rule_to_dict(rule)
        assert d["options"]["keep_body_text"] is True
        assert d["options"]["follow_links"] is False

    def test_tags_serialized(self):
        rule = _make_minimal_rule("test").model_copy(update={"tags": ["ecommerce", "blog"]})
        d = rule_to_dict(rule)
        assert d["tags"] == ["ecommerce", "blog"]

    def test_display_name(self):
        rule = _make_minimal_rule("test").model_copy(update={"display_name": "Test Rule"})
        d = rule_to_dict(rule)
        assert d["display_name"] == "Test Rule"

    def test_author(self):
        rule = _make_minimal_rule("test").model_copy(update={"author": "user@example.com"})
        d = rule_to_dict(rule)
        assert d["author"] == "user@example.com"

    def test_test_urls_serialized(self):
        rule = _make_minimal_rule("test").model_copy(update={"test_urls": [{"url": "https://example.com/page1"}]})
        d = rule_to_dict(rule)
        assert len(d["test_urls"]) == 1
        assert d["test_urls"][0]["url"] == "https://example.com/page1"


# ═══════════════════════════════════════════════════════════════════════
# export_rule — 边界情况
# ═══════════════════════════════════════════════════════════════════════


class TestExportRuleEdge:
    """export_rule 补充测试——test_rules_lifecycle.py 已有基础覆盖。"""

    def test_author_always_stripped(self):
        d = export_rule(RuleSchema.model_validate({"name": "test", "author": "someone@example.com"}))
        assert d["author"] == ""

    def test_author_stripped_even_when_empty_in_input(self):
        d = export_rule(RuleSchema.model_validate({"name": "test", "author": ""}))
        assert d["author"] == ""

    def test_test_urls_always_empty(self):
        d = export_rule(RuleSchema.model_validate({"name": "test", "test_urls": [{"url": "https://a.com"}]}))
        assert d["test_urls"] == []

    def test_export_note_present(self):
        d = export_rule(RuleSchema.model_validate({"name": "test"}))
        assert "_export_note" in d
        assert "test_urls" in d["_export_note"]

    def test_missing_keys_get_defaults(self):
        d = export_rule(RuleSchema.model_validate({"name": "minimal"}))
        assert d["schema_version"] == 1
        assert d["version"] == 1
        assert d["tags"] == []
        assert d["enabled"] is True

    def test_preserves_user_fields(self):
        d = export_rule(
            RuleSchema.model_validate(
                {
                    "name": "test",
                    "fields": {"title": {"selector": "h1", "extract": "text"}},
                }
            )
        )
        assert d["fields"]["title"]["selector"] == "h1"


# ═══════════════════════════════════════════════════════════════════════
# safe_read_rule_file — BOM + 非 dict 拒绝
# ═══════════════════════════════════════════════════════════════════════


class TestSafeReadRuleFile:
    def test_bom_stripped(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write('﻿{"name": "test"}\n')
            tmp = Path(f.name)
        try:
            result = safe_read_rule_file(tmp)
            assert result["name"] == "test"
        finally:
            tmp.unlink()

    def test_non_dict_raises(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("[1, 2, 3]\n")
            tmp = Path(f.name)
        try:
            with pytest.raises(ValueError, match="JSON 对象"):
                safe_read_rule_file(tmp)
        finally:
            tmp.unlink()

    def test_non_dict_string_raises(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write('"a string"\n')
            tmp = Path(f.name)
        try:
            with pytest.raises(ValueError, match="JSON 对象"):
                safe_read_rule_file(tmp)
        finally:
            tmp.unlink()

    def test_non_dict_number_raises(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("42\n")
            tmp = Path(f.name)
        try:
            with pytest.raises(ValueError, match="JSON 对象"):
                safe_read_rule_file(tmp)
        finally:
            tmp.unlink()

    def test_invalid_json_raises(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{invalid}\n")
            tmp = Path(f.name)
        try:
            with pytest.raises(ValueError, match="JSON 解析失败"):
                safe_read_rule_file(tmp)
        finally:
            tmp.unlink()

    def test_valid_nested_object(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"name": "test", "fields": {"a": {"selector": "h1"}}}, f)
            tmp = Path(f.name)
        try:
            result = safe_read_rule_file(tmp)
            assert result["fields"]["a"]["selector"] == "h1"
        finally:
            tmp.unlink()


# ═══════════════════════════════════════════════════════════════════════
# import_rule_preview — markdown 清洗 + 非 rule 拒绝
# ═══════════════════════════════════════════════════════════════════════


class TestImportRulePreviewEdge:
    """import_rule_preview 补充——test_rules_lifecycle.py 已有基础覆盖。"""

    def test_cleans_markdown_wrapper(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write('```json\n{"name": "test", "match": {"domains": ["a.com"]}}\n```\n')
            tmp = Path(f.name)
        try:
            preview = import_rule_preview(tmp)
            assert preview["name"] == "test"
        finally:
            tmp.unlink()

    def test_missing_name_raises(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write('{"fields": {"a": {"selector": "h1"}}}\n')
            tmp = Path(f.name)
        try:
            with pytest.raises(ValueError, match="缺少 name 字段"):
                import_rule_preview(tmp)
        finally:
            tmp.unlink()

    def test_not_a_dict_raises(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write('[{"name": "test"}]\n')
            tmp = Path(f.name)
        try:
            with pytest.raises(ValueError):
                import_rule_preview(tmp)
        finally:
            tmp.unlink()

    def test_fields_count_in_preview(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(
                {
                    "name": "test",
                    "match": {},
                    "fields": {"title": {"selector": "h1"}, "price": {"selector": ".price"}},
                },
                f,
            )
            tmp = Path(f.name)
        try:
            preview = import_rule_preview(tmp)
            assert preview["fields_count"] == 2
        finally:
            tmp.unlink()

    def test_fields_count_zero(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"name": "test", "match": {}}, f)
            tmp = Path(f.name)
        try:
            preview = import_rule_preview(tmp)
            assert preview["fields_count"] == 0
        finally:
            tmp.unlink()

    def test_invalid_json_raises_in_preview(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("not json at all")
            tmp = Path(f.name)
        try:
            with pytest.raises(ValueError, match="无法解析为 JSON"):
                import_rule_preview(tmp)
        finally:
            tmp.unlink()


# ═══════════════════════════════════════════════════════════════════════
# _check_duplicate_keys
# ═══════════════════════════════════════════════════════════════════════


class TestCheckDuplicateKeys:
    """_check_duplicate_keys — object_pairs_hook 直接测试。"""

    def test_no_duplicates(self):
        result = _check_duplicate_keys([("a", 1), ("b", 2)])
        assert result == {"a": 1, "b": 2}

    def test_duplicate_key_raises(self):
        with pytest.raises(ValueError, match="重复 key"):
            _check_duplicate_keys([("a", 1), ("a", 2)])

    def test_duplicate_second_position(self):
        with pytest.raises(ValueError, match="重复 key.*b"):
            _check_duplicate_keys([("a", 1), ("b", 2), ("b", 3)])

    def test_three_duplicates_raises_on_second(self):
        with pytest.raises(ValueError):
            _check_duplicate_keys([("a", 1), ("a", 2), ("a", 3)])

    def test_single_pair(self):
        result = _check_duplicate_keys([("key", "value")])
        assert result == {"key": "value"}

    def test_empty_pairs(self):
        result = _check_duplicate_keys([])
        assert result == {}

    def test_nested_values_preserved(self):
        result = _check_duplicate_keys([("a", {"nested": True}), ("b", [1, 2, 3])])
        assert result == {"a": {"nested": True}, "b": [1, 2, 3]}

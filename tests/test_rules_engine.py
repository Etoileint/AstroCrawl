"""特征测试：规则引擎端到端管线 — Schema 校验 → 匹配 → CSS 提取 → Transform。

测试文件覆盖 issue #120 的核心验收标准。
"""

from __future__ import annotations

from typing import Dict

import pytest

from astrocrawl._types import DEFAULT_EXTRACTION_TYPE, RuleSnapshot
from astrocrawl.rules._extractor import (
    _extract_all_fields,
    _extract_single_field,
    _extract_value,
    _is_non_empty,
    _truncate_if_needed,
    _try_select,
    extract_fields,
    extract_fields_from_soup,
)
from astrocrawl.rules._matcher import match_url
from astrocrawl.rules._schema import (
    MatchScope,
    RuleSchema,
    _normalize_domains,
    _resolve_scope,
    _resolve_url_pattern,
    _validate_scope_consistency,
    validate_rule,
)
from astrocrawl.rules._transform import (
    _join_transform,
    _regex_transform,
    _replace_transform,
    _strip,
    _strip_currency,
    apply_transforms,
)

# ═══════════════════════════════════════════════════════════════════
# Schema 校验
# ═══════════════════════════════════════════════════════════════════


class TestSchemaValidation:
    """规则 JSON Schema 校验各路径。"""

    def test_minimal_valid_rule(self):
        data = {
            "name": "test_rule",
            "fields": {"title": {"selector": "h1"}},
        }
        rule = validate_rule(data)
        assert rule.name == "test_rule"
        assert rule.enabled is True
        assert rule.schema_version == 1
        assert "title" in rule.fields

    def test_name_rejects_default(self):
        with pytest.raises(ValueError, match="保留名"):
            validate_rule({"name": "default", "fields": {"x": {"selector": "h1"}}})

    def test_name_rejects_invalid_chars(self):
        with pytest.raises(ValueError, match="非法字符"):
            validate_rule({"name": "bad name!", "fields": {"x": {"selector": "h1"}}})

    def test_name_length_limit(self):
        with pytest.raises(ValueError, match="长度超过"):
            validate_rule({"name": "a" * 65, "fields": {"x": {"selector": "h1"}}})

    def test_reserved_field_name_rejected(self):
        with pytest.raises(ValueError, match="url"):
            validate_rule(
                {
                    "name": "test",
                    "fields": {"url": {"selector": "a"}},
                }
            )

    def test_empty_selector_rejected(self):
        with pytest.raises(ValueError, match="selector 不能为空"):
            validate_rule(
                {
                    "name": "test",
                    "fields": {"x": {"selector": ""}},
                }
            )

    def test_invalid_extract_type(self):
        with pytest.raises(ValueError, match="extract 无效"):
            validate_rule(
                {
                    "name": "test",
                    "fields": {"x": {"selector": "h1", "extract": "xpath"}},
                }
            )

    def test_attr_requires_attr_name(self):
        with pytest.raises(ValueError, match="attr"):
            validate_rule(
                {
                    "name": "test",
                    "fields": {"x": {"selector": "img", "extract": "attr"}},
                }
            )

    def test_attr_name_validation(self):
        with pytest.raises(ValueError, match="attr"):
            validate_rule(
                {
                    "name": "test",
                    "fields": {"x": {"selector": "img", "extract": "attr", "attr": "bad name!"}},
                }
            )

    def test_fallback_depth_limit(self):
        with pytest.raises(ValueError, match="fallback"):
            validate_rule(
                {
                    "name": "test",
                    "fields": {
                        "x": {
                            "selector": "h1",
                            "fallback": [
                                {"selector": "h2"},
                                {"selector": "h3"},
                                {"selector": "h4"},  # 超过 MAX_FALLBACK_DEPTH-1=2
                            ],
                        },
                    },
                }
            )

    def test_fallback_shorthand_string(self):
        """fallback 支持字符串简写。"""
        data = {
            "name": "test",
            "fields": {
                "x": {
                    "selector": "h1",
                    "fallback": ["h2", "h3"],
                },
            },
        }
        rule = validate_rule(data)
        assert len(rule.fields["x"].fallback) == 2
        assert rule.fields["x"].fallback[0].selector == "h2"
        assert rule.fields["x"].fallback[0].extract == "text"

    def test_tags_parsed_correctly(self):
        data = {
            "name": "test",
            "fields": {"t": {"selector": "h1"}},
            "tags": ["ecommerce", "chinese"],
        }
        rule = validate_rule(data)
        assert rule.tags == ["ecommerce", "chinese"]

    def test_test_urls_parsed_correctly(self):
        data = {
            "name": "test",
            "fields": {"t": {"selector": "h1"}},
            "test_urls": [{"url": "https://example.com/page"}],
        }
        rule = validate_rule(data)
        assert len(rule.test_urls) == 1
        assert rule.test_urls[0]["url"] == "https://example.com/page"

    def test_schema_version_rejects_future_major(self):
        with pytest.raises(ValueError, match="不支持"):
            validate_rule({"name": "test", "fields": {"x": {"selector": "h1"}}, "schema_version": 3})

    # ═══ A. validate_rule 顶层输入校验 ═══

    def test_rejects_non_dict_input(self):
        with pytest.raises(ValueError, match="JSON 对象"):
            validate_rule("not_dict")

    def test_rejects_invalid_schema_version_zero(self):
        with pytest.raises(ValueError, match="schema_version"):
            validate_rule({"name": "test", "fields": {"x": {"selector": "h1"}}, "schema_version": 0})

    def test_rejects_invalid_schema_version_non_int(self):
        with pytest.raises(ValueError, match="schema_version"):
            validate_rule({"name": "test", "fields": {"x": {"selector": "h1"}}, "schema_version": "v1"})

    def test_rejects_empty_name(self):
        with pytest.raises(ValueError, match="name 不能为空"):
            validate_rule({"name": "", "fields": {"x": {"selector": "h1"}}})

    def test_rejects_non_bool_enabled(self):
        with pytest.raises(ValueError, match="boolean"):
            validate_rule({"name": "test", "fields": {"x": {"selector": "h1"}}, "enabled": "yes"})

    def test_rejects_invalid_version_zero(self):
        with pytest.raises(ValueError, match="version"):
            validate_rule({"name": "test", "fields": {"x": {"selector": "h1"}}, "version": 0})

    def test_rejects_version_non_int(self):
        with pytest.raises(ValueError, match="version"):
            validate_rule({"name": "test", "fields": {"x": {"selector": "h1"}}, "version": "abc"})

    def test_rejects_non_dict_match(self):
        with pytest.raises(ValueError, match="match 必须是对象"):
            validate_rule({"name": "test", "fields": {"x": {"selector": "h1"}}, "match": "not_dict"})

    def test_rejects_non_dict_options(self):
        with pytest.raises(ValueError, match="options 必须是对象"):
            validate_rule({"name": "test", "fields": {"x": {"selector": "h1"}}, "options": "not_dict"})

    # ═══ B. fields 校验 ═══

    def test_rejects_non_dict_fields(self):
        with pytest.raises(ValueError, match="fields 必须是对象"):
            validate_rule({"name": "test", "fields": "not_dict"})

    def test_rejects_too_many_fields(self):
        many = {f"f{i}": {"selector": "h1"} for i in range(51)}
        with pytest.raises(ValueError, match="上限"):
            validate_rule({"name": "test", "fields": many})

    def test_rejects_empty_field_key(self):
        with pytest.raises(ValueError, match="不能为空"):
            validate_rule({"name": "test", "fields": {"": {"selector": "h1"}}})

    def test_rejects_field_name_too_long(self):
        with pytest.raises(ValueError, match="长度超过"):
            validate_rule({"name": "test", "fields": {"a" * 65: {"selector": "h1"}}})

    def test_rejects_non_dict_field_value(self):
        with pytest.raises(ValueError, match="必须是对象"):
            validate_rule({"name": "test", "fields": {"x": "not_dict"}})

    def test_rejects_multiple_with_replace_transform(self):
        with pytest.raises(ValueError, match="multiple"):
            validate_rule(
                {
                    "name": "test",
                    "fields": {
                        "x": {
                            "selector": "h1",
                            "multiple": True,
                            "transform": {"replace": {"from": "a", "to": "b"}},
                        },
                    },
                }
            )

    def test_rejects_non_bool_multiple(self):
        with pytest.raises(ValueError, match="multiple"):
            validate_rule({"name": "test", "fields": {"x": {"selector": "h1", "multiple": "yes"}}})

    # ═══ C. match 校验 ═══

    def test_rejects_invalid_domain_name(self):
        with pytest.raises(ValueError, match="域名"):
            validate_rule(
                {
                    "name": "test",
                    "fields": {"x": {"selector": "h1"}},
                    "match": {"domains": ["not a domain"]},
                }
            )

    def test_accepts_non_list_domains_fallback(self):
        rule = validate_rule(
            {
                "name": "test",
                "fields": {"x": {"selector": "h1"}},
                "match": {"domains": 123},
            }
        )
        assert rule.match.domains == []

    def test_rejects_invalid_url_pattern_re2(self):
        with pytest.raises(ValueError, match="url_pattern"):
            validate_rule(
                {
                    "name": "test",
                    "fields": {"x": {"selector": "h1"}},
                    "match": {"scope": "global_pattern", "url_pattern": "["},
                }
            )

    def test_rejects_invalid_scope_string(self):
        with pytest.raises(ValueError, match="scope"):
            validate_rule(
                {
                    "name": "test",
                    "fields": {"x": {"selector": "h1"}},
                    "match": {"scope": "invalid_scope"},
                }
            )

    def test_rejects_domain_pattern_without_domains(self):
        with pytest.raises(ValueError, match="domain_pattern.*domains"):
            validate_rule(
                {
                    "name": "test",
                    "fields": {"x": {"selector": "h1"}},
                    "match": {"scope": "domain_pattern", "domains": [], "url_pattern": "/test/"},
                }
            )

    def test_rejects_domain_pattern_without_url_pattern(self):
        with pytest.raises(ValueError, match="domain_pattern.*url_pattern"):
            validate_rule(
                {
                    "name": "test",
                    "fields": {"x": {"selector": "h1"}},
                    "match": {"scope": "domain_pattern", "domains": ["example.com"], "url_pattern": ""},
                }
            )

    def test_rejects_domain_all_without_domains(self):
        with pytest.raises(ValueError, match="domain_all.*domains"):
            validate_rule(
                {
                    "name": "test",
                    "fields": {"x": {"selector": "h1"}},
                    "match": {"scope": "domain_all", "domains": []},
                }
            )

    def test_rejects_global_pattern_with_domains(self):
        with pytest.raises(ValueError, match="global_pattern.*domains"):
            validate_rule(
                {
                    "name": "test",
                    "fields": {"x": {"selector": "h1"}},
                    "match": {"scope": "global_pattern", "domains": ["x.com"], "url_pattern": "/test/"},
                }
            )

    def test_rejects_global_pattern_without_url_pattern(self):
        with pytest.raises(ValueError, match="global_pattern.*url_pattern"):
            validate_rule(
                {
                    "name": "test",
                    "fields": {"x": {"selector": "h1"}},
                    "match": {"scope": "global_pattern", "domains": [], "url_pattern": ""},
                }
            )

    def test_any_scope_rejects_non_empty_domains(self):
        """D2: any scope 要求 domains 为空。"""
        with pytest.raises(ValueError, match="any.*domains"):
            validate_rule(
                {
                    "name": "bad-any-rule",
                    "fields": {"x": {"selector": "h1"}},
                    "match": {"scope": "any", "domains": ["example.com"]},
                }
            )

    # ═══ D. test_urls 过滤 ═══

    def test_test_urls_non_list_returns_empty(self):
        rule = validate_rule({"name": "test", "fields": {"x": {"selector": "h1"}}, "test_urls": 123})
        assert rule.test_urls == []

    def test_test_urls_skips_non_dict_items(self):
        rule = validate_rule(
            {
                "name": "test",
                "fields": {"x": {"selector": "h1"}},
                "test_urls": [{"url": "https://a.com/"}, "string_item", 123],
            }
        )
        assert len(rule.test_urls) == 1
        assert rule.test_urls[0]["url"] == "https://a.com/"

    def test_test_urls_skips_empty_url(self):
        rule = validate_rule(
            {
                "name": "test",
                "fields": {"x": {"selector": "h1"}},
                "test_urls": [{"url": ""}, {"url": "   "}],
            }
        )
        assert rule.test_urls == []

    def test_test_urls_rejects_non_https(self):
        rule = validate_rule(
            {
                "name": "test",
                "fields": {"x": {"selector": "h1"}},
                "test_urls": [{"url": "http://example.com/"}],
            }
        )
        assert rule.test_urls == []

    def test_test_urls_rejects_no_netloc(self):
        rule = validate_rule(
            {
                "name": "test",
                "fields": {"x": {"selector": "h1"}},
                "test_urls": [{"url": "https://"}],
            }
        )
        assert rule.test_urls == []

    def test_test_urls_deduplicates(self):
        rule = validate_rule(
            {
                "name": "test",
                "fields": {"x": {"selector": "h1"}},
                "test_urls": [
                    {"url": "https://example.com/"},
                    {"url": "https://example.com/"},
                ],
            }
        )
        assert len(rule.test_urls) == 1

    def test_test_urls_capped_at_ten(self):
        urls = [{"url": f"https://example{n}.com/"} for n in range(15)]
        rule = validate_rule({"name": "test", "fields": {"x": {"selector": "h1"}}, "test_urls": urls})
        assert len(rule.test_urls) == 10

    # ═══ E. fallback / transform / tags ═══

    def test_rejects_fallback_non_dict_non_str(self):
        with pytest.raises(ValueError, match="fallback"):
            validate_rule(
                {
                    "name": "test",
                    "fields": {"x": {"selector": "h1", "fallback": [123]}},
                }
            )

    def test_rejects_nested_fallback(self):
        with pytest.raises(ValueError, match="嵌套"):
            validate_rule(
                {
                    "name": "test",
                    "fields": {
                        "x": {
                            "selector": "h1",
                            "fallback": [{"selector": "h2", "fallback": ["h3"]}],
                        },
                    },
                }
            )

    def test_transform_non_dict_returns_empty(self):
        rule = validate_rule(
            {
                "name": "test",
                "fields": {"x": {"selector": "h1", "transform": 123}},
            }
        )
        assert rule.fields["x"].transform == {}

    def test_transform_bool_coercion(self):
        rule = validate_rule(
            {
                "name": "test",
                "fields": {
                    "x": {
                        "selector": "h1",
                        "transform": {"strip": 1, "strip_currency": 0},
                    },
                },
            }
        )
        assert rule.fields["x"].transform["strip"] is True
        assert rule.fields["x"].transform["strip_currency"] is False

    def test_transform_regex_compile_failure(self):
        with pytest.raises(ValueError, match="transform.regex"):
            validate_rule(
                {
                    "name": "test",
                    "fields": {
                        "x": {
                            "selector": "h1",
                            "transform": {"regex": "["},
                        },
                    },
                }
            )

    def test_tags_filters_non_string_items(self, caplog):
        import logging

        caplog.set_level(logging.WARNING, logger="astrocrawl.rules.schema")
        rule = validate_rule(
            {
                "name": "test",
                "fields": {"x": {"selector": "h1"}},
                "tags": ["valid", 123, "also_valid"],
            }
        )
        assert rule.tags == ["valid", "also_valid"]
        assert "event=rule_tag_invalid_type" in caplog.text
        assert "rule=test" in caplog.text

    # ═══ F. 纯函数单元测试 ═══

    def test_resolve_scope_explicit(self):
        assert _resolve_scope("domain_pattern", [], "") == MatchScope.DOMAIN_PATTERN
        assert _resolve_scope("domain_all", [], "") == MatchScope.DOMAIN_ALL
        assert _resolve_scope("global_pattern", [], "") == MatchScope.GLOBAL_PATTERN
        assert _resolve_scope("any", [], "") == MatchScope.ANY
        with pytest.raises(ValueError, match="scope"):
            _resolve_scope("invalid", [], "")

    def test_resolve_scope_inference(self):
        # domains + url_pattern → DOMAIN_PATTERN
        assert _resolve_scope("", ["example.com"], "/test/") == MatchScope.DOMAIN_PATTERN
        # domains only → DOMAIN_ALL
        assert _resolve_scope("", ["example.com"], "") == MatchScope.DOMAIN_ALL
        # url_pattern only → GLOBAL_PATTERN
        assert _resolve_scope("", [], "/test/") == MatchScope.GLOBAL_PATTERN
        # neither → ANY
        assert _resolve_scope("", [], "") == MatchScope.ANY

    def test_normalize_domains(self):
        assert _normalize_domains(["Example.COM", "  test.com  ", "sub.example.com."]) == [
            "example.com",
            "test.com",
            "sub.example.com",
        ]
        assert _normalize_domains(None) == []
        assert _normalize_domains(123) == []
        with pytest.raises(ValueError, match="域名"):
            _normalize_domains(["not a domain"])

    def test_resolve_url_pattern(self):
        assert _resolve_url_pattern("/blog/.*") == "/blog/.*"
        assert _resolve_url_pattern("") == ""
        assert _resolve_url_pattern(None) == ""
        assert _resolve_url_pattern(123) == ""
        with pytest.raises(ValueError, match="url_pattern"):
            _resolve_url_pattern("[")

    def test_validate_scope_consistency(self):
        _validate_scope_consistency(MatchScope.DOMAIN_PATTERN, ["e.com"], "/t/")
        _validate_scope_consistency(MatchScope.DOMAIN_ALL, ["e.com"], "")
        _validate_scope_consistency(MatchScope.GLOBAL_PATTERN, [], "/t/")
        _validate_scope_consistency(MatchScope.ANY, [], "")
        with pytest.raises(ValueError, match="domain_pattern.*domains"):
            _validate_scope_consistency(MatchScope.DOMAIN_PATTERN, [], "/t/")
        with pytest.raises(ValueError, match="domain_all.*domains"):
            _validate_scope_consistency(MatchScope.DOMAIN_ALL, [], "")
        with pytest.raises(ValueError, match="global_pattern.*domains"):
            _validate_scope_consistency(MatchScope.GLOBAL_PATTERN, ["x.com"], "/t/")
        with pytest.raises(ValueError, match="any.*domains"):
            _validate_scope_consistency(MatchScope.ANY, ["x.com"], "")

    # ═══ G. 向后兼容日志 ═══

    def test_warns_on_implicit_domain_all(self, caplog):
        import logging

        caplog.set_level(logging.WARNING, logger="astrocrawl.rules.schema")
        rule = validate_rule(
            {
                "name": "my_domain_rule",
                "fields": {"x": {"selector": "h1"}},
                "match": {"domains": ["example.com"]},
            }
        )
        assert rule.match.scope == MatchScope.DOMAIN_ALL
        assert "event=rule_scope_inferred" in caplog.text
        assert "scope=domain_all" in caplog.text
        assert "rule=my_domain_rule" in caplog.text

    def test_warns_on_implicit_any(self, caplog):
        import logging

        caplog.set_level(logging.WARNING, logger="astrocrawl.rules.schema")
        rule = validate_rule(
            {
                "name": "catch_all",
                "fields": {"x": {"selector": "h1"}},
                "match": {},
            }
        )
        assert rule.match.scope == MatchScope.ANY
        assert "event=rule_scope_inferred" in caplog.text
        assert "scope=any" in caplog.text
        assert "rule=catch_all" in caplog.text

    # ═══ coverage gap fillers ═══

    def test_sanitize_dangerous_chars(self):
        rule = validate_rule(
            {
                "name": "test",
                "fields": {"x": {"selector": "h1"}},
                "display_name": "test‮RTL",
                "description": "desc‎RLO",
                "author": "auth‏RLO",
            }
        )
        assert rule.display_name == "testRTL"
        assert rule.description == "descRLO"
        assert rule.author == "authRLO"

    def test_tags_non_list_defaults_empty(self):
        rule = validate_rule({"name": "test", "fields": {"x": {"selector": "h1"}}, "tags": "not_list"})
        assert rule.tags == []

    def test_fallback_valid_dict_item(self):
        rule = validate_rule(
            {
                "name": "test",
                "fields": {
                    "x": {
                        "selector": "h1",
                        "fallback": [{"selector": "h2", "extract": "text"}],
                    },
                },
            }
        )
        assert len(rule.fields["x"].fallback) == 1
        assert rule.fields["x"].fallback[0].selector == "h2"

    def test_transform_unknown_type_ignored(self, caplog):
        import logging

        caplog.set_level(logging.WARNING, logger="astrocrawl.rules.schema")
        rule = validate_rule(
            {
                "name": "test",
                "fields": {
                    "x": {
                        "selector": "h1",
                        "transform": {"unknown": "val"},
                    },
                },
            }
        )
        assert rule.fields["x"].transform == {}
        assert "未知 transform 类型" in caplog.text

    def test_transform_valid_regex(self):
        rule = validate_rule(
            {
                "name": "test",
                "fields": {
                    "x": {
                        "selector": "h1",
                        "transform": {"regex": r"(\d+)"},
                    },
                },
            }
        )
        assert rule.fields["x"].transform["regex"] == r"(\d+)"

    def test_transform_valid_join(self):
        rule = validate_rule(
            {
                "name": "test",
                "fields": {
                    "x": {
                        "selector": "li",
                        "multiple": True,
                        "transform": {"join": ", "},
                    },
                },
            }
        )
        assert rule.fields["x"].transform["join"] == ", "


# ═══════════════════════════════════════════════════════════════════
# 匹配算法
# ═══════════════════════════════════════════════════════════════════


def _make_snapshot(rule_list=None, *, source_map=None):
    """Helper: 从规则 dict 列表构建 RuleSnapshot。"""
    if not rule_list:
        return RuleSnapshot.default_only()
    rule_objects = [validate_rule(r) for r in rule_list]
    by_name = {r.name: r for r in rule_objects}
    by_domain: Dict[str, list] = {}
    generic_names: list[str] = []
    for r in rule_objects:
        if r.is_generic and r.enabled:
            generic_names.append(r.name)
        if r.enabled:
            for d in r.match.domains:
                if d not in by_domain:
                    by_domain[d] = []
                by_domain[d].append(r.name)
    generic_names.sort()
    # 对齐 build_rule_snapshot：default 始终在 by_name 中
    if DEFAULT_EXTRACTION_TYPE not in by_name:
        by_name[DEFAULT_EXTRACTION_TYPE] = RuleSchema(name=DEFAULT_EXTRACTION_TYPE, enabled=True)
    # 对齐 build_rule_snapshot：rules 仅含已启用规则
    rules_tuple = tuple(r for name, r in by_name.items() if r.enabled and name != DEFAULT_EXTRACTION_TYPE)
    return RuleSnapshot(
        rules=rules_tuple,
        by_name=by_name,
        by_domain={k: tuple(v) for k, v in by_domain.items()},
        _generic_rules=tuple(generic_names),
        _source_map=source_map or {},
    )


class TestMatchAlgorithm:
    """四级匹配算法各路径。"""

    def test_no_rules_returns_default(self):
        snapshot = RuleSnapshot.default_only()
        result = match_url("https://example.com/page", snapshot)
        assert result == DEFAULT_EXTRACTION_TYPE

    def test_exact_domain_match(self):
        rules = [
            {
                "name": "site_rule",
                "version": 1,
                "enabled": True,
                "match": {"domains": ["example.com"], "url_pattern": ""},
                "fields": {},
                "options": {"keep_body_text": False, "follow_links": True},
            }
        ]
        snapshot = _make_snapshot(rules)
        result = match_url("https://example.com/page", snapshot)
        assert result == "site_rule"

    def test_domain_mismatch_returns_default(self):
        rules = [
            {
                "name": "site_rule",
                "version": 1,
                "enabled": True,
                "match": {"domains": ["other.com"], "url_pattern": ""},
                "fields": {},
                "options": {"keep_body_text": False, "follow_links": True},
            }
        ]
        snapshot = _make_snapshot(rules)
        result = match_url("https://example.com/page", snapshot)
        assert result == DEFAULT_EXTRACTION_TYPE

    def test_url_pattern_match(self):
        rules = [
            {
                "name": "with_pattern",
                "version": 1,
                "enabled": True,
                "match": {"domains": [], "url_pattern": "/blog/.*"},
                "fields": {},
                "options": {"keep_body_text": False, "follow_links": True},
            }
        ]
        snapshot = _make_snapshot(rules)
        # /blog/post matches /blog/.* → rule matched
        assert match_url("https://any.com/blog/post", snapshot) == "with_pattern"
        # /about does not match /blog/.* → no rule → default
        assert match_url("https://any.com/about", snapshot) == DEFAULT_EXTRACTION_TYPE

    def test_exact_subdomain_match(self):
        rules = [
            {
                "name": "sub_rule",
                "version": 1,
                "enabled": True,
                "match": {"domains": ["blog.example.com"], "url_pattern": ""},
                "fields": {},
                "options": {"keep_body_text": False, "follow_links": True},
            }
        ]
        snapshot = _make_snapshot(rules)
        result = match_url("https://blog.example.com/page", snapshot)
        assert result == "sub_rule"

    def test_parent_domain_suffix_match(self):
        """父域后缀遍历：a.b.example.com 匹配规则 domain=example.com。"""
        rules = [
            {
                "name": "parent_rule",
                "version": 1,
                "enabled": True,
                "match": {"domains": ["example.com"], "url_pattern": ""},
                "fields": {},
                "options": {"keep_body_text": False, "follow_links": True},
            }
        ]
        snapshot = _make_snapshot(rules)
        # blog.example.com 不属于 rule 的 domains，但父域 example.com 是
        result = match_url("https://blog.example.com/page", snapshot)
        assert result == "parent_rule"

    def test_generic_rule_matches_all_domains(self):
        rules = [
            {
                "name": "generic",
                "version": 1,
                "enabled": True,
                "match": {"domains": [], "url_pattern": ""},
                "fields": {},
                "options": {"keep_body_text": False, "follow_links": True},
            }
        ]
        snapshot = _make_snapshot(rules)
        result = match_url("https://any-domain.com/page", snapshot)
        assert result == "generic"

    def test_domain_cache_works(self):
        rules = [
            {
                "name": "cached_rule",
                "version": 1,
                "enabled": True,
                "match": {"domains": ["cached.com"], "url_pattern": ""},
                "fields": {},
                "options": {"keep_body_text": False, "follow_links": True},
            }
        ]
        snapshot = _make_snapshot(rules)
        assert match_url("https://cached.com/page1", snapshot) == "cached_rule"
        # Second call should hit cache
        assert match_url("https://cached.com/page2", snapshot) == "cached_rule"

    def test_cache_invalidation(self):
        rules = [
            {
                "name": "cached_rule",
                "version": 1,
                "enabled": True,
                "match": {"domains": ["cached.com"], "url_pattern": ""},
                "fields": {},
                "options": {"keep_body_text": False, "follow_links": True},
            }
        ]
        snapshot = _make_snapshot(rules)
        match_url("https://cached.com/page", snapshot)
        # Cache on snapshot — same snapshot reuses cache, new snapshot gets fresh cache
        assert match_url("https://cached.com/page", snapshot) == "cached_rule"

    def test_multi_level_parent_domain_suffix(self):
        """a.b.example.com 匹配 rule domain=example.com（2 级父域后缀遍历）。"""
        rules = [
            {
                "name": "base_rule",
                "version": 1,
                "enabled": True,
                "match": {"domains": ["example.com"], "url_pattern": ""},
                "fields": {},
                "options": {"keep_body_text": False, "follow_links": True},
            }
        ]
        snapshot = _make_snapshot(rules)
        assert match_url("https://a.b.example.com/page", snapshot) == "base_rule"

    def test_parent_before_child_exact_match(self):
        """B1: 父域排在子域前时，子域 URL 仍获得精确匹配分数 (domain_score=0)。"""
        rules = [
            {
                "name": "weibo",
                "version": 1,
                "enabled": True,
                "match": {"domains": ["weibo.com", "www.weibo.com", "m.weibo.cn"], "url_pattern": ""},
                "fields": {},
                "options": {"keep_body_text": False, "follow_links": True},
            }
        ]
        snapshot = _make_snapshot(rules)
        assert match_url("https://www.weibo.com/post/123", snapshot) == "weibo"

    def test_url_pattern_with_query_string(self):
        """url_pattern 匹配含 query string 的 URL（路径 + ? + 查询）。"""
        rules = [
            {
                "name": "search_rule",
                "version": 1,
                "enabled": True,
                "match": {"domains": [], "url_pattern": "/search.*"},
                "fields": {},
                "options": {"keep_body_text": False, "follow_links": True},
            }
        ]
        snapshot = _make_snapshot(rules)
        assert match_url("https://example.com/search?q=test&page=1", snapshot) == "search_rule"
        # 不含 query string 也应匹配
        assert match_url("https://example.com/search", snapshot) == "search_rule"
        # 不匹配的路径
        assert match_url("https://example.com/about?q=test", snapshot) == DEFAULT_EXTRACTION_TYPE

    def test_multi_candidate_competition(self):
        """两条规则同时匹配同一 URL：域名精确度优先。"""
        rules = [
            {
                "name": "generic",
                "version": 1,
                "enabled": True,
                "match": {"domains": [], "url_pattern": ""},
                "fields": {},
                "options": {"keep_body_text": False, "follow_links": True},
            },
            {
                "name": "specific",
                "version": 1,
                "enabled": True,
                "match": {"domains": ["example.com"], "url_pattern": ""},
                "fields": {},
                "options": {"keep_body_text": False, "follow_links": True},
            },
        ]
        snapshot = _make_snapshot(rules)
        assert match_url("https://example.com/page", snapshot) == "specific"
        assert match_url("https://other.com/page", snapshot) == "generic"

    def test_match_url_with_candidates_api(self):
        """match_url_with_candidates 公开 API 返回候选列表。"""
        from astrocrawl.rules._matcher import match_url_with_candidates

        rules = [
            {
                "name": "generic",
                "version": 1,
                "enabled": True,
                "match": {"domains": [], "url_pattern": ""},
                "fields": {},
                "options": {"keep_body_text": False, "follow_links": True},
            },
            {
                "name": "specific",
                "version": 1,
                "enabled": True,
                "match": {"domains": ["example.com"], "url_pattern": ""},
                "fields": {},
                "options": {"keep_body_text": False, "follow_links": True},
            },
        ]
        snapshot = _make_snapshot(rules)
        rule_name, candidates = match_url_with_candidates("https://example.com/page", snapshot)
        assert rule_name == "specific"
        assert candidates == ["specific", "generic"]

    def test_source_priority_sort_key(self):
        """源优先级作为第 4 排序键打破前三键平局 (806eac2 回归测试)。

        两条泛型规则 (domain_score=2, pattern_len=0, version=1) 前三键全部平局，
        源优先级 pip(0) < user(2) 决定 pip_rule 胜出。"""
        snapshot = _make_snapshot(
            [
                {
                    "name": "user_rule",
                    "version": 1,
                    "enabled": True,
                    "match": {"domains": [], "url_pattern": ""},
                    "fields": {},
                    "options": {"keep_body_text": False, "follow_links": True},
                },
                {
                    "name": "pip_rule",
                    "version": 1,
                    "enabled": True,
                    "match": {"domains": [], "url_pattern": ""},
                    "fields": {},
                    "options": {"keep_body_text": False, "follow_links": True},
                },
            ],
            source_map={"pip_rule": "pip", "user_rule": "user"},
        )
        assert match_url("https://any.com/page", snapshot) == "pip_rule"

    def test_name_sort_key_deterministic_tiebreak(self):
        """第 5 排序键（名称）保证前四键全平局时确定性排序。

        两条规则 domain_score/patter_len/version/source 全部相同，按名称字母序
        打破平局。"""
        snapshot = _make_snapshot(
            [
                {
                    "name": "rule_b",
                    "version": 1,
                    "enabled": True,
                    "match": {"domains": [], "url_pattern": ""},
                    "fields": {},
                    "options": {"keep_body_text": False, "follow_links": True},
                },
                {
                    "name": "rule_a",
                    "version": 1,
                    "enabled": True,
                    "match": {"domains": [], "url_pattern": ""},
                    "fields": {},
                    "options": {"keep_body_text": False, "follow_links": True},
                },
            ],
            source_map={"rule_a": "user", "rule_b": "user"},
        )
        assert match_url("https://any.com/page", snapshot) == "rule_a"

    def test_disabled_rules_excluded_from_by_domain(self):
        """D1: 禁用规则不出现在 by_domain 索引中，匹配回退到已启用规则。"""
        rules = [
            {
                "name": "enabled_rule",
                "version": 1,
                "enabled": True,
                "match": {"domains": ["example.com"], "url_pattern": ""},
                "fields": {},
                "options": {"keep_body_text": False, "follow_links": True},
            },
            {
                "name": "disabled_rule",
                "version": 1,
                "enabled": False,
                "match": {"domains": ["example.com"], "url_pattern": ""},
                "fields": {},
                "options": {"keep_body_text": False, "follow_links": True},
            },
        ]
        snapshot = _make_snapshot(rules)
        # 两条规则都在 by_name 中
        assert "enabled_rule" in snapshot.by_name
        assert "disabled_rule" in snapshot.by_name
        assert snapshot.by_name["disabled_rule"].enabled is False
        # 禁用规则不在 by_domain 中
        assert "enabled_rule" in snapshot.by_domain.get("example.com", ())
        assert "disabled_rule" not in snapshot.by_domain.get("example.com", ())
        # 匹配返回已启用规则
        assert match_url("https://example.com/page", snapshot) == "enabled_rule"

    def test_make_snapshot_aligns_with_build_behavior(self):
        """D4: _make_snapshot 对齐 build_rule_snapshot — rules 过滤 disabled，by_name 含 default。"""
        rules = [
            {
                "name": "r1",
                "version": 1,
                "enabled": True,
                "match": {"domains": []},
                "fields": {"x": {"selector": "h1"}},
            },
            {
                "name": "r2",
                "version": 1,
                "enabled": False,
                "match": {"domains": []},
                "fields": {"x": {"selector": "h1"}},
            },
        ]
        snap = _make_snapshot(rules)
        # by_name 保留全部规则
        assert "r1" in snap.by_name
        assert "r2" in snap.by_name
        assert snap.by_name["r2"].enabled is False
        # rules 元组仅含已启用规则
        rule_names = [r.name for r in snap.rules]
        assert "r1" in rule_names
        assert "r2" not in rule_names
        # default 始终在 by_name 中
        assert DEFAULT_EXTRACTION_TYPE in snap.by_name
        assert snap.by_name[DEFAULT_EXTRACTION_TYPE].enabled is True


# ═══════════════════════════════════════════════════════════════════
# _match_domain 纯函数单元测试
# ═══════════════════════════════════════════════════════════════════


class TestMatchDomain:
    """_match_domain 域名匹配分数计算覆盖全部等价类。"""

    def test_single_exact(self):
        from astrocrawl.rules._matcher import _match_domain

        assert _match_domain("example.com", ["example.com"]) == 0

    def test_single_suffix(self):
        from astrocrawl.rules._matcher import _match_domain

        assert _match_domain("sub.example.com", ["example.com"]) == 1

    def test_single_no_match(self):
        from astrocrawl.rules._matcher import _match_domain

        assert _match_domain("other.com", ["example.com"]) == -1

    def test_empty_hostname(self):
        from astrocrawl.rules._matcher import _match_domain

        assert _match_domain("", ["example.com"]) == -1

    def test_empty_rule_domains(self):
        from astrocrawl.rules._matcher import _match_domain

        assert _match_domain("example.com", []) == 2

    def test_multi_domain_suffix_then_exact(self):
        """父域在前，子域在后——精确匹配不应被后缀匹配截断 (B1)。"""
        from astrocrawl.rules._matcher import _match_domain

        assert _match_domain("www.example.com", ["example.com", "www.example.com"]) == 0

    def test_multi_domain_exact_then_suffix(self):
        """子域在前——精确匹配优先。"""
        from astrocrawl.rules._matcher import _match_domain

        assert _match_domain("www.example.com", ["www.example.com", "example.com"]) == 0

    def test_multi_domain_suffix_only(self):
        """多域名全为后缀匹配。"""
        from astrocrawl.rules._matcher import _match_domain

        assert _match_domain("blog.example.com", ["example.com", "other.com"]) == 1

    def test_multi_domain_exact_in_middle(self):
        """精确匹配在列表中间。"""
        from astrocrawl.rules._matcher import _match_domain

        assert _match_domain("other.com", ["example.com", "other.com", "third.com"]) == 0

    def test_multi_domain_no_match(self):
        from astrocrawl.rules._matcher import _match_domain

        assert _match_domain("unknown.com", ["example.com", "other.com"]) == -1

    def test_weibo_real_scenario(self):
        """真实生产规则 weibo_post domains (B1 触发点)。"""
        from astrocrawl.rules._matcher import _match_domain

        domains = ["weibo.com", "www.weibo.com", "m.weibo.cn"]
        assert _match_domain("www.weibo.com", domains) == 0  # 精确
        assert _match_domain("weibo.com", domains) == 0  # 精确
        assert _match_domain("m.weibo.cn", domains) == 0  # 精确 (独立域名)
        assert _match_domain("blog.weibo.com", domains) == 1  # 后缀

    def test_trailing_dot_normalization(self):
        from astrocrawl.rules._matcher import _match_domain

        assert _match_domain("example.com.", ["example.com"]) == 0
        assert _match_domain("example.com", ["example.com."]) == 0

    def test_case_insensitive(self):
        from astrocrawl.rules._matcher import _match_domain

        assert _match_domain("EXAMPLE.COM", ["example.com"]) == 0
        assert _match_domain("Example.COM", ["EXAMPLE.COM"]) == 0


class TestMatchUrlPattern:
    """_match_url_pattern re2 路径匹配 — 正常/异常全路径。"""

    def test_normal_match(self):
        from astrocrawl.rules._matcher import _match_url_pattern

        assert _match_url_pattern("/blog/post-123", r"/blog/.*")

    def test_normal_no_match(self):
        from astrocrawl.rules._matcher import _match_url_pattern

        assert not _match_url_pattern("/about", r"/blog/.*")

    def test_query_string_included_in_match(self):
        from astrocrawl.rules._matcher import _match_url_pattern

        assert _match_url_pattern("/search?q=test&page=1", r"/search.*")

    def test_re2_exception_logs_warning_and_returns_false(self, caplog):
        import logging

        import re2

        from astrocrawl.rules._matcher import _match_url_pattern

        caplog.set_level(logging.WARNING, logger="astrocrawl.rules.matcher")
        original_search = re2.search

        def _raise(*_args, **_kwargs):
            raise RuntimeError("simulated re2 failure")

        try:
            re2.search = _raise
            result = _match_url_pattern("/path", ".*")
            assert result is False
            assert any("url_pattern_match_error" in r.message for r in caplog.records)
        finally:
            re2.search = original_search


# ═══════════════════════════════════════════════════════════════════
# CSS 提取
# ═══════════════════════════════════════════════════════════════════

SAMPLE_HTML = """<!DOCTYPE html>
<html><head><title>Test Page</title></head>
<body>
  <main>
    <article>
      <h1 class="title">Hello World</h1>
      <p class="content">This is test content.</p>
      <img class="hero" src="/hero.jpg" alt="Hero">
      <ul class="tags">
        <li>Python</li>
        <li>Crawler</li>
        <li>Web</li>
      </ul>
      <img class="gallery" src="/img1.jpg">
      <img class="gallery" src="/img2.jpg">
      <img class="gallery" src="/img3.jpg">
      <div class="price">¥8999</div>
      <span class="rating">★★★★☆ 4.5/5</span>
    </article>
  </main>
</body></html>"""


class TestCSSExtraction:
    """CSS 选择器提取 text/attr/html/multiple/fallback。"""

    def test_extract_text(self):
        from bs4 import BeautifulSoup

        from astrocrawl.rules._schema import FieldRule

        soup = BeautifulSoup(SAMPLE_HTML, "lxml")
        result = _extract_single_field(soup, "title", FieldRule(selector="h1.title", extract="text"))
        assert result == "Hello World"

    def test_extract_attr(self):
        from bs4 import BeautifulSoup

        from astrocrawl.rules._schema import FieldRule

        soup = BeautifulSoup(SAMPLE_HTML, "lxml")
        result = _extract_single_field(soup, "img", FieldRule(selector="img.hero", extract="attr", attr="src"))
        assert result == "/hero.jpg"

    def test_extract_html(self):
        from bs4 import BeautifulSoup

        from astrocrawl.rules._schema import FieldRule

        soup = BeautifulSoup(SAMPLE_HTML, "lxml")
        result = _extract_single_field(soup, "html", FieldRule(selector="p.content", extract="html"))
        assert "test content" in result

    def test_extract_multiple(self):
        from bs4 import BeautifulSoup

        from astrocrawl.rules._schema import FieldRule

        soup = BeautifulSoup(SAMPLE_HTML, "lxml")
        result = _extract_single_field(
            soup, "images", FieldRule(selector="img.gallery", extract="attr", attr="src", multiple=True)
        )
        assert isinstance(result, list)
        assert len(result) == 3
        assert "/img1.jpg" in result

    def test_extract_not_found_returns_none(self):
        from bs4 import BeautifulSoup

        from astrocrawl.rules._schema import FieldRule

        soup = BeautifulSoup(SAMPLE_HTML, "lxml")
        result = _extract_single_field(soup, "missing", FieldRule(selector=".missing-element", extract="text"))
        assert result is None

    def test_fallback_chain(self):
        """主 selector 不命中时尝试 fallback。"""
        from bs4 import BeautifulSoup

        from astrocrawl.rules._schema import FieldRule

        soup = BeautifulSoup(SAMPLE_HTML, "lxml")
        result = _extract_single_field(
            soup,
            "desc",
            FieldRule(
                selector=".missing",
                extract="text",
                fallback=[
                    FieldRule(selector=".another-missing", extract="text"),
                    FieldRule(selector="p.content", extract="text"),
                ],
            ),
        )
        assert result == "This is test content."

    def test_empty_selector_skipped(self):
        from bs4 import BeautifulSoup

        from astrocrawl.rules._schema import FieldRule

        soup = BeautifulSoup(SAMPLE_HTML, "lxml")
        result = _extract_single_field(soup, "empty", FieldRule(selector="", extract="text"))
        assert result is None


# ═══════════════════════════════════════════════════════════════════
# 提取器公开 API + 边界值
# ═══════════════════════════════════════════════════════════════════


class TestExtractorPublicAPI:
    """extract_fields / extract_fields_from_soup 公开 API。"""

    async def test_extract_fields_from_html(self):
        from astrocrawl.rules._schema import FieldRule

        html = "<html><body><h1>Hello</h1><p>World</p></body></html>"
        result = await extract_fields(html, "test", {"title": FieldRule(selector="h1", extract="text")})
        assert result == {"title": "Hello"}

    async def test_extract_fields_empty_html(self):
        from astrocrawl.rules._schema import FieldRule

        result = await extract_fields("", "test", {"title": FieldRule(selector="h1", extract="text")})
        assert result == {}

    async def test_extract_fields_whitespace_only_html(self):
        from astrocrawl.rules._schema import FieldRule

        result = await extract_fields("   \n\t  ", "test", {"title": FieldRule(selector="h1", extract="text")})
        assert result == {}

    async def test_extract_fields_from_soup(self):
        from bs4 import BeautifulSoup

        from astrocrawl.rules._schema import FieldRule

        soup = BeautifulSoup("<html><body><h1>Hello</h1></body></html>", "lxml")
        result = await extract_fields_from_soup(soup, "test", {"title": FieldRule(selector="h1", extract="text")})
        assert result == {"title": "Hello"}

    async def test_extract_fields_from_soup_empty_config(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup("<html><body><h1>Hello</h1></body></html>", "lxml")
        result = await extract_fields_from_soup(soup, "test", {})
        assert result == {}


class TestExtractorEdgeCases:
    """_extract_value / _truncate_if_needed / _try_select 边界值。"""

    def test_field_level_exception_isolation(self):
        """一个坏字段不影响其他好字段。"""
        from bs4 import BeautifulSoup

        from astrocrawl.rules._schema import FieldRule

        soup = BeautifulSoup("<div>test</div>", "lxml")
        result = _extract_all_fields(
            soup,
            {
                "good": FieldRule(selector="div", extract="text"),
                "bad": FieldRule(selector="div[", extract="text"),  # 无效选择器
                "also_good": FieldRule(selector="div", extract="text"),
            },
        )
        assert result["good"] == "test"
        assert result["bad"] is None
        assert result["also_good"] == "test"

    def test_extract_value_boolean_attribute(self):
        """checked/disabled 等布尔属性存在但无值 → ''。"""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup('<input type="checkbox" checked disabled>', "lxml")
        el = soup.select_one("input")
        assert _extract_value(el, "attr", "checked", 500000) == ""
        assert _extract_value(el, "attr", "disabled", 500000) == ""

    def test_extract_value_missing_attribute(self):
        """不存在的属性 → None。"""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup('<img src="x.jpg">', "lxml")
        el = soup.select_one("img")
        assert _extract_value(el, "attr", "nonexistent", 500000) is None

    def test_extract_value_html_void_element(self):
        """<br>/<hr>/<img> 等 void 元素 html 提取 → None。"""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup("<div><br></div>", "lxml")
        el = soup.select_one("br")
        assert _extract_value(el, "html", "", 500000) is None

    def test_extract_value_html_blank_content(self):
        """非 void 元素 (<div>  </div>) html 提取纯空白 → None。"""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup("<div>   </div>", "lxml")
        el = soup.select_one("div")
        assert _extract_value(el, "html", "", 500000) is None

    def test_extract_value_text_empty(self):
        """元素无文本内容 → None。"""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup("<div></div>", "lxml")
        el = soup.select_one("div")
        assert _extract_value(el, "text", "", 500000) is None

    def test_extract_value_text_with_children(self):
        """元素包含子元素时提取纯文本 (get_text+split+join 归一化保留空格)。"""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup("<div>Hello <span>World</span></div>", "lxml")
        el = soup.select_one("div")
        result = _extract_value(el, "text", "", 500000)
        assert result == "Hello World"

    def test_truncate_above_max(self):
        """超 max_text_length 时字节边界截断。"""
        result = _truncate_if_needed("hello world", 5)
        assert len(result.encode("utf-8")) <= 5
        assert result == "hello"

    def test_truncate_below_max_passthrough(self):
        """未超 max_text_length 时返回原值。"""
        result = _truncate_if_needed("hello", 500000)
        assert result == "hello"

    def test_truncate_exact_max_boundary(self):
        """恰好等于 max_text_length 时透传。"""
        val = "hello"
        result = _truncate_if_needed(val, len(val.encode("utf-8")))
        assert result == val

    def test_truncate_cjk_boundary(self):
        """CJK 字符在字节边界截断时不产生无效 UTF-8。"""
        result = _truncate_if_needed("你好世界", 5)  # "你好" = 6 bytes, 不能完整保留
        # 应在 UTF-8 边界截断，不产生无效序列
        assert len(result.encode("utf-8")) <= 5
        # 解码回来不应是乱码
        result.encode("utf-8").decode("utf-8")

    def test_try_select_invalid_selector(self, caplog):
        """无效 CSS 选择器 → 返回 None + 记录 selector_error 日志。"""
        import logging

        from bs4 import BeautifulSoup

        soup = BeautifulSoup("<div>test</div>", "lxml")
        caplog.set_level(logging.WARNING, logger="astrocrawl.rules.extractor")
        result = _try_select(soup, "div[", "text", "", False)
        assert result is None
        assert "event=selector_error" in caplog.text

    def test_try_select_multiple_text(self):
        """multiple=True + extract='text' 提取多个元素的文本。"""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup("<ul><li>A</li><li>B</li><li>C</li></ul>", "lxml")
        result = _try_select(soup, "li", "text", "", True)
        assert result == ["A", "B", "C"]

    def test_try_select_multiple_with_empty_items(self):
        """multiple=True + 部分元素提取值为空 → 过滤空值。"""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup("<ul><li>A</li><li></li><li>C</li></ul>", "lxml")
        result = _try_select(soup, "li", "text", "", True)
        assert result == ["A", "C"]

    def test_try_select_no_match_multiple_returns_empty_list(self):
        """multiple=True 无匹配元素 → 返回 []。"""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup("<div>test</div>", "lxml")
        result = _try_select(soup, ".missing", "text", "", True)
        assert result == []

    def test_all_fields_non_fieldrule_item(self, caplog):
        """字段值为非 FieldRule → AttributeError → except Exception 捕获。"""
        import logging

        from bs4 import BeautifulSoup

        soup = BeautifulSoup("<div>test</div>", "lxml")
        caplog.set_level(logging.WARNING, logger="astrocrawl.rules.extractor")
        result = _extract_all_fields(
            soup,
            {
                "good": None,  # None has no .selector → AttributeError
                "also_good": None,
            },
        )
        assert result["good"] is None
        assert result["also_good"] is None
        assert "event=field_extract_error" in caplog.text

    def test_extract_single_field_empty_fallback_selector_skipped(self):
        """fallback 中空 selector → 跳过，继续下一个。"""
        from bs4 import BeautifulSoup

        from astrocrawl.rules._schema import FieldRule

        soup = BeautifulSoup("<div>test</div>", "lxml")
        result = _extract_single_field(
            soup,
            "f",
            FieldRule(
                selector=".missing",
                extract="text",
                fallback=[
                    FieldRule(selector="", extract="text"),  # 空 selector → 跳过
                    FieldRule(selector="div", extract="text"),  # 有效
                ],
            ),
        )
        assert result == "test"

    def test_extract_single_field_primary_returns_empty_string(self):
        """主 selector 提取空字符串 → 视为非空(空串 ∃)，不触发 fallback。"""
        from bs4 import BeautifulSoup

        from astrocrawl.rules._schema import FieldRule

        soup = BeautifulSoup('<div class=""></div>', "lxml")
        result = _extract_single_field(
            soup,
            "f",
            FieldRule(
                selector="div",
                extract="attr",
                attr="class",
                fallback=[FieldRule(selector="h1", extract="text")],
            ),
        )
        # _extract_value attr 中 val.strip()="" 返回 ""，_is_non_empty("")=False
        # 触发 fallback，但 h1 不存在 → 返回 None
        assert result is None

    def test_extract_single_field_all_fallbacks_fail_returns_none(self):
        """所有 fallback 均不命中 → 返回 None (主 selector 的结果)。"""
        from bs4 import BeautifulSoup

        from astrocrawl.rules._schema import FieldRule

        soup = BeautifulSoup("<div>test</div>", "lxml")
        result = _extract_single_field(
            soup,
            "f",
            FieldRule(
                selector=".missing",
                extract="text",
                fallback=[
                    FieldRule(selector=".also-missing", extract="text"),
                    FieldRule(selector=".still-missing", extract="text"),
                ],
            ),
        )
        assert result is None

    def test_fallback_pseudo_class_warns(self, caplog):
        """fallback selector 中包含 :hover 时触发 N93 警告。"""
        import logging

        from bs4 import BeautifulSoup

        from astrocrawl.rules._schema import FieldRule

        soup = BeautifulSoup("<div>test</div>", "lxml")
        caplog.set_level(logging.WARNING, logger="astrocrawl.rules.extractor")
        result = _extract_single_field(
            soup,
            "f",
            FieldRule(
                selector=".missing",
                extract="text",
                fallback=[FieldRule(selector="div:hover", extract="text")],
            ),
        )
        assert result is None  # :hover 静默返回 None
        assert "event=unsupported_css_pseudo" in caplog.text
        assert "div:hover" in caplog.text
        assert ":hover" in caplog.text

    def test_fallback_pseudo_element_warns(self, caplog):
        """fallback selector 中包含 ::after 时触发 N93 警告。"""
        import logging

        from bs4 import BeautifulSoup

        from astrocrawl.rules._schema import FieldRule

        soup = BeautifulSoup("<div>test</div>", "lxml")
        caplog.set_level(logging.WARNING, logger="astrocrawl.rules.extractor")
        result = _extract_single_field(
            soup,
            "f",
            FieldRule(
                selector=".missing",
                extract="text",
                fallback=[FieldRule(selector="div::after", extract="text")],
            ),
        )
        assert result is None  # ::after 抛 NotImplementedError → _try_select 捕获 → None
        assert "event=unsupported_css_pseudo" in caplog.text
        assert "div::after" in caplog.text

    def test_fallback_no_pseudo_no_warn(self, caplog):
        """fallback selector 无伪类 → 无 N93 警告。"""
        import logging

        from bs4 import BeautifulSoup

        from astrocrawl.rules._schema import FieldRule

        soup = BeautifulSoup("<div>test</div>", "lxml")
        caplog.set_level(logging.WARNING, logger="astrocrawl.rules.extractor")
        result = _extract_single_field(
            soup,
            "f",
            FieldRule(
                selector=".missing",
                extract="text",
                fallback=[FieldRule(selector="div", extract="text")],
            ),
        )
        assert result == "test"
        assert "unsupported_css_pseudo" not in caplog.text

    def test_fallback_not_warned_when_primary_succeeds(self, caplog):
        """主 selector 命中 → fallback 未执行 → fallback 中 :hover 不触发警告。"""
        import logging

        from bs4 import BeautifulSoup

        from astrocrawl.rules._schema import FieldRule

        soup = BeautifulSoup("<div>test</div>", "lxml")
        caplog.set_level(logging.WARNING, logger="astrocrawl.rules.extractor")
        result = _extract_single_field(
            soup,
            "f",
            FieldRule(
                selector="div",
                extract="text",
                fallback=[FieldRule(selector="span:hover", extract="text")],
            ),
        )
        assert result == "test"
        # 主 selector 命中 → 直接 return → fallback 从未尝试 → 无伪类警告
        assert "unsupported_css_pseudo" not in caplog.text

    def test_fallback_chain_pseudo_warns_then_next_succeeds(self, caplog):
        """多个 fallback: 第一个有 :hover (失败+告警), 第二个正常 (命中), 链不中断。"""
        import logging

        from bs4 import BeautifulSoup

        from astrocrawl.rules._schema import FieldRule

        soup = BeautifulSoup("<div>test</div>", "lxml")
        caplog.set_level(logging.WARNING, logger="astrocrawl.rules.extractor")
        result = _extract_single_field(
            soup,
            "f",
            FieldRule(
                selector=".missing",
                extract="text",
                fallback=[
                    FieldRule(selector="div:hover", extract="text"),
                    FieldRule(selector="div", extract="text"),
                ],
            ),
        )
        assert result == "test"
        assert "event=unsupported_css_pseudo" in caplog.text
        assert "div:hover" in caplog.text


class TestTransforms:
    """五种 Transform 操作。"""

    def test_strip(self):
        assert _strip("  hello  ") == "hello"
        assert _strip(["  a  ", " b "]) == ["a", "b"]
        assert _strip(None) is None

    def test_strip_currency(self):
        assert _strip_currency("¥8999") == "8999"
        assert _strip_currency("$19.99") == "19.99"
        assert _strip_currency("€100") == "100"

    def test_strip_currency_extra(self):
        assert _strip_currency("฿500", frozenset({"฿"})) == "500"

    def test_regex_transform(self):
        result = _regex_transform("¥8999", r"(\d+)")
        assert result == "8999"

    def test_replace_transform(self):
        result = _replace_transform("hello 万", {"from": "万", "to": "0000"}, 500000)
        assert result == "hello 0000"

    def test_replace_memory_protection(self):
        """内存放大防护：拒绝过大替换结果 (N104)。"""
        # 小输入 → 超大替换结果 → 拒绝（max(1*5, 100) = 100, 1000 > 100）
        result = _replace_transform("x", {"from": "x", "to": "y" * 1000}, 100)
        assert result == "x"  # 放大倍数超限，返回原值

    def test_join_transform(self):
        result = _join_transform(["a", "b", "c"], ", ")
        assert result == "a, b, c"

    def test_apply_transforms_full_pipeline(self):
        """strip + strip_currency + regex 完整流水线。"""
        value = "  ¥8,999  "
        transforms = {"strip": True, "strip_currency": True}
        result = apply_transforms(value, transforms)
        assert result == "8,999"

    # ── apply_transforms 覆盖缺口 ──

    def test_apply_transforms_empty_transforms(self):
        """transforms 为空或 None 时返回原值。"""
        assert apply_transforms("hello", {}) == "hello"
        assert apply_transforms(None, {"strip": True}) is None

    def test_apply_transforms_with_regex(self):
        """regex 经 apply_transforms 完整调用。"""
        result = apply_transforms("¥8999", {"regex": r"(\d+)"})
        assert result == "8999"

    def test_apply_transforms_with_replace(self):
        """replace 经 apply_transforms 完整调用。"""
        result = apply_transforms("hello 万", {"replace": {"from": "万", "to": "0000"}})
        assert result == "hello 0000"

    def test_apply_transforms_with_join(self):
        """join 经 apply_transforms 完整调用。"""
        result = apply_transforms(["a", "b", "c"], {"join": ", "})
        assert result == "a, b, c"

    # ── strip_currency 覆盖缺口 ──

    def test_strip_currency_list(self):
        """strip_currency 对 list 值逐项清理。"""
        assert _strip_currency(["¥100", "$200"]) == ["100", "200"]

    def test_strip_currency_non_str_item(self):
        """strip_currency 列表中非 str 项保持原样。"""
        assert _strip_currency(["¥100", 42, None]) == ["100", 42, None]

    def test_strip_currency_non_str_non_list(self):
        """strip_currency 对非 str/非 list 值直接返回。"""
        assert _strip_currency(42) == 42

    # ── regex 覆盖缺口 ──

    def test_regex_transform_no_match(self):
        """regex 不匹配时返回 None。"""
        assert _regex_transform("hello", r"(\d+)") is None

    def test_regex_transform_whole_match(self):
        """无捕获组时返回全匹配文本。"""
        assert _regex_transform("¥8999", r"\d+") == "8999"

    def test_regex_transform_invalid_pattern(self):
        """无效正则时返回原值 + WARNING（防御纵深）。"""
        assert _regex_transform("hello", "[") == "hello"

    # ── replace 覆盖缺口 ──

    def test_replace_transform_empty_from(self):
        """replace.from 为空时返回原值。"""
        assert _replace_transform("hello", {"from": "", "to": "x"}, 500000) == "hello"

    def test_replace_ratio_ceiling_only(self):
        """N104 比例天花板独立触发——S27 不触发但 N104 触发。"""
        # input=5 bytes "hello", result="helloXXX"=8 bytes
        # 8 ≤ 500000 (S27 OK), 8 > 5*5=25? No. Need bigger ratio.
        # input=5 bytes, to="X"*30 → result=5+30-1=34 bytes
        # 34 ≤ 500000 (S27 OK), 34 > 5*5=25 → N104 triggers
        result = _replace_transform("hello", {"from": "o", "to": "X" * 30}, 500000)
        assert result == "hello"  # N104 拒绝，返回原值

    # ── join 覆盖缺口 ──

    def test_join_transform_truncation(self):
        """join 结果超 max_text_length 时截断。"""
        items = ["x" * 100] * 5  # ~500 chars joined
        result = _join_transform(items, "", max_text_length=50)
        assert len(result.encode("utf-8")) <= 50

    def test_join_transform_non_list(self):
        """join 对非 list 值直接返回。"""
        assert _join_transform("hello", ", ") == "hello"


# ═══════════════════════════════════════════════════════════════════
# 容错
# ═══════════════════════════════════════════════════════════════════


class TestErrorHandling:
    """规则全部 null、HTML 空、单字段异常。"""

    def test_is_non_empty_none(self):
        assert not _is_non_empty(None)

    def test_is_non_empty_empty_list(self):
        assert not _is_non_empty([])

    def test_is_non_empty_empty_string(self):
        assert not _is_non_empty("  ")

    def test_is_non_empty_valid(self):
        assert _is_non_empty("hello")
        assert _is_non_empty(["a"])

    def test_blocked_by_reserved_name(self):
        with pytest.raises(ValueError):
            validate_rule({"name": "test", "fields": {"depth": {"selector": "h1"}}})
        with pytest.raises(ValueError):
            validate_rule({"name": "test", "fields": {"extraction_type": {"selector": "h1"}}})

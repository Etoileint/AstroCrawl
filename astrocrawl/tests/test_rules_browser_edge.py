"""补充测试: rules 子模块 + browser 辅助函数 — 边界用例和未覆盖函数。

- _schema.py: sanitize_display_text
- _loader.py: _check_json_depth, _deduplicate_rules
- _transform.py: _replace_transform (amplification guard), _join_transform (truncation)
- _extractor.py: _warn_unsupported_pseudo
- browser_pool.py: _backoff_with_jitter, _safe_unroute
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock

from astrocrawl.browser.browser_pool import _backoff_with_jitter, _safe_unroute
from astrocrawl.rules._extractor import _warn_unsupported_pseudo
from astrocrawl.rules._loader import _check_json_depth, _deduplicate_rules
from astrocrawl.rules._schema import RuleSchema, sanitize_display_text
from astrocrawl.rules._transform import _join_transform, _replace_transform

# ═══════════════════════════════════════════════════════════════════════
# sanitize_display_text (C5: Unicode 控制字符清洗)
# ═══════════════════════════════════════════════════════════════════════


class TestSanitizeDisplayText:
    def test_normal_text_passthrough(self):
        assert sanitize_display_text("Hello World") == "Hello World"

    def test_empty_string_returns_empty(self):
        assert sanitize_display_text("") == ""

    def test_bidi_control_removed(self):
        # U+202A LEFT-TO-RIGHT EMBEDDING
        assert "‪" not in sanitize_display_text("a‪b")

    def test_bidi_isolate_removed(self):
        # U+2066 LEFT-TO-RIGHT ISOLATE
        assert "⁦" not in sanitize_display_text("a⁦b")

    def test_null_byte_removed(self):
        assert "\x00" not in sanitize_display_text("a\x00b")

    def test_delete_char_removed(self):
        assert "\x7f" not in sanitize_display_text("a\x7fb")

    def test_interlinear_removed(self):
        # U+FFF9 INTERLINEAR ANNOTATION ANCHOR
        assert "￹" not in sanitize_display_text("a￹b")

    def test_tab_and_newline_preserved(self):
        # Tabs (\x09) and newlines (\x0A) are in C0 but excluded from _DANGEROUS_CODES
        result = sanitize_display_text("a\tb\nc")
        assert "\t" in result
        assert "\n" in result

    def test_mixed_dangerous_and_safe(self):
        result = sanitize_display_text("H\x00e\x7fl\x00lo")
        assert result == "Hello"


# ═══════════════════════════════════════════════════════════════════════
# _check_json_depth (S24)
# ═══════════════════════════════════════════════════════════════════════


class TestCheckJsonDepth:
    def test_flat_dict_within_limit(self):
        assert _check_json_depth({"a": 1, "b": 2}, max_depth=5)

    def test_deeply_nested_exceeds(self):
        obj = {"a": {"b": {"c": {"d": {"e": {"f": 1}}}}}}
        assert not _check_json_depth(obj, max_depth=4)

    def test_nested_list_exceeds(self):
        obj = [[[[[1]]]]]
        assert not _check_json_depth(obj, max_depth=3)

    def test_flat_list_within_limit(self):
        assert _check_json_depth([1, 2, 3], max_depth=5)

    def test_primitive_always_true(self):
        assert _check_json_depth("string", max_depth=0)
        assert _check_json_depth(42, max_depth=0)

    def test_dict_at_exact_limit(self):
        obj = {"a": {"b": {"c": 1}}}
        assert _check_json_depth(obj, max_depth=3)  # depth 0→1→2→3

    def test_empty_dict_and_list(self):
        assert _check_json_depth({}, max_depth=0)
        assert _check_json_depth([], max_depth=0)


# ═══════════════════════════════════════════════════════════════════════
# _deduplicate_rules (S35)
# ═══════════════════════════════════════════════════════════════════════


def _r(name: str, version: int = 1) -> RuleSchema:
    return RuleSchema(name=name, version=version)


class TestDeduplicateRules:
    def test_no_duplicates_passthrough(self):
        rules = [(_r("a"), Path("a.json"), "user"), (_r("b"), Path("b.json"), "remote")]
        result = _deduplicate_rules(rules)
        assert len(result) == 2

    def test_same_name_higher_priority_wins(self):
        # pip(0) > remote(1) > user(2), 数字越小优先级越高
        rules = [(_r("a", version=1), Path("a_remote.json"), "remote"), (_r("a", version=2), Path("a_pip.json"), "pip")]
        result = _deduplicate_rules(rules)
        # pip 优先级 > remote，pip 的 version=2 获胜
        assert result[0][0].version == 2

    def test_same_priority_higher_version_wins(self):
        rules = [(_r("a", version=1), Path("a_v1.json"), "user"), (_r("a", version=3), Path("a_v3.json"), "user")]
        result = _deduplicate_rules(rules)
        assert result[0][0].version == 3

    def test_same_priority_lower_version_loses(self):
        rules = [(_r("a", version=3), Path("a_v3.json"), "user"), (_r("a", version=1), Path("a_v1.json"), "user")]
        result = _deduplicate_rules(rules)
        assert result[0][0].version == 3

    def test_unknown_source_priority_defaults_99(self):
        rules = [(_r("a"), Path("a.json"), "unknown_source_xyz"), (_r("a"), Path("a_user.json"), "user")]
        result = _deduplicate_rules(rules)
        assert result[0][0].version == 1

    def test_mixed_names_kept(self):
        rules = [
            (_r("a"), Path("a.json"), "user"),
            (_r("b"), Path("b.json"), "user"),
            (_r("a"), Path("a2.json"), "user"),
        ]
        result = _deduplicate_rules(rules)
        assert len(result) == 2


# ═══════════════════════════════════════════════════════════════════════
# _replace_transform (S22+N104: 内存放大防护)
# ═══════════════════════════════════════════════════════════════════════


class TestReplaceTransform:
    def test_basic_replace(self):
        result = _replace_transform("hello world", {"from": "world", "to": "earth"}, max_text_length=100000)
        assert result == "hello earth"

    def test_no_from_key_returns_original(self):
        result = _replace_transform("hello", {"to": "x"}, max_text_length=100000)
        assert result == "hello"

    def test_oversize_guard_returns_original(self):
        # max_text_length=5, result is longer → reject
        result = _replace_transform("abc", {"from": "abc", "to": "123456789"}, max_text_length=5)
        assert result == "abc"

    def test_amplification_guard_returns_original(self):
        # TRANSFORM_MEMORY_MULTIPLIER is likely 10; 1 char → 100 chars should trigger
        long_str = "x" * 100
        result = _replace_transform("a", {"from": "a", "to": long_str}, max_text_length=100000)
        assert result == "a"


# ═══════════════════════════════════════════════════════════════════════
# _join_transform (M9)
# ═══════════════════════════════════════════════════════════════════════


class TestJoinTransform:
    def test_joins_list(self):
        result = _join_transform(["a", "b", "c"], ", ")
        assert result == "a, b, c"

    def test_non_list_returns_as_is(self):
        result = _join_transform("not-a-list", ", ")
        assert result == "not-a-list"

    def test_empty_list(self):
        assert _join_transform([], ", ") == ""

    def test_truncation_on_oversize(self):
        # Set max_text_length very low to trigger truncation
        result = _join_transform(["hello", "world"], " ", max_text_length=3)
        assert len(result.encode("utf-8")) <= 3


# ═══════════════════════════════════════════════════════════════════════
# _warn_unsupported_pseudo (N93)
# ═══════════════════════════════════════════════════════════════════════


class TestWarnUnsupportedPseudo:
    # === 伪类检测 — 应告警 (caplog) ===

    def test_warns_on_element_pseudo(self, caplog):
        """div:hover — 最常见的元素+伪类模式。"""
        caplog.set_level(logging.WARNING, logger="astrocrawl.rules.extractor")
        _warn_unsupported_pseudo("div:hover", "myfield")
        assert "unsupported_css_pseudo" in caplog.text
        assert ":hover" in caplog.text

    def test_warns_on_standalone_pseudo(self, caplog):
        """:hover — 独立伪类选择器。"""
        caplog.set_level(logging.WARNING, logger="astrocrawl.rules.extractor")
        _warn_unsupported_pseudo(":hover", "myfield")
        assert "unsupported_css_pseudo" in caplog.text

    def test_warns_on_focus_pseudo(self, caplog):
        """a:focus — :focus 伪类检测。"""
        caplog.set_level(logging.WARNING, logger="astrocrawl.rules.extractor")
        _warn_unsupported_pseudo("a:focus", "myfield")
        assert "unsupported_css_pseudo" in caplog.text
        assert ":focus" in caplog.text

    def test_warns_on_visited_pseudo(self, caplog):
        """a:visited — :visited 伪类检测。"""
        caplog.set_level(logging.WARNING, logger="astrocrawl.rules.extractor")
        _warn_unsupported_pseudo("a:visited", "myfield")
        assert "unsupported_css_pseudo" in caplog.text
        assert ":visited" in caplog.text

    def test_warns_on_active_pseudo(self, caplog):
        """button:active — :active 伪类检测。"""
        caplog.set_level(logging.WARNING, logger="astrocrawl.rules.extractor")
        _warn_unsupported_pseudo("button:active", "myfield")
        assert "unsupported_css_pseudo" in caplog.text
        assert ":active" in caplog.text

    def test_warns_on_descendant_pseudo(self, caplog):
        """div :hover — 后代选择器+伪类。"""
        caplog.set_level(logging.WARNING, logger="astrocrawl.rules.extractor")
        _warn_unsupported_pseudo("div :hover", "myfield")
        assert "unsupported_css_pseudo" in caplog.text

    def test_warns_on_child_combinator(self, caplog):
        """div > span:hover — CombinedSelector + 伪类。"""
        caplog.set_level(logging.WARNING, logger="astrocrawl.rules.extractor")
        _warn_unsupported_pseudo("div > span:hover", "myfield")
        assert "unsupported_css_pseudo" in caplog.text

    def test_warns_on_adjacent_sibling(self, caplog):
        """div + span:hover — 相邻兄弟选择器。"""
        caplog.set_level(logging.WARNING, logger="astrocrawl.rules.extractor")
        _warn_unsupported_pseudo("div + span:hover", "myfield")
        assert "unsupported_css_pseudo" in caplog.text

    def test_warns_on_nested_pseudo(self, caplog):
        """:not(:hover) — Negation 嵌套伪类。"""
        caplog.set_level(logging.WARNING, logger="astrocrawl.rules.extractor")
        _warn_unsupported_pseudo(":not(:hover)", "myfield")
        assert "unsupported_css_pseudo" in caplog.text

    def test_warns_on_selector_list(self, caplog):
        """div:hover, a:focus — 逗号分隔选择器列表。"""
        caplog.set_level(logging.WARNING, logger="astrocrawl.rules.extractor")
        _warn_unsupported_pseudo("div:hover, a:focus", "myfield")
        assert "unsupported_css_pseudo" in caplog.text

    def test_warns_on_multi_pseudo_class(self, caplog):
        """div:hover:focus — 多个伪类仅报告首个匹配。"""
        caplog.set_level(logging.WARNING, logger="astrocrawl.rules.extractor")
        _warn_unsupported_pseudo("div:hover:focus", "myfield")
        assert "unsupported_css_pseudo" in caplog.text

    def test_warns_on_pseudo_and_element(self, caplog):
        """div:hover::after — 伪类 + 伪元素组合。"""
        caplog.set_level(logging.WARNING, logger="astrocrawl.rules.extractor")
        _warn_unsupported_pseudo("div:hover::after", "myfield")
        assert "unsupported_css_pseudo" in caplog.text

    # === 伪元素检测 — 应告警 (caplog) ===

    def test_warns_on_pseudo_element_after(self, caplog):
        """div::after — ::after 伪元素。"""
        caplog.set_level(logging.WARNING, logger="astrocrawl.rules.extractor")
        _warn_unsupported_pseudo("div::after", "myfield")
        assert "unsupported_css_pseudo" in caplog.text
        assert "::after" in caplog.text

    def test_warns_on_pseudo_element_before(self, caplog):
        """div::before — ::before 伪元素。"""
        caplog.set_level(logging.WARNING, logger="astrocrawl.rules.extractor")
        _warn_unsupported_pseudo("div::before", "myfield")
        assert "unsupported_css_pseudo" in caplog.text

    def test_warns_on_pseudo_element_selection(self, caplog):
        """div::selection — ::selection 伪元素。"""
        caplog.set_level(logging.WARNING, logger="astrocrawl.rules.extractor")
        _warn_unsupported_pseudo("div::selection", "myfield")
        assert "unsupported_css_pseudo" in caplog.text

    # === 不应告警 ===

    def test_no_warn_on_class_name(self, caplog):
        """.btn-hover — class 选择器不含伪类。"""
        caplog.set_level(logging.WARNING, logger="astrocrawl.rules.extractor")
        _warn_unsupported_pseudo(".btn-hover", "myfield")
        assert "unsupported_css_pseudo" not in caplog.text

    def test_no_warn_on_attribute_value(self, caplog):
        """[data-x=":hover"] — 属性值内含 :hover 字符串，非伪类。"""
        caplog.set_level(logging.WARNING, logger="astrocrawl.rules.extractor")
        _warn_unsupported_pseudo('[data-x=":hover"]', "myfield")
        assert "unsupported_css_pseudo" not in caplog.text

    def test_no_warn_on_longer_pseudo_name(self, caplog):
        """:hovering — Pseudo ident='hovering' ≠ 'hover' 不匹配。"""
        caplog.set_level(logging.WARNING, logger="astrocrawl.rules.extractor")
        _warn_unsupported_pseudo(":hovering", "myfield")
        assert "unsupported_css_pseudo" not in caplog.text

    def test_no_warn_on_supported_pseudo(self, caplog):
        """:nth-child(2) — BS4 原生支持的伪类。"""
        caplog.set_level(logging.WARNING, logger="astrocrawl.rules.extractor")
        _warn_unsupported_pseudo(":nth-child(2)", "myfield")
        assert "unsupported_css_pseudo" not in caplog.text

    # === 边界 ===

    def test_no_warn_on_invalid_selector(self, caplog):
        """div[ — 无效 CSS 选择器，cssselect 解析异常→静默跳过。"""
        caplog.set_level(logging.WARNING, logger="astrocrawl.rules.extractor")
        _warn_unsupported_pseudo("div[", "myfield")
        assert "unsupported_css_pseudo" not in caplog.text


# ═══════════════════════════════════════════════════════════════════════
# _backoff_with_jitter
# ═══════════════════════════════════════════════════════════════════════


class TestBackoffWithJitter:
    def test_full_jitter_in_range(self):
        for _ in range(100):
            result = _backoff_with_jitter(10.0, strategy="full")
            assert 0.0 <= result <= 10.0

    def test_equal_jitter_in_range(self):
        for _ in range(100):
            result = _backoff_with_jitter(10.0, strategy="equal")
            assert 5.0 <= result <= 10.0

    def test_default_is_full(self):
        result = _backoff_with_jitter(10.0)
        assert 0.0 <= result <= 10.0

    def test_zero_backoff(self):
        assert _backoff_with_jitter(0.0) == 0.0


# ═══════════════════════════════════════════════════════════════════════
# _safe_unroute
# ═══════════════════════════════════════════════════════════════════════


class TestSafeUnroute:
    async def test_none_page_noop(self):
        await _safe_unroute(None, timeout=1.0)

    async def test_closed_page_noop(self):
        page = MagicMock()
        page.is_closed.return_value = True
        await _safe_unroute(page, timeout=1.0)

    async def test_unroute_called(self):
        page = MagicMock()
        page.is_closed.return_value = False
        page.unroute_all.return_value = None  # sync return

        await _safe_unroute(page, timeout=1.0)
        page.unroute_all.assert_called_once()

    async def test_unroute_exception_handled(self):
        page = MagicMock()
        page.is_closed.return_value = False

        async def _fail():
            raise Exception("unroute failed")

        page.unroute_all.return_value = _fail()

        await _safe_unroute(page, timeout=1.0)

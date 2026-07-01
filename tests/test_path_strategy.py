"""PathSwitch 路径路由策略测试 — 从 test_types.py 搬迁（ADR-0010 Phase 5）。

覆盖: for_mode() 工厂 / 构造参数校验 / 属性查询 / fallback_for_error / fallback_for_proxy_exhaustion。
"""

from __future__ import annotations

import pytest

from astrocrawl._path_strategy import _CONNECTIVITY_ERRORS, _NON_PROXY_ERRORS, PathSwitch
from astrocrawl._types import FetchErrorCategory

# ═══════════════════════════════════════════════════════════════════════
# PathSwitch — 构造 / 工厂 / 属性 / 回退决策
# ═══════════════════════════════════════════════════════════════════════


class TestPathSwitchForMode:
    def test_direct_only(self):
        ps = PathSwitch.for_mode("direct_only")
        assert ps.main == "direct"
        assert ps.fallback is None
        assert ps.trigger is None
        assert ps.scope == "url"
        assert ps.on_exhausted == "fail"
        assert not ps.has_fallback

    def test_proxy_only(self):
        ps = PathSwitch.for_mode("proxy_only")
        assert ps.main == "proxy"
        assert ps.fallback is None
        assert ps.on_exhausted == "pause"
        assert not ps.has_fallback

    def test_prefer_direct(self):
        ps = PathSwitch.for_mode("prefer_direct")
        assert ps.main == "direct"
        assert ps.fallback == "proxy"
        assert ps.trigger == "connectivity_error"
        assert ps.scope == "url"
        assert ps.has_fallback

    def test_prefer_proxy(self):
        ps = PathSwitch.for_mode("prefer_proxy")
        assert ps.main == "proxy"
        assert ps.fallback == "direct"
        assert ps.trigger == "all_proxies_dead"
        assert ps.scope == "url"
        assert ps.has_fallback

    def test_unknown_mode_raises(self):
        with pytest.raises(ValueError, match="Unknown proxy_mode"):
            PathSwitch.for_mode("invalid")

    def test_valid_modes_class_variable(self):
        assert PathSwitch._VALID_MODES == frozenset(
            {
                "direct_only",
                "proxy_only",
                "prefer_direct",
                "prefer_proxy",
            }
        )

    def test_mode_configs_consistency_with_valid_modes(self):
        """_VALID_MODES 从 _MODE_CONFIGS 推导，确保 SSOT 一致。"""
        assert PathSwitch._VALID_MODES == frozenset(PathSwitch._MODE_CONFIGS.keys())

    def test_all_valid_modes_accepted(self):
        for mode in PathSwitch._VALID_MODES:
            ps = PathSwitch.for_mode(mode)
            assert isinstance(ps, PathSwitch)


class TestPathSwitchInitValidation:
    def test_main_must_be_proxy_or_direct(self):
        with pytest.raises(ValueError, match="main must be"):
            PathSwitch(main="ssh")
        PathSwitch(main="proxy")
        PathSwitch(main="direct")

    def test_fallback_must_be_proxy_direct_or_none(self):
        with pytest.raises(ValueError, match="fallback must be"):
            PathSwitch(main="direct", fallback="ssh")
        PathSwitch(main="direct", fallback="proxy")
        PathSwitch(main="direct", fallback="direct")
        PathSwitch(main="direct", fallback=None)

    def test_trigger_must_be_valid(self):
        with pytest.raises(ValueError, match="invalid trigger"):
            PathSwitch(main="direct", trigger="timeout")
        PathSwitch(main="direct", trigger="all_proxies_dead")
        PathSwitch(main="direct", trigger="connectivity_error")
        PathSwitch(main="direct", trigger=None)

    def test_scope_must_be_slot_or_url(self):
        with pytest.raises(ValueError, match="scope must be"):
            PathSwitch(main="direct", scope="request")
        PathSwitch(main="direct", scope="slot")
        PathSwitch(main="direct", scope="url")

    def test_on_exhausted_must_be_pause_or_fail(self):
        with pytest.raises(ValueError, match="on_exhausted must be"):
            PathSwitch(main="direct", on_exhausted="retry")
        PathSwitch(main="direct", on_exhausted="pause")
        PathSwitch(main="direct", on_exhausted="fail")

    def test_default_scope_is_url(self):
        ps = PathSwitch(main="proxy")
        assert ps.scope == "url"

    def test_default_on_exhausted_is_fail(self):
        ps = PathSwitch(main="proxy")
        assert ps.on_exhausted == "fail"

    def test_valid_full_config_accepted(self):
        ps = PathSwitch(
            main="proxy",
            fallback="direct",
            trigger="all_proxies_dead",
            scope="slot",
            on_exhausted="pause",
        )
        assert ps.main == "proxy"
        assert ps.fallback == "direct"
        assert ps.scope == "slot"
        assert ps.on_exhausted == "pause"


class TestPathSwitchProperties:
    def test_main_is_proxy(self):
        assert PathSwitch.for_mode("proxy_only").main_is_proxy is True
        assert PathSwitch.for_mode("prefer_proxy").main_is_proxy is True
        assert PathSwitch.for_mode("direct_only").main_is_proxy is False
        assert PathSwitch.for_mode("prefer_direct").main_is_proxy is False

    def test_has_fallback(self):
        assert PathSwitch.for_mode("direct_only").has_fallback is False
        assert PathSwitch.for_mode("proxy_only").has_fallback is False
        assert PathSwitch.for_mode("prefer_direct").has_fallback is True
        assert PathSwitch.for_mode("prefer_proxy").has_fallback is True

    def test_fallback_is_proxy(self):
        assert PathSwitch.for_mode("prefer_direct").fallback_is_proxy is True
        assert PathSwitch.for_mode("direct_only").fallback_is_proxy is False
        assert PathSwitch.for_mode("prefer_proxy").fallback_is_proxy is False
        assert PathSwitch.for_mode("proxy_only").fallback_is_proxy is False

    def test_fallback_is_direct(self):
        assert PathSwitch.for_mode("prefer_proxy").fallback_is_direct is True
        assert PathSwitch.for_mode("direct_only").fallback_is_direct is False
        assert PathSwitch.for_mode("prefer_direct").fallback_is_direct is False
        assert PathSwitch.for_mode("proxy_only").fallback_is_direct is False


class TestPathSwitchFallbackForError:
    def test_no_fallback_returns_false(self):
        ps = PathSwitch.for_mode("direct_only")
        for cat in _CONNECTIVITY_ERRORS:
            assert ps.should_fallback_for_error(cat) is False

    def test_wrong_trigger_returns_false(self):
        ps = PathSwitch.for_mode("prefer_proxy")
        for cat in _CONNECTIVITY_ERRORS:
            assert ps.should_fallback_for_error(cat) is False

    def test_all_connectivity_errors_trigger(self):
        ps = PathSwitch.for_mode("prefer_direct")
        for cat in _CONNECTIVITY_ERRORS:
            assert ps.should_fallback_for_error(cat) is True, f"{cat} should trigger"

    def test_non_connectivity_errors_do_not_trigger(self):
        ps = PathSwitch.for_mode("prefer_direct")
        all_cats = set(FetchErrorCategory)
        non_conn = all_cats - _CONNECTIVITY_ERRORS
        for cat in non_conn:
            assert ps.should_fallback_for_error(cat) is False, f"{cat} should not trigger"


class TestPathSwitchFallbackForProxyExhaustion:
    def test_no_fallback_returns_false(self):
        ps = PathSwitch.for_mode("direct_only")
        assert ps.should_fallback_for_proxy_exhaustion() is False
        assert ps.should_fallback_for_proxy_exhaustion(FetchErrorCategory.PROXY) is False

    def test_wrong_trigger_returns_false(self):
        ps = PathSwitch.for_mode("prefer_direct")
        assert ps.should_fallback_for_proxy_exhaustion() is False

    def test_no_category_returns_true(self):
        ps = PathSwitch.for_mode("prefer_proxy")
        assert ps.should_fallback_for_proxy_exhaustion(category=None) is True

    def test_non_proxy_errors_blocked(self):
        ps = PathSwitch.for_mode("prefer_proxy")
        for cat in _NON_PROXY_ERRORS:
            assert ps.should_fallback_for_proxy_exhaustion(cat) is False, f"{cat} should not trigger fallback"

    def test_neutral_categories_pass_through(self):
        ps = PathSwitch.for_mode("prefer_proxy")
        assert ps.should_fallback_for_proxy_exhaustion(FetchErrorCategory.CONTEXT_FAILURE) is True
        assert ps.should_fallback_for_proxy_exhaustion(FetchErrorCategory.TARGET_CLOSED) is True
        assert ps.should_fallback_for_proxy_exhaustion(FetchErrorCategory.ABORTED) is True
        assert ps.should_fallback_for_proxy_exhaustion(FetchErrorCategory.HTTP_5XX) is True
        assert ps.should_fallback_for_proxy_exhaustion(FetchErrorCategory.PROXY) is True
        assert ps.should_fallback_for_proxy_exhaustion(FetchErrorCategory.PROXY_EXHAUSTED) is True

    def test_connectivity_errors_pass_through(self):
        """连通性错误（如 TIMEOUT）不是 _NON_PROXY_ERRORS，应触发回退。"""
        ps = PathSwitch.for_mode("prefer_proxy")
        for cat in _CONNECTIVITY_ERRORS:
            assert ps.should_fallback_for_proxy_exhaustion(cat) is True, f"{cat} should trigger"

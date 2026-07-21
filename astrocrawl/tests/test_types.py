"""_types.py 共享内核类型完整测试套件。

覆盖 9 个实体：FetchErrorCategory, _CHROMIUM_ERROR_TABLE, classify_fetch_error,
DropReason, EnqueueResult, RuleMatchCache, RuleSnapshot, AsyncCloseable,
DEFAULT_EXTRACTION_TYPE, DOWNLOAD_EXTRACTION_TYPE。
（PathSwitch / _CONNECTIVITY_ERRORS / _NON_PROXY_ERRORS 已搬迁至 test_path_strategy.py）

设计原则：
- 每个断言验证一个不变式或行为
- Chromium 错误码前缀匹配语义：精确码优先于前缀族，族前缀优先于通配
- TTL 测试使用 3× 安全裕度避免调度抖动
- 结构完整性测试覆盖所有枚举成员数/值唯一性/格式约定
"""

from __future__ import annotations

import dataclasses
import time
from enum import Enum
from pathlib import Path

import pytest

from astrocrawl._path_strategy import _CONNECTIVITY_ERRORS, _NON_PROXY_ERRORS
from astrocrawl._types import (
    _CHROMIUM_ERROR_TABLE,
    _FALLBACK_PATTERNS,
    DEFAULT_EXTRACTION_TYPE,
    DOWNLOAD_EXTRACTION_TYPE,
    DropReason,
    EnqueueResult,
    FetchErrorCategory,
    RuleMatchCache,
    RuleSnapshot,
    classify_fetch_error,
)

# ═══════════════════════════════════════════════════════════════════════
# 模块级常量
# ═══════════════════════════════════════════════════════════════════════


class TestModuleConstants:
    def test_default_extraction_type(self):
        assert DEFAULT_EXTRACTION_TYPE == "default"

    def test_download_extraction_type(self):
        assert DOWNLOAD_EXTRACTION_TYPE == "download"


# ═══════════════════════════════════════════════════════════════════════
# FetchErrorCategory — 结构完整性
# ═══════════════════════════════════════════════════════════════════════


class TestFetchErrorCategory:
    def test_exactly_15_members(self):
        assert len(FetchErrorCategory) == 15

    def test_is_enum(self):
        assert issubclass(FetchErrorCategory, Enum)  # type: ignore[arg-type]

    def test_all_values_are_lowercase_strings(self):
        for member in FetchErrorCategory:
            assert isinstance(member.value, str)
            assert member.value == member.value.lower()

    def test_unique_values(self):
        values = [m.value for m in FetchErrorCategory]
        assert len(values) == len(set(values))

    def test_known_member_names(self):
        names = {m.name for m in FetchErrorCategory}
        assert names == {
            "DNS",
            "SSL",
            "TIMEOUT",
            "CONNECTION_REFUSED",
            "CONNECTION_RESET",
            "TARGET_CLOSED",
            "CONTEXT_FAILURE",
            "ABORTED",
            "PROXY",
            "PROXY_EXHAUSTED",
            "HTTP_4XX",
            "HTTP_5XX",
            "DOWNLOAD",
            "TOO_MANY_REDIRECTS",
            "GENERIC",
        }

    def test_member_lookup_by_value(self):
        assert FetchErrorCategory("dns") == FetchErrorCategory.DNS
        assert FetchErrorCategory("proxy") == FetchErrorCategory.PROXY
        assert FetchErrorCategory("generic") == FetchErrorCategory.GENERIC


# ═══════════════════════════════════════════════════════════════════════
# _CHROMIUM_ERROR_TABLE — 结构完整性
# ═══════════════════════════════════════════════════════════════════════


class TestChromiumErrorTable:
    def test_all_categories_covered(self):
        """每个 FetchErrorCategory 至少被一个表项或回退模式覆盖（GENERIC 是默认兜底）。"""
        covered: set[FetchErrorCategory] = {c for _, c in _CHROMIUM_ERROR_TABLE}
        for _, c in _FALLBACK_PATTERNS:
            covered.add(c)
        covered.add(FetchErrorCategory.GENERIC)  # 隐式默认
        for cat in FetchErrorCategory:
            assert cat in covered, f"{cat} not covered by error table or fallbacks"

    def test_no_prefix_ambiguity(self):
        """更精确的条目必须排在更宽泛的条目之前。"""
        for i, (pat_i, _cat_i) in enumerate(_CHROMIUM_ERROR_TABLE):
            if not pat_i.endswith("_"):
                continue
            for j, (pat_j, _cat_j) in enumerate(_CHROMIUM_ERROR_TABLE):
                if j <= i or not pat_j.endswith("_"):
                    continue
                if pat_j.startswith(pat_i) and len(pat_j) > len(pat_i):
                    pytest.fail(f"Prefix {pat_i!r} (pos {i}) shadows narrower {pat_j!r} (pos {j})")

    def test_exact_entries_before_family_prefixes(self):
        """精确匹配码 (如 net::ERR_TIMED_OUT) 必须排在族前缀 (如 net::ERR_SSL_) 之前。"""
        exact_positions: dict[str, int] = {}
        family_positions: dict[str, int] = {}
        for i, (pat, _) in enumerate(_CHROMIUM_ERROR_TABLE):
            if pat.endswith("_"):
                family_positions[pat] = i
            else:
                exact_positions[pat] = i
        for exact, exact_pos in exact_positions.items():
            for family, family_pos in family_positions.items():
                if exact.startswith(family):
                    if exact_pos > family_pos:
                        pytest.fail(f"Exact {exact!r} (pos {exact_pos}) after family {family!r} (pos {family_pos})")

    def test_net_err_wildcard_is_last(self):
        """通配 net::ERR_ 必须是最后一项。"""
        last_pat, last_cat = _CHROMIUM_ERROR_TABLE[-1]
        assert last_pat == "net::ERR_", f"Last entry should be catch-all, got {last_pat!r}"
        assert last_cat == FetchErrorCategory.GENERIC

    def test_every_entry_classifies_to_its_category(self):
        """每个精确匹配项应正确分类该 Chromium 错误码。"""
        for pattern, category in _CHROMIUM_ERROR_TABLE:
            if pattern.endswith("_"):
                # 前缀族：构造一个合成错误码验证
                synthetic_code = pattern + "SYNTHETIC_TEST"
                assert classify_fetch_error(synthetic_code) == category, (
                    f"Prefix {pattern!r} should classify {synthetic_code!r} as {category.value}"
                )
            else:
                assert classify_fetch_error(pattern) == category, (
                    f"Exact {pattern!r} should classify as {category.value}"
                )

    def test_socks_errors_classify_as_proxy(self):
        """SOCKS5 代理错误应归入 PROXY 分类——回归审计发现 #1。"""
        assert classify_fetch_error("net::ERR_SOCKS_CONNECTION_FAILED") == FetchErrorCategory.PROXY
        assert classify_fetch_error("net::ERR_SOCKS_CONNECTION_HOST_UNREACHABLE") == FetchErrorCategory.PROXY

    def test_cert_errors_classify_as_ssl(self):
        """证书错误族应归入 SSL。"""
        assert classify_fetch_error("net::ERR_CERT_COMMON_NAME_INVALID") == FetchErrorCategory.SSL
        assert classify_fetch_error("net::ERR_CERT_DATE_INVALID") == FetchErrorCategory.SSL
        assert classify_fetch_error("net::ERR_SSL_PROTOCOL_ERROR") == FetchErrorCategory.SSL

    def test_unknown_chromium_error_is_generic(self):
        """无法识别的 net::ERR_ 错码 → GENERIC。"""
        assert classify_fetch_error("net::ERR_WHATEVER_UNKNOWN_ERROR") == FetchErrorCategory.GENERIC

    def test_no_chromium_code_falls_back_to_generic(self):
        """不包含 Chromium 错误码且不匹配任何回退模式 → GENERIC。"""
        assert classify_fetch_error("Something went wrong") == FetchErrorCategory.GENERIC


# ═══════════════════════════════════════════════════════════════════════
# classify_fetch_error — 首次匹配语义回归 + 边界值
# ═══════════════════════════════════════════════════════════════════════


class TestClassifyFetchErrorFirstMatch:
    """Chromium 错误码提取优先于任何文本匹配——不再依赖 ERROR_PATTERNS 的 dict 顺序。

    classify_fetch_error 内部先提取 net::ERR_XXX 前缀，再按表匹配。
    文本层面的歧义（如 "Protocol error"）不会干扰已提取的 Chromium 错误码。"""

    def test_chromium_code_always_wins_over_text(self):
        """Chromium 错误码提取优先——即使字符串中包含宽泛文本模式。"""
        err = "Protocol error (Page.navigate): net::ERR_CONNECTION_REFUSED"
        assert classify_fetch_error(err) == FetchErrorCategory.CONNECTION_REFUSED

    def test_chromium_code_reset_wins_over_protocol_error(self):
        err = "Protocol error (Page.navigate): net::ERR_CONNECTION_RESET"
        assert classify_fetch_error(err) == FetchErrorCategory.CONNECTION_RESET

    def test_fallback_timeout_without_chromium_code(self):
        """不包含 Chromium 错误码的 asyncio 超时 → 回退模式匹配。"""
        err = "Timeout exceeded (asyncio safety net)"
        assert classify_fetch_error(err) == FetchErrorCategory.TIMEOUT

    def test_no_chromium_code_no_fallback_is_generic(self):
        """既无 Chromium 错误码也无回退匹配 → GENERIC。旧系统中 "Protocol error" 被过宽匹配为 TARGET_CLOSED。"""
        assert (
            classify_fetch_error("Protocol error (Page.navigate): Unknown internal error") == FetchErrorCategory.GENERIC
        )

    def test_unrecognized_text_is_generic(self):
        """不以任何已知模式开头的错误 → GENERIC。"""
        assert classify_fetch_error("Unable to find target with given id") == FetchErrorCategory.GENERIC

    def test_first_chromium_code_wins_with_multiple_codes(self):
        """多个 Chromium 错误码同时出现 → 取第一个（regex search 自然行为）。"""
        err = "net::ERR_TIMED_OUT during net::ERR_NAME_NOT_RESOLVED lookup"
        assert classify_fetch_error(err) == FetchErrorCategory.TIMEOUT


class TestClassifyFetchErrorEdgeCases:
    def test_empty_string_returns_generic(self):
        assert classify_fetch_error("") == FetchErrorCategory.GENERIC

    def test_none_returns_generic(self):
        """防御性：falsy 输入回退到 GENERIC。"""
        assert classify_fetch_error(None) == FetchErrorCategory.GENERIC  # type: ignore[arg-type]

    def test_whitespace_only_returns_generic(self):
        assert classify_fetch_error("   ") == FetchErrorCategory.GENERIC

    def test_nonexistent_pattern_returns_generic(self):
        assert classify_fetch_error("xyzzy_nonexistent_pattern_12345") == FetchErrorCategory.GENERIC

    def test_download_category(self):
        assert classify_fetch_error("Download is starting") == FetchErrorCategory.DOWNLOAD

    def test_too_many_redirects_category(self):
        assert classify_fetch_error("net::ERR_TOO_MANY_REDIRECTS") == FetchErrorCategory.TOO_MANY_REDIRECTS

    def test_http_429_is_4xx_not_5xx(self):
        assert classify_fetch_error("net::HTTP_429: Rate limit") == FetchErrorCategory.HTTP_4XX

    def test_http_500_is_5xx(self):
        assert classify_fetch_error("net::HTTP_500: Internal error") == FetchErrorCategory.HTTP_5XX

    def test_connection_closed_is_reset(self):
        assert classify_fetch_error("net::ERR_CONNECTION_CLOSED") == FetchErrorCategory.CONNECTION_RESET

    def test_context_failure_chinese(self):
        assert classify_fetch_error("上下文恢复失败，槽位已失效") == FetchErrorCategory.CONTEXT_FAILURE

    def test_target_closed_page_crashed(self):
        assert classify_fetch_error("Page crashed unexpectedly") == FetchErrorCategory.TARGET_CLOSED

    def test_target_closed_browser_closed(self):
        assert classify_fetch_error("Browser closed during navigation") == FetchErrorCategory.TARGET_CLOSED

    def test_target_closed_execution_context(self):
        assert classify_fetch_error("Execution context was destroyed in navigation") == FetchErrorCategory.TARGET_CLOSED

    def test_proxy_exhausted_chinese(self):
        assert classify_fetch_error("代理轮换失败——无可用替代代理") == FetchErrorCategory.PROXY_EXHAUSTED

    def test_playwright_timeout_message_has_chromium_code(self):
        """Playwright 超时消息中包含 Chromium 错误码时应按错误码分类。"""
        assert classify_fetch_error("Page.goto: Timeout 30000ms exceeded.") == FetchErrorCategory.TIMEOUT

    def test_async_timeout_safety_net(self):
        assert classify_fetch_error("Timeout exceeded (asyncio safety net)") == FetchErrorCategory.TIMEOUT


# ═══════════════════════════════════════════════════════════════════════
# _CONNECTIVITY_ERRORS / _NON_PROXY_ERRORS
# ═══════════════════════════════════════════════════════════════════════


class TestConnectivityErrors:
    def test_exact_members(self):
        assert _CONNECTIVITY_ERRORS == frozenset(
            {
                FetchErrorCategory.CONNECTION_REFUSED,
                FetchErrorCategory.TIMEOUT,
                FetchErrorCategory.CONNECTION_RESET,
                FetchErrorCategory.GENERIC,
            }
        )

    def test_is_frozenset(self):
        assert isinstance(_CONNECTIVITY_ERRORS, frozenset)

    def test_no_proxy_categories(self):
        assert FetchErrorCategory.PROXY not in _CONNECTIVITY_ERRORS
        assert FetchErrorCategory.PROXY_EXHAUSTED not in _CONNECTIVITY_ERRORS

    def test_all_members_are_fetch_error_categories(self):
        for cat in _CONNECTIVITY_ERRORS:
            assert isinstance(cat, FetchErrorCategory)


class TestNonProxyErrors:
    def test_exact_members(self):
        assert _NON_PROXY_ERRORS == frozenset(
            {
                FetchErrorCategory.DNS,
                FetchErrorCategory.SSL,
                FetchErrorCategory.HTTP_4XX,
                FetchErrorCategory.DOWNLOAD,
                FetchErrorCategory.TOO_MANY_REDIRECTS,
            }
        )

    def test_is_frozenset(self):
        assert isinstance(_NON_PROXY_ERRORS, frozenset)

    def test_all_members_are_fetch_error_categories(self):
        for cat in _NON_PROXY_ERRORS:
            assert isinstance(cat, FetchErrorCategory)


class TestErrorCategorySetsNoOverlap:
    def test_no_overlap_between_sets(self):
        assert _CONNECTIVITY_ERRORS.isdisjoint(_NON_PROXY_ERRORS)

    def test_uncovered_categories_are_neutral(self):
        all_cats = set(FetchErrorCategory)
        covered = _CONNECTIVITY_ERRORS | _NON_PROXY_ERRORS
        uncovered = all_cats - covered
        assert uncovered == {
            FetchErrorCategory.PROXY,
            FetchErrorCategory.PROXY_EXHAUSTED,
            FetchErrorCategory.CONTEXT_FAILURE,
            FetchErrorCategory.TARGET_CLOSED,
            FetchErrorCategory.ABORTED,
            FetchErrorCategory.HTTP_5XX,
        }


# ═══════════════════════════════════════════════════════════════════════
# DropReason
# ═══════════════════════════════════════════════════════════════════════


class TestDropReason:
    def test_exactly_9_members(self):
        assert len(DropReason) == 9

    def test_unique_values(self):
        values = [m.value for m in DropReason]
        assert len(values) == len(set(values))

    def test_all_values_are_lowercase_snake_case(self):
        for member in DropReason:
            assert member.value == member.value.lower()
            assert " " not in member.value
            assert "_" in member.value or member.value.isalpha()

    def test_known_member_names(self):
        names = {m.name for m in DropReason}
        assert names == {
            "EXCLUDE_PATTERN",
            "NOFOLLOW_LINK",
            "CROSS_DOMAIN",
            "INVALID_URL",
            "QUEUE_FULL",
            "ALREADY_VISITED",
            "SKIP_DUPLICATE_LINKS",
            "SAME_PAGE_DUP",
            "DOWNLOAD_CANDIDATE",
        }

    def test_member_value_correct(self):
        assert DropReason.EXCLUDE_PATTERN.value == "exclude_pattern"
        assert DropReason.NOFOLLOW_LINK.value == "nofollow_link"
        assert DropReason.CROSS_DOMAIN.value == "cross_domain"
        assert DropReason.INVALID_URL.value == "invalid_url"
        assert DropReason.QUEUE_FULL.value == "queue_full"
        assert DropReason.ALREADY_VISITED.value == "already_visited"
        assert DropReason.SKIP_DUPLICATE_LINKS.value == "skip_duplicate_links"
        assert DropReason.SAME_PAGE_DUP.value == "same_page_dup"
        assert DropReason.DOWNLOAD_CANDIDATE.value == "download_candidate"


# ═══════════════════════════════════════════════════════════════════════
# EnqueueResult
# ═══════════════════════════════════════════════════════════════════════


class TestEnqueueResult:
    def test_exactly_3_members(self):
        assert len(EnqueueResult) == 3

    def test_known_member_names(self):
        names = {m.name for m in EnqueueResult}
        assert names == {"ENQUEUED", "QUEUE_FULL", "DUPLICATE"}

    def test_unique_values(self):
        values = [m.value for m in EnqueueResult]
        assert len(values) == len(set(values))

    def test_enqueued_is_the_only_positive_result(self):
        assert EnqueueResult.ENQUEUED.value == "enqueued"
        assert EnqueueResult.QUEUE_FULL.value == "queue_full"
        assert EnqueueResult.DUPLICATE.value == "duplicate"
        # ENQUEUED 是唯一「成功」状态
        assert EnqueueResult.QUEUE_FULL != EnqueueResult.ENQUEUED
        assert EnqueueResult.DUPLICATE != EnqueueResult.ENQUEUED

    def test_is_enum(self):
        assert issubclass(EnqueueResult, Enum)  # type: ignore[arg-type]


# ═══════════════════════════════════════════════════════════════════════
# RuleMatchCache — 基本操作 / LRU 淘汰 / TTL 过期 / 边界值
# ═══════════════════════════════════════════════════════════════════════


class TestRuleMatchCacheBasic:
    def test_get_miss_returns_none(self):
        assert RuleMatchCache().get("nonexistent.com") is None

    def test_set_and_get(self):
        cache = RuleMatchCache()
        cache.set("example.com", "my_rule")
        assert cache.get("example.com") == "my_rule"

    def test_set_overwrites_value(self):
        cache = RuleMatchCache()
        cache.set("example.com", "old_rule")
        cache.set("example.com", "new_rule")
        assert cache.get("example.com") == "new_rule"

    def test_different_domains_independent(self):
        cache = RuleMatchCache()
        cache.set("a.com", "rule_a")
        cache.set("b.com", "rule_b")
        assert cache.get("a.com") == "rule_a"
        assert cache.get("b.com") == "rule_b"

    def test_len_empty_cache(self):
        assert len(RuleMatchCache()) == 0

    def test_len_tracks_entries(self):
        cache = RuleMatchCache()
        cache.set("a.com", "r1")
        assert len(cache) == 1
        cache.set("b.com", "r2")
        assert len(cache) == 2

    def test_default_maxsize(self):
        assert RuleMatchCache()._maxsize == 10000

    def test_default_ttl(self):
        assert RuleMatchCache()._ttl == 3600

    def test_empty_string_domain_accepted(self):
        """空字符串域名技术上合法——缓存不校验域名格式。"""
        cache = RuleMatchCache()
        cache.set("", "rule")
        assert cache.get("") == "rule"

    def test_empty_string_rule_name_accepted(self):
        cache = RuleMatchCache()
        cache.set("example.com", "")
        assert cache.get("example.com") == ""


class TestRuleMatchCacheLRU:
    def test_evicts_oldest_on_overflow(self):
        cache = RuleMatchCache(maxsize=3)
        cache.set("a.com", "r1")
        cache.set("b.com", "r2")
        cache.set("c.com", "r3")
        cache.set("d.com", "r4")
        assert cache.get("a.com") is None
        assert cache.get("b.com") == "r2"
        assert cache.get("c.com") == "r3"
        assert cache.get("d.com") == "r4"
        assert len(cache) == 3

    def test_get_renews_lru_position(self):
        cache = RuleMatchCache(maxsize=3)
        cache.set("a.com", "r1")
        cache.set("b.com", "r2")
        cache.set("c.com", "r3")
        assert cache.get("a.com") == "r1"  # → front
        cache.set("d.com", "r4")  # evicts b.com
        assert cache.get("a.com") == "r1"
        assert cache.get("b.com") is None
        assert cache.get("c.com") == "r3"
        assert cache.get("d.com") == "r4"

    def test_set_existing_renews_lru_position(self):
        cache = RuleMatchCache(maxsize=3)
        cache.set("a.com", "r1")
        cache.set("b.com", "r2")
        cache.set("c.com", "r3")
        cache.set("a.com", "r1_new")  # overwrite → front
        cache.set("d.com", "r4")  # evicts b.com
        assert cache.get("a.com") == "r1_new"
        assert cache.get("b.com") is None
        assert cache.get("c.com") == "r3"
        assert cache.get("d.com") == "r4"

    def test_exact_maxsize_no_eviction(self):
        cache = RuleMatchCache(maxsize=3)
        cache.set("a.com", "r1")
        cache.set("b.com", "r2")
        cache.set("c.com", "r3")
        assert len(cache) == 3
        assert cache.get("a.com") == "r1"
        assert cache.get("b.com") == "r2"
        assert cache.get("c.com") == "r3"

    def test_bulk_insert_beyond_maxsize(self):
        cache = RuleMatchCache(maxsize=5)
        for i in range(30):
            cache.set(f"d{i}.com", f"r{i}")
        assert len(cache) == 5
        for i in range(25):
            assert cache.get(f"d{i}.com") is None
        for i in range(25, 30):
            assert cache.get(f"d{i}.com") == f"r{i}"

    def test_maxsize_one(self):
        cache = RuleMatchCache(maxsize=1)
        cache.set("a.com", "r1")
        cache.set("b.com", "r2")
        assert cache.get("a.com") is None
        assert cache.get("b.com") == "r2"
        assert len(cache) == 1

    def test_maxsize_one_overwrite_no_eviction(self):
        cache = RuleMatchCache(maxsize=1)
        cache.set("a.com", "r1")
        cache.set("a.com", "r2")
        assert len(cache) == 1
        assert cache.get("a.com") == "r2"


class TestRuleMatchCacheTTL:
    # TTL 安全裕度: sleep ≥ 3× ttl，避免 CI 调度抖动导致假失败

    def test_entry_expires_after_ttl(self):
        cache = RuleMatchCache(ttl=0.01)
        cache.set("example.com", "my_rule")
        assert cache.get("example.com") == "my_rule"
        time.sleep(0.03)
        assert cache.get("example.com") is None

    def test_expired_entry_removed_from_len(self):
        cache = RuleMatchCache(ttl=0.01)
        cache.set("example.com", "my_rule")
        assert len(cache) == 1
        time.sleep(0.03)
        cache.get("example.com")
        assert len(cache) == 0

    def test_not_yet_expired_still_valid(self):
        cache = RuleMatchCache(ttl=60.0)
        cache.set("example.com", "my_rule")
        assert cache.get("example.com") == "my_rule"

    def test_within_ttl_boundary(self):
        cache = RuleMatchCache(ttl=0.10)
        cache.set("example.com", "my_rule")
        time.sleep(0.01)
        assert cache.get("example.com") == "my_rule"

    def test_multiple_entries_expire_independently(self):
        cache = RuleMatchCache(ttl=0.01)
        cache.set("a.com", "r1")
        time.sleep(0.03)  # a.com 过期
        cache.set("b.com", "r2")
        assert cache.get("a.com") is None
        assert cache.get("b.com") == "r2"
        # 只删过期的，不影响未过期的
        time.sleep(0.03)  # b.com 也过期
        assert cache.get("b.com") is None

    def test_get_on_expired_does_not_affect_other_entries(self):
        cache = RuleMatchCache(ttl=0.01)
        cache.set("a.com", "r1")
        cache.set("b.com", "r2")
        time.sleep(0.03)  # 两者都过期
        assert cache.get("a.com") is None  # 惰性逐出 a
        assert len(cache) == 1  # b 仍在，但获取时才逐出
        assert cache.get("b.com") is None  # 惰性逐出 b
        assert len(cache) == 0


# ═══════════════════════════════════════════════════════════════════════
# RuleSnapshot — default_only / 访问器 / 不可变性 / 独立实例
# ═══════════════════════════════════════════════════════════════════════


class TestRuleSnapshotDefaultOnly:
    def test_returns_snapshot(self):
        assert isinstance(RuleSnapshot.default_only(), RuleSnapshot)

    def test_rules_tuple_empty(self):
        assert RuleSnapshot.default_only().rules == ()

    def test_by_name_has_default_entry(self):
        snap = RuleSnapshot.default_only()
        assert "default" in snap.by_name
        assert snap.by_name["default"].name == "default"

    def test_by_domain_empty(self):
        assert RuleSnapshot.default_only().by_domain == {}

    def test_get_rule_default(self):
        rule = RuleSnapshot.default_only().get_rule("default")
        assert rule is not None
        assert rule.name == "default"
        assert rule.enabled is True

    def test_get_rule_miss(self):
        assert RuleSnapshot.default_only().get_rule("nonexistent") is None


class TestRuleSnapshotAccessors:
    def test_get_path_returns_path_object(self):
        snap = RuleSnapshot(_path_map={"my_rule": "/rules/my_rule.json"})
        p = snap.get_path("my_rule")
        assert isinstance(p, Path)
        assert str(p) == "/rules/my_rule.json"

    def test_get_path_miss_returns_none(self):
        assert RuleSnapshot().get_path("nonexistent") is None

    def test_get_path_empty_string_key_returns_none(self):
        assert RuleSnapshot().get_path("") is None

    def test_get_source_hit(self):
        snap = RuleSnapshot(_source_map={"my_rule": "user"})
        assert snap.get_source("my_rule") == "user"

    def test_get_source_pip(self):
        snap = RuleSnapshot(_source_map={"preset": "pip"})
        assert snap.get_source("preset") == "pip"

    def test_get_source_remote(self):
        snap = RuleSnapshot(_source_map={"external": "remote"})
        assert snap.get_source("external") == "remote"

    def test_get_source_miss_returns_none(self):
        assert RuleSnapshot().get_source("nonexistent") is None


class TestRuleSnapshotImmutability:
    def test_is_frozen_dataclass(self):
        assert dataclasses.is_dataclass(RuleSnapshot)
        snap = RuleSnapshot()
        with pytest.raises(dataclasses.FrozenInstanceError):
            snap.rules = ()  # type: ignore[misc]

    def test_default_only_instances_are_independent(self):
        a = RuleSnapshot.default_only()
        b = RuleSnapshot.default_only()
        assert a is not b
        assert a.by_name is not b.by_name
        assert a._match_cache is not b._match_cache

    def test_empty_snapshot_instances_are_independent(self):
        a = RuleSnapshot()
        b = RuleSnapshot()
        assert a is not b
        assert a.by_name is not b.by_name
        # 空快照的 by_name 是独立 dict
        b.by_name["test"] = None  # type: ignore[index]
        assert "test" not in a.by_name


class TestRuleSnapshotFullConstruction:
    def test_all_fields_populated(self):
        """全字段构造——验证所有字段独立存储。"""
        from astrocrawl.rules._schema import RuleSchema

        rule = RuleSchema(name="test_rule", enabled=True)
        cache = RuleMatchCache(maxsize=10)
        snap = RuleSnapshot(
            rules=(rule,),
            by_name={"test_rule": rule, "default": rule},
            by_domain={"example.com": ("test_rule",)},
            _generic_rules=("global_rule",),
            _match_cache=cache,
            _path_map={"test_rule": "/rules/test.json"},
            _source_map={"test_rule": "user"},
        )
        assert len(snap.rules) == 1
        assert snap.rules[0].name == "test_rule"
        assert snap.get_rule("test_rule") is rule
        assert snap.get_rule("default") is rule
        assert snap.by_domain["example.com"] == ("test_rule",)
        assert snap._generic_rules == ("global_rule",)
        assert snap._match_cache is cache
        assert str(snap.get_path("test_rule")) == "/rules/test.json"
        assert snap.get_source("test_rule") == "user"


# AsyncCloseable tests moved to astrobasis/tests/test_types.py

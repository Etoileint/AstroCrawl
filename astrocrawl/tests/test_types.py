"""_types.py 共享内核类型完整测试套件。

覆盖 9 个实体：FetchErrorCategory, ERROR_PATTERNS, classify_fetch_error,
DropReason, EnqueueResult, RuleMatchCache, RuleSnapshot, AsyncCloseable,
DEFAULT_EXTRACTION_TYPE, DOWNLOAD_EXTRACTION_TYPE。
（PathSwitch / _CONNECTIVITY_ERRORS / _NON_PROXY_ERRORS 已搬迁至 test_path_strategy.py）

设计原则：
- 每个断言验证一个不变式或行为
- 首次匹配语义回归：具体模式在 dict 中的位置必须先于宽泛模式 (如 "Protocol error")，
  合成测试字符串防范未来重排导致降级分类
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
    DEFAULT_EXTRACTION_TYPE,
    DOWNLOAD_EXTRACTION_TYPE,
    ERROR_PATTERNS,
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
# ERROR_PATTERNS — SSOT 结构完整性
# ═══════════════════════════════════════════════════════════════════════


class TestErrorPatternsSSOT:
    def test_every_category_has_entry(self):
        for cat in FetchErrorCategory:
            assert cat in ERROR_PATTERNS, f"{cat} missing from ERROR_PATTERNS"

    def test_no_duplicate_patterns_across_categories(self):
        seen: dict[str, FetchErrorCategory] = {}
        for cat, patterns in ERROR_PATTERNS.items():
            for pat in patterns:
                if pat in seen:
                    pytest.fail(f"Pattern {pat!r} appears in both {seen[pat].value} and {cat.value}")
                seen[pat] = cat

    def test_generic_has_empty_pattern_list(self):
        assert ERROR_PATTERNS[FetchErrorCategory.GENERIC] == []

    def test_no_pattern_is_empty_string(self):
        for cat, patterns in ERROR_PATTERNS.items():
            for pat in patterns:
                assert pat != "", f"{cat.value} has empty string pattern"

    def test_total_pattern_count(self):
        total = sum(len(v) for v in ERROR_PATTERNS.values())
        assert total == 34  # 回归锚点

    def test_pattern_order_is_deterministic(self):
        keys = list(ERROR_PATTERNS.keys())
        assert keys == [
            FetchErrorCategory.DNS,
            FetchErrorCategory.SSL,
            FetchErrorCategory.TIMEOUT,
            FetchErrorCategory.CONNECTION_REFUSED,
            FetchErrorCategory.CONNECTION_RESET,
            FetchErrorCategory.TARGET_CLOSED,
            FetchErrorCategory.CONTEXT_FAILURE,
            FetchErrorCategory.ABORTED,
            FetchErrorCategory.PROXY,
            FetchErrorCategory.PROXY_EXHAUSTED,
            FetchErrorCategory.HTTP_4XX,
            FetchErrorCategory.HTTP_5XX,
            FetchErrorCategory.DOWNLOAD,
            FetchErrorCategory.TOO_MANY_REDIRECTS,
            FetchErrorCategory.GENERIC,
        ]

    def test_every_pattern_classifies_to_its_category(self):
        """每个模式独立测试：确认模式字符串正确映射到所属类别。"""
        for cat, patterns in ERROR_PATTERNS.items():
            for pat in patterns:
                assert classify_fetch_error(f"prefix {pat} suffix") == cat, (
                    f"Pattern {pat!r} should classify as {cat.value}"
                )


# ═══════════════════════════════════════════════════════════════════════
# classify_fetch_error — 首次匹配语义回归 + 边界值
# ═══════════════════════════════════════════════════════════════════════


class TestClassifyFetchErrorFirstMatch:
    """首次匹配语义回归——dict 插入顺序即匹配优先级。

    以下 "before_protocol_error" 测试使用合成字符串验证：具体网络错误码
    在 ERROR_PATTERNS 中的位置必须先于 TARGET_CLOSED 的宽泛 "Protocol error"，
    否则具体错误会被宽泛模式拦截。Playwright 真实错误中 CDP Protocol error
    与 Chrome net error 不会同时出现，但此测试防止未来有人重排 ERROR_PATTERNS
    时将具体错误类别移到 TARGET_CLOSED 之后。"""

    def test_connection_refused_before_protocol_error(self):
        err = "Protocol error (Page.navigate): net::ERR_CONNECTION_REFUSED"
        assert classify_fetch_error(err) == FetchErrorCategory.CONNECTION_REFUSED

    def test_connection_reset_before_protocol_error(self):
        err = "Protocol error (Page.navigate): net::ERR_CONNECTION_RESET"
        assert classify_fetch_error(err) == FetchErrorCategory.CONNECTION_RESET

    def test_timeout_before_protocol_error(self):
        err = "Protocol error (Page.navigate): Timeout 30000ms exceeded."
        assert classify_fetch_error(err) == FetchErrorCategory.TIMEOUT

    def test_protocol_error_catch_all(self):
        assert (
            classify_fetch_error("Protocol error (Page.navigate): Unknown internal error")
            == FetchErrorCategory.TARGET_CLOSED
        )

    def test_unable_to_find_catch_all(self):
        assert classify_fetch_error("Unable to find target with given id") == FetchErrorCategory.TARGET_CLOSED

    def test_case_sensitive_match(self):
        assert classify_fetch_error("timeout ") == FetchErrorCategory.GENERIC
        assert classify_fetch_error("Timeout ") == FetchErrorCategory.TIMEOUT

    def test_multiple_patterns_first_wins(self):
        """错误同时包含 DNS(pos 0) 和 TIMEOUT(pos 2) 模式 → DNS 先匹配胜出。"""
        err = "net::ERR_TIMED_OUT during net::ERR_NAME_NOT_RESOLVED lookup"
        assert classify_fetch_error(err) == FetchErrorCategory.DNS


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

    def test_target_closed_page_crashed(self):
        assert classify_fetch_error("Page crashed unexpectedly") == FetchErrorCategory.TARGET_CLOSED

    def test_target_closed_browser_closed(self):
        assert classify_fetch_error("Browser closed during navigation") == FetchErrorCategory.TARGET_CLOSED

    def test_connection_closed_is_reset(self):
        assert classify_fetch_error("net::ERR_CONNECTION_CLOSED") == FetchErrorCategory.CONNECTION_RESET

    def test_context_failure_chinese(self):
        assert classify_fetch_error("上下文恢复失败，槽位已失效") == FetchErrorCategory.CONTEXT_FAILURE

    def test_proxy_exhausted_chinese(self):
        assert classify_fetch_error("代理轮换失败——无可用替代代理") == FetchErrorCategory.PROXY_EXHAUSTED


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


# AsyncCloseable tests moved to astrobase/tests/test_types.py

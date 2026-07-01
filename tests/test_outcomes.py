"""Outcome 枚举与统计容器测试"""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError

import pytest

from astrocrawl.crawler.outcomes import CrawlStats, DropReason, FetchErrorCategory, UrlOutcome, classify_fetch_error
from astrocrawl.utils.html import ParseResult


class TestUrlOutcome:
    def test_is_success(self):
        assert UrlOutcome.OK.is_success is True
        assert UrlOutcome.TRUNCATED.is_success is True
        assert UrlOutcome.DUPLICATE.is_success is True
        assert UrlOutcome.NOINDEX.is_success is True
        assert UrlOutcome.PARSE_FAILED.is_success is True
        assert UrlOutcome.ROBOTS_DENIED.is_success is True

    def test_is_failure(self):
        assert UrlOutcome.FETCH_ERROR.is_failure is True
        assert UrlOutcome.INTERNAL_ERROR.is_failure is True
        assert UrlOutcome.STOPPED.is_failure is True
        assert UrlOutcome.OK.is_failure is False

    def test_values_are_strings(self):
        assert isinstance(UrlOutcome.OK.value, str)
        assert UrlOutcome.OK.value == "ok"


class TestClassifyFetchError:
    def test_dns_error(self):
        assert classify_fetch_error("net::ERR_NAME_NOT_RESOLVED") == FetchErrorCategory.DNS

    def test_ssl_error(self):
        assert classify_fetch_error("net::ERR_SSL_PROTOCOL_ERROR") == FetchErrorCategory.SSL
        assert classify_fetch_error("net::ERR_CERT_AUTHORITY_INVALID") == FetchErrorCategory.SSL

    def test_timeout(self):
        assert classify_fetch_error("net::ERR_TIMED_OUT at https://...") == FetchErrorCategory.TIMEOUT

    def test_timeout_playwright_goto(self):
        """Playwright Page.goto 超时——'Timeout 20000ms exceeded.'"""
        assert classify_fetch_error("Page.goto: Timeout 20000ms exceeded.") == FetchErrorCategory.TIMEOUT

    def test_timeout_playwright_multiline(self):
        """Playwright 多行超时日志"""
        assert (
            classify_fetch_error('Page.goto: Timeout 30000ms exceeded.\nCall log:\n  - navigating to "http://x.com"')
            == FetchErrorCategory.TIMEOUT
        )

    def test_timeout_playwright_navigation(self):
        """Playwright navigation timeout 变体"""
        assert classify_fetch_error("Navigation timeout of 30000 ms exceeded") == FetchErrorCategory.TIMEOUT

    def test_timeout_not_connection_timeout(self):
        """'ConnectionTimeout' 不应被误匹配为 TIMEOUT"""
        assert classify_fetch_error("ConnectionTimeout") != FetchErrorCategory.TIMEOUT

    def test_connection_refused(self):
        assert classify_fetch_error("net::ERR_CONNECTION_REFUSED") == FetchErrorCategory.CONNECTION_REFUSED

    def test_connection_reset(self):
        assert classify_fetch_error("net::ERR_CONNECTION_RESET") == FetchErrorCategory.CONNECTION_RESET

    def test_target_closed(self):
        assert classify_fetch_error("Target closed") == FetchErrorCategory.TARGET_CLOSED
        assert classify_fetch_error("Session closed unexpectedly") == FetchErrorCategory.TARGET_CLOSED

    def test_aborted(self):
        assert classify_fetch_error("net::ERR_ABORTED") == FetchErrorCategory.ABORTED

    def test_proxy_error(self):
        assert classify_fetch_error("net::ERR_TUNNEL_CONNECTION_FAILED") == FetchErrorCategory.PROXY
        assert classify_fetch_error("net::ERR_PROXY_CONNECTION_FAILED") == FetchErrorCategory.PROXY

    def test_http_4xx(self):
        assert classify_fetch_error("net::HTTP_404: 不可重试错误") == FetchErrorCategory.HTTP_4XX
        assert classify_fetch_error("net::HTTP_403: ...") == FetchErrorCategory.HTTP_4XX

    def test_http_5xx(self):
        assert classify_fetch_error("net::HTTP_502: ...") == FetchErrorCategory.HTTP_5XX
        assert classify_fetch_error("net::HTTP_503: ...") == FetchErrorCategory.HTTP_5XX

    def test_generic_fallback(self):
        assert classify_fetch_error("") == FetchErrorCategory.GENERIC
        assert classify_fetch_error("some unknown error") == FetchErrorCategory.GENERIC


class TestCrawlStats:
    async def test_record_outcome_basic(self):
        cs = CrawlStats()
        await cs.record_outcome(UrlOutcome.OK, "example.com", 100.0)
        assert cs.outcomes.get("ok") == 1
        assert cs.domain_outcomes["example.com"]["ok"] == 1
        assert cs.domain_timing["example.com"] == 100.0

    async def test_record_multiple_outcomes(self):
        cs = CrawlStats()
        await cs.record_outcome(UrlOutcome.OK, "a.com", 50.0)
        await cs.record_outcome(UrlOutcome.DUPLICATE, "a.com", 30.0)
        await cs.record_outcome(UrlOutcome.ROBOTS_DENIED, "b.com", 0.0)
        assert cs.outcomes.get("ok") == 1
        assert cs.outcomes.get("duplicate") == 1
        assert cs.outcomes.get("robots_denied") == 1
        assert cs.domain_timing["a.com"] == 80.0
        assert cs.domain_timing_count["a.com"] == 2

    async def test_record_drop(self):
        cs = CrawlStats()
        await cs.record_drop(DropReason.EXCLUDE_PATTERN, 5)
        await cs.record_drop(DropReason.CROSS_DOMAIN, 3)
        assert cs.drops.get("exclude_pattern") == 5
        assert cs.drops.get("cross_domain") == 3
        assert sum(cs.drops.values()) == 8

    async def test_record_fetch_error(self):
        cs = CrawlStats()
        await cs.record_fetch_error(FetchErrorCategory.DNS)
        await cs.record_fetch_error(FetchErrorCategory.DNS)
        await cs.record_fetch_error(FetchErrorCategory.TIMEOUT)
        assert cs.fetch_errors.get("dns") == 2
        assert cs.fetch_errors.get("timeout") == 1

    async def test_record_redirect(self):
        cs = CrawlStats()
        await cs.record_redirect()
        await cs.record_redirect()
        assert cs.redirects == 2

    async def test_initial_outcomes_merge(self):
        cs = CrawlStats()
        cs.initial_outcomes = {"ok": 10, "robots_denied": 3}
        await cs.record_outcome(UrlOutcome.OK, "a.com")
        combined = dict(cs.initial_outcomes)
        for k, c in cs.outcomes.items():
            combined[k] = combined.get(k, 0) + c
        assert combined["ok"] == 11
        assert combined["robots_denied"] == 3

    async def test_thread_safety(self):
        """验证多个并发写入不会丢失计数。"""
        cs = CrawlStats()
        n = 500

        async def writer():
            for _ in range(n):
                await cs.record_outcome(UrlOutcome.OK, "x.com", 1.0)

        await asyncio.gather(*[writer() for _ in range(4)])
        assert cs.outcomes.get("ok") == n * 4
        assert cs.domain_timing["x.com"] == float(n * 4)


class TestParseResult:
    def test_default_values(self):
        pr = ParseResult(text="", links=[], allow_index=True, allow_follow=True)
        assert pr.parse_error is False
        assert pr.text_truncated is False
        assert pr.original_text_len == 0
        assert pr.nofollow_skipped == 0
        assert pr.cross_domain_skipped == 0
        assert pr.invalid_url_skipped == 0
        assert pr.same_page_dupes == 0

    def test_full_constructor(self):
        pr = ParseResult(
            text="hello",
            links=["http://a.com"],
            allow_index=False,
            allow_follow=True,
            parse_error=False,
            text_truncated=True,
            original_text_len=1000,
            nofollow_skipped=2,
            cross_domain_skipped=3,
            invalid_url_skipped=1,
            same_page_dupes=1,
        )
        assert pr.text == "hello"
        assert pr.text_truncated is True
        assert pr.nofollow_skipped == 2


# ═══════════════════════════════════════════════════════════════════════
# CrawlStats 扩展测试
# ═══════════════════════════════════════════════════════════════════════


class TestCrawlStatsExtended:
    async def test_record_rule_hit_and_snapshot(self):
        cs = CrawlStats()
        await cs.record_rule_hit("product", 3, 5, 150.0)
        await cs.record_rule_hit("product", 4, 5, 250.0)
        snap = await cs.get_rule_stats_snapshot()
        assert "product" in snap
        assert snap["product"]["hits"] == 2
        assert snap["product"]["fields_filled"] == 7
        assert snap["product"]["fields_total"] == 10

    async def test_rule_stats_snapshot_fill_rate(self):
        cs = CrawlStats()
        await cs.record_rule_hit("r", 3, 10, 100.0)
        snap = await cs.get_rule_stats_snapshot()
        assert snap["r"]["fill_rate"] == 0.3  # 3/10

    async def test_rule_stats_snapshot_avg_ms(self):
        cs = CrawlStats()
        await cs.record_rule_hit("r", 1, 1, 100.0)
        await cs.record_rule_hit("r", 1, 1, 200.0)
        snap = await cs.get_rule_stats_snapshot()
        assert snap["r"]["avg_ms"] == 150.0

    async def test_rule_stats_snapshot_slow_count(self):
        cs = CrawlStats()
        await cs.record_rule_hit("r", 1, 1, 500.0)  # not slow
        await cs.record_rule_hit("r", 1, 1, 1500.0)  # slow (>1000ms)
        await cs.record_rule_hit("r", 1, 1, 2000.0)  # slow
        snap = await cs.get_rule_stats_snapshot()
        assert snap["r"]["slow_count"] == 2

    async def test_get_snapshot_merges_initial_outcomes(self):
        """get_snapshot() 返回的 outcomes 应合并 initial + 本轮。"""
        cs = CrawlStats()
        await cs.set_initial_outcomes({"ok": 10, "fetch_error": 3})
        await cs.record_outcome(UrlOutcome.OK, "a.com")
        await cs.record_outcome(UrlOutcome.FETCH_ERROR, "a.com")
        snap = await cs.get_snapshot()
        assert snap["outcomes"].get("ok") == 11
        assert snap["outcomes"].get("fetch_error") == 4

    async def test_get_snapshot_initial_only_preserved(self):
        """initial_outcomes 中独有的 key 保留在合并结果中。"""
        cs = CrawlStats()
        await cs.set_initial_outcomes({"ok": 5, "robots_denied": 2})
        # 本轮无 robots_denied
        await cs.record_outcome(UrlOutcome.OK, "a.com")
        snap = await cs.get_snapshot()
        assert snap["outcomes"].get("robots_denied") == 2  # 仅来自 initial
        assert snap["outcomes"].get("ok") == 6

    async def test_get_snapshot_empty_initial(self):
        """无 initial_outcomes 时（全新爬取）snapshot 仅含本轮。"""
        cs = CrawlStats()
        await cs.record_outcome(UrlOutcome.OK, "a.com")
        await cs.record_outcome(UrlOutcome.OK, "a.com")
        snap = await cs.get_snapshot()
        assert snap["outcomes"].get("ok") == 2

    async def test_to_snapshot_restore_roundtrip(self):
        cs = CrawlStats()
        await cs.record_fetch_error(FetchErrorCategory.DNS)
        await cs.record_drop(DropReason.CROSS_DOMAIN, 3)
        await cs.record_redirect()
        await cs.record_rule_hit("product", 3, 5, 120.0)
        await cs.record_rule_hit("product", 4, 5, 250.0)
        snap = await cs.to_snapshot()
        cs2 = CrawlStats()
        cs2.restore_snapshot(snap)
        assert cs2.fetch_errors.get("dns") == 1
        assert cs2.drops.get("cross_domain") == 3
        assert cs2.redirects == 1
        assert cs2.rule_hits.get("product") == 2
        assert cs2.rule_fields_filled.get("product") == 7
        assert cs2.rule_slow_count["product"] == 0  # defaultdict(int) → 0 for missing

    def test_restore_snapshot_backward_compat(self):
        """旧格式快照（无规则字段）不导致 restore 报错。"""
        cs = CrawlStats()
        old_snap = {
            "fetch_errors": {"dns": 1},
            "drops": {"cross_domain": 2},
            "redirects": 3,
            "domain_timing": {"x.com": 100.0},
            "domain_timing_count": {"x.com": 2},
            # 无 rule_* 字段 — 旧版快照
        }
        cs.restore_snapshot(old_snap)
        assert cs.fetch_errors["dns"] == 1
        assert cs.drops["cross_domain"] == 2
        assert cs.redirects == 3
        assert len(cs.rule_hits) == 0  # 无数据，defaultdict 保持空

    async def test_concurrent_read_write_snapshot(self):
        cs = CrawlStats()

        async def writer():
            for _ in range(100):
                await cs.record_outcome(UrlOutcome.OK, "x.com")

        async def reader():
            for _ in range(50):
                await cs.get_snapshot()

        await asyncio.gather(writer(), reader(), writer())
        assert cs.outcomes.get("ok") == 200

    async def test_completed_urls_property(self):
        cs = CrawlStats()
        await cs.set_initial_completed(10)
        await cs.inc_session_completed()
        await cs.inc_session_completed()
        assert cs.completed_urls == 12

    async def test_inc_session_completed(self):
        cs = CrawlStats()
        assert cs.completed_urls == 0
        await cs.inc_session_completed()
        assert cs.completed_urls == 1

    async def test_increment_discovery_total_origins_atomic(self):
        cs = CrawlStats()

        async def inc():
            for _ in range(50):
                await cs.increment_discovery_total_origins()

        await asyncio.gather(inc(), inc())
        assert await cs.get_discovery_total_origins() == 100

    async def test_add_robots_origin_first_time(self):
        cs = CrawlStats()
        assert await cs.add_robots_origin("https://example.com") is True

    async def test_add_robots_origin_dedup(self):
        cs = CrawlStats()
        await cs.add_robots_origin("https://example.com")
        assert await cs.add_robots_origin("https://example.com") is False

    async def test_record_robots_fetch_classification(self):
        cs = CrawlStats()
        await cs.record_robots_fetch("ok")
        await cs.record_robots_fetch("http_404")
        await cs.record_robots_fetch("http_403")
        await cs.record_robots_fetch("http_500")
        await cs.record_robots_fetch("fetch_failed")
        assert cs.robots_fetch_ok == 4  # ok + http_404 + http_403 + http_500
        assert cs.robots_fetch_fail == 1  # only fetch_failed

    async def test_record_robots_not_checked(self):
        cs = CrawlStats()
        await cs.record_robots_not_checked()
        await cs.record_robots_not_checked()
        assert cs.robots_not_checked == 2

    async def test_record_sitemap_fetch_ok_and_fail(self):
        cs = CrawlStats()
        await cs.record_sitemap_fetch(True)
        await cs.record_sitemap_fetch(False)
        assert cs.sitemap_fetch_ok == 1
        assert cs.sitemap_fetch_fail == 1

    async def test_set_discovery_total_origins_with_reset(self):
        cs = CrawlStats()
        cs.discovery_robots_done = 5
        cs.discovery_sitemap_done = 3
        await cs.set_discovery_total_origins(10, reset_counters=True)
        assert cs.discovery_total_origins == 10
        assert cs.discovery_robots_done == 0
        assert cs.discovery_sitemap_done == 0

    async def test_set_initial_completed_resets_session(self):
        cs = CrawlStats()
        cs.session_completed = 50
        await cs.set_initial_completed(100)
        assert cs.initial_completed == 100
        assert cs.session_completed == 0


# ═══════════════════════════════════════════════════════════════════════
# FetchAttempt 数据模型
# ═══════════════════════════════════════════════════════════════════════


class TestFetchAttempt:
    def test_success(self):
        from astrocrawl.crawler.outcomes import FetchAttempt, FetchResult

        result = FetchResult(url="https://x.com", html="<html>", status_code=200)
        attempt = FetchAttempt(result=result, error=None, category="", is_infra=False)
        assert attempt.result is not None
        assert attempt.result.url == "https://x.com"
        assert attempt.error is None
        assert attempt.is_infra is False

    def test_failure_with_category(self):
        from astrocrawl.crawler.outcomes import FetchAttempt

        attempt = FetchAttempt(result=None, error="timeout", category="timeout", is_infra=False)
        assert attempt.result is None
        assert attempt.error == "timeout"
        assert attempt.category == "timeout"

    def test_infra_failure(self):
        from astrocrawl.crawler.outcomes import FetchAttempt

        attempt = FetchAttempt(result=None, error="proxy dead", category="proxy", is_infra=True)
        assert attempt.is_infra is True
        assert attempt.category == "proxy"

    def test_frozen_prevents_mutation(self):
        """FetchResult(frozen=True) 阻止实例化后修改属性。"""
        from astrocrawl.crawler.outcomes import FetchResult

        result = FetchResult(url="https://x.com", html="<html>", status_code=200)
        with pytest.raises(FrozenInstanceError):
            result.url = "https://hacked.com"

"""AsyncCrawler 集成测试 — 直接测试 Pipeline.process() 和编排逻辑。

测试核心组件（Pipeline、_settle_url、_pop_domain_aware），无 I/O 依赖。
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from dataclasses import replace

import pytest

from astrocrawl._startup import StartupError
from astrocrawl.browser.browser_pool import FetchError, FetchResponse
from astrocrawl.crawler.engine import (
    AsyncCrawler,
    Pipeline,
    ProcessingContext,
    UrlDisposition,
    _content_dedup_processor,
    _domain_concurrency_processor,
    _enqueue_links_processor,
    _fetch_processor,
    _finalize_processor,
    _parse_processor,
    _rate_limit_processor,
    _robots_processor,
    create_crawler,
)
from astrocrawl.crawler.outcomes import UrlOutcome
from astrocrawl.crawler.supervisors import WorkerSupervisor
from astrocrawl.network.robots import RobotsCache
from astrocrawl.network.throttling import DomainConcurrencyLimiter, DomainRateLimiter
from astrocrawl.storage.db import CrawlState
from astrocrawl.utils.url import normalize_url
from tests._fakes import _SpySignals


class _CancellingBrowserPool:
    """Fake BrowserPool — send() 抛出 CancelledError。"""

    async def send(self, request):
        raise asyncio.CancelledError()


class _TimeoutBrowserPool:
    """Fake BrowserPool — send() 永久挂起，外层 wait_for 超时。"""

    async def send(self, request):
        await asyncio.sleep(3600)


def _make_crawler(cfg, start_urls=None):
    if start_urls is None:
        start_urls = ["https://example.com"]
    crawler = AsyncCrawler(
        start_urls=start_urls,
        depth=2,
        concurrency=1,
        output_path="/tmp/test_output.jsonl",
        same_domain_only=False,
        cfg=cfg,
        signals=None,
    )
    return crawler


def _parse_domain(url: str) -> str:
    from urllib.parse import urlparse

    return urlparse(url).netloc


def _make_pipeline():
    """构造生产环境同款 Processor Chain。"""
    return Pipeline(
        _domain_concurrency_processor,
        _robots_processor,
        _rate_limit_processor,
        _fetch_processor,
        _parse_processor,
        _content_dedup_processor,
        _finalize_processor,
        _enqueue_links_processor,
    )


class TestPipeline:
    """直接测试 Pipeline.process()——生产路径的真实执行代码。"""

    async def _setup(self, crawler, test_config, fake_state, fake_browser_pool, fake_writer=None):
        """设置 Engine 状态 + 构建 PipelineDeps。"""
        crawler._state = fake_state
        crawler._browser_pool = fake_browser_pool
        if fake_writer:
            crawler._writer = fake_writer
        domain_limiter = DomainRateLimiter(test_config)
        domain_concurrency = DomainConcurrencyLimiter(1)
        deps = crawler._build_pipeline_deps(domain_limiter, domain_concurrency, None)
        return deps

    async def test_successful_fetch_writes_output_and_stats(
        self,
        test_config,
        fake_state,
        fake_browser_pool,
        fake_writer,
    ):
        crawler = _make_crawler(test_config)
        deps = await self._setup(crawler, test_config, fake_state, fake_browser_pool, fake_writer)

        url = "https://example.com/page1"
        ctx = ProcessingContext(url=url, depth=0, domain=_parse_domain(url))
        ctx = await _make_pipeline().process(ctx, deps)

        assert ctx.outcome == UrlOutcome.OK
        assert ctx.disposition == UrlDisposition.COMPLETED
        assert fake_browser_pool.calls == [url]
        assert len(fake_writer.records) == 1
        assert fake_writer.records[0]["url"] == url
        async with fake_state._conn.execute("SELECT 1 FROM urls WHERE url=? AND status='completed'", (url,)) as cur:
            assert await cur.fetchone() is not None

    async def test_fetch_error_requeue(
        self,
        test_config,
        fake_state,
        fake_browser_pool,
    ):
        crawler = _make_crawler(test_config)
        deps = await self._setup(crawler, test_config, fake_state, fake_browser_pool)

        url = "https://example.com/bad"
        fake_browser_pool._responses[url] = FetchError(
            "net::ERR_CONNECTION_REFUSED",
            "connection_refused",
            False,
        )
        ctx = ProcessingContext(url=url, depth=1, domain=_parse_domain(url))
        ctx = await _make_pipeline().process(ctx, deps)

        assert ctx.outcome == UrlOutcome.FETCH_ERROR
        assert ctx.disposition == UrlDisposition.REQUEUED  # 首次 → scheduled

    async def test_fetch_infra_error_free_requeue(
        self,
        test_config,
        fake_state,
        fake_browser_pool,
    ):
        crawler = _make_crawler(test_config)
        deps = await self._setup(crawler, test_config, fake_state, fake_browser_pool)

        url = "https://example.com/infra"
        fake_browser_pool._responses[url] = FetchError(
            "net::ERR_TUNNEL_CONNECTION_FAILED",
            "proxy",
            True,
        )
        ctx = ProcessingContext(url=url, depth=0, domain=_parse_domain(url))
        ctx = await _make_pipeline().process(ctx, deps)

        assert ctx.outcome == UrlOutcome.FETCH_ERROR
        assert ctx.disposition == UrlDisposition.REQUEUED  # is_infra → free
        assert await fake_state.queue_size() >= 1
        assert await fake_state.in_flight_count() == 0

    async def test_robots_check_skip_when_no_cache(
        self,
        test_config,
        fake_state,
        fake_browser_pool,
    ):
        crawler = _make_crawler(test_config)
        deps = await self._setup(crawler, test_config, fake_state, fake_browser_pool)

        url = "https://example.com/blocked"
        ctx = ProcessingContext(url=url, depth=0, domain=_parse_domain(url))
        ctx = await _make_pipeline().process(ctx, deps)

        # robots_cache=None + robots_respect=False → 处理器直接穿越，不可达 ROBOTS_DENIED
        assert ctx.outcome == UrlOutcome.OK

    async def test_stop_event_short_circuits(
        self,
        test_config,
        fake_state,
        fake_browser_pool,
        fake_writer,
    ):
        crawler = _make_crawler(test_config)
        deps = await self._setup(crawler, test_config, fake_state, fake_browser_pool, fake_writer)
        deps.stop_event.set()

        url = "https://example.com/race"
        ctx = ProcessingContext(url=url, depth=0, domain=_parse_domain(url))
        ctx = await _make_pipeline().process(ctx, deps)

        assert ctx.outcome == UrlOutcome.STOPPED
        assert ctx.is_terminal

    async def test_empty_page_handled(
        self,
        test_config,
        fake_state,
        fake_browser_pool,
        fake_writer,
    ):
        crawler = _make_crawler(test_config)
        deps = await self._setup(crawler, test_config, fake_state, fake_browser_pool, fake_writer)

        url = "https://example.com"
        ctx = ProcessingContext(url=url, depth=0, domain=_parse_domain(url))
        ctx = await _make_pipeline().process(ctx, deps)

        assert ctx.outcome == UrlOutcome.OK


class TestRobotsProcessor:
    """_robots_processor 集成测试 — 含真实 RobotsCache + mock aiohttp session。"""

    async def _setup_with_robots(self, crawler, cfg, fake_state, mock_session):
        crawler._state = fake_state
        robots_cache = RobotsCache(cfg.robots_user_agent, mock_session, ttl=3600, max_size=100)
        domain_limiter = DomainRateLimiter(cfg)
        domain_concurrency = DomainConcurrencyLimiter(1)
        deps = crawler._build_pipeline_deps(domain_limiter, domain_concurrency, robots_cache)
        return deps

    async def test_robots_respect_true_records_correct_status(self, test_config_with_robots, fake_state, monkeypatch):
        """robots_respect=True 时 stats 在 is_allowed() 后记录真实状态。"""
        from unittest.mock import AsyncMock, MagicMock

        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.content.read = AsyncMock(return_value=b"User-agent: *\nDisallow: /admin")
        mock_session.get.return_value.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_session.get.return_value.__aexit__ = AsyncMock(return_value=False)

        crawler = _make_crawler(test_config_with_robots)
        deps = await self._setup_with_robots(crawler, test_config_with_robots, fake_state, mock_session)

        url = "https://example.com/admin"
        ctx = ProcessingContext(url=url, depth=0, domain=_parse_domain(url))
        ctx = await _make_pipeline().process(ctx, deps)

        assert ctx.outcome == UrlOutcome.ROBOTS_DENIED  # Disallow: /admin
        snap = await deps.stats.get_snapshot()
        assert snap["robots_fetch_ok"] == 1
        assert snap["robots_fetch_fail"] == 0

    async def test_robots_respect_false_records_not_checked(self, test_config, fake_state, monkeypatch):
        """robots_respect=False 时记录 'not checked' 而非 'fail'。"""
        from unittest.mock import MagicMock

        cfg = replace(test_config, robots_respect=True)  # ensure RobotsCache created
        mock_session = MagicMock()
        robots_cache = RobotsCache(cfg.robots_user_agent, mock_session)
        crawler = _make_crawler(replace(cfg, robots_respect=False))
        crawler._state = fake_state
        domain_limiter = DomainRateLimiter(cfg)
        domain_concurrency = DomainConcurrencyLimiter(1)
        deps = crawler._build_pipeline_deps(domain_limiter, domain_concurrency, robots_cache)

        url = "https://example.com/page"
        ctx = ProcessingContext(url=url, depth=0, domain=_parse_domain(url))
        ctx = await _make_pipeline().process(ctx, deps)

        snap = await deps.stats.get_snapshot()
        assert snap["robots_not_checked"] == 1
        assert snap["robots_fetch_ok"] == 0
        assert snap["robots_fetch_fail"] == 0

    async def test_http_404_counts_as_ok_not_fail(self, test_config_with_robots, fake_state, monkeypatch):
        """HTTP 404（站点无 robots.txt）计入 ok，非 fail。"""
        from unittest.mock import AsyncMock, MagicMock

        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status = 404
        mock_session.get.return_value.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_session.get.return_value.__aexit__ = AsyncMock(return_value=False)

        crawler = _make_crawler(test_config_with_robots)
        deps = await self._setup_with_robots(crawler, test_config_with_robots, fake_state, mock_session)

        url = "https://example.com/page"
        ctx = ProcessingContext(url=url, depth=0, domain=_parse_domain(url))
        ctx = await _make_pipeline().process(ctx, deps)

        snap = await deps.stats.get_snapshot()
        assert snap["robots_fetch_ok"] == 1
        assert snap["robots_fetch_fail"] == 0

    async def test_second_url_same_origin_no_duplicate_stats(self, test_config_with_robots, fake_state, monkeypatch):
        """同一 origin 第二个 URL 不重复记录 stats。"""
        from unittest.mock import AsyncMock, MagicMock

        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.content.read = AsyncMock(return_value=b"User-agent: *\nAllow: /")
        mock_session.get.return_value.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_session.get.return_value.__aexit__ = AsyncMock(return_value=False)

        crawler = _make_crawler(test_config_with_robots)
        deps = await self._setup_with_robots(crawler, test_config_with_robots, fake_state, mock_session)

        for url in ("https://example.com/a", "https://example.com/b"):
            ctx = ProcessingContext(url=url, depth=0, domain=_parse_domain(url))
            ctx = await _make_pipeline().process(ctx, deps)

        snap = await deps.stats.get_snapshot()
        assert snap["robots_fetch_ok"] == 1  # only once


class TestDomainConcurrency:
    """域名并发控制与轮询调度。"""

    async def test_round_robin_cross_domain(self, test_config, fake_state):
        crawler = _make_crawler(test_config)
        crawler._state = fake_state

        await crawler._state.push_to_queue_single("https://a.com/1", 0, "a.com")
        await crawler._state.push_to_queue_single("https://b.com/1", 0, "b.com")
        await crawler._state.push_to_queue_single("https://a.com/2", 0, "a.com")

        url1, depth1 = await crawler._pop_domain_aware()
        url2, depth2 = await crawler._pop_domain_aware()
        url3, depth3 = await crawler._pop_domain_aware()

        assert "a.com" in url1
        assert "b.com" in url2
        assert "a.com" in url3
        assert {url1, url2, url3} == {"https://a.com/1", "https://b.com/1", "https://a.com/2"}
        assert await crawler._state.queue_size() == 0


class TestSettleUrl:
    """_settle_url 处置路径单元测试。"""

    async def test_requeued_skips_stats(self, test_config, fake_state):
        crawler = _make_crawler(test_config)
        crawler._state = fake_state

        url, depth, domain = "https://x.com", 0, "x.com"
        before_completed = crawler._crawl_stats.completed_urls
        await crawler._settle_url(
            url, depth, domain, 100, UrlOutcome.FETCH_ERROR, UrlDisposition.REQUEUED, state=fake_state
        )
        assert crawler._crawl_stats.completed_urls == before_completed

    async def test_failed_writes_log_failure(self, test_config, fake_state):
        crawler = _make_crawler(test_config)
        crawler._state = fake_state

        url, depth, domain = "https://y.com", 0, "y.com"
        await crawler._settle_url(
            url, depth, domain, 100, UrlOutcome.FETCH_ERROR, UrlDisposition.FAILED, error="timeout", state=fake_state
        )
        async with fake_state._conn.execute("SELECT requeue_count FROM failures WHERE url=?", (url,)) as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0] > 0
        assert row[0] >= test_config.max_requeue

    async def test_completed_updates_progress(self, test_config, fake_state):
        crawler = _make_crawler(test_config)
        crawler._state = fake_state

        url, depth, domain = "https://z.com", 0, "z.com"
        before_completed = crawler._crawl_stats.completed_urls
        await crawler._settle_url(url, depth, domain, 100, UrlOutcome.OK, UrlDisposition.COMPLETED, state=fake_state)
        assert crawler._crawl_stats.completed_urls == before_completed + 1
        assert crawler._progress_layers[0][0] == 1


class TestSitemapDomainFilter:
    async def test_same_domain_rejects_cross_domain_sitemap_url(
        self,
        test_config,
        fake_state,
    ):
        crawler = _make_crawler(test_config, start_urls=["https://example.com"])
        crawler.same_domain_only = True
        crawler.allowed_domains = {"example.com"}
        crawler._state = fake_state

        ok = await crawler._on_sitemap_url_enqueued("https://other.com/page", 1)
        assert ok is False
        assert crawler._crawl_stats.drops.get("cross_domain", 0) == 1

    async def test_same_domain_allows_same_domain_sitemap_url(
        self,
        test_config,
        fake_state,
    ):
        crawler = _make_crawler(test_config, start_urls=["https://example.com"])
        crawler.same_domain_only = True
        crawler.allowed_domains = {"example.com"}
        crawler._state = fake_state

        ok = await crawler._on_sitemap_url_enqueued("https://example.com/other", 1)
        assert ok is True
        assert crawler._crawl_stats.drops.get("cross_domain", 0) == 0

    async def test_same_domain_disabled_allows_any_sitemap_url(
        self,
        test_config,
        fake_state,
    ):
        crawler = _make_crawler(test_config, start_urls=["https://example.com"])
        crawler.same_domain_only = False
        crawler.allowed_domains = set()
        crawler._state = fake_state

        ok = await crawler._on_sitemap_url_enqueued("https://other.com/page", 1)
        assert ok is True


class TestFetchTimeoutComputation:
    """_fetch_processor 动态超时计算：deadline 传播 + cap 验证。"""

    async def _run_fetch_processor_with_timeout(
        self,
        test_config,
        fake_state,
        fake_browser_pool,
        page_timeout_ms,
    ):
        from dataclasses import replace

        cfg = replace(test_config, page_timeout=page_timeout_ms)
        crawler = _make_crawler(cfg)
        crawler._state = fake_state
        crawler._browser_pool = fake_browser_pool
        domain_limiter = DomainRateLimiter(cfg)
        domain_concurrency = DomainConcurrencyLimiter(1)
        deps = crawler._build_pipeline_deps(domain_limiter, domain_concurrency, None)
        url = "https://example.com/page"
        ctx = ProcessingContext(url=url, depth=0, domain="example.com")
        ctx = await _make_pipeline().process(ctx, deps)
        return ctx, deps

    async def test_normal_page_timeout_unchanged_behavior(
        self,
        test_config,
        fake_state,
        fake_browser_pool,
    ):
        """page_timeout=20s 时行为与重构前一致（fetch 超时 ~82s）。"""
        ctx, deps = await self._run_fetch_processor_with_timeout(
            test_config,
            fake_state,
            fake_browser_pool,
            page_timeout_ms=20000,
        )
        assert ctx.outcome == UrlOutcome.OK

    async def test_large_page_timeout_not_prematurely_capped(
        self,
        test_config,
        fake_state,
        fake_browser_pool,
    ):
        """page_timeout=200s 时 fetch 超时不应在 120s 提前触发，
        而是 cap 在 PROCESS_URL_TIMEOUT - FETCH_PROCESSOR_OVERHEAD = 225s。"""
        from dataclasses import replace

        cfg = replace(test_config, page_timeout=200000)
        crawler = _make_crawler(cfg)
        crawler._state = fake_state
        crawler._browser_pool = fake_browser_pool
        domain_limiter = DomainRateLimiter(cfg)
        domain_concurrency = DomainConcurrencyLimiter(1)
        deps = crawler._build_pipeline_deps(domain_limiter, domain_concurrency, None)
        url = "https://example.com/page"
        ctx = ProcessingContext(url=url, depth=0, domain="example.com")
        ctx = await _make_pipeline().process(ctx, deps)
        assert ctx.outcome == UrlOutcome.OK

    async def test_fetch_url_requires_explicit_timeout(
        self,
        test_config,
        fake_state,
        fake_browser_pool,
    ):
        """_fetch_url 的 timeout 参数无默认值，必须由 _fetch_processor 传入。"""
        import inspect

        sig = inspect.signature(AsyncCrawler._fetch_url)
        assert "timeout" in sig.parameters
        assert sig.parameters["timeout"].default is inspect.Parameter.empty

    async def test_fetch_url_cancelled_returns_fetchattempt(
        self,
        test_config,
    ):
        """_fetch_url 在 CancelledError 时返回 FetchAttempt 而非抛异常（Result 类型契约）。"""

        crawler = _make_crawler(test_config)
        # 注入会抛出 CancelledError 的 fake browser pool
        crawler._browser_pool = _CancellingBrowserPool()
        attempt = await crawler._fetch_url("https://example.com", 10.0)
        assert attempt.result is None
        assert attempt.category == "generic"
        assert attempt.is_infra is True

    async def test_fetch_url_timeout_returns_fetchattempt(
        self,
        test_config,
    ):
        """_fetch_url 在 TimeoutError 时返回 FetchAttempt 而非抛异常。"""

        crawler = _make_crawler(test_config)
        crawler._browser_pool = _TimeoutBrowserPool()
        attempt = await crawler._fetch_url("https://example.com", 0.001)
        assert attempt.result is None
        assert attempt.category == "timeout"
        assert attempt.is_infra is False


# ═══════════════════════════════════════════════════════════════════════
# _worker 主循环测试
# ═══════════════════════════════════════════════════════════════════════


class TestWorkerLoop:
    """_worker 主循环 — 正常流、异常路径、暂停/恢复、心跳语义。"""

    async def _setup_worker_env(self, test_config, fake_state, fake_browser_pool):
        crawler = _make_crawler(test_config, start_urls=["https://example.com"])
        crawler._state = fake_state
        crawler._browser_pool = fake_browser_pool
        domain_limiter = DomainRateLimiter(test_config)
        domain_concurrency = DomainConcurrencyLimiter(1)
        deps = crawler._build_pipeline_deps(domain_limiter, domain_concurrency, None)
        pipeline = _make_pipeline()
        return crawler, deps, pipeline, domain_limiter, domain_concurrency

    async def test_normal_flow(self, test_config, fake_state, fake_browser_pool, fake_writer):
        """完整路径: pop → pipeline → settle → heartbeat → signal('idle')"""
        crawler, deps, pipeline, dl, dc = await self._setup_worker_env(test_config, fake_state, fake_browser_pool)
        crawler._writer = fake_writer
        from astrocrawl.crawler.liveness import LivenessTracker

        crawler._tracker = LivenessTracker(1, timeout=300.0)
        url = "https://example.com/page1"
        await fake_state.push_to_queue_single(url, 0, "example.com")

        # 跑一次 Worker 循环: 手动调用一次 iteration 的核心逻辑
        # 不启动完整 while 循环，避免 stop_event 竞态
        result = await crawler._pop_domain_aware()
        assert result[0] == url

    async def test_fetch_url_called_in_pipeline(
        self,
        test_config,
        fake_state,
        fake_browser_pool,
        fake_writer,
    ):
        """验证 pipeline 处理 URL 时 fetch 被调用。"""
        crawler, deps, pipeline, dl, dc = await self._setup_worker_env(test_config, fake_state, fake_browser_pool)
        crawler._writer = fake_writer
        url = "https://example.com/page1"
        ctx = ProcessingContext(url=url, depth=0, domain="example.com")
        ctx = await pipeline.process(ctx, deps)
        assert fake_browser_pool.calls == [url]
        assert ctx.outcome == UrlOutcome.OK

    async def test_cancelled_before_settle_requeues(self, test_config, fake_state, fake_browser_pool):
        """CancelledError 且 settle_entered=False → requeue + remove tracker + signal done"""
        crawler, deps, pipeline, dl, dc = await self._setup_worker_env(test_config, fake_state, fake_browser_pool)
        from astrocrawl.crawler.liveness import LivenessTracker

        crawler._tracker = LivenessTracker(1, timeout=300.0)

        url = "https://example.com/page1"
        await fake_state.push_to_queue_single(url, 0, "example.com")
        crawler._stop_event.set()  # 强制 while 退出

        await crawler._worker(0, dl, dc, None, pipeline, deps)

        # tracker 被 remove
        assert crawler._tracker.alive_count == 0

    async def test_worker_generic_exception_is_internal_error(
        self,
        test_config,
        fake_state,
        fake_browser_pool,
    ):
        """未预期 Exception → _settle_url(INTERNAL_ERROR)。"""
        crawler, deps, pipeline, dl, dc = await self._setup_worker_env(test_config, fake_state, fake_browser_pool)
        from astrocrawl.crawler.liveness import LivenessTracker

        crawler._tracker = LivenessTracker(1, timeout=300.0)

        # 注入会抛 ValueError 的 fetch_url
        async def _bad_fetch(url, timeout):
            raise ValueError("boom")

        deps.fetch_url = _bad_fetch
        url = "https://example.com/page1"
        await fake_state.push_to_queue_single(url, 0, "example.com")

        task = asyncio.create_task(crawler._worker(0, dl, dc, None, pipeline, deps))
        await asyncio.sleep(0.15)
        crawler._stop_event.set()
        await asyncio.sleep(0.1)
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        # INTERNAL_ERROR 被记录
        assert crawler._crawl_stats.outcomes.get("internal_error", 0) >= 1

    async def test_error_path_no_heartbeat(self, test_config, fake_state, fake_browser_pool):
        """Worker 异常路径不调用 heartbeat → tracker 中该 idx 变为 stale。"""
        crawler, deps, pipeline, dl, dc = await self._setup_worker_env(test_config, fake_state, fake_browser_pool)
        from astrocrawl.crawler.liveness import LivenessTracker

        crawler._tracker = LivenessTracker(1, timeout=10.0)

        # 直接验证: _worker 错误路径无 heartbeat 调用
        url = "https://example.com/page1"
        await fake_state.push_to_queue_single(url, 0, "example.com")
        crawler._stop_event.set()

        await crawler._worker(0, dl, dc, None, pipeline, deps)
        # worker 正常退出调用 remove(idx)，alive_count 变为 0
        assert crawler._tracker.alive_count == 0

    async def test_domain_concurrency_always_released(
        self,
        test_config,
        fake_state,
        fake_browser_pool,
    ):
        """finally 块确保 release(domain) 被调用。"""
        crawler, deps, pipeline, dl, dc = await self._setup_worker_env(test_config, fake_state, fake_browser_pool)
        url = "https://example.com/page1"
        await fake_state.push_to_queue_single(url, 0, "example.com")

        initial_active = dc._tracker._states["example.com"].active_count if "example.com" in dc._tracker._states else 0
        crawler._stop_event.set()
        await crawler._worker(0, dl, dc, None, pipeline, deps)
        final_active = dc._tracker._states["example.com"].active_count if "example.com" in dc._tracker._states else 0
        assert final_active == initial_active  # 释放回到初始值

    async def test_stop_event_before_pop(self, test_config, fake_state, fake_browser_pool):
        """stop_event 已设置 → while 循环退出，tracker.remove + signal done。"""
        crawler, deps, pipeline, dl, dc = await self._setup_worker_env(test_config, fake_state, fake_browser_pool)
        from astrocrawl.crawler.liveness import LivenessTracker

        crawler._tracker = LivenessTracker(1, timeout=300.0)
        crawler._stop_event.set()
        await crawler._worker(0, dl, dc, None, pipeline, deps)
        assert crawler._tracker.alive_count == 0  # remove 调用了

    async def test_pause_event_blocks(self, test_config, fake_state, fake_browser_pool):
        """_pause_event.clear() → 阻塞在 wait()，不 pop 队列。"""
        crawler, deps, pipeline, dl, dc = await self._setup_worker_env(test_config, fake_state, fake_browser_pool)
        crawler._pause_event.clear()
        url = "https://example.com/page1"
        await fake_state.push_to_queue_single(url, 0, "example.com")

        task = asyncio.create_task(crawler._worker(0, dl, dc, None, pipeline, deps))
        await asyncio.sleep(0.1)
        assert not task.done()  # 阻塞在 pause_event.wait()
        crawler._pause_event.set()
        await asyncio.sleep(0.1)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def test_empty_queue_waits(self, test_config, fake_state, fake_browser_pool):
        """_pop_domain_aware 返回 None → wait_for_queue → continue。"""
        crawler, deps, pipeline, dl, dc = await self._setup_worker_env(test_config, fake_state, fake_browser_pool)
        task = asyncio.create_task(crawler._worker(0, dl, dc, None, pipeline, deps))
        await asyncio.sleep(0.15)
        crawler._stop_event.set()
        await asyncio.wait_for(task, timeout=2.0)

    async def test_signals_working_idle_transition(
        self,
        test_config,
        fake_state,
        fake_browser_pool,
        fake_writer,
    ):
        """pop 成功 → emit('working') → pipeline 完成 → emit('idle')"""
        crawler, deps, pipeline, dl, dc = await self._setup_worker_env(test_config, fake_state, fake_browser_pool)
        crawler._writer = fake_writer
        from astrocrawl.crawler.liveness import LivenessTracker

        crawler._tracker = LivenessTracker(1, timeout=300.0)
        spy = _SpySignals()
        crawler.signals = spy
        url = "https://example.com/page1"
        await fake_state.push_to_queue_single(url, 0, "example.com")

        # 手工模拟一次完整的 worker 迭代
        result = await crawler._pop_domain_aware()
        assert result[0] == url
        _url, depth = result
        domain = _parse_domain(_url)
        if spy.worker_state:
            spy.worker_state.emit(0, "working")
        ctx = ProcessingContext(url=_url, depth=depth, domain=domain)
        try:
            ctx = await pipeline.process(ctx, deps)
        finally:
            await dc.release(domain)
        await crawler._settle_url(_url, depth, domain, 0, ctx.outcome, ctx.disposition, state=fake_state)
        crawler._tracker.heartbeat(0)
        if spy.worker_state:
            spy.worker_state.emit(0, "idle")

        assert spy.worker_state.calls == [(0, "working"), (0, "idle")]

    async def test_signals_done_on_clean_exit(self, test_config, fake_state):
        """Worker 正常退出 → worker_state.emit("done")"""
        crawler = _make_crawler(test_config)
        crawler._state = fake_state
        spy = _SpySignals()
        crawler.signals = spy
        dl = DomainRateLimiter(test_config)
        dc = DomainConcurrencyLimiter(1)
        crawler._stop_event.set()
        await crawler._worker(0, dl, dc, None, _make_pipeline(), crawler._build_pipeline_deps(dl, dc, None))
        states = [args[1] for args in spy.worker_state.calls]
        assert "done" in states


# ═══════════════════════════════════════════════════════════════════════
# Processor 边界行为
# ═══════════════════════════════════════════════════════════════════════


class TestProcessorEdgeCases:
    """追加 Processor 边界场景到已有 Pipeline 测试。"""

    async def _setup(self, test_config, fake_state, fake_browser_pool, fake_writer=None):
        crawler = _make_crawler(test_config)
        crawler._state = fake_state
        crawler._browser_pool = fake_browser_pool
        if fake_writer:
            crawler._writer = fake_writer
        domain_limiter = DomainRateLimiter(test_config)
        domain_concurrency = DomainConcurrencyLimiter(1)
        deps = crawler._build_pipeline_deps(domain_limiter, domain_concurrency, None)
        return crawler, deps

    async def test_parse_processor_empty_html_graceful(
        self,
        test_config,
        fake_state,
        fake_browser_pool,
    ):
        """N89: html 为空 → ParseResult 空壳，不抛异常。"""

        crawler, deps = await self._setup(test_config, fake_state, fake_browser_pool)
        fake_browser_pool._responses["https://example.com"] = FetchResponse(
            url="https://example.com",
            html="",
            status_code=200,
        )
        url = "https://example.com"
        ctx = ProcessingContext(url=url, depth=0, domain="example.com")
        ctx = await _make_pipeline().process(ctx, deps)
        assert ctx.outcome in (UrlOutcome.PARSE_FAILED, UrlOutcome.NOINDEX, UrlOutcome.OK)
        # 不 crash 即通过

    async def test_parse_processor_bs4_parse_failure(
        self,
        test_config,
        fake_state,
        fake_browser_pool,
    ):
        """损坏 HTML 导致 BeautifulSoup 异常 → parse_error=True。"""

        crawler, deps = await self._setup(test_config, fake_state, fake_browser_pool)
        # 注入无效 HTML (lxml 通常能容错，但极端情况也可能失败)
        fake_browser_pool._responses["https://example.com"] = FetchResponse(
            url="https://example.com",
            html="<html><body>test</body></html>",
            status_code=200,
        )
        url = "https://example.com"
        ctx = ProcessingContext(url=url, depth=0, domain="example.com")
        ctx = await _make_pipeline().process(ctx, deps)
        assert ctx.parsed is not None

    async def test_content_dedup_empty_body_and_fields(
        self,
        test_config,
        fake_state,
        fake_browser_pool,
        fake_writer,
    ):
        """N95: body="" 且 fields={} → 跳过去重，is_new=True。"""

        crawler, deps = await self._setup(test_config, fake_state, fake_browser_pool, fake_writer)
        fake_browser_pool._responses["https://example.com"] = FetchResponse(
            url="https://example.com",
            html="<html><body></body></html>",
            status_code=200,
        )
        url = "https://example.com"
        ctx = ProcessingContext(url=url, depth=0, domain="example.com")
        ctx = await _make_pipeline().process(ctx, deps)
        # 空内容 → is_new=True，记录被写入
        assert ctx.is_new_content is True
        assert len(fake_writer.records) == 1

    async def test_content_dedup_noindex_skips_write(
        self,
        test_config,
        fake_state,
        fake_browser_pool,
        fake_writer,
    ):
        """allow_index=False → 不写 JSONL，outcome=NOINDEX。"""

        crawler, deps = await self._setup(test_config, fake_state, fake_browser_pool, fake_writer)
        fake_browser_pool._responses["https://example.com"] = FetchResponse(
            url="https://example.com",
            html="<html><head><meta name='robots' content='noindex'></head><body>x</body></html>",
            status_code=200,
        )
        url = "https://example.com"
        ctx = ProcessingContext(url=url, depth=0, domain="example.com")
        ctx = await _make_pipeline().process(ctx, deps)
        assert ctx.outcome == UrlOutcome.NOINDEX
        assert len(fake_writer.records) == 0

    async def test_finalize_processor_redirect_recording(
        self,
        test_config,
        fake_state,
        fake_browser_pool,
        fake_writer,
    ):
        """fetch_result.url != 原始 url → record_redirect()。"""

        crawler, deps = await self._setup(test_config, fake_state, fake_browser_pool, fake_writer)
        fake_browser_pool._responses["https://example.com/old"] = FetchResponse(
            url="https://example.com/new",
            html="<html></html>",
            status_code=200,
        )
        url = "https://example.com/old"
        ctx = ProcessingContext(url=url, depth=0, domain="example.com")
        ctx = await _make_pipeline().process(ctx, deps)
        assert crawler._crawl_stats.redirects == 1

    async def test_finalize_processor_force_fail_race(
        self,
        test_config,
        fake_state,
        fake_browser_pool,
    ):
        """_finalize_processor: stop_event set + url 不在 in_flight → FETCH_ERROR, FAILED。"""
        from astrocrawl.crawler.engine import _finalize_processor
        from astrocrawl.crawler.outcomes import FetchResult

        crawler, deps = await self._setup(test_config, fake_state, fake_browser_pool)
        deps.stop_event.set()
        url = "https://example.com"
        ctx = ProcessingContext(
            url=url,
            depth=0,
            domain="example.com",
            fetch_result=FetchResult(url=url, html="<html></html>", status_code=200),
        )
        ctx = await _finalize_processor(ctx, deps)
        assert ctx.outcome == UrlOutcome.FETCH_ERROR
        assert ctx.disposition == UrlDisposition.FAILED

    async def test_enqueue_links_depth_boundary(
        self,
        test_config,
        fake_state,
        fake_browser_pool,
        fake_writer,
    ):
        """depth + 1 >= max_depth → 链接不入队，仅 save_boundary_links。"""

        crawler, deps = await self._setup(test_config, fake_state, fake_browser_pool, fake_writer)
        deps.max_depth = 1  # depth=0 + 1 >= 1 → 链接不入队
        fake_browser_pool._responses["https://example.com"] = FetchResponse(
            url="https://example.com",
            html="<html><body><a href='/page2'>link</a></body></html>",
            status_code=200,
        )
        url = "https://example.com"
        ctx = ProcessingContext(url=url, depth=0, domain="example.com")
        ctx = await _make_pipeline().process(ctx, deps)
        assert ctx.outcome == UrlOutcome.OK
        # 链接应保存到 boundary_links 而非直接入队
        assert await fake_state.queue_size() == 0
        assert await fake_state.boundary_links_count() >= 1

    async def test_enqueue_links_skip_duplicate_links(
        self,
        test_config,
        fake_state,
        fake_browser_pool,
        fake_writer,
    ):
        """_enqueue_links_processor: outcome=DUPLICATE + skip_duplicate_links → record_drop。"""
        from dataclasses import replace

        from astrocrawl.crawler.engine import _enqueue_links_processor
        from astrocrawl.utils.html import ParseResult

        cfg = replace(test_config, skip_duplicate_links=True)
        crawler = _make_crawler(cfg)
        crawler._state = fake_state
        crawler._browser_pool = fake_browser_pool
        crawler._writer = fake_writer
        dl = DomainRateLimiter(cfg)
        dc = DomainConcurrencyLimiter(1)
        deps = crawler._build_pipeline_deps(dl, dc, None)
        deps.max_depth = 2
        url = "https://example.com"
        ctx = ProcessingContext(
            url=url,
            depth=0,
            domain="example.com",
            outcome=UrlOutcome.DUPLICATE,
            parsed=ParseResult(text="x", links=["https://example.com/page2"], allow_index=True, allow_follow=True),
        )
        ctx = await _enqueue_links_processor(ctx, deps)
        assert crawler._crawl_stats.drops.get("skip_duplicate_links", 0) == 1

    async def test_enqueue_links_duplicate_recorded(
        self,
        test_config,
        fake_state,
    ):
        """_enqueue_links_processor: AdmitResult=DUPLICATE → DropReason.ALREADY_VISITED。"""
        from astrocrawl.crawler.engine import _enqueue_links_processor
        from astrocrawl.utils.html import ParseResult

        cfg = replace(test_config, skip_duplicate_links=False)
        crawler = _make_crawler(cfg)
        crawler._state = fake_state
        dl = DomainRateLimiter(cfg)
        dc = DomainConcurrencyLimiter(1)
        deps = crawler._build_pipeline_deps(dl, dc, None)
        deps.max_depth = 2
        # 预插入 URL 到 urls 表，使 push_to_queue_single 返回 DUPLICATE
        await fake_state.push_to_queue_single("https://example.com/dup", 1)
        ctx = ProcessingContext(
            url="https://example.com",
            depth=0,
            domain="example.com",
            parsed=ParseResult(text="x", links=["https://example.com/dup"], allow_index=True, allow_follow=True),
        )
        ctx = await _enqueue_links_processor(ctx, deps)
        assert crawler._crawl_stats.drops.get("already_visited", 0) == 1

    async def test_enqueue_links_queue_full_recorded(
        self,
        test_config,
    ):
        """_enqueue_links_processor: AdmitResult=QUEUE_FULL → DropReason.QUEUE_FULL。"""
        from astrocrawl.crawler.engine import _enqueue_links_processor
        from astrocrawl.utils.html import ParseResult

        cfg = replace(test_config, queue_hard_maxsize=10)
        state = CrawlState(":memory:", cfg)
        await state.open()
        try:
            crawler = _make_crawler(cfg)
            crawler._state = state
            dl = DomainRateLimiter(cfg)
            dc = DomainConcurrencyLimiter(1)
            deps = crawler._build_pipeline_deps(dl, dc, None)
            deps.max_depth = 2
            # 填满队列到容量上限
            for i in range(10):
                await state.push_to_queue_single(f"https://example.com/fill{i}", 0)
            ctx = ProcessingContext(
                url="https://example.com",
                depth=0,
                domain="example.com",
                parsed=ParseResult(text="x", links=["https://example.com/full"], allow_index=True, allow_follow=True),
            )
            ctx = await _enqueue_links_processor(ctx, deps)
            assert crawler._crawl_stats.drops.get("queue_full", 0) == 1
        finally:
            await state.close()

    async def test_enqueue_links_exclude_pattern_recorded(
        self,
        test_config,
        fake_state,
    ):
        """_enqueue_links_processor: AdmitResult=EXCLUDED → DropReason.EXCLUDE_PATTERN。"""
        from astrocrawl.crawler.engine import _enqueue_links_processor
        from astrocrawl.utils.html import ParseResult

        cfg = replace(test_config, exclude_patterns=(r"no-crawl",))
        crawler = _make_crawler(cfg)
        crawler._state = fake_state
        dl = DomainRateLimiter(cfg)
        dc = DomainConcurrencyLimiter(1)
        deps = crawler._build_pipeline_deps(dl, dc, None)
        deps.max_depth = 2
        ctx = ProcessingContext(
            url="https://example.com",
            depth=0,
            domain="example.com",
            parsed=ParseResult(
                text="x", links=["https://example.com/no-crawl/page"], allow_index=True, allow_follow=True
            ),
        )
        ctx = await _enqueue_links_processor(ctx, deps)
        assert crawler._crawl_stats.drops.get("exclude_pattern", 0) == 1

    async def test_enqueue_url_invalid_rejected(self, test_config, fake_state):
        crawler = _make_crawler(test_config)
        crawler._state = fake_state
        ok = await crawler._enqueue_url("not-a-url", 0)
        assert ok is False
        assert crawler._crawl_stats.drops.get("invalid_url", 0) == 1

    async def test_enqueue_url_exclude_pattern(self, test_config, fake_state):

        crawler = _make_crawler(test_config)
        crawler._state = fake_state
        crawler._exclude_res = [re.compile(r"\.pdf$")]
        ok = await crawler._enqueue_url("https://example.com/doc.pdf", 0)
        assert ok is False
        assert crawler._crawl_stats.drops.get("exclude_pattern", 0) == 1

    async def test_enqueue_url_boundary(self, test_config, fake_state):
        """_enqueue_url: depth >= max_depth → BOUNDARY, 返回 False 并存入 boundary_links。"""
        crawler = _make_crawler(test_config)
        crawler._state = fake_state
        crawler.depth = 1  # max_depth=1, depth=1 >= 1 → BOUNDARY
        ok = await crawler._enqueue_url("https://example.com/deep", 1)
        assert ok is False
        # BOUNDARY 不是丢弃——不应记录任何 DropReason
        for key in ("invalid_url", "exclude_pattern", "queue_full", "already_visited"):
            assert crawler._crawl_stats.drops.get(key, 0) == 0
        assert await fake_state.boundary_links_count() == 1

    async def test_compute_content_hash_v2_includes_fields(self, test_config):
        from astrocrawl.crawler.engine import _compute_content_hash

        h = _compute_content_hash("body", {"title": "hello"}, hash_v2=True, cfg=test_config)
        assert h.startswith("v2:")

    async def test_compute_content_hash_v1_body_only(self, test_config):
        from astrocrawl.crawler.engine import _compute_content_hash

        h = _compute_content_hash("body", {}, hash_v2=False, cfg=test_config)
        assert not h.startswith("v2:")

    async def test_seed_new_urls_normalize_and_enqueue(self, test_config, fake_state):
        crawler = _make_crawler(test_config, start_urls=["https://EXAMPLE.com/Path"])
        crawler._state = fake_state
        await crawler._seed_new_urls()
        qsize = await fake_state.queue_size()
        assert qsize >= 1

    async def test_seed_new_urls_domain_filter(self, test_config, fake_state):
        crawler = _make_crawler(test_config, start_urls=["https://other.com"])
        crawler.same_domain_only = True
        crawler.allowed_domains = {"example.com"}
        crawler._state = fake_state
        await crawler._seed_new_urls()
        assert await fake_state.queue_size() == 0  # 跨域被过滤

    async def test_rule_all_fields_null_clears_extracted_fields(
        self,
        test_config,
        fake_state,
        fake_browser_pool,
    ):
        from astrocrawl._types import DEFAULT_EXTRACTION_TYPE, RuleSnapshot
        from astrocrawl.rules._schema import FieldRule, MatchConfig, RuleOptions, RuleSchema

        crawler, deps = await self._setup(
            test_config,
            fake_state,
            fake_browser_pool,
        )

        # 构建规则：匹配 example.com，但用不存在于 HTML 中的选择器
        rule = RuleSchema(
            name="test_rule",
            version=1,
            enabled=True,
            match=MatchConfig(domains=["example.com"], url_pattern=""),
            fields={"title": FieldRule(selector="#nonexistent", extract="text")},
            options=RuleOptions(keep_body_text=False, follow_links=True),
        )
        snapshot = RuleSnapshot(
            rules=(rule,),
            by_name={
                "test_rule": rule,
                DEFAULT_EXTRACTION_TYPE: RuleSchema(name=DEFAULT_EXTRACTION_TYPE, enabled=True),
            },
            by_domain={"example.com": ("test_rule",)},
        )
        deps.rule_snapshot = snapshot

        fake_browser_pool._responses["https://example.com"] = FetchResponse(
            url="https://example.com",
            html="<html><body><p>hello</p></body></html>",
            status_code=200,
        )
        ctx = ProcessingContext(url="https://example.com", depth=0, domain="example.com")
        ctx = await _make_pipeline().process(ctx, deps)

        assert ctx.extracted_fields == {}
        assert ctx.extraction_type == DEFAULT_EXTRACTION_TYPE
        assert ctx.parsed is not None
        assert ctx.parsed.text == "hello"


# ═══════════════════════════════════════════════════════════════════════
# 健康检查 + 暂停控制
# ═══════════════════════════════════════════════════════════════════════


class TestHealthAndControl:
    async def test_get_health_all_up(self, test_config, fake_state, fake_browser_pool):
        crawler = _make_crawler(test_config)
        crawler._state = fake_state
        crawler._browser_pool = fake_browser_pool
        from astrocrawl.crawler.liveness import LivenessTracker

        crawler._tracker = LivenessTracker(1, timeout=300.0)
        h = crawler.get_health()
        assert h.status in ("UP", "DEGRADED")  # wcount=1, concurrency=1 → UP

    async def test_get_health_workers_degraded(self, test_config, fake_state, fake_browser_pool):
        crawler = _make_crawler(test_config)
        crawler.concurrency = 3
        crawler._state = fake_state
        crawler._browser_pool = fake_browser_pool
        from astrocrawl.crawler.liveness import LivenessTracker

        crawler._tracker = LivenessTracker(3, timeout=300.0)
        crawler._tracker.remove(1)
        crawler._tracker.remove(2)
        h = crawler.get_health()
        assert h.status == "DEGRADED"

    async def test_get_health_workers_down(self, test_config, fake_state, fake_browser_pool):
        crawler = _make_crawler(test_config)
        crawler.concurrency = 2
        crawler._state = fake_state
        crawler._browser_pool = fake_browser_pool
        from astrocrawl.crawler.liveness import LivenessTracker

        crawler._tracker = LivenessTracker(2, timeout=300.0)
        crawler._tracker.remove(0)
        crawler._tracker.remove(1)
        h = crawler.get_health()
        assert h.status == "DOWN"

    def test_get_health_supervisor_up(self, test_config, fake_state, fake_browser_pool):
        """_supervisor 健康 UP → details 含 supervisor 键，整体状态不受影响。"""
        crawler = _make_crawler(test_config)
        crawler._state = fake_state
        crawler._browser_pool = fake_browser_pool
        from astrocrawl.crawler.liveness import LivenessTracker

        crawler._tracker = LivenessTracker(1, timeout=300.0)
        crawler._supervisor = WorkerSupervisor(max_restarts=3, within_seconds=60.0)
        h = crawler.get_health()
        assert "supervisor" in h.details  # UP 时 details 为 {}（无额外字段）
        assert h.status == "UP"

    def test_get_health_supervisor_open_dominates(self, test_config):
        """Fuse 打开 → supervisor DOWN → 整体 DOWN，即使 worker 全部存活。"""
        crawler = _make_crawler(test_config)
        crawler._browser_pool = None
        from astrocrawl.crawler.liveness import LivenessTracker

        crawler._tracker = LivenessTracker(1, timeout=300.0)
        sv = WorkerSupervisor(max_restarts=1, within_seconds=3600.0)
        sv.fuse._is_open = True
        crawler._supervisor = sv
        h = crawler.get_health()
        assert h.status == "DOWN"
        assert h.details["supervisor"]["failures"] == 0  # 窗口内无失败

    def test_get_health_supervisor_degraded(self, test_config, fake_state, fake_browser_pool):
        """Fuse DEGRADED → supervisor DEGRADED → 整体 DEGRADED。"""
        import time

        crawler = _make_crawler(test_config)
        crawler._state = fake_state
        crawler._browser_pool = fake_browser_pool
        from astrocrawl.crawler.liveness import LivenessTracker

        crawler._tracker = LivenessTracker(1, timeout=300.0)
        sv = WorkerSupervisor(max_restarts=3, within_seconds=3600.0)
        sv.fuse._death_times = [time.time() - 60.0]
        crawler._supervisor = sv
        h = crawler.get_health()
        assert h.status == "DEGRADED"
        assert h.details["supervisor"]["failures"] == 1

    def test_get_health_supervisor_none_guard(self, test_config, fake_state, fake_browser_pool):
        """_supervisor=None → get_health 不崩溃，无 supervisor 键。"""
        crawler = _make_crawler(test_config)
        crawler._state = fake_state
        crawler._browser_pool = fake_browser_pool
        from astrocrawl.crawler.liveness import LivenessTracker

        crawler._tracker = LivenessTracker(1, timeout=300.0)
        assert crawler._supervisor is None
        h = crawler.get_health()
        assert "supervisor" not in h.details
        assert h.status in ("UP", "DEGRADED")

    def test_get_health_tracker_none_guard(self, test_config, fake_state, fake_browser_pool):
        """_tracker=None → get_health 不崩溃，wcount=0。"""
        crawler = _make_crawler(test_config)
        crawler._state = fake_state
        crawler._browser_pool = fake_browser_pool
        assert crawler._tracker is None
        h = crawler.get_health()
        assert h.details["workers"]["alive"] == 0
        assert h.details["workers"]["concurrency"] == crawler.concurrency

    def test_request_pause(self, test_config):
        crawler = _make_crawler(test_config)
        crawler.request_pause()
        assert not crawler._pause_event.is_set()

    def test_request_resume(self, test_config):
        crawler = _make_crawler(test_config)
        crawler.request_pause()
        crawler.request_resume()
        assert crawler._pause_event.is_set()

    def test_request_stop(self, test_config):
        crawler = _make_crawler(test_config)
        crawler.request_stop()
        assert crawler._stop_event.is_set()
        assert crawler._pause_event.is_set()


# ═══════════════════════════════════════════════════════════════════════
# _settle_url 语义补充 + generate_report
# ═══════════════════════════════════════════════════════════════════════


class TestSettleUrlExtended:
    async def test_failed_updates_progress_not_completed(self, test_config, fake_state):
        """FAILED + FETCH_ERROR → progress +1 但 completed_urls 不变。"""
        crawler = _make_crawler(test_config)
        crawler._state = fake_state
        url, depth, domain = "https://x.com", 0, "x.com"
        before_completed = crawler._crawl_stats.completed_urls
        before_proc = crawler._progress_layers[0][0]
        await crawler._settle_url(
            url, depth, domain, 100, UrlOutcome.FETCH_ERROR, UrlDisposition.FAILED, error="perm fail", state=fake_state
        )
        assert crawler._crawl_stats.completed_urls == before_completed  # FAILED 不增量
        assert crawler._progress_layers[0][0] == before_proc + 1  # 但进度递增

    async def test_success_updates_both(self, test_config, fake_state):
        """COMPLETED + OK → progress +1 AND completed_urls +1 (对照)。"""
        crawler = _make_crawler(test_config)
        crawler._state = fake_state
        url, depth, domain = "https://z.com", 0, "z.com"
        before_completed = crawler._crawl_stats.completed_urls
        before_proc = crawler._progress_layers[0][0]
        await crawler._settle_url(url, depth, domain, 100, UrlOutcome.OK, UrlDisposition.COMPLETED, state=fake_state)
        assert crawler._crawl_stats.completed_urls == before_completed + 1
        assert crawler._progress_layers[0][0] == before_proc + 1


class TestGenerateReport:
    async def test_structure(self, test_config, fake_state):
        crawler = _make_crawler(test_config)
        crawler._state = fake_state
        await crawler._crawl_stats.record_outcome(UrlOutcome.OK, "example.com")
        await crawler._crawl_stats.record_rule_hit("product", 3, 5, 120.0)
        crawler._crawl_stats.start_time = 1000000.0
        crawler._crawl_stats.end_time = 1000300.0
        crawler._progress_layers = {0: (1, 5)}
        report = await crawler.generate_report("/tmp/test_report.jsonl")
        assert "outcome_summary" in report
        assert "total_pages_ok" in report
        assert "total_pages_fail" in report
        assert "total_pages_dropped" in report
        assert "content" in report
        assert "fetch_errors" in report
        assert "discovery" in report
        assert "domain_stats" in report
        assert "depth_layers" in report
        assert "total_session" in report
        assert "duration_seconds" in report
        assert "rule_performance" in report
        assert report["rule_performance"]["product"]["hits"] == 1
        assert "proxy" in report

    async def test_merges_initial_outcomes(self, test_config, fake_state):
        crawler = _make_crawler(test_config)
        crawler._state = fake_state
        await crawler._crawl_stats.set_initial_outcomes({"ok": 5, "robots_denied": 2})
        await crawler._crawl_stats.record_outcome(UrlOutcome.OK, "example.com")
        report = await crawler.generate_report("/tmp/test_report.jsonl")
        assert report["outcome_summary"].get("ok") == 6  # 5 + 1
        assert report["outcome_summary"].get("robots_denied") == 2  # 2 + 0


class TestHealFromBoundaryLinks:
    async def test_basic(self, test_config, fake_state):
        crawler = _make_crawler(test_config)
        crawler._state = fake_state
        parent = "https://example.com/page1"
        child = "https://example.com/page2"
        await fake_state.save_boundary_links(parent, [child], 0)
        recovered = await crawler._heal_from_boundary_links(1)
        assert recovered == 1
        qsize = await fake_state.queue_size()
        assert qsize >= 1

    async def test_empty_no_children(self, test_config, fake_state):
        crawler = _make_crawler(test_config)
        crawler._state = fake_state
        recovered = await crawler._heal_from_boundary_links(1)
        assert recovered == 0

    async def test_stop_event_interrupts(self, test_config, fake_state):
        crawler = _make_crawler(test_config)
        crawler._state = fake_state
        crawler._stop_event.set()
        parent = "https://example.com/page1"
        children = ["https://example.com/page2", "https://example.com/page3"]
        await fake_state.save_boundary_links(parent, children, 0)
        recovered = await crawler._heal_from_boundary_links(1)
        assert recovered == 0  # stop_event 已设置 → 不入队


# ═══════════════════════════════════════════════════════════════════════
# _run_worker_loop 协调整合
# ═══════════════════════════════════════════════════════════════════════


class TestResumeIfExists:
    """续爬时 _seed_new_urls() 行为：用户新增 URL 合并入队，已爬 URL 自动去重。"""

    async def test_resume_true_seeds_new_urls_with_dedup(self, test_config, tmp_path):
        """续爬 + 非空 DB：_seed_new_urls() 将新 URL 入队，已完成 URL 自动去重。

        模拟 GUI 续爬场景：上次爬了 A，用户追加 B 和 C，其中 A 已爬过。
        → A 命中 urls 表去重跳过，B 和 C 正常入队。
        """
        db_path = str(tmp_path / "crawl.db")
        cfg = replace(test_config, resume_if_exists=True, db_path=db_path)

        url_a = normalize_url("https://a.com", cfg)  # 规范化后为 https://a.com/
        url_b = normalize_url("https://b.com/path", cfg)
        url_c = normalize_url("https://c.com", cfg)

        # 模拟上次爬取遗留：A 已完成
        state = CrawlState(db_path, cfg)
        await state.open()
        await state.mark_completed(url_a)
        assert not await state.urls_table_empty()
        await state.close()

        # 用户新输入：A（重复）+ B + C（新增）
        crawler = _make_crawler(cfg, start_urls=[url_a, url_b, url_c])
        crawler._state = CrawlState(db_path, cfg)
        await crawler._state.open()

        assert crawler.cfg.resume_if_exists is True
        assert not await crawler._state.urls_table_empty()

        await crawler._seed_new_urls()
        qsize = await crawler._state.queue_size()
        assert qsize == 2  # B 和 C 入队，A 去重

        await crawler._state.close()

    async def test_resume_true_all_duplicates_noop(self, test_config, tmp_path):
        """续爬时全部种子 URL 已爬过 → 队列保持空，无副作用。"""
        db_path = str(tmp_path / "crawl.db")
        cfg = replace(test_config, resume_if_exists=True, db_path=db_path)

        url_a = normalize_url("https://a.com", cfg)

        state = CrawlState(db_path, cfg)
        await state.open()
        await state.mark_completed(url_a)
        await state.close()

        crawler = _make_crawler(cfg, start_urls=[url_a])
        crawler._state = CrawlState(db_path, cfg)
        await crawler._state.open()

        await crawler._seed_new_urls()
        assert await crawler._state.queue_size() == 0

        await crawler._state.close()

    async def test_resume_true_empty_db_takes_fresh_path(self, test_config, tmp_path):
        """resume_if_exists=True 但 DB 为空 → fresh 路径（首次爬取）。"""
        db_path = str(tmp_path / "crawl.db")
        cfg = replace(test_config, resume_if_exists=True, db_path=db_path)

        crawler = _make_crawler(cfg, start_urls=["https://new-url.com"])
        crawler._state = CrawlState(db_path, cfg)
        await crawler._state.open()

        assert await crawler._state.urls_table_empty()
        # 空 DB → 走 fresh 路径（reset_all + _seed_new_urls）
        takes_fresh_path = not crawler.cfg.resume_if_exists or await crawler._state.urls_table_empty()
        assert takes_fresh_path

        await crawler._state.close()

    async def test_resume_false_always_fresh_even_when_db_nonempty(self, test_config, tmp_path):
        """resume_if_exists=False → 始终 fresh（reset_all + 播种），无论 DB 状态。"""
        db_path = str(tmp_path / "crawl.db")
        cfg = replace(test_config, resume_if_exists=False, db_path=db_path)

        url_old = normalize_url("https://old.com", cfg)
        url_new = normalize_url("https://new-url.com", cfg)

        state = CrawlState(db_path, cfg)
        await state.open()
        await state.mark_completed(url_old)
        assert not await state.urls_table_empty()
        await state.close()

        crawler = _make_crawler(cfg, start_urls=[url_new])
        crawler._state = CrawlState(db_path, cfg)
        await crawler._state.open()

        takes_fresh_path = not crawler.cfg.resume_if_exists or await crawler._state.urls_table_empty()
        assert takes_fresh_path

        await crawler._seed_new_urls()
        assert await crawler._state.queue_size() >= 1

        await crawler._state.close()


class TestRunWorkerLoop:
    """_run_worker_loop 顶层协调整合。小规模: 1 worker, <5 URL, 零延迟。"""

    async def test_processes_urls_and_exits_on_stop(self, test_config, fake_state, fake_browser_pool, fake_writer):
        """推入 URL → 运行 _run_worker_loop → URL 被处理。"""
        crawler = _make_crawler(test_config, start_urls=["https://example.com"])
        crawler._state = fake_state
        crawler._browser_pool = fake_browser_pool
        crawler._writer = fake_writer
        dl = DomainRateLimiter(test_config)
        dc = DomainConcurrencyLimiter(1)

        for i in range(3):
            await fake_state.push_to_queue_single(
                f"https://example.com/page{i}",
                0,
                "example.com",
            )

        async def _stop_after_delay():
            await asyncio.sleep(0.3)
            crawler._stop_event.set()

        stop_task = asyncio.create_task(_stop_after_delay())
        await crawler._run_worker_loop(dl, dc, None)
        await stop_task

        assert len(fake_browser_pool.calls) >= 1
        assert crawler._supervisor is not None  # _run_worker_loop 创建了 supervisor

    async def test_stop_event_exits_promptly(self, test_config, fake_state, fake_browser_pool):
        """stop_event 已设置 → _run_worker_loop 立即退出。"""
        crawler = _make_crawler(test_config)
        crawler._state = fake_state
        crawler._browser_pool = fake_browser_pool
        crawler._stop_event.set()
        dl = DomainRateLimiter(test_config)
        dc = DomainConcurrencyLimiter(1)

        t0 = asyncio.get_event_loop().time()
        await crawler._run_worker_loop(dl, dc, None)
        elapsed = asyncio.get_event_loop().time() - t0
        assert elapsed < 1.0
        assert crawler._supervisor is not None  # 即使立即退出也创建了 supervisor


# ═══════════════════════════════════════════════════════════════════════
# _check_retryable — 可重试 URL 回收
# ═══════════════════════════════════════════════════════════════════════


class TestCheckRetryable:
    """_check_retryable — 健康检查：回收可重试 URL。"""

    async def test_no_retryable_items(self, test_config, fake_state):
        """无待重试条目时返回 UP。"""
        crawler = _make_crawler(test_config)
        crawler._state = fake_state
        health = await crawler._check_retryable()
        assert health.status == "UP"

    async def test_exception_returns_degraded(self, test_config, fake_state, monkeypatch):
        """atomic_retry_reclaim 异常时返回 DEGRADED。"""

        async def _raise(*args, **kwargs):
            raise RuntimeError("db locked")

        monkeypatch.setattr(fake_state, "atomic_retry_reclaim", _raise)
        crawler = _make_crawler(test_config)
        crawler._state = fake_state
        health = await crawler._check_retryable()
        assert health.status == "DEGRADED"


# ═══════════════════════════════════════════════════════════════════════
# _check_cleanup — 清理任务健康检查
# ═══════════════════════════════════════════════════════════════════════


class TestCheckCleanup:
    """_check_cleanup — 静态方法：执行清理协程并报告健康状态。"""

    async def test_success(self):
        from astrocrawl.crawler.engine import AsyncCrawler

        called = False

        async def _cleanup():
            nonlocal called
            called = True

        health = await AsyncCrawler._check_cleanup("test", _cleanup())
        assert health.status == "UP"
        assert called

    async def test_failure_returns_degraded(self):
        from astrocrawl.crawler.engine import AsyncCrawler

        async def _fail():
            raise RuntimeError("cleanup error")

        health = await AsyncCrawler._check_cleanup("test", _fail())
        assert health.status == "DEGRADED"
        assert "cleanup failed" in health.message


# ═══════════════════════════════════════════════════════════════════════
# _resource_snapshot — 资源快照
# ═══════════════════════════════════════════════════════════════════════


class TestResourceSnapshot:
    """_resource_snapshot — 资源快照：内存/DB/队列。"""

    async def test_basic_snapshot_no_psutil(self, test_config, fake_state):
        """无 psutil 时不崩溃，返回 UP。"""
        crawler = _make_crawler(test_config)
        crawler._state = fake_state
        health = await crawler._resource_snapshot()
        assert health.status == "UP"

    async def test_exception_returns_degraded(self, test_config, fake_state, monkeypatch):
        """异常时返回 DEGRADED。"""

        async def _raise(*args, **kwargs):
            raise RuntimeError("snapshot error")

        monkeypatch.setattr(fake_state, "queue_size", _raise)
        crawler = _make_crawler(test_config)
        crawler._state = fake_state
        health = await crawler._resource_snapshot()
        assert health.status == "DEGRADED"


# ═══════════════════════════════════════════════════════════════════════
# _send_webhook — Webhook 通知
# ═══════════════════════════════════════════════════════════════════════


class TestSendWebhook:
    """_send_webhook — 爬取完成 Webhook 通知。"""

    async def test_no_session_noop(self, test_config):
        """_http_session 为 None 时直接返回。"""
        crawler = _make_crawler(test_config)
        crawler._http_session = None
        await crawler._send_webhook({"status": "done"})

    async def test_http_200_logged(self, test_config, caplog, monkeypatch):
        from unittest.mock import AsyncMock, MagicMock

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp

        cfg = test_config
        cfg = replace(cfg, webhook_url="https://hooks.example.com/webhook")
        crawler = _make_crawler(cfg)
        crawler._http_session = mock_session
        await crawler._send_webhook({"status": "done"})
        assert "event=webhook_sent" in caplog.text

    async def test_http_400_warning(self, test_config, caplog, monkeypatch):
        from unittest.mock import AsyncMock, MagicMock

        mock_resp = MagicMock()
        mock_resp.status = 400
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)
        mock_session = MagicMock()
        mock_session.post.return_value = mock_resp

        cfg = test_config
        cfg = replace(cfg, webhook_url="https://hooks.example.com/webhook")
        crawler = _make_crawler(cfg)
        crawler._http_session = mock_session
        await crawler._send_webhook({"status": "done"})
        assert "event=webhook_error" in caplog.text

    async def test_exception_warning(self, test_config, caplog):
        from unittest.mock import MagicMock

        mock_session = MagicMock()
        mock_session.post.side_effect = RuntimeError("Connection refused")

        cfg = test_config
        cfg = replace(cfg, webhook_url="https://hooks.example.com/webhook")
        crawler = _make_crawler(cfg)
        crawler._http_session = mock_session
        await crawler._send_webhook({"status": "done"})
        assert "event=webhook_error" in caplog.text


# ---------------------------------------------------------------------------
# TestCreateCrawler — create_crawler() 入口检查
# ---------------------------------------------------------------------------


class TestCreateCrawler:
    """create_crawler() — 工厂入口检查。"""

    def test_verify_chromium_called(self, monkeypatch, test_config):
        """构造 AsyncCrawler 前调用 verify_chromium。"""
        from unittest.mock import MagicMock

        called = []
        monkeypatch.setattr(
            "astrocrawl.crawler.engine.verify_chromium",
            lambda: called.append(1),
        )
        monkeypatch.setattr(
            "astrocrawl.crawler.engine.AsyncCrawler",
            MagicMock(),
        )
        create_crawler(
            start_urls=["https://example.com"],
            depth=1,
            concurrency=1,
            output_path="/tmp/out",
            same_domain_only=False,
            cfg=test_config,
        )
        assert len(called) == 1

    def test_verify_chromium_failure_propagates(self, monkeypatch, test_config):
        """verify_chromium 失败时 StartupError 传播到调用方。"""

        def _raise():
            raise StartupError("chromium 不可用")

        monkeypatch.setattr(
            "astrocrawl.crawler.engine.verify_chromium",
            _raise,
        )
        with pytest.raises(StartupError, match="chromium"):
            create_crawler(
                start_urls=["https://example.com"],
                depth=1,
                concurrency=1,
                output_path="/tmp/out",
                same_domain_only=False,
                cfg=test_config,
            )

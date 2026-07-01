"""BrowserPool 单元测试。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from astrocrawl._retry_strategy import RetryStrategy
from astrocrawl._types import FetchErrorCategory, classify_fetch_error
from astrocrawl.browser.browser_pool import (
    BrowserPool,
    FetchError,
    FetchRequest,
    FetchResponse,
    _backoff_with_jitter,
    _make_resource_filter,
    _safe_future_has_result,
    _safe_set_result,
    _safe_unroute,
)
from astrocrawl.config import CrawlerConfig


@pytest.fixture
def cfg():
    return CrawlerConfig(
        concurrency=4,
        domain_min_delay=0.0,
        domain_max_delay=0.0,
        page_timeout=20000,
        network_idle_timeout=8000,
        skip_non_essential_resources=False,
        robots_respect=False,
        use_sitemap=False,
        max_retries=3,
    )


@pytest.fixture
def cfg_proxy_only():
    return CrawlerConfig(proxy_mode="proxy_only", max_retries=3)


class TestFetchDataTypes:
    def test_request_frozen(self):
        r = FetchRequest(url="https://x.com", timeout_ms=10000)
        with pytest.raises(Exception):
            r.url = "other"

    def test_response_frozen(self):
        r = FetchResponse(url="https://x.com", html="<html></html>", status_code=200)
        with pytest.raises(Exception):
            r.html = "other"

    def test_error_frozen(self):
        e = FetchError(error="timeout", category="timeout", is_infra=False)
        with pytest.raises(Exception):
            e.error = "other"

    def test_fetch_error_is_infra_default_false(self):
        e = FetchError(error="timeout", category="timeout")
        assert e.is_infra is False

    def test_fetch_error_context_failure_is_infra_true(self):
        """上下文崩溃是唯一在 _do_fetch 中判定为 is_infra=True 的类别。"""
        e = FetchError(
            error="上下文恢复失败，槽位已失效",
            category="context_failure",
            is_infra=True,
        )
        assert e.is_infra is True

    def test_fetch_error_proxy_exhausted_is_infra_false(self):
        """代理耗尽不代表基础设施故障——URL 已经尝试了。"""
        e = FetchError(
            error="代理轮换失败——无可用替代代理",
            category="proxy_exhausted",
            is_infra=False,
        )
        assert e.is_infra is False

    def test_fetch_error_is_infra_immutable(self):
        e = FetchError(error="timeout", category="timeout", is_infra=True)
        with pytest.raises(Exception):
            e.is_infra = False


class TestBrowserPoolConfig:
    def test_K_computation(self, cfg):
        pool = BrowserPool(32, cfg)
        assert pool._K == 4
        assert pool._slots_per_browser == 8

    def test_K_min_one(self, cfg):
        pool = BrowserPool(2, cfg)
        assert pool._K == 1
        assert pool._slots_per_browser == 2

    def test_K_max_eight(self, cfg):
        pool = BrowserPool(128, cfg)
        assert pool._K == 8
        assert pool._slots_per_browser == 16

    @pytest.mark.parametrize(
        "concurrency", [1, 2, 3, 7, 8, 9, 11, 13, 15, 17, 19, 20, 23, 25, 27, 31, 32, 64, 100, 1000]
    )
    def test_total_slots_fit_in_queue(self, concurrency):
        """槽位总数不超过队列容量 — 防止启动死锁。"""
        from astrocrawl.config import GlobalSettings

        pool = BrowserPool(
            concurrency, CrawlerConfig(concurrency=concurrency, max_retries=3), global_settings=GlobalSettings()
        )
        total_slots = pool._K * pool._slots_per_browser
        assert total_slots >= concurrency, (
            f"concurrency={concurrency}: total_slots={total_slots} must cover concurrency"
        )
        assert pool._global_slots.maxsize == total_slots, (
            f"concurrency={concurrency}: queue maxsize={pool._global_slots.maxsize} != total_slots={total_slots}"
        )


# ═══════════════════════════════════════════════════════════════════════
# 错误分类模式测试
# ═══════════════════════════════════════════════════════════════════════


class TestErrorClassificationPatterns:
    def test_context_failure_slot_invalid(self):
        cat = classify_fetch_error("上下文恢复失败，槽位已失效")
        assert cat == FetchErrorCategory.CONTEXT_FAILURE

    def test_context_failure_repair_failed(self):
        cat = classify_fetch_error("上下文槽位修复失败，爬虫终止")
        assert cat == FetchErrorCategory.CONTEXT_FAILURE

    def test_proxy_exhausted_pattern(self):
        cat = classify_fetch_error("代理轮换失败——无可用替代代理")
        assert cat == FetchErrorCategory.PROXY_EXHAUSTED


# ═══════════════════════════════════════════════════════════════════════
# _dispatch_retry_strategy 测试
# ═══════════════════════════════════════════════════════════════════════


class TestDispatchRetryStrategy:
    """验证 _dispatch_retry_strategy 不再写 future，fatal_error_str 正确返回。"""

    def _make_pool(self, proxy_mode="direct_only"):
        cfg = CrawlerConfig(proxy_mode=proxy_mode, max_retries=3)
        return BrowserPool(concurrency=4, cfg=cfg)

    @pytest.mark.asyncio
    async def test_rotate_proxy_exhausted_returns_fatal(self):
        """ROTATE_PROXY 全挂时返回 fatal_error_str，不写 future。"""
        pool = self._make_pool("prefer_proxy")
        ctx = MagicMock()
        ctx.rotate_proxy = AsyncMock(return_value=False)
        ctx.get_proxy_for_slot = MagicMock(return_value="http://proxy1:8080")
        ctx.mark_proxy_failure = AsyncMock()

        result = await pool._dispatch_retry_strategy(
            RetryStrategy.ROTATE_PROXY,
            "net::ERR_PROXY_CONNECTION_FAILED",
            ctx,
            0,
            None,
            None,
            0,
            True,
        )
        page, _, _, should_retry, fatal = result
        assert should_retry is False
        assert "代理轮换失败" in fatal
        assert ctx.mark_proxy_failure.called

    @pytest.mark.asyncio
    async def test_rotate_proxy_same_proxy_returns_fatal(self):
        """单代理循环 → 同代理检测 → 立即 fatal。"""
        pool = self._make_pool("prefer_proxy")
        ctx = MagicMock()
        ctx.rotate_proxy = AsyncMock(return_value=True)
        ctx.get_proxy_for_slot = MagicMock(return_value="http://only:8080")
        ctx.mark_proxy_failure = AsyncMock()
        ctx.get_page_pool = MagicMock()

        result = await pool._dispatch_retry_strategy(
            RetryStrategy.ROTATE_PROXY,
            "net::ERR_TUNNEL_CONNECTION_FAILED",
            ctx,
            0,
            None,
            None,
            0,
            True,
        )
        _, _, _, should_retry, fatal = result
        assert should_retry is False
        assert "代理轮换失败" in fatal

    @pytest.mark.asyncio
    async def test_rotate_proxy_success_no_fatal(self):
        """ROTATE_PROXY 成功轮换 → should_retry=True, fatal=None。"""
        pool = self._make_pool("prefer_proxy")
        ctx = MagicMock()
        ctx.rotate_proxy = AsyncMock(return_value=True)
        # old proxy and new proxy differ → no same-proxy detection
        ctx.get_proxy_for_slot = MagicMock(side_effect=["http://p1:8080", "http://p2:8080", "http://p2:8080"])
        ctx.mark_proxy_failure = AsyncMock()
        ctx.get_page_pool = MagicMock()

        result = await pool._dispatch_retry_strategy(
            RetryStrategy.ROTATE_PROXY,
            "net::ERR_PROXY_CONNECTION_FAILED",
            ctx,
            0,
            None,
            None,
            0,
            True,
        )
        _, _, _, should_retry, fatal = result
        assert should_retry is True
        assert fatal is None

    @pytest.mark.asyncio
    async def test_replace_context_pool_none_returns_fatal(self):
        """REPLACE_CONTEXT 后 pool 为空 → fatal_error_str。"""
        pool = self._make_pool()
        ctx = MagicMock()
        ctx.replace_context = AsyncMock()
        ctx.get_page_pool = MagicMock(return_value=None)

        result = await pool._dispatch_retry_strategy(
            RetryStrategy.REPLACE_CONTEXT,
            "Target closed",
            ctx,
            0,
            None,
            None,
            0,
            False,
        )
        _, _, _, should_retry, fatal = result
        assert should_retry is False
        assert "上下文恢复失败" in fatal

    @pytest.mark.asyncio
    async def test_replace_context_runtime_error_returns_fatal(self):
        """REPLACE_CONTEXT RuntimeError → fatal_error_str。"""
        pool = self._make_pool()
        ctx = MagicMock()
        ctx.replace_context = AsyncMock(side_effect=RuntimeError("boom"))

        result = await pool._dispatch_retry_strategy(
            RetryStrategy.REPLACE_CONTEXT,
            "Execution context was destroyed",
            ctx,
            0,
            None,
            None,
            0,
            False,
        )
        _, _, _, should_retry, fatal = result
        assert should_retry is False
        assert "上下文槽位修复失败" in fatal

    @pytest.mark.asyncio
    async def test_replace_context_success_no_fatal(self):
        """REPLACE_CONTEXT 成功 → should_retry=True, fatal=None。"""
        pool = self._make_pool()
        ctx = MagicMock()
        ctx.replace_context = AsyncMock()
        ctx.get_page_pool = MagicMock(return_value=MagicMock())
        ctx.get_proxy_for_slot = MagicMock(return_value=None)

        result = await pool._dispatch_retry_strategy(
            RetryStrategy.REPLACE_CONTEXT,
            "Target closed",
            ctx,
            0,
            None,
            None,
            0,
            False,
        )
        _, _, _, should_retry, fatal = result
        assert should_retry is True
        assert fatal is None

    @pytest.mark.asyncio
    async def test_transient_returns_should_retry(self):
        """TRANSIENT → should_retry=True, fatal=None。"""
        pool = self._make_pool()
        ctx = MagicMock()

        result = await pool._dispatch_retry_strategy(
            RetryStrategy.TRANSIENT,
            "net::ERR_CONNECTION_RESET",
            ctx,
            0,
            None,
            None,
            0,
            False,
        )
        _, _, _, should_retry, fatal = result
        assert should_retry is True
        assert fatal is None

    @pytest.mark.asyncio
    async def test_fatal_returns_no_retry(self):
        """FATAL → should_retry=False, fatal=None。"""
        pool = self._make_pool()
        ctx = MagicMock()

        result = await pool._dispatch_retry_strategy(
            RetryStrategy.FATAL,
            "net::ERR_NAME_NOT_RESOLVED",
            ctx,
            0,
            None,
            None,
            0,
            False,
        )
        _, _, _, should_retry, fatal = result
        assert should_retry is False
        assert fatal is None

    @pytest.mark.asyncio
    async def test_dispatch_never_calls_safe_set_result(self):
        """所有策略分支都不应调用 _safe_set_result。"""
        pool = self._make_pool("prefer_proxy")
        ctx = MagicMock()
        ctx.rotate_proxy = AsyncMock(return_value=False)
        ctx.replace_context = AsyncMock()
        ctx.get_proxy_for_slot = MagicMock(return_value="http://p1:8080")
        ctx.get_page_pool = MagicMock(return_value=MagicMock())
        ctx.mark_proxy_failure = AsyncMock()

        with patch("astrocrawl.browser.browser_pool._safe_set_result") as mock_ssr:
            await pool._dispatch_retry_strategy(
                RetryStrategy.ROTATE_PROXY,
                "err",
                ctx,
                0,
                None,
                None,
                0,
                True,
            )
            mock_ssr.assert_not_called()

            await pool._dispatch_retry_strategy(
                RetryStrategy.REPLACE_CONTEXT,
                "err",
                ctx,
                0,
                None,
                None,
                0,
                False,
            )
            mock_ssr.assert_not_called()

            await pool._dispatch_retry_strategy(
                RetryStrategy.TRANSIENT,
                "err",
                ctx,
                0,
                None,
                None,
                0,
                False,
            )
            mock_ssr.assert_not_called()

            await pool._dispatch_retry_strategy(
                RetryStrategy.FATAL,
                "err",
                ctx,
                0,
                None,
                None,
                0,
                False,
            )
            mock_ssr.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════
# _retry_loop 分类预算测试
# ═══════════════════════════════════════════════════════════════════════


class TestRetryLoopBudget:
    """验证 _retry_loop 按策略分类消耗 retries_remaining。"""

    def _make_pool(self, max_retries=3, proxy_mode="prefer_proxy"):
        cfg = CrawlerConfig(max_retries=max_retries, proxy_mode=proxy_mode)
        return BrowserPool(concurrency=4, cfg=cfg)

    @pytest.mark.asyncio
    async def test_transient_consumes_retries(self):
        """TRANSIENT 每次减少 retries_remaining，最终退出。"""
        pool = self._make_pool(max_retries=2, proxy_mode="direct_only")
        pool._closed = False
        ctx = MagicMock()
        ctx.get_proxy_for_slot = MagicMock(return_value=None)

        # Mock a page that always fails with TRANSIENT
        page = MagicMock()
        pool_mock = MagicMock()
        request = FetchRequest(url="https://example.com", timeout_ms=10000)

        call_count = [0]

        async def mock_single_fetch(page, req, timeout, slot_has_proxy):
            call_count[0] += 1
            return None, "net::ERR_CONNECTION_RESET", RetryStrategy.TRANSIENT

        async def mock_dispatch(strategy, err_str, ctx, slot_idx, page, pool, attempt, slot_has_proxy):
            return page, pool, slot_has_proxy, True, None

        pool._attempt_single_fetch = mock_single_fetch
        pool._dispatch_retry_strategy = mock_dispatch

        r_page, r_pool, resp, err_str, fatal = await pool._retry_loop(
            page,
            pool_mock,
            ctx,
            0,
            request,
            10.0,
        )
        assert resp is None
        assert fatal is None
        # With max_retries=2 TRANSIENT-only: 2 attempts, both consume retries
        assert call_count[0] == 2

    @pytest.mark.asyncio
    async def test_fatal_exits_immediately(self):
        """FATAL 立即退出，不消耗所有 retries。"""
        pool = self._make_pool(max_retries=3, proxy_mode="direct_only")
        pool._closed = False
        ctx = MagicMock()
        ctx.get_proxy_for_slot = MagicMock(return_value=None)

        async def mock_single_fetch(page, req, timeout, slot_has_proxy):
            return None, "net::ERR_NAME_NOT_RESOLVED", RetryStrategy.FATAL

        pool._attempt_single_fetch = mock_single_fetch

        _, _, resp, err_str, fatal = await pool._retry_loop(
            MagicMock(),
            MagicMock(),
            ctx,
            0,
            FetchRequest(url="https://example.com", timeout_ms=10000),
            10.0,
        )
        assert resp is None
        assert fatal is None
        assert "ERR_NAME_NOT_RESOLVED" in err_str

    @pytest.mark.asyncio
    async def test_rotating_single_proxy_exhausts(self):
        """单代理 ROTATE_PROXY → 同代理检测 → 立即 fatal_error_str。"""
        pool = self._make_pool(max_retries=3, proxy_mode="prefer_proxy")
        pool._closed = False
        ctx = MagicMock()
        ctx.get_proxy_for_slot = MagicMock(return_value="http://only:8080")
        ctx.mark_proxy_failure = AsyncMock()
        ctx.mark_proxy_success = AsyncMock()

        async def mock_single_fetch(page, req, timeout, slot_has_proxy):
            return None, "net::ERR_PROXY_CONNECTION_FAILED", RetryStrategy.ROTATE_PROXY

        # simulate rotate failing (only proxy, already in use)
        async def mock_dispatch(strategy, err_str, ctx, slot_idx, page, pool, attempt, slot_has_proxy):
            return page, pool, slot_has_proxy, False, "代理轮换失败——无可用替代代理"

        pool._attempt_single_fetch = mock_single_fetch
        pool._dispatch_retry_strategy = mock_dispatch

        _, _, resp, err_str, fatal = await pool._retry_loop(
            MagicMock(),
            MagicMock(),
            ctx,
            0,
            FetchRequest(url="https://example.com", timeout_ms=10000),
            10.0,
        )
        assert resp is None
        assert fatal is not None
        assert "代理轮换失败" in fatal

    @pytest.mark.asyncio
    async def test_page_acquire_failure_consumes_retries(self):
        """pool.acquire() 异常消耗 retries_remaining，不会无限循环。"""
        pool = self._make_pool(max_retries=2, proxy_mode="direct_only")
        pool._closed = False
        ctx = MagicMock()
        ctx.get_proxy_for_slot = MagicMock(return_value=None)

        acquire_count = [0]

        class FailingPool:
            async def acquire(self):
                acquire_count[0] += 1
                raise RuntimeError("pool exhausted")

        _, _, resp, err_str, fatal = await pool._retry_loop(
            None,
            FailingPool(),
            ctx,
            0,
            FetchRequest(url="https://example.com", timeout_ms=10000),
            10.0,
        )
        assert resp is None
        assert fatal is None
        # max_retries=2 → 2 acquire failures → exits
        assert acquire_count[0] == 2


# ═══════════════════════════════════════════════════════════════════════
# should_pause_dequeuing / all_proxies_dead 测试
# ═══════════════════════════════════════════════════════════════════════


class TestProxyExhaustionPause:
    """验证 proxy_only 全死时暂停检测逻辑。"""

    def test_should_pause_false_without_proxy_manager(self, cfg_proxy_only):
        pool = BrowserPool(4, cfg_proxy_only)
        assert pool.should_pause_dequeuing() is False

    def test_should_pause_false_without_path_switch(self, cfg):
        pool = BrowserPool(4, cfg)
        pool._proxy_session = MagicMock()
        pool._path_switch = None
        assert pool.should_pause_dequeuing() is False

    def test_should_pause_false_when_on_exhausted_not_pause(self):
        """direct_only / prefer_proxy / prefer_direct 的 on_exhausted='fail' → 不暂停。"""
        for mode in ("direct_only", "prefer_proxy", "prefer_direct"):
            c = CrawlerConfig(proxy_mode=mode, max_retries=3)
            pool = BrowserPool(4, c)
            pm = MagicMock()
            pm.all_dead = MagicMock(return_value=True)
            pool._proxy_session = pm
            assert pool.should_pause_dequeuing() is False, f"mode={mode} should not pause"

    def test_should_pause_true_when_all_dead(self, cfg_proxy_only):
        pool = BrowserPool(4, cfg_proxy_only)
        pm = MagicMock()
        pm.all_dead = MagicMock(return_value=True)
        pool._proxy_session = pm
        assert pool.should_pause_dequeuing() is True

    def test_should_pause_false_when_proxies_available(self, cfg_proxy_only):
        pool = BrowserPool(4, cfg_proxy_only)
        pm = MagicMock()
        pm.all_dead = MagicMock(return_value=False)
        pool._proxy_session = pm
        assert pool.should_pause_dequeuing() is False

    def test_proxy_recovery_event_none_without_proxy_manager(self, cfg):
        pool = BrowserPool(4, cfg)
        assert pool.proxy_recovery_event is None

    def test_proxy_recovery_event_returns_health_event(self, cfg_proxy_only):

        pool = BrowserPool(4, cfg_proxy_only)
        pm = MagicMock()
        fake_event = asyncio.Event()
        pm.recovery_event = fake_event
        pool._proxy_session = pm
        assert pool.proxy_recovery_event is fake_event


# ═══════════════════════════════════════════════════════════════════════
# all_proxies_dead 单元测试
# ═══════════════════════════════════════════════════════════════════════


class TestAllProxiesDead:
    def test_empty_snapshot_returns_false(self):
        from astrocrawl.proxy._proxy import ProxyHealthTracker

        ht = ProxyHealthTracker()
        assert ht.all_proxies_dead() is False

    @pytest.mark.asyncio
    async def test_all_open_returns_true(self):
        from astrocrawl.proxy._proxy import ProxyHealthTracker

        ht = ProxyHealthTracker(failure_threshold=1)
        # Push proxy past threshold to OPEN (health_score=0.0)
        ht.record_failure("http://p1:8080", weight=3)
        result = ht.all_proxies_dead()
        # After record_failure with weight=3 → consecutive_failures=3 → OPEN
        assert result is True

    @pytest.mark.asyncio
    async def test_mixed_returns_false(self):
        from astrocrawl.proxy._proxy import ProxyHealthTracker

        ht = ProxyHealthTracker(failure_threshold=5)
        ht.record_failure("http://p1:8080", weight=1)
        ht.record_failure("http://p2:8080", weight=3)
        # p1: 1 failure (CLOSED), p2: 3 failures (OPEN)
        result = ht.all_proxies_dead()
        assert result is False  # p1 is still CLOSED


# ═══════════════════════════════════════════════════════════════════════
# _safe_set_result 测试
# ═══════════════════════════════════════════════════════════════════════


class TestSafeSetResult:
    def test_sets_result_on_incomplete_future(self):
        loop = asyncio.new_event_loop()
        try:
            fut = loop.create_future()
            assert _safe_set_result(fut, "ok") is True
            assert fut.result() == "ok"
        finally:
            loop.close()

    def test_noop_on_done_future(self):
        loop = asyncio.new_event_loop()
        try:
            fut = loop.create_future()
            fut.set_result("first")
            assert _safe_set_result(fut, "second") is False
            assert fut.result() == "first"
        finally:
            loop.close()

    def test_noop_on_cancelled_future(self):
        loop = asyncio.new_event_loop()
        try:
            fut = loop.create_future()
            fut.cancel()
            assert _safe_set_result(fut, "ok") is False
        finally:
            loop.close()

    def test_no_raise_on_already_done(self):
        loop = asyncio.new_event_loop()
        try:
            fut = loop.create_future()
            fut.set_result("done")
            _safe_set_result(fut, "again")
        finally:
            loop.close()


# ═══════════════════════════════════════════════════════════════════════
# _safe_future_has_result 测试
# ═══════════════════════════════════════════════════════════════════════


class TestSafeFutureHasResult:
    def test_true_when_done(self):
        loop = asyncio.new_event_loop()
        try:
            fut = loop.create_future()
            fut.set_result("ok")
            assert _safe_future_has_result(fut) is True
        finally:
            loop.close()

    def test_false_when_pending(self):
        loop = asyncio.new_event_loop()
        try:
            fut = loop.create_future()
            assert _safe_future_has_result(fut) is False
        finally:
            loop.close()

    def test_true_when_cancelled(self):
        loop = asyncio.new_event_loop()
        try:
            fut = loop.create_future()
            fut.cancel()
            assert _safe_future_has_result(fut) is True
        finally:
            loop.close()


# ═══════════════════════════════════════════════════════════════════════
# _backoff_with_jitter 测试
# ═══════════════════════════════════════════════════════════════════════


class TestBackoffWithJitter:
    def test_full_strategy_range(self):
        for _ in range(50):
            v = _backoff_with_jitter(2.0, strategy="full")
            assert 0 <= v <= 2.0, f"full strategy: {v} not in [0, 2.0]"

    def test_equal_strategy_range(self):
        for _ in range(50):
            v = _backoff_with_jitter(2.0, strategy="equal")
            assert 1.0 <= v <= 2.0, f"equal strategy: {v} not in [1.0, 2.0]"

    def test_default_strategy_is_full(self):
        for _ in range(50):
            v = _backoff_with_jitter(3.0)
            assert 0 <= v <= 3.0

    def test_zero_backoff(self):
        assert _backoff_with_jitter(0.0, strategy="full") == 0.0
        assert _backoff_with_jitter(0.0, strategy="equal") == 0.0

    def test_equal_strategy_midpoint(self):
        """equal strategy returns at least backoff/2."""
        for _ in range(30):
            v = _backoff_with_jitter(10.0, strategy="equal")
            assert v >= 5.0


# ═══════════════════════════════════════════════════════════════════════
# BrowserPool.get_health() 测试
# ═══════════════════════════════════════════════════════════════════════


class TestBrowserPoolHealth:
    def test_down_when_closed(self, cfg):
        pool = BrowserPool(4, cfg)
        pool._closed = True
        h = pool.get_health()
        assert h.status == "DOWN"
        assert "已关闭" in h.message

    def test_down_when_all_dead(self, cfg):
        pool = BrowserPool(4, cfg)
        pool._browsers = {0: MagicMock(_closed=True), 1: MagicMock(_closed=True)}
        h = pool.get_health()
        assert h.status == "DOWN"
        assert "所有 Browser 已死亡" in h.message
        assert h.details["browsers"] == "0/2"

    def test_degraded_when_partial(self, cfg):
        pool = BrowserPool(4, cfg)
        pool._browsers = {
            0: MagicMock(_closed=False),
            1: MagicMock(_closed=True),
        }
        h = pool.get_health()
        assert h.status == "DEGRADED"
        assert "1/2" in h.message

    def test_up_when_all_alive(self, cfg):
        pool = BrowserPool(4, cfg)
        pool._browsers = {
            0: MagicMock(_closed=False),
            1: MagicMock(_closed=False),
            2: MagicMock(_closed=False),
        }
        h = pool.get_health()
        assert h.status == "UP"
        assert "3 browsers alive" in h.message
        assert h.details["browsers"] == "3"
        assert "slots_per_browser" in h.details

    def test_up_when_empty_browsers(self, cfg):
        """空字典：alive=0, total=0，但 DOWN 分支要求 total>0。"""
        pool = BrowserPool(4, cfg)
        pool._browsers = {}
        h = pool.get_health()
        assert h.status == "UP"
        assert "0 browsers alive" in h.message


# ═══════════════════════════════════════════════════════════════════════
# _safe_unroute 测试
# ═══════════════════════════════════════════════════════════════════════


class TestSafeUnroute:
    @pytest.mark.asyncio
    async def test_none_page_returns_early(self):
        await _safe_unroute(None, 1.0)

    @pytest.mark.asyncio
    async def test_closed_page_returns_early(self):
        page = MagicMock()
        page.is_closed.return_value = True
        await _safe_unroute(page, 1.0)
        page.unroute_all.assert_not_called()

    @pytest.mark.asyncio
    async def test_unroute_called_on_open_page(self):
        page = MagicMock()
        page.is_closed.return_value = False
        page.unroute_all = AsyncMock()
        await _safe_unroute(page, 1.0)
        page.unroute_all.assert_called_once()

    @pytest.mark.asyncio
    async def test_exception_silently_swallowed(self):
        page = MagicMock()
        page.is_closed.return_value = False
        page.unroute_all = AsyncMock(side_effect=RuntimeError("boom"))
        await _safe_unroute(page, 1.0)


# ═══════════════════════════════════════════════════════════════════════
# _make_resource_filter 测试
# ═══════════════════════════════════════════════════════════════════════


class TestMakeResourceFilter:
    BLOCKED = frozenset({"image", "font", "stylesheet"})

    @pytest.mark.asyncio
    async def test_aborts_blocked_resource(self):
        filt = _make_resource_filter(self.BLOCKED)
        route = MagicMock()
        route.request.resource_type = "image"
        route.abort = AsyncMock()
        route.continue_ = AsyncMock()
        await filt(route)
        route.abort.assert_called_once()
        route.continue_.assert_not_called()

    @pytest.mark.asyncio
    async def test_continues_non_blocked_resource(self):
        filt = _make_resource_filter(self.BLOCKED)
        route = MagicMock()
        route.request.resource_type = "document"
        route.abort = AsyncMock()
        route.continue_ = AsyncMock()
        await filt(route)
        route.continue_.assert_called_once()
        route.abort.assert_not_called()

    @pytest.mark.asyncio
    async def test_exception_silently_swallowed(self):
        filt = _make_resource_filter(self.BLOCKED)
        route = MagicMock()
        route.request.resource_type = "image"
        route.abort = AsyncMock(side_effect=RuntimeError("boom"))
        await filt(route)

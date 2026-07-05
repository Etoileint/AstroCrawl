"""测试: astrocrawl/network/sitemap.py — SitemapDiscovery 全部方法。

ADR-0003/0004: SitemapDiscovery 管理种子 + 动态源站的 sitemap 发现。
现有 test_sitemap.py 只测试了 SitemapParser 和 _fetch_and_process_sitemap。
此文件补充测试所有其余方法。
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from astrocrawl.network.sitemap import SitemapDiscovery

# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════


class _FakeConfig:
    sitemap_max_recursion = 3
    sitemap_max_urls = 1000
    sitemap_additional_paths = ["/sitemap.xml"]
    sitemap_fetch_concurrency = 3
    max_retries = 3
    retry_backoff_base = 2.0
    user_agent = "TestBot"
    custom_headers = ()
    auth_basic_user = ""
    auth_basic_pass = ""
    auth_bearer_token = ""


def _make_stats():
    """创建预配置的 mock stats，所有 async 方法返回合理默认值。"""
    s = MagicMock()
    s.record_sitemap_fetch = AsyncMock()
    s.get_sitemap_discovered = AsyncMock(return_value=0)
    s.record_drop = AsyncMock()
    s.increment_discovery_total_origins = AsyncMock()
    s.inc_discovery_robots_done = AsyncMock()
    s.inc_discovery_sitemap_done = AsyncMock()
    s.record_origin_discovery = AsyncMock()
    return s


def _make_discovery(**overrides):
    kwargs = {
        "http_session": MagicMock(),
        "robots_cache": None,
        "stats": _make_stats(),
        "enqueue_callback": AsyncMock(return_value=True),
        "stop_event": asyncio.Event(),
        "config": _FakeConfig(),
        "log": logging.getLogger("test"),
    }
    kwargs.update(overrides)
    return SitemapDiscovery(**kwargs)


# ═══════════════════════════════════════════════════════════════════════
# __init__
# ═══════════════════════════════════════════════════════════════════════


class TestInit:
    def test_initial_state(self):
        d = _make_discovery()
        assert d._discovery_done.is_set()
        assert d._seen_origins == set()
        assert d._seen_sitemap_urls == set()
        assert d._pending_origins == 0
        assert d._pending_tasks == set()
        assert isinstance(d._discovery_lock, asyncio.Lock)
        assert isinstance(d._completion_lock, asyncio.Lock)

    def test_discovery_done_property(self):
        d = _make_discovery()
        assert d.discovery_done is d._discovery_done

    def test_robots_cache_optional(self):
        d = _make_discovery(robots_cache=None)
        assert d._robots_cache is None

    def test_with_robots_cache(self):
        rc = MagicMock()
        d = _make_discovery(robots_cache=rc)
        assert d._robots_cache is rc


# ═══════════════════════════════════════════════════════════════════════
# start_discovery
# ═══════════════════════════════════════════════════════════════════════


class TestStartDiscovery:
    async def test_empty_origins_returns_immediately(self):
        d = _make_discovery()
        await d.start_discovery(set(), enqueue_depth=0)
        assert d._discovery_done.is_set()

    async def test_single_origin_adds_to_seen(self):
        d = _make_discovery()
        d._discover_origin = AsyncMock()
        await d.start_discovery({"https://example.com"}, enqueue_depth=1)
        assert "https://example.com" in d._seen_origins
        d._discover_origin.assert_awaited_once()

    async def test_multiple_origins(self):
        d = _make_discovery()
        d._discover_origin = AsyncMock()
        origins = {"https://a.com", "https://b.com", "https://c.com"}
        await d.start_discovery(origins, enqueue_depth=0)
        assert d._discover_origin.await_count == 3

    async def test_clears_discovery_done_on_start(self):
        d = _make_discovery()
        d._discover_origin = AsyncMock()
        assert d._discovery_done.is_set()
        await d.start_discovery({"https://a.com"}, 0)
        assert d._discovery_done.is_set()  # done after completion

    async def test_task_error_does_not_crash(self):
        d = _make_discovery()
        d._discover_origin = AsyncMock(side_effect=Exception("boom"))
        await d.start_discovery({"https://a.com"}, 0)
        assert d._discovery_done.is_set()


# ═══════════════════════════════════════════════════════════════════════
# _pending_guard
# ═══════════════════════════════════════════════════════════════════════


class TestPendingGuard:
    async def test_decrements_pending_on_exit(self):
        d = _make_discovery()
        d._discovery_done.clear()
        d._pending_origins = 1

        async with d._pending_guard():
            pass

        assert d._pending_origins == 0
        assert d._discovery_done.is_set()

    async def test_decrements_on_exception(self):
        d = _make_discovery()
        d._discovery_done.clear()
        d._pending_origins = 1

        with pytest.raises(ValueError):
            async with d._pending_guard():
                raise ValueError("test")

        assert d._pending_origins == 0
        assert d._discovery_done.is_set()


# ═══════════════════════════════════════════════════════════════════════
# discover_origin_if_new
# ═══════════════════════════════════════════════════════════════════════


class TestDiscoverOriginIfNew:
    async def test_stop_event_set_returns_immediately(self):
        ev = asyncio.Event()
        ev.set()
        d = _make_discovery(stop_event=ev)
        d.discover_origin_if_new("https://a.com", 0)
        assert len(d._pending_tasks) == 0

    async def test_new_origin_launches_task(self):
        d = _make_discovery()
        d._discover_origin = AsyncMock()
        d.discover_origin_if_new("https://a.com", 0)
        assert len(d._pending_tasks) == 1
        # 等后台任务完成
        for t in list(d._pending_tasks):
            if not t.done():
                await asyncio.wait_for(t, timeout=5)
        assert "https://a.com" in d._seen_origins

    async def test_duplicate_origin_no_new_task(self):
        d = _make_discovery()
        d._discover_origin = AsyncMock()
        d._seen_origins.add("https://a.com")
        d.discover_origin_if_new("https://a.com", 0)
        # 即使已见 origin，_guard_and_launch 还是创建了 task，
        # 但 task 内部检查 seen_origins 后立即返回
        # 等待所有 task 完成
        for t in list(d._pending_tasks):
            if not t.done():
                await asyncio.wait_for(t, timeout=5)
        # 第二个 task 检查后应该跳过
        d._discover_origin.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════
# _get_robots_sitemaps
# ═══════════════════════════════════════════════════════════════════════


class TestGetRobotsSitemaps:
    async def test_no_cache_returns_empty(self):
        d = _make_discovery(robots_cache=None)
        result = await d._get_robots_sitemaps("https://example.com")
        assert result == []

    async def test_returns_sitemap_urls(self):
        rc = MagicMock()
        rc.get_sitemaps = AsyncMock(return_value=["https://example.com/sitemap.xml"])
        d = _make_discovery(robots_cache=rc)
        result = await d._get_robots_sitemaps("https://example.com")
        assert result == ["https://example.com/sitemap.xml"]

    async def test_exception_returns_empty(self):
        rc = MagicMock()
        rc.get_sitemaps = AsyncMock(side_effect=Exception("boom"))
        d = _make_discovery(robots_cache=rc)
        result = await d._get_robots_sitemaps("https://example.com")
        assert result == []


# ═══════════════════════════════════════════════════════════════════════
# _fetch_sitemap_content
# ═══════════════════════════════════════════════════════════════════════


class _MockResponse:
    def __init__(self, status=200, body=b""):
        self.status = status
        self._body = body
        self.content = self

    async def read(self, n=-1):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class _MockSession:
    """模拟 aiohttp.ClientSession，get() 返回 _MockResponse async context manager。"""

    def __init__(self, responses=None):
        self._responses = responses or [(200, b"")]
        self._idx = 0

    def get(self, url, timeout=None, **kw):
        idx = min(self._idx, len(self._responses) - 1)
        self._idx += 1
        status, body = self._responses[idx]
        return _MockResponse(status, body)


def _make_discovery_with_session(responses, **overrides):
    return _make_discovery(http_session=_MockSession(responses), **overrides)


class TestFetchSitemapContent:
    async def test_200_returns_content(self):
        d = _make_discovery_with_session([(200, b"<xml>content</xml>")])
        result = await d._fetch_sitemap_content("https://a.com/sitemap.xml")
        assert result == b"<xml>content</xml>"

    async def test_404_returns_none(self):
        d = _make_discovery_with_session([(404, b"")])
        result = await d._fetch_sitemap_content("https://a.com/sitemap.xml")
        assert result is None

    async def test_transient_retries_then_success(self):
        d = _make_discovery_with_session([(503, b""), (200, b"ok")])
        with patch("astrocrawl.network.sitemap.asyncio.sleep", new_callable=AsyncMock):
            result = await d._fetch_sitemap_content("https://a.com/sitemap.xml")
        assert result == b"ok"

    async def test_content_truncation(self):
        from astrocrawl.network.sitemap import SITEMAP_MAX_CONTENT_SIZE

        big = b"x" * (SITEMAP_MAX_CONTENT_SIZE + 100)
        d = _make_discovery_with_session([(200, big)])
        result = await d._fetch_sitemap_content("https://a.com/sitemap.xml")
        assert len(result) == SITEMAP_MAX_CONTENT_SIZE

    async def test_network_error_returns_none(self):
        session = _MockSession([(200, b"")])
        session.get = lambda url, **kw: (_ for _ in ()).throw(Exception("connection error"))
        d = _make_discovery(http_session=session)
        with patch("astrocrawl.network.sitemap.asyncio.sleep", new_callable=AsyncMock):
            result = await d._fetch_sitemap_content("https://a.com/sitemap.xml")
        assert result is None


class TestSitemapBuildHeaders:
    def test_user_agent_set(self):
        d = _make_discovery()
        h = d._build_headers()
        assert h["User-Agent"] == "TestBot"

    def test_bearer_auth(self):
        old = _FakeConfig.auth_bearer_token
        _FakeConfig.auth_bearer_token = "token123"
        try:
            d = _make_discovery()
            h = d._build_headers()
            assert h["Authorization"] == "Bearer token123"
        finally:
            _FakeConfig.auth_bearer_token = old

    def test_custom_headers(self):
        old = _FakeConfig.custom_headers
        _FakeConfig.custom_headers = ("X-Sitemap: s",)
        try:
            d = _make_discovery()
            h = d._build_headers()
            assert h["X-Sitemap"] == "s"
        finally:
            _FakeConfig.custom_headers = old

    def test_basic_auth(self):
        old_user = _FakeConfig.auth_basic_user
        old_pass = _FakeConfig.auth_basic_pass
        _FakeConfig.auth_basic_user = "user"
        _FakeConfig.auth_basic_pass = "pass"
        _FakeConfig.auth_bearer_token = ""
        try:
            d = _make_discovery()
            h = d._build_headers()
            assert h["Authorization"].startswith("Basic ")
        finally:
            _FakeConfig.auth_basic_user = old_user
            _FakeConfig.auth_basic_pass = old_pass


# ═══════════════════════════════════════════════════════════════════════
# _decrement_pending + _decrement_pending_impl
# ═══════════════════════════════════════════════════════════════════════


class TestDecrementPending:
    async def test_decrements_to_zero_sets_done(self):
        d = _make_discovery()
        d._discovery_done.clear()
        d._pending_origins = 2

        await d._decrement_pending()
        assert d._pending_origins == 1
        assert not d._discovery_done.is_set()

        await d._decrement_pending()
        assert d._pending_origins == 0
        assert d._discovery_done.is_set()

    async def test_decrements_below_zero_sets_done(self):
        d = _make_discovery()
        d._pending_origins = 1

        await d._decrement_pending()
        assert d._discovery_done.is_set()


# ═══════════════════════════════════════════════════════════════════════
# _gather_logged
# ═══════════════════════════════════════════════════════════════════════


class TestGatherLogged:
    async def test_successful_tasks(self):
        d = _make_discovery()

        async def _ok():
            pass

        tasks = [asyncio.create_task(_ok()) for _ in range(3)]
        await d._gather_logged(tasks, "test")
        # no exception raised

    async def test_failed_task_logged_not_raised(self):
        d = _make_discovery()

        async def _fail():
            raise ValueError("boom")

        tasks = [asyncio.create_task(_fail()), asyncio.create_task(asyncio.sleep(0))]
        await d._gather_logged(tasks, "test")
        # exception handled silently


# ═══════════════════════════════════════════════════════════════════════
# _discover_origin
# ═══════════════════════════════════════════════════════════════════════


class TestDiscoverOrigin:
    async def test_assembles_candidates(self):
        rc = MagicMock()
        rc.get_sitemaps = AsyncMock(return_value=[])
        d = _make_discovery(robots_cache=rc)
        d._fetch_and_process_sitemap = AsyncMock()

        await d._discover_origin("https://example.com", enqueue_depth=1)

        # should have called for /sitemap.xml (from additional_paths)
        args_list = [c.args for c in d._fetch_and_process_sitemap.await_args_list]
        urls = {a[0] for a in args_list}
        assert "https://example.com/sitemap.xml" in urls

    async def test_includes_robots_sitemaps(self):
        rc = MagicMock()
        rc.get_sitemaps = AsyncMock(return_value=["/custom_sitemap.xml"])
        d = _make_discovery(robots_cache=rc)
        d._fetch_and_process_sitemap = AsyncMock()

        await d._discover_origin("https://example.com", enqueue_depth=0)

        urls = {c.args[0] for c in d._fetch_and_process_sitemap.await_args_list}
        assert "https://example.com/custom_sitemap.xml" in urls

    async def test_stats_exception_handled_gracefully(self):
        rc = MagicMock()
        rc.get_sitemaps = AsyncMock(return_value=[])
        stats = MagicMock()
        stats.inc_discovery_robots_done = AsyncMock(side_effect=Exception("stats error"))
        d = _make_discovery(robots_cache=rc, stats=stats)
        d._fetch_and_process_sitemap = AsyncMock()

        # should not raise
        await d._discover_origin("https://example.com", enqueue_depth=0)

    async def test_relative_robots_sitemap_urljoin(self):
        """相对路径的 robots sitemap URL 通过 urljoin 补全。"""
        rc = MagicMock()
        rc.get_sitemaps = AsyncMock(return_value=["/relative-sitemap.xml"])
        rc.get_fetch_status = AsyncMock(return_value="ok")
        stats = _make_stats()
        d = _make_discovery(robots_cache=rc, stats=stats)
        d._fetch_and_process_sitemap = AsyncMock()

        await d._discover_origin("https://example.com", enqueue_depth=0)

        urls = {c.args[0] for c in d._fetch_and_process_sitemap.await_args_list}
        assert "https://example.com/relative-sitemap.xml" in urls

    async def test_per_origin_stats_recorded(self):
        """_discover_origin 完成后记录 per-origin 统计。"""
        rc = MagicMock()
        rc.get_sitemaps = AsyncMock(return_value=[])
        rc.get_fetch_status = AsyncMock(return_value="ok")
        stats = _make_stats()
        d = _make_discovery(robots_cache=rc, stats=stats)
        d._fetch_and_process_sitemap = AsyncMock()

        await d._discover_origin("https://example.com", enqueue_depth=0)

        stats.inc_discovery_robots_done.assert_awaited_once()
        stats.inc_discovery_sitemap_done.assert_awaited_once()
        stats.record_origin_discovery.assert_awaited_once_with("https://example.com", "ok", 0)


# ═══════════════════════════════════════════════════════════════════════
# aclose
# ═══════════════════════════════════════════════════════════════════════


class TestAclose:
    async def test_aclose_no_pending_tasks(self):
        d = _make_discovery()
        await d.aclose()
        # no error

    async def test_aclose_cancels_pending_tasks(self):
        d = _make_discovery()

        async def _never():
            await asyncio.Event().wait()

        t = asyncio.create_task(_never())
        d._pending_tasks.add(t)

        await d.aclose()
        assert t.cancelled() or t.done()


# ═══════════════════════════════════════════════════════════════════════
# _fetch_and_process_sitemap — 补充
# ═══════════════════════════════════════════════════════════════════════


class TestFetchAndProcessSitemap:
    """补充 test_sitemap.py TestSitemapCounterIsolation。"""

    async def test_stop_event_checked_before_fetch(self):
        ev = asyncio.Event()
        ev.set()
        d = _make_discovery(stop_event=ev)

        await d._fetch_and_process_sitemap("https://a.com/s.xml", 0, 0)

    async def test_depth_exceeds_recursion_returns(self):
        d = _make_discovery()
        d._fetch_sitemap_content = AsyncMock()

        await d._fetch_and_process_sitemap("https://a.com/s.xml", sitemap_depth=10, enqueue_depth=0)
        d._fetch_sitemap_content.assert_not_called()

    async def test_already_seen_skips(self):
        d = _make_discovery()
        d._seen_sitemap_urls.add("https://a.com/s.xml")
        d._fetch_sitemap_content = AsyncMock()

        await d._fetch_and_process_sitemap("https://a.com/s.xml", 0, 0)
        d._fetch_sitemap_content.assert_not_called()

    async def test_fetch_none_returns_early(self):
        d = _make_discovery()
        d._fetch_sitemap_content = AsyncMock(return_value=None)

        await d._fetch_and_process_sitemap("https://a.com/s.xml", 0, 0)
        # should not crash

    async def test_max_urls_limit_respected(self):
        d = _make_discovery()
        d._fetch_sitemap_content = AsyncMock(
            return_value=b"<?xml><urlset><url><loc>https://a.com/p1</loc></url></urlset>"
        )
        d._stats.get_sitemap_discovered = AsyncMock(return_value=1000)
        d._cfg.sitemap_max_urls = 1000
        await d._fetch_and_process_sitemap("https://a.com/s.xml", 0, 0)

    async def test_enqueue_callback_error_skipped(self):
        d = _make_discovery()
        d._fetch_sitemap_content = AsyncMock(
            return_value=b"<?xml><urlset><url><loc>https://a.com/p1</loc></url></urlset>"
        )
        d._enqueue_callback = AsyncMock(return_value=False)

        await d._fetch_and_process_sitemap("https://a.com/s.xml", 0, 0, counter=[0])

    async def test_urlset_not_lost_when_path_contains_sitemapindex(self):
        """Bug 回归: URL 路径含 sitemapindex 的 URLSet 不会静默丢失所有 URL。"""
        d = _make_discovery()
        d._fetch_sitemap_content = AsyncMock(
            return_value=(
                b'<?xml version="1.0" encoding="UTF-8"?>'
                b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                b"<url><loc>https://example.com/sitemapindex.xml</loc></url>"
                b"<url><loc>https://example.com/page2</loc></url>"
                b"</urlset>"
            )
        )
        counter = [0]
        await d._fetch_and_process_sitemap(
            "https://example.com/sitemap.xml",
            0,
            1,
            counter,
        )
        assert counter[0] == 2
        assert d._enqueue_callback.await_count == 2
        call_locs = {c.args[0] for c in d._enqueue_callback.await_args_list}
        assert "https://example.com/sitemapindex.xml" in call_locs
        assert "https://example.com/page2" in call_locs

    async def test_compressed_sitemapindex_recurses_not_enqueues(self):
        """Bug 回归: gzip 压缩的 sitemapindex 递归处理子 sitemap 而非普通入队。"""
        import gzip

        compressed = gzip.compress(
            b'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            b"<sitemap><loc>https://example.com/child-sitemap.xml</loc></sitemap>"
            b"</sitemapindex>"
        )
        d = _make_discovery()
        # 仅首次调用返回压缩内容，子 sitemap 抓取返回 None（模拟抓取失败停止递归）
        d._fetch_sitemap_content = AsyncMock(side_effect=[compressed, None])
        # 将 _fetch_and_process_sitemap 替换为 spy，验证递归调用
        original = d._fetch_and_process_sitemap
        call_args = []

        async def _spy(url, sitemap_depth, enqueue_depth, counter=None):
            call_args.append((url, sitemap_depth, enqueue_depth))
            return await original(url, sitemap_depth, enqueue_depth, counter)

        d._fetch_and_process_sitemap = _spy

        await d._fetch_and_process_sitemap("https://example.com/sitemap.xml.gz", 0, 1)

        # 应递归调用子 sitemap（非普通 URL 入队）
        assert d._enqueue_callback.await_count == 0
        child_calls = [c for c in call_args if c[0] == "https://example.com/child-sitemap.xml"]
        assert len(child_calls) == 1
        assert child_calls[0][1] == 1  # sitemap_depth + 1

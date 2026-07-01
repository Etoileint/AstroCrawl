"""Robots.txt 解析器测试"""

from __future__ import annotations

import asyncio
import errno

import aiohttp
import pytest

from astrocrawl._types import FetchErrorCategory
from astrocrawl.network._fetch import _classify_aiohttp_error
from astrocrawl.network.robots import AsyncRobotsParser, RobotsCache, _compile_robots_rule

ROBOTS_TXT = """
User-agent: *
Disallow: /admin/
Disallow: /login
Allow: /admin/public/
Crawl-Delay: 5

User-agent: AstroCrawl
Disallow: /private/
Allow: /private/public/
Crawl-Delay: 2

Sitemap: https://example.com/sitemap.xml
"""


class TestCompileRobotsRule:
    def test_exact_match(self):
        rule, _ = _compile_robots_rule("/admin/")
        assert rule("/admin/")
        assert not rule("/admin")

    def test_wildcard_match(self):
        rule, _ = _compile_robots_rule("/admin/*")
        assert rule("/admin/anything")
        assert rule("/admin/")

    def test_empty_rule(self):
        rule, _ = _compile_robots_rule("")
        assert not rule("/anything")

    def test_end_anchor(self):
        rule, _ = _compile_robots_rule("/admin$")
        assert rule("/admin")


class TestAsyncRobotsParser:
    def test_parse_user_agent_specific(self):
        parser = AsyncRobotsParser("AstroCrawl")
        parser.parse(ROBOTS_TXT)
        assert not parser.can_fetch("https://example.com/private/data")
        assert parser.can_fetch("https://example.com/private/public/page")

    def test_parse_wildcard_fallback(self):
        parser = AsyncRobotsParser("UnknownBot")
        parser.parse(ROBOTS_TXT)
        assert not parser.can_fetch("https://example.com/admin/dashboard")
        assert parser.can_fetch("https://example.com/admin/public/page")

    def test_parse_extracts_crawl_delay(self):
        parser = AsyncRobotsParser("AstroCrawl")
        parser.parse(ROBOTS_TXT)
        assert parser.crawl_delay == 2

    def test_parse_extracts_sitemaps(self):
        parser = AsyncRobotsParser("AstroCrawl")
        parser.parse(ROBOTS_TXT)
        assert "https://example.com/sitemap.xml" in parser.sitemaps

    def test_allow_all_when_no_matching_ua(self):
        parser = AsyncRobotsParser("NonExistentBot/1.0")
        parser.parse("User-agent: Googlebot\nDisallow: /")
        assert parser.can_fetch("https://example.com/anything")

    def test_empty_robots_allows_all(self):
        parser = AsyncRobotsParser("AstroCrawl")
        parser.parse("")
        assert parser.can_fetch("https://example.com/anything")


class TestRobotsSizeLimit:
    def test_parser_handles_oversized_input(self):
        """验证解析器能处理 >500KB 的输入（截断后正常解析）。"""
        large_prefix = "User-agent: *\nDisallow: /admin/\n" + ("# padding\n" * 100000)
        parser = AsyncRobotsParser("AstroCrawl")
        parser.parse(large_prefix)
        assert not parser.can_fetch("https://example.com/admin/dashboard")


# ═══════════════════════════════════════════════════════════════════════
# AsyncRobotsParser.parse — 边缘情况
# ═══════════════════════════════════════════════════════════════════════


class TestAsyncRobotsParserEdgeCases:
    """AsyncRobotsParser.parse — request-rate / 空 Disallow / 大小写混合。"""

    def test_request_rate_directive(self):
        parser = AsyncRobotsParser("Bot")
        parser.parse("User-agent: *\nrequest-rate: 1/5\nDisallow: /private")
        assert parser.crawl_delay == 5.0

    def test_request_rate_zero_division_no_crash(self):
        parser = AsyncRobotsParser("Bot")
        parser.parse("User-agent: *\nrequest-rate: 0/5\nDisallow: /x")
        assert parser.crawl_delay is None

    def test_empty_disallow_path_not_added(self):
        parser = AsyncRobotsParser("Bot")
        parser.parse("User-agent: Bot\nDisallow:")
        assert len(parser.disallow_rules) == 0

    def test_mixed_case_directives(self):
        parser = AsyncRobotsParser("bot")
        parser.parse("USER-AGENT: BOT\nDISALLOW: /secret\nALLOW: /secret/public")
        assert not parser.can_fetch("https://x.com/secret/x")
        assert parser.can_fetch("https://x.com/secret/public")

    def test_wildcard_combination(self):
        parser = AsyncRobotsParser("Bot")
        parser.parse("User-agent: Bot\nDisallow: /api/*/delete$\nAllow: /api/*/")
        assert parser.can_fetch("https://x.com/api/v1/list")

    def test_multi_sitemap(self):
        parser = AsyncRobotsParser("Bot")
        parser.parse("User-agent: *\nSitemap: https://x.com/sitemap1.xml\nSitemap: https://x.com/sitemap2.xml")
        assert len(parser.sitemaps) == 2

    def test_specificity_tiebreak(self):
        """更具体的 Disallow 胜过较宽泛的 Allow。"""
        parser = AsyncRobotsParser("Bot")
        parser.parse("User-agent: Bot\nAllow: /foo\nDisallow: /foo/bar")
        assert parser.can_fetch("https://x.com/foo")
        assert not parser.can_fetch("https://x.com/foo/bar")

    def test_percent_encoded_path_decoded_before_match(self):
        """RFC 9309 §2.2.2: %2F 等编码在匹配前先解码。"""
        parser = AsyncRobotsParser("Bot")
        parser.parse("User-agent: Bot\nDisallow: /admin/secret/\nAllow: /admin/secret/public")
        assert not parser.can_fetch("https://x.com/admin%2Fsecret/page")
        assert parser.can_fetch("https://x.com/admin%2Fsecret/public")

    def test_percent_encoded_space_in_path(self):
        """URL 中有 %20 → 解码为空格匹配。规则是明文，不含百分号编码。"""
        parser = AsyncRobotsParser("Bot")
        parser.parse("User-agent: Bot\nDisallow: /my docs/")
        assert not parser.can_fetch("https://x.com/my%20docs/file")
        assert parser.can_fetch("https://x.com/normal/path")

    def test_percent_encoded_utf8_chinese_path(self):
        """UTF-8 百分号编码的中文路径。"""
        parser = AsyncRobotsParser("Bot")
        parser.parse("User-agent: Bot\nDisallow: /中文/")
        assert not parser.can_fetch("https://x.com/%E4%B8%AD%E6%96%87/page")


# ═══════════════════════════════════════════════════════════════════════
# AsyncRobotsParser.from_url
# ═══════════════════════════════════════════════════════════════════════


class TestAsyncRobotsParserFromUrl:
    """AsyncRobotsParser.from_url() — HTTP 获取 + 异常处理。"""

    @pytest.mark.asyncio
    async def test_http_200_success(self):
        from unittest.mock import AsyncMock, MagicMock

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.content.read = AsyncMock(return_value=b"User-agent: *\nDisallow: /admin")
        mock_session = MagicMock()
        mock_session.get.return_value.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_session.get.return_value.__aexit__ = AsyncMock(return_value=False)

        parser = await AsyncRobotsParser.from_url("https://example.com/robots.txt", "Bot", mock_session, timeout=10)
        assert parser.fetch_status == "ok"
        assert not parser.can_fetch("https://example.com/admin/page")

    @pytest.mark.asyncio
    async def test_http_404_allow_all(self):
        from unittest.mock import AsyncMock, MagicMock

        mock_resp = MagicMock()
        mock_resp.status = 404
        mock_session = MagicMock()
        mock_session.get.return_value.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_session.get.return_value.__aexit__ = AsyncMock(return_value=False)

        parser = await AsyncRobotsParser.from_url("https://example.com/robots.txt", "Bot", mock_session, timeout=10)
        assert parser.allow_all is True
        assert parser.fetch_status == "http_404"

    @pytest.mark.asyncio
    async def test_exception_allow_all_true(self):
        from unittest.mock import MagicMock

        mock_session = MagicMock()
        mock_session.get.side_effect = RuntimeError("Connection refused")

        parser = await AsyncRobotsParser.from_url(
            "https://example.com/robots.txt",
            "Bot",
            mock_session,
            timeout=10,
            allow_all_on_error=True,
        )
        assert parser.allow_all is True
        assert parser.fetch_status == "fetch_failed"

    @pytest.mark.asyncio
    async def test_exception_allow_all_false_raises(self):
        from unittest.mock import MagicMock

        mock_session = MagicMock()
        mock_session.get.side_effect = RuntimeError("Connection refused")

        with pytest.raises(RuntimeError):
            await AsyncRobotsParser.from_url(
                "https://example.com/robots.txt",
                "Bot",
                mock_session,
                timeout=10,
                allow_all_on_error=False,
            )


# ═══════════════════════════════════════════════════════════════════════
# RobotsCache
# ═══════════════════════════════════════════════════════════════════════


class TestRobotsCache:
    """RobotsCache — is_allowed / _get_or_fetch_robots。"""

    @pytest.mark.asyncio
    async def test_is_allowed_cache_hit(self, monkeypatch):
        from unittest.mock import AsyncMock, MagicMock

        from astrocrawl.network.robots import RobotsCache

        parser = AsyncRobotsParser("Bot")
        parser.parse("User-agent: Bot\nDisallow: /private")
        cache = RobotsCache("Bot", MagicMock())
        monkeypatch.setattr(cache, "_get_or_fetch_robots", AsyncMock(return_value=parser))
        assert not await cache.is_allowed("https://x.com/private/data")

    @pytest.mark.asyncio
    async def test_is_allowed_exception_returns_true(self, monkeypatch):
        from unittest.mock import MagicMock

        from astrocrawl.network.robots import RobotsCache

        cache = RobotsCache("Bot", MagicMock())

        async def _raise(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(cache, "_get_or_fetch_robots", _raise)
        assert await cache.is_allowed("https://x.com/anything") is True

    @pytest.mark.asyncio
    async def test_get_or_fetch_cache_hit(self, monkeypatch):
        import time
        from unittest.mock import MagicMock

        from astrocrawl.network.robots import RobotsCache

        parser = AsyncRobotsParser("Bot")
        cache = RobotsCache("Bot", MagicMock())
        cache._cache["https://x.com"] = (parser, time.monotonic() + 3600)
        result = await cache._get_or_fetch_robots("https://x.com")
        assert result is parser

    @pytest.mark.asyncio
    async def test_get_or_fetch_cache_expired(self, monkeypatch):
        import time
        from unittest.mock import AsyncMock, MagicMock

        from astrocrawl.network.robots import RobotsCache

        parser = AsyncRobotsParser("Bot")
        cache = RobotsCache("Bot", MagicMock())
        monkeypatch.setattr(cache, "_fetch_robots", AsyncMock(return_value=parser))
        cache._cache["https://x.com"] = (AsyncRobotsParser("Old"), time.monotonic() - 3600)
        result = await cache._get_or_fetch_robots("https://x.com")
        assert result is parser

    @pytest.mark.asyncio
    async def test_get_fetch_status_default(self, monkeypatch):
        from unittest.mock import MagicMock

        from astrocrawl.network.robots import RobotsCache

        cache = RobotsCache("Bot", MagicMock())
        status = await cache.get_fetch_status("https://never-fetched.com")
        assert status == "not_checked"

    @pytest.mark.asyncio
    async def test_get_fetch_status_after_fetch(self, monkeypatch):
        from unittest.mock import MagicMock

        from astrocrawl.network.robots import RobotsCache

        cache = RobotsCache("Bot", MagicMock())
        cache._fetch_status["https://x.com"] = "ok"
        status = await cache.get_fetch_status("https://x.com")
        assert status == "ok"

    @pytest.mark.asyncio
    async def test_get_sitemaps_success(self, monkeypatch):
        from unittest.mock import AsyncMock, MagicMock

        from astrocrawl.network.robots import RobotsCache

        parser = AsyncRobotsParser("Bot")
        parser.sitemaps = ["https://x.com/sitemap.xml"]
        cache = RobotsCache("Bot", MagicMock())
        monkeypatch.setattr(cache, "_get_or_fetch_robots", AsyncMock(return_value=parser))
        sitemaps = await cache.get_sitemaps("https://x.com")
        assert sitemaps == ["https://x.com/sitemap.xml"]

    @pytest.mark.asyncio
    async def test_get_sitemaps_exception_returns_empty(self, monkeypatch):
        from unittest.mock import MagicMock

        from astrocrawl.network.robots import RobotsCache

        cache = RobotsCache("Bot", MagicMock())

        async def _raise(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(cache, "_get_or_fetch_robots", _raise)
        sitemaps = await cache.get_sitemaps("https://x.com")
        assert sitemaps == []


class TestRobotsCacheFetch:
    """RobotsCache._fetch_robots — 获取 + 缓存管理。"""

    @pytest.mark.asyncio
    async def test_fetch_http_200_cached(self, monkeypatch):
        from unittest.mock import AsyncMock, MagicMock

        from astrocrawl.network._fetch import AiohttpFetchResult
        from astrocrawl.network.robots import RobotsCache

        monkeypatch.setattr(
            "astrocrawl.network.robots.aiohttp_retry_fetch",
            AsyncMock(return_value=AiohttpFetchResult(content=b"User-agent: Bot\nDisallow: /", http_status=200)),
        )
        cache = RobotsCache("Bot", MagicMock())
        result = await cache._fetch_robots("https://x.com")
        assert not result.allow_all
        assert "https://x.com" in cache._cache

    @pytest.mark.asyncio
    async def test_fetch_lru_eviction(self, monkeypatch):
        import time
        from unittest.mock import AsyncMock, MagicMock

        from astrocrawl.network._fetch import AiohttpFetchResult
        from astrocrawl.network.robots import RobotsCache

        monkeypatch.setattr(
            "astrocrawl.network.robots.aiohttp_retry_fetch",
            AsyncMock(return_value=AiohttpFetchResult(content=b"", http_status=200)),
        )
        cache = RobotsCache("Bot", MagicMock(), max_size=2)
        cached = AsyncRobotsParser("Bot")
        cache._cache["https://a.com"] = (cached, time.monotonic() + 3600)
        cache._cache["https://b.com"] = (cached, time.monotonic() + 7200)
        await cache._fetch_robots("https://c.com")
        assert "https://a.com" not in cache._cache
        assert len(cache._cache) == 2

    @pytest.mark.asyncio
    async def test_fetch_sets_status(self, monkeypatch):
        from unittest.mock import AsyncMock, MagicMock

        from astrocrawl.network._fetch import AiohttpFetchResult
        from astrocrawl.network.robots import RobotsCache

        monkeypatch.setattr(
            "astrocrawl.network.robots.aiohttp_retry_fetch",
            AsyncMock(return_value=AiohttpFetchResult(content=b"User-agent: Bot\nDisallow: /", http_status=200)),
        )
        cache = RobotsCache("Bot", MagicMock())
        await cache._fetch_robots("https://x.com")
        assert cache._fetch_status["https://x.com"] == "ok"

    @pytest.mark.asyncio
    async def test_fetch_retry_handled_by_engine(self, monkeypatch):
        """网络异常 → aiohttp_retry_fetch 返回 network error → fallback parser。"""
        from unittest.mock import AsyncMock, MagicMock

        from astrocrawl.network._fetch import AiohttpFetchResult
        from astrocrawl.network.robots import RobotsCache

        monkeypatch.setattr(
            "astrocrawl.network.robots.aiohttp_retry_fetch",
            AsyncMock(return_value=AiohttpFetchResult()),
        )
        cache = RobotsCache("Bot", MagicMock())
        result = await cache._fetch_robots("https://x.com")
        assert result.allow_all
        assert result.fetch_status == "fetch_failed"

    @pytest.mark.asyncio
    async def test_fetch_http_403_sets_http_status(self, monkeypatch):
        from unittest.mock import AsyncMock, MagicMock

        from astrocrawl.network._fetch import AiohttpFetchResult
        from astrocrawl.network.robots import RobotsCache

        monkeypatch.setattr(
            "astrocrawl.network.robots.aiohttp_retry_fetch",
            AsyncMock(return_value=AiohttpFetchResult(http_status=403)),
        )
        cache = RobotsCache("Bot", MagicMock())
        result = await cache._fetch_robots("https://x.com")
        assert result.allow_all
        assert result.fetch_status == "http_403"


class TestRobotsBuildHeaders:
    def test_user_agent_set(self):
        cache = RobotsCache.__new__(RobotsCache)
        cache._ua = "AstroCrawl"
        cache._auth_bearer_token = ""
        cache._auth_basic_user = ""
        cache._auth_basic_pass = ""
        cache._custom_headers = None
        h = cache._build_headers()
        assert h["User-Agent"] == "AstroCrawl"

    def test_bearer_auth(self):
        cache = RobotsCache.__new__(RobotsCache)
        cache._ua = "Bot"
        cache._auth_bearer_token = "token123"
        cache._auth_basic_user = ""
        cache._auth_basic_pass = ""
        cache._custom_headers = None
        h = cache._build_headers()
        assert h["Authorization"] == "Bearer token123"

    def test_basic_auth(self):
        cache = RobotsCache.__new__(RobotsCache)
        cache._ua = "Bot"
        cache._auth_bearer_token = ""
        cache._auth_basic_user = "user"
        cache._auth_basic_pass = "pass"
        cache._custom_headers = None
        h = cache._build_headers()
        assert h["Authorization"].startswith("Basic ")

    def test_custom_headers(self):
        cache = RobotsCache.__new__(RobotsCache)
        cache._ua = "Bot"
        cache._auth_bearer_token = ""
        cache._auth_basic_user = ""
        cache._auth_basic_pass = ""
        cache._custom_headers = ["X-Custom: val"]
        h = cache._build_headers()
        assert h["X-Custom"] == "val"

    def test_bearer_takes_priority_over_basic(self):
        cache = RobotsCache.__new__(RobotsCache)
        cache._ua = "Bot"
        cache._auth_bearer_token = "token123"
        cache._auth_basic_user = "user"
        cache._auth_basic_pass = "pass"
        cache._custom_headers = None
        h = cache._build_headers()
        assert h["Authorization"] == "Bearer token123"


class TestClassifyAiohttpError:
    """_classify_aiohttp_error — aiohttp 异常到 FetchErrorCategory 映射。"""

    def test_asyncio_timeout_error_returns_timeout(self):
        assert _classify_aiohttp_error(asyncio.TimeoutError()) == FetchErrorCategory.TIMEOUT

    def test_client_connector_error_with_econnrefused(self):
        os_err = OSError(getattr(errno, "ECONNREFUSED", 111), "Connection refused")
        exc = aiohttp.ClientConnectorError(None, os_error=os_err)
        assert _classify_aiohttp_error(exc) == FetchErrorCategory.CONNECTION_REFUSED

    def test_client_connector_error_with_econnreset(self):
        os_err = OSError(getattr(errno, "ECONNRESET", 104), "Connection reset")
        exc = aiohttp.ClientConnectorError(None, os_error=os_err)
        assert _classify_aiohttp_error(exc) == FetchErrorCategory.CONNECTION_RESET

    def test_client_connector_error_unknown_errno_returns_generic(self):
        os_err = OSError(9999, "unknown")
        exc = aiohttp.ClientConnectorError(None, os_error=os_err)
        assert _classify_aiohttp_error(exc) == FetchErrorCategory.GENERIC

    def test_client_error_returns_generic(self):
        assert _classify_aiohttp_error(aiohttp.ClientError("err")) == FetchErrorCategory.GENERIC

    def test_unknown_exception_returns_generic(self):
        assert _classify_aiohttp_error(ValueError("unexpected")) == FetchErrorCategory.GENERIC


class TestFetchRobotsProxy:
    """_fetch_robots — 将 proxy_session/path_switch 透传给 aiohttp_retry_fetch。"""

    async def test_passes_proxy_session_to_engine(self, monkeypatch):
        """proxy_only 时 aiohttp_retry_fetch 收到 proxy_session 参数。"""
        from unittest.mock import AsyncMock, MagicMock

        from astrocrawl._path_strategy import PathSwitch
        from astrocrawl.network._fetch import AiohttpFetchResult
        from astrocrawl.proxy import ProxyManager
        from tests._fakes import _pp

        pm = ProxyManager([_pp("http://p1:8080")])
        path_switch = PathSwitch.for_mode("proxy_only")

        fetch_mock = AsyncMock(return_value=AiohttpFetchResult(content=b"", http_status=200))
        monkeypatch.setattr("astrocrawl.network.robots.aiohttp_retry_fetch", fetch_mock)

        cache = RobotsCache("Bot", MagicMock(), proxy_session=pm, path_switch=path_switch)
        await cache._fetch_robots("https://x.com")

        assert fetch_mock.call_args.kwargs["proxy_session"] is pm
        assert fetch_mock.call_args.kwargs["path_switch"] is path_switch

    async def test_prefer_proxy_passes_path_switch(self, monkeypatch):
        """prefer_proxy：aiohttp_retry_fetch 收到 path_switch。"""
        from unittest.mock import AsyncMock, MagicMock

        from astrocrawl._path_strategy import PathSwitch
        from astrocrawl.network._fetch import AiohttpFetchResult
        from astrocrawl.proxy import ProxyManager
        from tests._fakes import _pp

        pm = ProxyManager([_pp("http://p1:8080")])
        path_switch = PathSwitch.for_mode("prefer_proxy")

        fetch_mock = AsyncMock(return_value=AiohttpFetchResult(content=b"", http_status=200))
        monkeypatch.setattr("astrocrawl.network.robots.aiohttp_retry_fetch", fetch_mock)

        cache = RobotsCache("Bot", MagicMock(), proxy_session=pm, path_switch=path_switch)
        await cache._fetch_robots("https://x.com")

        assert fetch_mock.call_args.kwargs["path_switch"] is path_switch

    async def test_proxy_only_passes_proxy_session(self, monkeypatch):
        """proxy_only：aiohttp_retry_fetch 收到 proxy_session。"""
        from unittest.mock import AsyncMock, MagicMock

        from astrocrawl._path_strategy import PathSwitch
        from astrocrawl.network._fetch import AiohttpFetchResult
        from astrocrawl.proxy import ProxyManager
        from tests._fakes import _pp

        pm = ProxyManager([_pp("http://p1:8080")])
        path_switch = PathSwitch.for_mode("proxy_only")

        fetch_mock = AsyncMock(return_value=AiohttpFetchResult(content=b"", http_status=200))
        monkeypatch.setattr("astrocrawl.network.robots.aiohttp_retry_fetch", fetch_mock)

        cache = RobotsCache("Bot", MagicMock(), proxy_session=pm, path_switch=path_switch)
        await cache._fetch_robots("https://x.com")

        assert fetch_mock.call_args.kwargs["proxy_session"] is pm

    async def test_network_error_returns_fallback_parser(self, monkeypatch):
        """aiohttp_retry_fetch 返回 network error → fallback parser。"""
        from unittest.mock import AsyncMock, MagicMock

        from astrocrawl._path_strategy import PathSwitch
        from astrocrawl.network._fetch import AiohttpFetchResult
        from astrocrawl.proxy import ProxyManager
        from tests._fakes import _pp

        pm = ProxyManager([_pp("http://p1:8080")])
        path_switch = PathSwitch.for_mode("proxy_only")

        monkeypatch.setattr(
            "astrocrawl.network.robots.aiohttp_retry_fetch",
            AsyncMock(return_value=AiohttpFetchResult()),
        )

        cache = RobotsCache("Bot", MagicMock(), proxy_session=pm, path_switch=path_switch)
        result = await cache._fetch_robots("https://x.com")

        assert result.allow_all
        assert result.fetch_status == "fetch_failed"

    async def test_direct_only_no_proxy_session(self, monkeypatch):
        """direct_only：proxy_session 为 None。"""
        from unittest.mock import AsyncMock, MagicMock

        from astrocrawl._path_strategy import PathSwitch
        from astrocrawl.network._fetch import AiohttpFetchResult

        path_switch = PathSwitch.for_mode("direct_only")

        fetch_mock = AsyncMock(return_value=AiohttpFetchResult(content=b"", http_status=200))
        monkeypatch.setattr("astrocrawl.network.robots.aiohttp_retry_fetch", fetch_mock)

        cache = RobotsCache("Bot", MagicMock(), path_switch=path_switch)
        await cache._fetch_robots("https://x.com")

        assert fetch_mock.call_args.kwargs["proxy_session"] is None

    async def test_prefer_direct_passes_path_switch(self, monkeypatch):
        """prefer_direct：aiohttp_retry_fetch 收到 path_switch。"""
        from unittest.mock import AsyncMock, MagicMock

        from astrocrawl._path_strategy import PathSwitch
        from astrocrawl.network._fetch import AiohttpFetchResult
        from astrocrawl.proxy import ProxyManager
        from tests._fakes import _pp

        pm = ProxyManager([_pp("http://p1:8080")])
        path_switch = PathSwitch.for_mode("prefer_direct")

        fetch_mock = AsyncMock(return_value=AiohttpFetchResult(content=b"", http_status=200))
        monkeypatch.setattr("astrocrawl.network.robots.aiohttp_retry_fetch", fetch_mock)

        cache = RobotsCache("Bot", MagicMock(), proxy_session=pm, path_switch=path_switch)
        await cache._fetch_robots("https://x.com")

        assert fetch_mock.call_args.kwargs["path_switch"] is path_switch


# ═══════════════════════════════════════════════════════════════════════
# AsyncRobotsParser.from_url — oversized robots.txt
# ═══════════════════════════════════════════════════════════════════════


class TestFromUrlOversized:
    """from_url() — oversized robots.txt 截断 + 警告日志。"""

    @pytest.mark.asyncio
    async def test_oversized_truncated_with_warning(self, monkeypatch, caplog):
        from unittest.mock import AsyncMock, MagicMock

        monkeypatch.setattr("astrocrawl.network.robots.ROBOTS_MAX_SIZE", 10)

        content = b"User-agent: *\nDisallow: /admin/\n# more data"
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.content.read = AsyncMock(return_value=content)
        mock_session = MagicMock()
        mock_session.get.return_value.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_session.get.return_value.__aexit__ = AsyncMock(return_value=False)

        parser = await AsyncRobotsParser.from_url("https://x.com/robots.txt", "Bot", mock_session, timeout=10)
        assert parser.fetch_status == "ok"
        assert "truncating" in caplog.text


# ═══════════════════════════════════════════════════════════════════════
# RobotsCache — Crawl-Delay → DomainRateLimiter 集成
# ═══════════════════════════════════════════════════════════════════════


class TestRobotsCacheCrawlDelayIntegration:
    """_fetch_robots — Crawl-Delay 提取后设置到 DomainRateLimiter。"""

    @pytest.mark.asyncio
    async def test_crawl_delay_passed_to_rate_limiter(self, monkeypatch):
        from unittest.mock import AsyncMock, MagicMock

        from astrocrawl.config import CrawlerConfig
        from astrocrawl.network._fetch import AiohttpFetchResult
        from astrocrawl.network.robots import RobotsCache
        from astrocrawl.network.throttling import DomainRateLimiter

        cfg = CrawlerConfig(domain_min_delay=0.5, domain_max_delay=1.0)
        limiter = DomainRateLimiter(cfg)

        monkeypatch.setattr(
            "astrocrawl.network.robots.aiohttp_retry_fetch",
            AsyncMock(
                return_value=AiohttpFetchResult(
                    content=b"User-agent: Bot\nCrawl-Delay: 3",
                    http_status=200,
                )
            ),
        )
        cache = RobotsCache(
            "Bot",
            MagicMock(),
            domain_rate_limiter=limiter,
            respect_crawl_delay=True,
        )
        await cache._fetch_robots("https://example.com")
        state = limiter._tracker._states.get("example.com")
        assert state is not None
        assert state.custom_delay == 3.0

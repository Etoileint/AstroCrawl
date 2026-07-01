"""Sitemap 解析器测试"""

from __future__ import annotations

import asyncio
import gzip
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

from astrocrawl.network.sitemap import SitemapDiscovery, SitemapEntry, SitemapParser

URLSET_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://example.com/page1</loc>
    <lastmod>2024-01-15</lastmod>
    <changefreq>daily</changefreq>
    <priority>0.8</priority>
  </url>
  <url>
    <loc>https://example.com/page2</loc>
    <lastmod>2024-06-01T10:00:00Z</lastmod>
  </url>
  <url>
    <loc>https://example.com/page3</loc>
  </url>
</urlset>"""

SITEMAP_INDEX_XML = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap>
    <loc>https://example.com/sitemap1.xml</loc>
    <lastmod>2024-01-15</lastmod>
  </sitemap>
  <sitemap>
    <loc>https://example.com/sitemap2.xml</loc>
  </sitemap>
</sitemapindex>"""

URLSET_WITH_SITEMAPINDEX_PATH = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/sitemapindex.xml</loc></url>
  <url><loc>https://example.com/page2</loc></url>
</urlset>"""

NAMESPACED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<ns:urlset xmlns:ns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <ns:url>
    <ns:loc>https://example.com/page1</ns:loc>
    <ns:priority>0.5</ns:priority>
  </ns:url>
</ns:urlset>"""


class TestSitemapEntry:
    def test_defaults(self):
        entry = SitemapEntry(loc="https://example.com/")
        assert entry.loc == "https://example.com/"
        assert entry.lastmod is None
        assert entry.changefreq is None
        assert entry.priority is None

    def test_full(self):
        dt = datetime(2024, 1, 15)
        entry = SitemapEntry(
            loc="https://example.com/",
            lastmod=dt,
            changefreq="daily",
            priority=0.8,
        )
        assert entry.loc == "https://example.com/"
        assert entry.lastmod == dt
        assert entry.changefreq == "daily"
        assert entry.priority == 0.8


class TestSitemapParserUrlset:
    def test_parse_urlset(self):
        entries = SitemapParser.parse(URLSET_XML)
        assert len(entries) == 3
        assert entries[0].loc == "https://example.com/page1"
        assert entries[0].changefreq == "daily"
        assert entries[0].priority == 0.8
        assert entries[0].lastmod is not None
        assert entries[0].lastmod.year == 2024

    def test_parse_urlset_iso_tz(self):
        entries = SitemapParser.parse(URLSET_XML)
        e = entries[1]
        assert e.loc == "https://example.com/page2"
        assert e.lastmod is not None

    def test_parse_urlset_minimal_loc_only(self):
        entries = SitemapParser.parse(URLSET_XML)
        e = entries[2]
        assert e.loc == "https://example.com/page3"
        assert e.lastmod is None
        assert e.changefreq is None
        assert e.priority is None


class TestSitemapParserIndex:
    def test_parse_sitemap_index(self):
        entries = SitemapParser.parse(SITEMAP_INDEX_XML)
        assert len(entries) == 2
        assert entries[0].loc == "https://example.com/sitemap1.xml"
        assert entries[0].lastmod is not None
        assert entries[1].loc == "https://example.com/sitemap2.xml"
        assert entries[1].lastmod is None

    def test_is_index(self):
        assert SitemapParser.is_index(SITEMAP_INDEX_XML) is True
        assert SitemapParser.is_index(URLSET_XML) is False

    def test_extract_index_urls(self):
        urls = SitemapParser.extract_index_urls(SITEMAP_INDEX_XML)
        assert len(urls) == 2
        assert urls[0] == "https://example.com/sitemap1.xml"
        assert urls[1] == "https://example.com/sitemap2.xml"

    def test_is_index_not_fooled_by_url_path(self):
        """Bug 回归: URL 路径含 sitemapindex 不触发误判。"""
        assert SitemapParser.is_index(URLSET_WITH_SITEMAPINDEX_PATH) is False

    def test_parse_urlset_with_sitemapindex_path(self):
        """Bug 回归: URL 路径含 sitemapindex 的 URLSet 不会丢失 URL。"""
        entries = SitemapParser.parse(URLSET_WITH_SITEMAPINDEX_PATH)
        assert len(entries) == 2
        locs = {e.loc for e in entries}
        assert "https://example.com/sitemapindex.xml" in locs
        assert "https://example.com/page2" in locs

    def test_parse_compressed_sitemapindex(self):
        """gzip 压缩的 sitemapindex 正确提取子 sitemap URL。"""
        import gzip

        compressed = gzip.compress(SITEMAP_INDEX_XML.encode("utf-8"))
        entries = SitemapParser.parse(compressed)
        assert len(entries) >= 1
        assert entries[0].loc == "https://example.com/sitemap1.xml"

    def test_is_index_compressed_sitemapindex(self):
        """gzip 压缩的 sitemapindex 被 is_index 正确识别（原 bug: 返回 False）。"""
        import gzip

        compressed = gzip.compress(SITEMAP_INDEX_XML.encode("utf-8"))
        assert SitemapParser.is_index(compressed) is True

    def test_extract_index_urls_not_fooled_by_urlset(self):
        """extract_index_urls 对含 sitemapindex 路径的 URLSet 返回空（不误提取）。"""
        result = SitemapParser.extract_index_urls(URLSET_WITH_SITEMAPINDEX_PATH)
        assert result == []

    def test_is_index_namespaced_root(self):
        """命名空间前缀的 sitemapindex 根元素被正确识别。"""
        ns_xml = (
            b'<ns:sitemapindex xmlns:ns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            b"<ns:sitemap><ns:loc>https://a.com/s1.xml</ns:loc></ns:sitemap>"
            b"</ns:sitemapindex>"
        )
        assert SitemapParser.is_index(ns_xml) is True

    def test_is_index_compressed_urlset(self):
        """gzip 压缩的 URLSet 不被误判为 index（解压后正确检测根元素）。"""
        import gzip

        compressed = gzip.compress(URLSET_XML.encode("utf-8"))
        assert SitemapParser.is_index(compressed) is False


class TestSitemapParserGzip:
    def test_gzip_decompression(self):
        gz_data = gzip.compress(URLSET_XML.encode("utf-8"))
        entries = SitemapParser.parse(gz_data)
        assert len(entries) == 3
        assert entries[0].loc == "https://example.com/page1"

    def test_gzip_bad_data_fallback(self):
        # 损坏的 gzip 数据 — 回退为原始解析
        bad_gz = b"\x1f\x8b" + b"\x00" * 100
        entries = SitemapParser.parse(bad_gz)
        # 应返回空列表而非抛异常
        assert isinstance(entries, list)


class TestSitemapParserEdgeCases:
    def test_malformed_xml(self):
        entries = SitemapParser.parse("<not-xml>")
        assert isinstance(entries, list)

    def test_empty_content(self):
        entries = SitemapParser.parse("")
        assert entries == []

    def test_empty_bytes(self):
        entries = SitemapParser.parse(b"")
        assert entries == []

    def test_namespaced_tags(self):
        entries = SitemapParser.parse(NAMESPACED_XML)
        assert len(entries) >= 1
        assert entries[0].loc == "https://example.com/page1"
        assert entries[0].priority == 0.5


class TestSitemapParserPriority:
    def test_valid(self):
        assert SitemapParser._parse_priority("0.5") == 0.5
        assert SitemapParser._parse_priority("0.0") == 0.0
        assert SitemapParser._parse_priority("1.0") == 1.0

    def test_over_max_clamped(self):
        assert SitemapParser._parse_priority("1.5") == 1.0
        assert SitemapParser._parse_priority("99") == 1.0

    def test_under_min_clamped(self):
        assert SitemapParser._parse_priority("-0.5") == 0.0
        assert SitemapParser._parse_priority("-100") == 0.0

    def test_invalid(self):
        assert SitemapParser._parse_priority("abc") is None
        assert SitemapParser._parse_priority("") is None
        assert SitemapParser._parse_priority(None) is None

    def test_nan(self):
        assert SitemapParser._parse_priority("NaN") is None
        assert SitemapParser._parse_priority("nan") is None
        assert SitemapParser._parse_priority("-nan") is None

    def test_infinity_clamped(self):
        assert SitemapParser._parse_priority("inf") == 1.0
        assert SitemapParser._parse_priority("-inf") == 0.0


class TestSitemapParserFreq:
    def test_valid(self):
        assert SitemapParser._parse_freq("daily") == "daily"
        assert SitemapParser._parse_freq("Always") == "always"
        assert SitemapParser._parse_freq(" NEVER ") == "never"

    def test_invalid(self):
        assert SitemapParser._parse_freq("sometimes") is None
        assert SitemapParser._parse_freq("") is None
        assert SitemapParser._parse_freq(None) is None


class TestSitemapParserDate:
    def test_w3c(self):
        dt = SitemapParser._parse_date("2024-01-15T10:00:00+00:00")
        assert dt is not None
        assert dt.year == 2024
        assert dt.month == 1
        assert dt.day == 15

    def test_w3c_with_z(self):
        dt = SitemapParser._parse_date("2024-06-01T10:00:00Z")
        assert dt is not None

    def test_date_only(self):
        dt = SitemapParser._parse_date("2024-01-15")
        assert dt is not None
        assert dt.year == 2024

    def test_fractional_seconds(self):
        dt = SitemapParser._parse_date("2024-01-15T10:00:00.123456Z")
        assert dt is not None

    def test_no_colon_timezone(self):
        dt = SitemapParser._parse_date("2024-01-15T10:00:00+0000")
        assert dt is not None
        dt2 = SitemapParser._parse_date("2024-01-15T10:00:00-0530")
        assert dt2 is not None

    def test_invalid(self):
        assert SitemapParser._parse_date("not-a-date") is None
        assert SitemapParser._parse_date("") is None
        assert SitemapParser._parse_date(None) is None

    def test_rfc_1123_numeric_timezone_positive(self):
        """RFC 1123 数值时区 +0000 正确解析。"""
        dt = SitemapParser._parse_date("Mon, 15 Jan 2024 10:00:00 +0000")
        assert dt is not None
        assert dt.year == 2024
        assert dt.month == 1
        assert dt.day == 15

    def test_rfc_1123_numeric_timezone_negative(self):
        """RFC 1123 数值时区 -0500 正确解析。"""
        dt = SitemapParser._parse_date("Mon, 15 Jan 2024 10:00:00 -0500")
        assert dt is not None
        assert dt.year == 2024

    def test_rfc_1123_utc(self):
        """RFC 1123 UTC 后缀正确解析。"""
        dt = SitemapParser._parse_date("Mon, 15 Jan 2024 10:00:00 UTC")
        assert dt is not None
        assert dt.year == 2024

    def test_rfc_1123_est(self):
        """RFC 1123 EST（废弃但现行）时区正确解析。"""
        dt = SitemapParser._parse_date("Mon, 15 Jan 2024 10:00:00 EST")
        assert dt is not None
        assert dt.year == 2024


class TestSitemapCounterIsolation:
    """per-origin 计数器隔离：并发 origin 发现时 URL 计数不串扰。"""

    async def test_local_counter_increments(self):
        """本地计数器正确记录成功入队的 URL 数。"""
        enqueue_count = 0

        async def _mock_enqueue(url, depth):
            nonlocal enqueue_count
            enqueue_count += 1
            return True

        mock_cfg = MagicMock()
        mock_cfg.sitemap_max_urls = 100000
        mock_cfg.sitemap_max_recursion = 2
        mock_cfg.sitemap_additional_paths = ()
        mock_cfg.sitemap_fetch_concurrency = 10

        discovery = SitemapDiscovery.__new__(SitemapDiscovery)
        discovery._http_session = None
        discovery._robots_cache = None
        discovery._stats = MagicMock()
        discovery._stats.get_sitemap_discovered = AsyncMock(return_value=0)
        discovery._stats.record_drop = AsyncMock()
        discovery._enqueue_callback = _mock_enqueue
        discovery._stop_event = asyncio.Event()
        discovery._cfg = mock_cfg
        discovery._log = MagicMock()
        discovery._discovery_lock = asyncio.Lock()
        discovery._seen_sitemap_urls = set()
        discovery._fetch_sem = asyncio.Semaphore(10)

        async def _mock_fetch(url):
            return URLSET_XML.encode() if "sitemap" in url else None

        discovery._fetch_sitemap_content = _mock_fetch

        counter = [0]
        await discovery._fetch_and_process_sitemap(
            "https://example.com/sitemap.xml",
            sitemap_depth=0,
            enqueue_depth=1,
            counter=counter,
        )
        assert counter[0] == 3
        assert enqueue_count == 3

    async def test_concurrent_counters_isolated(self):
        """并发 origin 计数器互不串扰。"""

        async def _mock_enqueue(url, depth):
            return True

        mock_cfg = MagicMock()
        mock_cfg.sitemap_max_urls = 100000
        mock_cfg.sitemap_max_recursion = 2
        mock_cfg.sitemap_additional_paths = ()
        mock_cfg.sitemap_fetch_concurrency = 10

        async def _make_discovery():
            discovery = SitemapDiscovery.__new__(SitemapDiscovery)
            discovery._http_session = None
            discovery._robots_cache = None
            discovery._stats = MagicMock()
            discovery._stats.get_sitemap_discovered = AsyncMock(return_value=0)
            discovery._stats.record_drop = AsyncMock()
            discovery._enqueue_callback = _mock_enqueue
            discovery._stop_event = asyncio.Event()
            discovery._cfg = mock_cfg
            discovery._log = MagicMock()
            discovery._discovery_lock = asyncio.Lock()
            discovery._seen_sitemap_urls = set()
            discovery._fetch_sem = asyncio.Semaphore(10)

            async def _mock_fetch(url):
                return URLSET_XML.encode()

            discovery._fetch_sitemap_content = _mock_fetch
            return discovery

        disc_a = await _make_discovery()
        disc_b = await _make_discovery()

        counter_a = [0]
        counter_b = [0]

        await asyncio.gather(
            disc_a._fetch_and_process_sitemap(
                "https://a.example.com/sitemap.xml",
                0,
                1,
                counter_a,
            ),
            disc_b._fetch_and_process_sitemap(
                "https://b.example.com/sitemap.xml",
                0,
                1,
                counter_b,
            ),
        )

        assert counter_a[0] == 3
        assert counter_b[0] == 3


class TestFetchSitemapContentProxy:
    """_fetch_sitemap_content — 代理注入 + PathSwitch 集成测试。"""

    async def test_fetch_uses_proxy_when_configured(self, monkeypatch):
        """main_is_proxy 时 session.get 收到 proxy= 参数。"""
        from unittest.mock import AsyncMock, MagicMock

        from astrocrawl._path_strategy import PathSwitch
        from astrocrawl.network._fetch import AiohttpFetchResult
        from astrocrawl.proxy import ProxyManager
        from tests._fakes import _pp

        pm = ProxyManager([_pp("http://p1:8080")])
        path_switch = PathSwitch.for_mode("proxy_only")

        mock_cfg = MagicMock()
        mock_cfg.sitemap_fetch_concurrency = 10
        mock_cfg.sitemap_additional_paths = ()
        mock_cfg.sitemap_max_recursion = 3
        mock_cfg.sitemap_max_urls = 100000
        mock_cfg.tracking_params = frozenset()
        mock_cfg.max_retries = 3
        mock_cfg.retry_backoff_base = 2.0
        mock_cfg.user_agent = "TestBot"
        mock_cfg.custom_headers = ()
        mock_cfg.auth_basic_user = ""
        mock_cfg.auth_basic_pass = ""
        mock_cfg.auth_bearer_token = ""

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.content.read = AsyncMock(return_value=b"<xml>ok</xml>")
        mock_stats = MagicMock()
        mock_stats.record_sitemap_fetch = AsyncMock()

        fetch_mock = AsyncMock(return_value=AiohttpFetchResult(content=b"<xml>ok</xml>", http_status=200))
        monkeypatch.setattr("astrocrawl.network.sitemap.aiohttp_retry_fetch", fetch_mock)

        discovery = SitemapDiscovery(
            http_session=MagicMock(),
            robots_cache=None,
            stats=mock_stats,
            enqueue_callback=AsyncMock(),
            stop_event=asyncio.Event(),
            config=mock_cfg,
            log=MagicMock(),
            proxy_session=pm,
            path_switch=path_switch,
        )

        result = await discovery._fetch_sitemap_content("https://a.com/sitemap.xml")
        assert result == b"<xml>ok</xml>"

        assert fetch_mock.call_args.kwargs["proxy_session"] is pm
        assert fetch_mock.call_args.kwargs["path_switch"] is path_switch

    async def test_proxy_only_no_fallback_to_direct(self, monkeypatch):
        """proxy_only：aiohttp_retry_fetch 返回 None → _fetch_sitemap_content 返回 None。"""
        from unittest.mock import AsyncMock, MagicMock

        from astrocrawl._path_strategy import PathSwitch
        from astrocrawl.network._fetch import AiohttpFetchResult
        from astrocrawl.proxy import ProxyManager
        from tests._fakes import _pp

        pm = ProxyManager([_pp("http://p1:8080")])
        path_switch = PathSwitch.for_mode("proxy_only")

        mock_cfg = MagicMock()
        mock_cfg.sitemap_fetch_concurrency = 10
        mock_cfg.sitemap_additional_paths = ()
        mock_cfg.sitemap_max_recursion = 3
        mock_cfg.sitemap_max_urls = 100000
        mock_cfg.tracking_params = frozenset()
        mock_cfg.max_retries = 3
        mock_cfg.retry_backoff_base = 2.0
        mock_cfg.user_agent = "TestBot"
        mock_cfg.custom_headers = ()
        mock_cfg.auth_basic_user = ""
        mock_cfg.auth_basic_pass = ""
        mock_cfg.auth_bearer_token = ""

        mock_stats = MagicMock()
        mock_stats.record_sitemap_fetch = AsyncMock()
        monkeypatch.setattr(
            "astrocrawl.network.sitemap.aiohttp_retry_fetch", AsyncMock(return_value=AiohttpFetchResult())
        )

        discovery = SitemapDiscovery(
            http_session=MagicMock(),
            robots_cache=None,
            stats=mock_stats,
            enqueue_callback=AsyncMock(),
            stop_event=asyncio.Event(),
            config=mock_cfg,
            log=MagicMock(),
            proxy_session=pm,
            path_switch=path_switch,
        )

        result = await discovery._fetch_sitemap_content("https://a.com/sitemap.xml")
        assert result is None

    async def test_prefer_proxy_fallback_to_direct(self, monkeypatch):
        """prefer_proxy：aiohttp_retry_fetch 收到 path_switch。"""
        from unittest.mock import AsyncMock, MagicMock

        from astrocrawl._path_strategy import PathSwitch
        from astrocrawl.network._fetch import AiohttpFetchResult
        from astrocrawl.proxy import ProxyManager
        from tests._fakes import _pp

        pm = ProxyManager([_pp("http://p1:8080")])
        path_switch = PathSwitch.for_mode("prefer_proxy")

        mock_cfg = MagicMock()
        mock_cfg.sitemap_fetch_concurrency = 10
        mock_cfg.sitemap_additional_paths = ()
        mock_cfg.sitemap_max_recursion = 3
        mock_cfg.sitemap_max_urls = 100000
        mock_cfg.tracking_params = frozenset()
        mock_cfg.max_retries = 3
        mock_cfg.retry_backoff_base = 2.0
        mock_cfg.user_agent = "TestBot"
        mock_cfg.custom_headers = ()
        mock_cfg.auth_basic_user = ""
        mock_cfg.auth_basic_pass = ""
        mock_cfg.auth_bearer_token = ""

        fetch_mock = AsyncMock(return_value=AiohttpFetchResult(content=b"<xml>ok</xml>", http_status=200))
        monkeypatch.setattr("astrocrawl.network.sitemap.aiohttp_retry_fetch", fetch_mock)

        discovery = SitemapDiscovery(
            http_session=MagicMock(),
            robots_cache=None,
            stats=MagicMock(),
            enqueue_callback=AsyncMock(),
            stop_event=asyncio.Event(),
            config=mock_cfg,
            log=MagicMock(),
            proxy_session=pm,
            path_switch=path_switch,
        )
        discovery._stats.record_sitemap_fetch = AsyncMock()

        result = await discovery._fetch_sitemap_content("https://a.com/sitemap.xml")
        assert result == b"<xml>ok</xml>"
        assert fetch_mock.call_args.kwargs["path_switch"] is path_switch

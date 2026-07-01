"""Aiohttp 重试引擎测试 — _classify_aiohttp_error / classify_from_category / aiohttp_retry_fetch"""

from __future__ import annotations

import asyncio
import errno
import logging
import ssl
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from astrocrawl._retry_strategy import RetryStrategy, classify_from_category
from astrocrawl._types import FetchErrorCategory
from astrocrawl.network._fetch import (
    AiohttpFetchResult,
    _backoff_with_jitter,
    _classify_aiohttp_error,
    aiohttp_retry_fetch,
)

# ═══════════════════════════════════════════════════════════════════════
# _classify_aiohttp_error — 增强版完整映射
# ═══════════════════════════════════════════════════════════════════════


class TestClassifyAiohttpError:
    def test_asyncio_timeout_error_returns_timeout(self):
        assert _classify_aiohttp_error(asyncio.TimeoutError()) == FetchErrorCategory.TIMEOUT

    def test_server_timeout_error_returns_timeout(self):
        assert _classify_aiohttp_error(aiohttp.ServerTimeoutError()) == FetchErrorCategory.TIMEOUT

    def test_client_ssl_error_returns_ssl(self):
        os_err = ssl.SSLError(1, "TLS error")
        exc = aiohttp.ClientSSLError(None, os_err)
        assert _classify_aiohttp_error(exc) == FetchErrorCategory.SSL

    def test_client_connector_error_with_econnrefused(self):
        os_err = OSError(errno.ECONNREFUSED, "Connection refused")
        exc = aiohttp.ClientConnectorError(None, os_error=os_err)
        assert _classify_aiohttp_error(exc) == FetchErrorCategory.CONNECTION_REFUSED

    def test_client_connector_error_with_econnreset(self):
        os_err = OSError(errno.ECONNRESET, "Connection reset")
        exc = aiohttp.ClientConnectorError(None, os_error=os_err)
        assert _classify_aiohttp_error(exc) == FetchErrorCategory.CONNECTION_RESET

    def test_client_connector_error_with_ssl_oserror(self):
        os_err = ssl.SSLError(1, "TLS handshake failed")
        exc = aiohttp.ClientConnectorError(None, os_error=os_err)
        assert _classify_aiohttp_error(exc) == FetchErrorCategory.SSL

    def test_client_connector_error_unknown_errno_returns_generic(self):
        os_err = OSError(9999, "unknown")
        exc = aiohttp.ClientConnectorError(None, os_error=os_err)
        assert _classify_aiohttp_error(exc) == FetchErrorCategory.GENERIC

    def test_client_error_returns_generic(self):
        assert _classify_aiohttp_error(aiohttp.ClientError("err")) == FetchErrorCategory.GENERIC

    def test_unknown_exception_returns_generic(self):
        assert _classify_aiohttp_error(ValueError("unexpected")) == FetchErrorCategory.GENERIC


# ═══════════════════════════════════════════════════════════════════════
# classify_from_category — 新公开方法
# ═══════════════════════════════════════════════════════════════════════


class TestClassifyFromCategory:
    def test_timeout_with_proxy_returns_rotate_proxy(self):
        assert classify_from_category(FetchErrorCategory.TIMEOUT, has_proxy=True) == RetryStrategy.ROTATE_PROXY

    def test_timeout_without_proxy_returns_transient(self):
        assert classify_from_category(FetchErrorCategory.TIMEOUT, has_proxy=False) == RetryStrategy.TRANSIENT

    def test_dns_on_proxy_returns_rotate_proxy(self):
        assert classify_from_category(FetchErrorCategory.DNS, has_proxy=True) == RetryStrategy.ROTATE_PROXY

    def test_dns_without_proxy_returns_fatal(self):
        assert classify_from_category(FetchErrorCategory.DNS, has_proxy=False) == RetryStrategy.FATAL

    def test_connection_refused_on_proxy_returns_rotate_proxy(self):
        assert (
            classify_from_category(FetchErrorCategory.CONNECTION_REFUSED, has_proxy=True) == RetryStrategy.ROTATE_PROXY
        )

    def test_ssl_returns_fatal(self):
        assert classify_from_category(FetchErrorCategory.SSL, has_proxy=False) == RetryStrategy.FATAL

    def test_generic_returns_transient(self):
        assert classify_from_category(FetchErrorCategory.GENERIC, has_proxy=False) == RetryStrategy.TRANSIENT

    def test_http_4xx_returns_fatal(self):
        assert classify_from_category(FetchErrorCategory.HTTP_4XX, has_proxy=False) == RetryStrategy.FATAL

    def test_http_5xx_returns_transient(self):
        assert classify_from_category(FetchErrorCategory.HTTP_5XX, has_proxy=False) == RetryStrategy.TRANSIENT

    def test_proxy_error_returns_rotate_proxy(self):
        assert classify_from_category(FetchErrorCategory.PROXY, has_proxy=True) == RetryStrategy.ROTATE_PROXY


# ═══════════════════════════════════════════════════════════════════════
# _backoff_with_jitter
# ═══════════════════════════════════════════════════════════════════════


class TestBackoffWithJitter:
    def test_returns_value_within_range(self):
        for _ in range(100):
            result = _backoff_with_jitter(3.0)
            assert 0.0 <= result <= 3.0

    def test_returns_value_within_range_large(self):
        for _ in range(100):
            result = _backoff_with_jitter(10.0)
            assert 0.0 <= result <= 10.0


# ═══════════════════════════════════════════════════════════════════════
# AiohttpFetchResult
# ═══════════════════════════════════════════════════════════════════════


class TestAiohttpFetchResult:
    def test_defaults(self):
        r = AiohttpFetchResult()
        assert r.content is None
        assert r.http_status == 0

    def test_success_result(self):
        r = AiohttpFetchResult(content=b"ok", http_status=200)
        assert r.content == b"ok"
        assert r.http_status == 200

    def test_http_error_result(self):
        r = AiohttpFetchResult(http_status=404)
        assert r.content is None
        assert r.http_status == 404


# ═══════════════════════════════════════════════════════════════════════
# aiohttp_retry_fetch — 集成测试
# ═══════════════════════════════════════════════════════════════════════


def _make_mock_session(responses):
    """构造 mock aiohttp.ClientSession，按序返回 responses。

    responses 中每项为 (status, body) 元组或 Exception 实例。
    Exception 实例会在 session.get() 的 __aenter__ 中抛出（模拟网络异常）。
    """

    class _MockContent:
        def __init__(self, body):
            self._body = body

        async def read(self, n=-1):
            data = self._body
            return data[:n] if n > 0 and len(data) > n else data

    class _MockResponse:
        def __init__(self, status, body=b""):
            self.status = status
            self.content = _MockContent(body)

    class _MockContextManager:
        def __init__(self, resp_or_exc):
            self._resp_or_exc = resp_or_exc

        async def __aenter__(self):
            if isinstance(self._resp_or_exc, Exception):
                raise self._resp_or_exc
            return self._resp_or_exc

        async def __aexit__(self, *args):
            pass

    idx = 0

    def _get(url, timeout=None, proxy=None, headers=None):
        nonlocal idx
        item = responses[min(idx, len(responses) - 1)]
        idx += 1
        return _MockContextManager(item if isinstance(item, Exception) else _MockResponse(item[0], item[1]))

    session = MagicMock()
    session.get = _get
    return session


@pytest.fixture
def _log():
    return logging.getLogger("test_fetch")


@pytest.fixture
def _direct_path_switch():
    from astrocrawl._path_strategy import PathSwitch

    return PathSwitch.for_mode("direct_only")


class TestAiohttpRetryFetch:
    @pytest.mark.asyncio
    async def test_200_returns_content(self, _log, _direct_path_switch):
        session = _make_mock_session([(200, b"hello")])
        result = await aiohttp_retry_fetch(
            url="https://example.com/test",
            http_session=session,
            proxy_session=None,
            path_switch=_direct_path_switch,
            timeout=5.0,
            max_retries=3,
            retry_backoff_base=2.0,
            log=_log,
        )
        assert result.content == b"hello"
        assert result.http_status == 200

    @pytest.mark.asyncio
    async def test_404_returns_http_status_no_content(self, _log, _direct_path_switch):
        session = _make_mock_session([(404, b"")])
        result = await aiohttp_retry_fetch(
            url="https://example.com/notfound",
            http_session=session,
            proxy_session=None,
            path_switch=_direct_path_switch,
            timeout=5.0,
            max_retries=3,
            retry_backoff_base=2.0,
            log=_log,
        )
        assert result.content is None
        assert result.http_status == 404

    @pytest.mark.asyncio
    async def test_503_transient_retries_then_succeeds(self, _log, _direct_path_switch):
        session = _make_mock_session([(503, b""), (200, b"ok")])
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("astrocrawl.network._fetch.asyncio.sleep", AsyncMock())
            result = await aiohttp_retry_fetch(
                url="https://example.com/busy",
                http_session=session,
                proxy_session=None,
                path_switch=_direct_path_switch,
                timeout=5.0,
                max_retries=3,
                retry_backoff_base=2.0,
                log=_log,
            )
        assert result.content == b"ok"
        assert result.http_status == 200

    @pytest.mark.asyncio
    async def test_all_retries_exhausted_returns_empty(self, _log, _direct_path_switch):
        session = _make_mock_session([(503, b"")] * 10)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("astrocrawl.network._fetch.asyncio.sleep", AsyncMock())
            result = await aiohttp_retry_fetch(
                url="https://example.com/down",
                http_session=session,
                proxy_session=None,
                path_switch=_direct_path_switch,
                timeout=5.0,
                max_retries=2,
                retry_backoff_base=2.0,
                log=_log,
            )
        assert result.content is None
        assert result.http_status == 503

    @pytest.mark.asyncio
    async def test_network_error_transient_retries_then_succeeds(self, _log, _direct_path_switch):
        exc = aiohttp.ClientError("transient")
        session = _make_mock_session([exc, (200, b"recovered")])
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("astrocrawl.network._fetch.asyncio.sleep", AsyncMock())
            result = await aiohttp_retry_fetch(
                url="https://example.com/flaky",
                http_session=session,
                proxy_session=None,
                path_switch=_direct_path_switch,
                timeout=5.0,
                max_retries=3,
                retry_backoff_base=2.0,
                log=_log,
            )
        assert result.content == b"recovered"

    @pytest.mark.asyncio
    async def test_max_bytes_truncation(self, _log, _direct_path_switch):
        session = _make_mock_session([(200, b"x" * 200)])
        result = await aiohttp_retry_fetch(
            url="https://example.com/big",
            http_session=session,
            proxy_session=None,
            path_switch=_direct_path_switch,
            timeout=5.0,
            max_retries=3,
            retry_backoff_base=2.0,
            max_bytes=100,
            log=_log,
        )
        assert len(result.content) == 100

    @pytest.mark.asyncio
    async def test_max_bytes_exact_size_no_truncation(self, _log, _direct_path_switch):
        session = _make_mock_session([(200, b"x" * 100)])
        result = await aiohttp_retry_fetch(
            url="https://example.com/exact",
            http_session=session,
            proxy_session=None,
            path_switch=_direct_path_switch,
            timeout=5.0,
            max_retries=3,
            retry_backoff_base=2.0,
            max_bytes=100,
            log=_log,
        )
        assert len(result.content) == 100

    @pytest.mark.asyncio
    async def test_proxy_success_marked(self, _log):
        from astrocrawl._path_strategy import PathSwitch
        from astrocrawl.proxy import ProxyManager
        from tests._fakes import _pp

        pm = ProxyManager([_pp("http://good:8080")])
        ps = PathSwitch.for_mode("proxy_only")
        session = _make_mock_session([(200, b"ok")])

        result = await aiohttp_retry_fetch(
            url="https://example.com/via-proxy",
            http_session=session,
            proxy_session=pm,
            path_switch=ps,
            timeout=5.0,
            max_retries=3,
            retry_backoff_base=2.0,
            log=_log,
        )
        assert result.content == b"ok"
        stats = pm.health.get_all_stats()
        assert "http://good:8080" in stats
        assert stats["http://good:8080"].total_successes >= 1

    @pytest.mark.asyncio
    async def test_rotate_proxy_on_timeout(self, _log):
        from astrocrawl._path_strategy import PathSwitch
        from astrocrawl.proxy import ProxyManager
        from tests._fakes import _pp

        pm = ProxyManager([_pp("http://bad:8080"), _pp("http://good:8080")])
        ps = PathSwitch.for_mode("proxy_only")
        session = _make_mock_session([asyncio.TimeoutError(), (200, b"ok")])
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("astrocrawl.network._fetch.asyncio.sleep", AsyncMock())
            result = await aiohttp_retry_fetch(
                url="https://example.com/timeout",
                http_session=session,
                proxy_session=pm,
                path_switch=ps,
                timeout=5.0,
                max_retries=3,
                retry_backoff_base=2.0,
                log=_log,
            )
        assert result.content == b"ok"
        stats = pm.health.get_all_stats()
        assert stats["http://bad:8080"].consecutive_failures >= 1
        assert stats["http://good:8080"].total_successes >= 1

    @pytest.mark.asyncio
    async def test_prefer_direct_fallback_to_proxy(self, _log):
        from astrocrawl._path_strategy import PathSwitch
        from astrocrawl.proxy import ProxyManager
        from tests._fakes import _pp

        pm = ProxyManager([_pp("http://fallback:8080")])
        ps = PathSwitch.for_mode("prefer_direct")
        exc = aiohttp.ClientConnectorError(None, os_error=OSError(errno.ECONNREFUSED, "refused"))
        session = _make_mock_session([exc, (200, b"via-proxy")])
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("astrocrawl.network._fetch.asyncio.sleep", AsyncMock())
            result = await aiohttp_retry_fetch(
                url="https://example.com/needs-proxy",
                http_session=session,
                proxy_session=pm,
                path_switch=ps,
                timeout=5.0,
                max_retries=3,
                retry_backoff_base=2.0,
                log=_log,
            )
        assert result.content == b"via-proxy"

    @pytest.mark.asyncio
    async def test_prefer_direct_transient_error_fallback_to_proxy(self, _log):
        from astrocrawl._path_strategy import PathSwitch
        from astrocrawl.proxy import ProxyManager
        from tests._fakes import _pp

        pm = ProxyManager([_pp("http://fallback:8080")])
        ps = PathSwitch.for_mode("prefer_direct")
        exc = aiohttp.ClientError("generic transient")
        session = _make_mock_session([exc, (200, b"via-proxy-transient")])
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("astrocrawl.network._fetch.asyncio.sleep", AsyncMock())
            result = await aiohttp_retry_fetch(
                url="https://example.com/transient-fallback",
                http_session=session,
                proxy_session=pm,
                path_switch=ps,
                timeout=5.0,
                max_retries=3,
                retry_backoff_base=2.0,
                log=_log,
            )
        assert result.content == b"via-proxy-transient"

    @pytest.mark.asyncio
    async def test_prefer_proxy_fallback_to_direct(self, _log):
        from astrocrawl._path_strategy import PathSwitch
        from astrocrawl.proxy import ProxyManager
        from tests._fakes import _pp

        pm = ProxyManager([_pp("http://bad:8080")])
        ps = PathSwitch.for_mode("prefer_proxy")
        exc_proxy = aiohttp.ClientConnectorError(None, os_error=OSError(errno.ECONNREFUSED, "refused"))
        exc_direct = aiohttp.ClientError("generic")
        session = _make_mock_session([exc_proxy, exc_direct, (200, b"direct-success")])
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("astrocrawl.network._fetch.asyncio.sleep", AsyncMock())
            result = await aiohttp_retry_fetch(
                url="https://example.com/fallback-direct",
                http_session=session,
                proxy_session=pm,
                path_switch=ps,
                timeout=5.0,
                max_retries=3,
                retry_backoff_base=2.0,
                log=_log,
            )
        assert result.content == b"direct-success"

    @pytest.mark.asyncio
    async def test_proxy_only_no_fallback(self, _log):
        from astrocrawl._path_strategy import PathSwitch
        from astrocrawl.proxy import ProxyManager
        from tests._fakes import _pp

        pm = ProxyManager([_pp("http://bad:8080")])
        ps = PathSwitch.for_mode("proxy_only")
        exc = aiohttp.ClientConnectorError(None, os_error=OSError(errno.ECONNREFUSED, "refused"))
        session = _make_mock_session([exc] * 5)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("astrocrawl.network._fetch.asyncio.sleep", AsyncMock())
            result = await aiohttp_retry_fetch(
                url="https://example.com/stuck",
                http_session=session,
                proxy_session=pm,
                path_switch=ps,
                timeout=5.0,
                max_retries=3,
                retry_backoff_base=2.0,
                log=_log,
            )
        assert result.content is None
        assert result.http_status == 0

    @pytest.mark.asyncio
    async def test_ssl_error_returns_empty_no_retry(self, _log, _direct_path_switch):
        session = _make_mock_session([aiohttp.ClientSSLError(None, ssl.SSLError(1, "TLS"))])
        result = await aiohttp_retry_fetch(
            url="https://example.com/bad-tls",
            http_session=session,
            proxy_session=None,
            path_switch=_direct_path_switch,
            timeout=5.0,
            max_retries=3,
            retry_backoff_base=2.0,
            log=_log,
        )
        assert result.content is None
        assert result.http_status == 0

    @pytest.mark.asyncio
    async def test_headers_passed_to_session(self, _log, _direct_path_switch):
        session = _make_mock_session([(200, b"ok")])
        calls = []

        def _spy_get(url, timeout=None, proxy=None, headers=None):
            calls.append({"headers": headers})
            return session._real_get(url, timeout=timeout, proxy=proxy, headers=headers)

        session._real_get = session.get
        session.get = _spy_get

        await aiohttp_retry_fetch(
            url="https://example.com/headers",
            http_session=session,
            proxy_session=None,
            path_switch=_direct_path_switch,
            timeout=5.0,
            max_retries=3,
            retry_backoff_base=2.0,
            headers={"User-Agent": "TestBot", "X-Custom": "v"},
            log=_log,
        )
        assert calls[0]["headers"]["User-Agent"] == "TestBot"
        assert calls[0]["headers"]["X-Custom"] == "v"

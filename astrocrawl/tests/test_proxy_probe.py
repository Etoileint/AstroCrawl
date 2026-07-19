"""TCP 探测测试 — probe_one + ProbeResult。

使用本地 asyncio TCP server 验证可达/不可达/超时路径。
"""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError

import pytest

from astrocrawl.proxy._config import ParsedProxy, ProxyAuth, ProxyType
from astrocrawl.proxy._probe import ProbeResult, probe_one


@pytest.mark.asyncio
class TestProbeOne:
    async def test_reachable(self):
        async def handler(reader, writer):
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        async with server:
            port = server.sockets[0].getsockname()[1] if server.sockets else 0
            parsed = ParsedProxy(type=ProxyType.HTTP, host="127.0.0.1", port=port, auth=ProxyAuth())
            result = await probe_one(parsed, timeout=5.0)
            assert result.reachable is True
            assert result.latency_ms is not None
            assert result.latency_ms > 0
            assert result.error is None

    async def test_connection_refused(self):
        """未监听端口 → reachable=False + error 非空。"""
        parsed = ParsedProxy(type=ProxyType.HTTP, host="127.0.0.1", port=19999, auth=ProxyAuth())
        result = await probe_one(parsed, timeout=5.0)
        assert result.reachable is False
        assert result.error is not None

    async def test_timeout(self):
        """不可路由 IP（TEST-NET-1 RFC 5737）→ 超时返回 reachable=False。"""
        parsed = ParsedProxy(type=ProxyType.HTTP, host="192.0.2.1", port=8080, auth=ProxyAuth())
        result = await probe_one(parsed, timeout=0.5)
        assert result.reachable is False
        assert result.error is not None

    async def test_dns_failure(self):
        """无效主机名 → OSError。"""
        parsed = ParsedProxy(
            type=ProxyType.HTTP, host="invalid-hostname-that-does-not-exist.invalid", port=8080, auth=ProxyAuth()
        )
        result = await probe_one(parsed, timeout=5.0)
        assert result.reachable is False
        assert result.error is not None

    async def test_unexpected_exception_caught(self, monkeypatch):
        """非 OSError/TimeoutError 的异常 → except Exception catch-all → error=类型名。"""

        async def _mock_open_connection(*args, **kwargs):
            raise ValueError("unexpected")

        monkeypatch.setattr("astrocrawl.proxy._probe.asyncio.open_connection", _mock_open_connection)
        parsed = ParsedProxy(type=ProxyType.HTTP, host="127.0.0.1", port=8080, auth=ProxyAuth())
        result = await probe_one(parsed, timeout=5.0)
        assert result.reachable is False
        assert result.error == "ValueError"


class TestProbeResult:
    def test_frozen(self):
        pr = ProbeResult(reachable=True, latency_ms=1.5)
        with pytest.raises(FrozenInstanceError):
            pr.reachable = False  # type: ignore[misc]

    def test_success_defaults(self):
        pr = ProbeResult(reachable=True, latency_ms=1.5)
        assert pr.reachable is True
        assert pr.latency_ms == 1.5
        assert pr.error is None

    def test_failure_defaults(self):
        pr = ProbeResult(reachable=False, error="timed out")
        assert pr.reachable is False
        assert pr.error == "timed out"
        assert pr.latency_ms is None

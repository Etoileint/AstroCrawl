"""ProxySession 测试 — 生命周期 / 委托 / bypass / 查询 / DI。

使用 DI mock 注入（unittest.mock.Mock / AsyncMock）验证委托行为。
对标 tests/test_ai_client.py 模式。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from astrocrawl.proxy._config import ParsedProxy, ProxyAuth, ProxyConfig, ProxyType
from astrocrawl.proxy._hook import LoggingProxyHook
from astrocrawl.proxy._probe import ProbeResult
from astrocrawl.proxy._proxy import CircuitState, ProxyHealthTracker, ProxyManager, ProxyStats
from astrocrawl.proxy._session import ProxySession


def _make_config(bypass_domains=None):
    parsed = ParsedProxy(type=ProxyType.HTTP, host="1.2.3.4", port=8080, auth=ProxyAuth(), weight=1)
    return ProxyConfig(proxies=(parsed,), bypass_domains=bypass_domains or ())


@pytest.mark.asyncio
class TestProxySessionLifecycle:
    """ProxySession 生命周期：__aenter__ / __aexit__ / close 幂等 / closed 属性。"""

    async def test_context_manager(self):
        config = _make_config()
        session = ProxySession(config)
        assert session.closed is False
        async with session as s:
            assert s is session
            assert session.closed is False
        assert session.closed is True

    async def test_close_idempotent(self):
        config = _make_config()
        session = ProxySession(config)
        async with session:
            pass
        await session.close()  # 第二次 close noop
        assert session.closed is True

    async def test_close_no_probes(self):
        """无代理端点时不启动探针——close 安全。"""
        config = ProxyConfig()
        async with ProxySession(config) as session:
            assert session.closed is False
        assert session.closed is True


@pytest.mark.asyncio
class TestProxySessionGetProxy:
    """get_proxy 委托给 mock Manager。"""

    async def test_delegate_to_manager(self):
        mock_mgr = AsyncMock(spec=ProxyManager)
        mock_mgr.get_proxy.return_value = "http://1.2.3.4:8080"
        mock_health = MagicMock(spec=ProxyHealthTracker)
        mock_health.recovery_event = asyncio.Event()
        config = _make_config()
        session = ProxySession(config, manager=mock_mgr, health_tracker=mock_health)
        result = await session.get_proxy()
        assert result == "http://1.2.3.4:8080"
        mock_mgr.get_proxy.assert_awaited_once_with(None)

    async def test_prefer_different_than_passed(self):
        mock_mgr = AsyncMock(spec=ProxyManager)
        mock_health = MagicMock(spec=ProxyHealthTracker)
        mock_health.recovery_event = asyncio.Event()
        config = _make_config()
        session = ProxySession(config, manager=mock_mgr, health_tracker=mock_health)
        await session.get_proxy(prefer_different_than="http://another:8080")
        mock_mgr.get_proxy.assert_awaited_once_with("http://another:8080")

    async def test_returns_none_gracefully(self):
        mock_mgr = AsyncMock(spec=ProxyManager)
        mock_mgr.get_proxy.return_value = None
        mock_health = MagicMock(spec=ProxyHealthTracker)
        mock_health.recovery_event = asyncio.Event()
        config = _make_config()
        session = ProxySession(config, manager=mock_mgr, health_tracker=mock_health)
        result = await session.get_proxy()
        assert result is None


class TestProxySessionBypass:
    """is_bypass — 覆盖全部 5 种模式 + 边界条件。"""

    def _session(self, domains):
        config = _make_config(bypass_domains=domains)
        mock_mgr = MagicMock(spec=ProxyManager)
        mock_health = MagicMock(spec=ProxyHealthTracker)
        mock_health.recovery_event = asyncio.Event()
        return ProxySession(config, manager=mock_mgr, health_tracker=mock_health)

    def test_empty_domains_returns_false(self):
        session = self._session(())
        assert session.is_bypass("http://example.com/path") is False

    def test_dot_prefix_matches_root_domain(self):
        """.example.com → 匹配 example.com。"""
        session = self._session((".example.com",))
        assert session.is_bypass("http://example.com") is True

    def test_dot_prefix_matches_subdomain(self):
        """.example.com → 匹配 a.example.com。"""
        session = self._session((".example.com",))
        assert session.is_bypass("http://a.example.com/path") is True

    def test_dot_prefix_matches_deep_subdomain(self):
        """.example.com → 匹配 a.b.example.com。"""
        session = self._session((".example.com",))
        assert session.is_bypass("http://a.b.example.com") is True

    def test_star_dot_only_matches_subdomain(self):
        """*.example.com → 仅匹配子域，不匹配根域。"""
        session = self._session(("*.example.com",))
        assert session.is_bypass("http://a.example.com") is True
        assert session.is_bypass("http://example.com") is False

    def test_exact_match(self):
        """example.com → 仅精确匹配。"""
        session = self._session(("example.com",))
        assert session.is_bypass("http://example.com") is True
        assert session.is_bypass("http://a.example.com") is False

    def test_ip_exact_match(self):
        session = self._session(("192.168.1.1",))
        assert session.is_bypass("http://192.168.1.1/path") is True
        assert session.is_bypass("http://192.168.1.2/path") is False

    def test_ip_glob(self):
        session = self._session(("192.168.*",))
        assert session.is_bypass("http://192.168.1.1") is True
        assert session.is_bypass("http://192.168.2.100") is True
        assert session.is_bypass("http://10.0.0.1") is False

    def test_star_matches_all(self):
        session = self._session(("*",))
        assert session.is_bypass("http://any-domain.com") is True
        assert session.is_bypass("http://192.168.1.1") is True

    def test_case_insensitive(self):
        session = self._session((".Example.COM",))
        assert session.is_bypass("http://EXAMPLE.COM") is True
        assert session.is_bypass("http://a.Example.Com") is True

    def test_no_port_considered(self):
        session = self._session(("example.com",))
        assert session.is_bypass("http://example.com:8080/path") is True

    def test_no_url_no_hostname(self):
        session = self._session(("*.example.com",))
        assert session.is_bypass("not-a-url") is False


class TestProxySessionFeedback:
    """mark_success / mark_failure 委托 + 返回值。"""

    def _session(self):
        config = _make_config()
        mock_mgr = AsyncMock(spec=ProxyManager)
        mock_health = MagicMock(spec=ProxyHealthTracker)
        mock_health.recovery_event = asyncio.Event()
        return ProxySession(config, manager=mock_mgr, health_tracker=mock_health), mock_mgr, mock_health

    @pytest.mark.asyncio
    async def test_mark_success_delegates(self):
        session, mock_mgr, _ = self._session()
        await session.mark_success("http://1.2.3.4:8080")
        mock_mgr.mark_success.assert_awaited_once_with("http://1.2.3.4:8080")

    @pytest.mark.asyncio
    async def test_mark_failure_delegates_and_returns_circuit_state(self):
        session, mock_mgr, _ = self._session()
        mock_mgr.mark_failure.return_value = CircuitState.OPEN
        state = await session.mark_failure("http://1.2.3.4:8080")
        assert state == CircuitState.OPEN
        mock_mgr.mark_failure.assert_awaited_once_with("http://1.2.3.4:8080", weight=1)

    def test_sync_mark_success(self):
        config = _make_config()
        mock_health = MagicMock(spec=ProxyHealthTracker)
        mock_health.recovery_event = asyncio.Event()
        session = ProxySession(config, manager=MagicMock(spec=ProxyManager), health_tracker=mock_health)
        session._sync_mark_success("http://1.2.3.4:8080")
        mock_health.record_success.assert_called_once_with("http://1.2.3.4:8080", set_recovery=False)

    def test_sync_mark_failure(self):
        config = _make_config()
        mock_health = MagicMock(spec=ProxyHealthTracker)
        mock_health.recovery_event = asyncio.Event()
        session = ProxySession(config, manager=MagicMock(spec=ProxyManager), health_tracker=mock_health)
        session._sync_mark_failure("http://1.2.3.4:8080")
        mock_health.record_failure.assert_called_once_with("http://1.2.3.4:8080")


class TestProxySessionQueries:
    """纯查询方法：has_healthy / all_dead / is_available / get_all_stats / proxies。"""

    def _session(self, proxies=("http://1.2.3.4:8080",)):
        manager = MagicMock(spec=ProxyManager)
        manager.proxies = list(proxies)
        health = MagicMock(spec=ProxyHealthTracker)
        health.get_all_stats.return_value = {}
        health.all_proxies_dead.return_value = False
        health.is_available.return_value = True
        health.recovery_event = asyncio.Event()
        config = _make_config()
        session = ProxySession(config, manager=manager, health_tracker=health)
        return session, health, manager

    def test_has_healthy_empty_pool(self):
        session, _, _ = self._session(proxies=())
        assert session.has_healthy() is False

    def test_has_healthy_unused_proxy(self):
        """未使用过的代理（不在 _stats 中）视为健康。"""
        session, health, _ = self._session()
        health.get_all_stats.return_value = {}
        assert session.has_healthy() is True

    def test_has_healthy_all_unavailable(self):
        session, health, _ = self._session(proxies=("http://1.2.3.4:8080", "http://2.3.4.5:8080"))
        stats1 = ProxyStats(state=CircuitState.OPEN)
        health.get_all_stats.return_value = {"http://1.2.3.4:8080": stats1, "http://2.3.4.5:8080": stats1}
        assert session.has_healthy() is False

    def test_all_dead(self):
        session, health, _ = self._session()
        health.all_proxies_dead.return_value = True
        assert session.all_dead() is True
        health.all_proxies_dead.return_value = False
        assert session.all_dead() is False

    def test_is_available(self):
        session, health, _ = self._session()
        health.is_available.return_value = True
        assert session.is_available("http://1.2.3.4:8080") is True

    def test_get_all_stats(self):
        session, health, _ = self._session()
        stats = ProxyStats()
        health.get_all_stats.return_value = {"http://1.2.3.4:8080": stats}
        result = session.get_all_stats()
        assert "http://1.2.3.4:8080" in result

    def test_proxies_property(self):
        session, _, manager = self._session()
        manager.proxies = ["http://1.2.3.4:8080", "http://2.3.4.5:8080"]
        result = session.proxies
        assert len(result) == 2

    def test_parsed_proxies(self):
        session, _, _ = self._session()
        result = session.parsed_proxies
        assert len(result) == 1
        assert isinstance(result[0], ParsedProxy)

    def test_healthy_proxies_in_pool(self):
        session, _, manager = self._session()
        manager.healthy_proxies_in_pool.return_value = ["http://1.2.3.4:8080"]
        result = session.healthy_proxies_in_pool()
        assert result == ["http://1.2.3.4:8080"]

    def test_has_healthy_mixed(self):
        """部分健康 + 部分不可用 → has_healthy 返回 True。"""
        session, health, manager = self._session(proxies=("http://healthy:8080", "http://dead:8080"))
        manager.proxies = ["http://healthy:8080", "http://dead:8080"]
        healthy_stats = ProxyStats()
        dead_stats = ProxyStats(state=CircuitState.OPEN)
        health.get_all_stats.return_value = {
            "http://healthy:8080": healthy_stats,
            "http://dead:8080": dead_stats,
        }
        assert session.has_healthy() is True


class TestProxySessionProbe:
    """probe_all 返回 dict[str, ProbeResult]（mock probe_one 避免网络依赖）。"""

    @pytest.mark.asyncio
    async def test_probe_all_returns_probe_result_dict(self, monkeypatch):
        mock_result = ProbeResult(reachable=True, latency_ms=1.5)
        monkeypatch.setattr(
            "astrocrawl.proxy._session.probe_one",
            AsyncMock(return_value=mock_result),
        )
        config = _make_config()
        session = ProxySession(config)
        results = await session.probe_all()
        assert isinstance(results, dict)
        for _url, res in results.items():
            assert isinstance(res, ProbeResult)
            assert res.reachable is True
            assert res.latency_ms == 1.5

    @pytest.mark.asyncio
    async def test_probe_all_handles_exception(self, monkeypatch):
        mock = AsyncMock(side_effect=OSError("connection refused"))
        monkeypatch.setattr("astrocrawl.proxy._session.probe_one", mock)
        config = _make_config()
        session = ProxySession(config)
        results = await session.probe_all()
        for res in results.values():
            assert res.reachable is False
            assert res.error is not None

    @pytest.mark.asyncio
    async def test_probe_all_empty(self):
        config = ProxyConfig()
        session = ProxySession(config)
        results = await session.probe_all()
        assert results == {}


class TestProxySessionRecoveryEvent:
    """recovery_event 透传。"""

    def test_transparent(self):
        config = _make_config()
        session = ProxySession(config)
        assert session.recovery_event is session.health.recovery_event


class TestProxySessionDI:
    """DI 注入：自定义 health_tracker / manager / hook 验证。"""

    def test_custom_injections(self):
        config = _make_config()
        custom_health = Mock(spec=ProxyHealthTracker)
        custom_health.recovery_event = asyncio.Event()
        custom_manager = Mock(spec=ProxyManager)
        custom_hook = Mock()

        session = ProxySession(config, health_tracker=custom_health, manager=custom_manager, hook=custom_hook)
        assert session.health is custom_health
        assert session._manager is custom_manager
        assert session._hook is custom_hook

    def test_defaults_created(self):
        config = _make_config()
        session = ProxySession(config)
        assert session.health is not None
        assert session._manager is not None
        assert isinstance(session._hook, LoggingProxyHook)

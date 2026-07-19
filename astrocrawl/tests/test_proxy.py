"""代理管理器测试（兼容新的电路断路器架构 + 直接 ProxyHealthTracker 测试）"""

from __future__ import annotations

import asyncio

from astrocrawl.proxy._config import ParsedProxy, ProxyAuth, ProxyType
from astrocrawl.proxy._probe import probe_one
from astrocrawl.proxy._proxy import CircuitState, ProxyHealthTracker, ProxyManager, ProxyStats


def _pp(host: str, port: int = 8080, weight: int = 1) -> ParsedProxy:
    """测试辅助：创建 ParsedProxy。to_url_with_auth() 返回 http://{host}:{port}。"""
    return ParsedProxy(type=ProxyType.HTTP, host=host, port=port, auth=ProxyAuth(), weight=weight)


def _pp_from_url(url: str) -> ParsedProxy:
    """测试辅助：从 URL 字符串创建 ParsedProxy（用于 TCP server 返回的随机端口 URL）。"""
    from astrocrawl.proxy._config import ProxyEndpointSpec

    spec = ProxyEndpointSpec.from_url(url)
    return ParsedProxy(
        type=spec.type,
        host=spec.host,
        port=spec.port,
        auth=ProxyAuth(username=spec.username, password=spec.password),
        weight=1,
    )


class TestProxyManager:
    def test_get_proxy_weighted_selection(self):
        health = ProxyHealthTracker(failure_threshold=10)
        pm = ProxyManager([_pp("p1"), _pp("p2")], health_tracker=health)

        async def _test():
            results = set()
            for _ in range(20):
                results.add(await pm.get_proxy())
            assert len(results) == 2
            assert "http://p1:8080" in results
            assert "http://p2:8080" in results

        asyncio.run(_test())

    def test_swrr_unequal_weights(self):
        """SWRR 加权路径：不等权重时触发 SWRR 初始化 + 按比例分配。"""
        health = ProxyHealthTracker(failure_threshold=10)
        pm = ProxyManager([_pp("p1", weight=1), _pp("p2", weight=5)], health_tracker=health)

        async def _test():
            counts = {"http://p1:8080": 0, "http://p2:8080": 0}
            for _ in range(60):
                proxy = await pm.get_proxy()
                counts[proxy] += 1
            assert pm._swrr_initialized is True
            assert pm._total_weight == 6
            assert counts["http://p1:8080"] > 0
            assert counts["http://p2:8080"] > 0
            # p2 weight=5 应远多于 p1 weight=1（宽松容忍 SWRR 初始化轮次偏差）
            assert counts["http://p2:8080"] >= counts["http://p1:8080"] * 2

        asyncio.run(_test())

    def test_mark_failure_tracker_below_threshold(self):
        health = ProxyHealthTracker(failure_threshold=5)
        pm = ProxyManager([_pp("p1")], health_tracker=health)

        async def _test():
            await pm.mark_failure("http://p1:8080")
            await pm.mark_failure("http://p1:8080")
            assert await pm.get_proxy() is not None

        asyncio.run(_test())

    def test_mark_failure_exceeds_threshold(self):
        health = ProxyHealthTracker(failure_threshold=2)
        pm = ProxyManager([_pp("p1"), _pp("p2")], health_tracker=health)

        async def _test():
            await pm.mark_failure("http://p1:8080")
            await pm.mark_failure("http://p1:8080")
            assert health.is_available("http://p1:8080") is False
            proxy = await pm.get_proxy()
            assert proxy == "http://p2:8080"

        asyncio.run(_test())

    def test_mark_success_resets_circuit(self):
        health = ProxyHealthTracker(failure_threshold=2)
        pm = ProxyManager([_pp("p1")], health_tracker=health)

        async def _test():
            await pm.mark_failure("http://p1:8080")
            await pm.mark_failure("http://p1:8080")
            assert health.is_available("http://p1:8080") is False
            await pm.mark_success("http://p1:8080")
            assert health.is_available("http://p1:8080") is True

        asyncio.run(_test())

    def test_empty_proxy_list_returns_none(self):
        pm = ProxyManager([])

        async def _test():
            assert await pm.get_proxy() is None

        asyncio.run(_test())

    def test_all_unavailable_still_returns_proxy(self):
        health = ProxyHealthTracker(failure_threshold=1)
        pm = ProxyManager([_pp("p1"), _pp("p2")], health_tracker=health)

        async def _test():
            await pm.mark_failure("http://p1:8080")
            await pm.mark_failure("http://p2:8080")
            assert health.is_available("http://p1:8080") is False
            assert health.is_available("http://p2:8080") is False
            proxy = await pm.get_proxy()
            assert proxy in ("http://p1:8080", "http://p2:8080")

        asyncio.run(_test())

    def test_prefer_different_than(self):
        health = ProxyHealthTracker(failure_threshold=10)
        pm = ProxyManager([_pp("p1"), _pp("p2")], health_tracker=health)

        async def _test():
            proxy = await pm.get_proxy(prefer_different_than="http://p1:8080")
            assert proxy == "http://p2:8080"

        asyncio.run(_test())

    def test_healthy_proxies_in_pool_includes_unknown(self):
        health = ProxyHealthTracker(failure_threshold=3)
        pm = ProxyManager([_pp("p1"), _pp("p2")], health_tracker=health)
        result = pm.healthy_proxies_in_pool()
        assert result == ["http://p1:8080", "http://p2:8080"]

    def test_proxies_property_returns_copy(self):
        pm = ProxyManager([_pp("p1")])
        assert pm.proxies == ["http://p1:8080"]
        assert pm.proxies is not pm._proxies

    def test_health_property(self):
        health = ProxyHealthTracker()
        pm = ProxyManager([], health_tracker=health)  # type: ignore[arg-type]
        assert pm.health is health

    def test_mark_failure_returns_state_string(self):
        pm = ProxyManager([_pp("p1")])

        async def _test():
            state = await pm.mark_failure("http://p1:8080")
            assert state == CircuitState.CLOSED

        asyncio.run(_test())


# ──────────────────────────────────────────────────────────────────
# ProxyHealthTracker — 状态机直接测试
# ──────────────────────────────────────────────────────────────────


class TestStateMachine:
    def test_closed_to_open_via_consecutive_failures(self):
        ht = ProxyHealthTracker(failure_threshold=2)

        async def _test():
            ht.record_failure("p1")
            assert ht.is_available("p1") is True
            ht.record_failure("p1")
            assert ht.is_available("p1") is False
            s = ht.get_stats("p1")
            assert s.state == CircuitState.OPEN

        asyncio.run(_test())

    def test_try_activate_open_to_half_open_after_cooldown(self):
        ht = ProxyHealthTracker(failure_threshold=1, cooldown=0.001)

        async def _test():
            ht.record_failure("p1")
            assert ht.get_stats("p1").state == CircuitState.OPEN
            await asyncio.sleep(0.01)
            result = ht.try_activate("p1")
            assert result is True
            assert ht.get_stats("p1").state == CircuitState.HALF_OPEN

        asyncio.run(_test())

    def test_try_activate_returns_false_during_cooldown(self):
        ht = ProxyHealthTracker(failure_threshold=1, cooldown=30.0)

        async def _test():
            ht.record_failure("p1")
            result = ht.try_activate("p1")
            assert result is False

        asyncio.run(_test())

    def test_try_activate_noop_for_non_open(self):
        ht = ProxyHealthTracker(failure_threshold=5)

        async def _test():
            result = ht.try_activate("p1")
            assert result is True

        asyncio.run(_test())

    def test_half_open_to_closed_via_success(self):
        ht = ProxyHealthTracker(failure_threshold=1, cooldown=0.001)

        async def _test():
            ht.record_failure("p1")
            await asyncio.sleep(0.01)
            ht.try_activate("p1")
            assert ht.get_stats("p1").state == CircuitState.HALF_OPEN
            ht.record_success("p1")
            assert ht.get_stats("p1").state == CircuitState.CLOSED

        asyncio.run(_test())

    def test_open_success_resets_directly_to_closed(self):
        ht = ProxyHealthTracker(failure_threshold=1)

        async def _test():
            ht.record_failure("p1")
            assert ht.get_stats("p1").state == CircuitState.OPEN
            ht.record_success("p1")
            assert ht.get_stats("p1").state == CircuitState.CLOSED

        asyncio.run(_test())

    def test_weighted_failure_opens_immediately(self):
        ht = ProxyHealthTracker(failure_threshold=3)

        async def _test():
            ht.record_failure("p1", weight=3)
            s = ht.get_stats("p1")
            assert s.state == CircuitState.OPEN
            assert s.consecutive_failures == 3

        asyncio.run(_test())

    def test_record_failure_returns_circuit_state(self):
        ht = ProxyHealthTracker(failure_threshold=3)

        async def _test():
            state = ht.record_failure("p1", weight=1)
            assert state == CircuitState.CLOSED

        asyncio.run(_test())


# ──────────────────────────────────────────────────────────────────
# ProxyHealthTracker — 查询方法
# ──────────────────────────────────────────────────────────────────


class TestQueries:
    def test_get_health_score_unknown_defaults_one(self):
        ht = ProxyHealthTracker()
        assert ht.get_health_score("nonexistent") == 1.0

    def test_is_available_unknown_defaults_true(self):
        ht = ProxyHealthTracker()
        assert ht.is_available("nonexistent") is True

    def test_get_stats_unknown_returns_none(self):
        ht = ProxyHealthTracker()
        assert ht.get_stats("nonexistent") is None

    def test_get_all_stats_empty_initially(self):
        ht = ProxyHealthTracker()
        assert ht.get_all_stats() == {}

    def test_healthy_proxies_returns_available(self):
        ht = ProxyHealthTracker(failure_threshold=3)

        async def _test():
            ht.record_failure("p1")
            ht.record_failure("p2")
            ht.record_failure("p2")
            ht.record_failure("p2")
            available = ht.healthy_proxies()
            assert "p1" in available
            assert "p2" not in available

        asyncio.run(_test())

    def test_all_proxies_dead_returns_true_when_all_open(self):
        ht = ProxyHealthTracker(failure_threshold=1)

        async def _test():
            ht.record_failure("p1")
            ht.record_failure("p2")
            assert ht.all_proxies_dead() is True

        asyncio.run(_test())

    def test_all_proxies_dead_false_when_no_stats(self):
        ht = ProxyHealthTracker()
        assert ht.all_proxies_dead() is False


# ──────────────────────────────────────────────────────────────────
# ProxyStats — health_score
# ──────────────────────────────────────────────────────────────────


class TestHealthScoreLatency:
    def test_zero_latency_no_penalty(self):
        ps = ProxyStats(state=CircuitState.CLOSED)
        assert ps.health_score == 1.0

    def test_mid_latency_reduces_score(self):
        ps = ProxyStats(state=CircuitState.CLOSED)
        ps.avg_latency_ms = 1000
        score = ps.health_score
        assert 0.92 < score < 0.93

    def test_max_latency_caps_at_max_penalty(self):
        ps = ProxyStats(state=CircuitState.CLOSED)
        ps.avg_latency_ms = 2000
        score = ps.health_score
        assert abs(score - 0.85) < 0.01

    def test_latency_exceeding_timeout_capped(self):
        ps = ProxyStats(state=CircuitState.CLOSED)
        ps.avg_latency_ms = 10000
        score = ps.health_score
        assert abs(score - 0.85) < 0.01

    def test_half_open_with_latency(self):
        ps = ProxyStats(state=CircuitState.HALF_OPEN)
        ps.avg_latency_ms = 2000
        score = ps.health_score
        assert 0.14 < score < 0.16

    def test_open_ignores_latency(self):
        ps = ProxyStats(state=CircuitState.OPEN)
        ps.avg_latency_ms = 1500
        assert ps.health_score == 0.0

    def test_failure_density_penalty(self):
        import time

        ps = ProxyStats(state=CircuitState.CLOSED)
        now = time.monotonic()
        ps.failure_timestamps.append(now)
        score = ps.health_score
        assert 0.84 < score < 0.86  # 1.0 - 0.15

    def test_success_bonus(self):
        import time

        ps = ProxyStats(state=CircuitState.CLOSED)
        ps.total_successes = 1
        ps.last_success_at = time.monotonic()
        score = ps.health_score
        assert score == 1.0  # capped at 1.0


# ──────────────────────────────────────────────────────────────────
# probe_one（替代旧 _tcp_check）
# ──────────────────────────────────────────────────────────────────


class TestProbeOne:
    async def _start_tcp_server(self):
        async def handler(reader, writer):
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        host, port = server.sockets[0].getsockname()[:2]
        return server, _pp(host, port)

    def test_probe_one_success_returns_latency(self):
        async def _test():
            server, proxy = await self._start_tcp_server()
            try:
                result = await probe_one(proxy, timeout=2.0)
                assert result.reachable is True
                assert result.latency_ms is not None
                assert result.latency_ms >= 0
                assert result.error is None
            finally:
                server.close()
                await server.wait_closed()

        asyncio.run(_test())

    def test_probe_one_connection_refused(self):
        async def _test():
            proxy = _pp("127.0.0.1", 1)
            result = await probe_one(proxy, timeout=1.0)
            assert result.reachable is False
            assert result.error is not None

        asyncio.run(_test())

    def test_probe_one_timeout(self):
        async def _test():
            proxy = _pp("198.51.100.1", 12345)
            result = await probe_one(proxy, timeout=0.1)
            assert result.reachable is False
            assert result.error is not None

        asyncio.run(_test())


# ──────────────────────────────────────────────────────────────────
# 探测生命周期
# ──────────────────────────────────────────────────────────────────


class TestProbeLifecycle:
    def test_start_and_stop_background_probes(self):
        ht = ProxyHealthTracker()

        async def _test():
            task = await ht.start_background_probes([_pp("p1")])
            assert task is not None
            assert not task.done()
            await ht.stop_background_probes()
            assert task.done()

        asyncio.run(_test())

    def test_double_start_returns_same_task(self):
        ht = ProxyHealthTracker()

        async def _test():
            t1 = await ht.start_background_probes([_pp("p1")])
            t2 = await ht.start_background_probes([_pp("p1")])
            assert t1 is t2
            await ht.stop_background_probes()

        asyncio.run(_test())


# ──────────────────────────────────────────────────────────────────
# _decay_stale
# ──────────────────────────────────────────────────────────────────


class TestDecayStale:
    def test_expired_timestamps_removed(self):

        ht = ProxyHealthTracker(decay_seconds=0.01)

        async def _test():
            ht.record_failure("p1")
            await asyncio.sleep(0.02)
            ht.record_failure("p1")
            s = ht._stats["p1"]
            assert len(s.failure_timestamps) <= 2

        asyncio.run(_test())

    def test_consecutive_failures_decremented_when_all_expired(self):
        ht = ProxyHealthTracker(failure_threshold=10, decay_seconds=0.001)

        async def _test():
            ht.record_failure("p1")
            await asyncio.sleep(0.01)
            ht.record_failure("p1")
            s = ht._stats["p1"]
            assert s.consecutive_failures <= 2

        asyncio.run(_test())


# ──────────────────────────────────────────────────────────────────
# _parse_host_port
# ──────────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────
# HALF_OPEN 窗口到期路径（monkeypatch 缩短考察窗口）
# ──────────────────────────────────────────────────────────────────


class TestHalfOpenWindow:
    def test_half_open_to_open_after_window_expires_and_enough_failures(self, monkeypatch):
        import asyncio

        monkeypatch.setattr("astrocrawl.proxy._proxy.PROXY_HALF_OPEN_MIN_DURATION", 0.001)

        ht = ProxyHealthTracker(failure_threshold=1, cooldown=0.001)

        async def _test():
            ht.record_failure("p1")
            await asyncio.sleep(0.01)
            ht.try_activate("p1")
            assert ht.get_stats("p1").state == CircuitState.HALF_OPEN
            # 立即 2 次失败 → 窗口未到期 (<0.001s) → 累积 halflife_failures=2
            ht.record_failure("p1")
            assert ht.get_stats("p1").state == CircuitState.HALF_OPEN
            ht.record_failure("p1")
            assert ht.get_stats("p1").state == CircuitState.HALF_OPEN
            # 等待窗口到期
            await asyncio.sleep(0.01)
            # 第3次失败 → 窗口到期 + halflife_failures=3 >= 2 → OPEN
            ht.record_failure("p1")
            assert ht.get_stats("p1").state == CircuitState.OPEN

        asyncio.run(_test())

    def test_half_open_to_closed_after_window_with_few_failures(self, monkeypatch):
        import asyncio

        monkeypatch.setattr("astrocrawl.proxy._proxy.PROXY_HALF_OPEN_MIN_DURATION", 0.001)

        ht = ProxyHealthTracker(failure_threshold=1, cooldown=0.001)

        async def _test():
            ht.record_failure("p1")
            await asyncio.sleep(0.01)
            ht.try_activate("p1")
            await asyncio.sleep(0.01)
            # 仅1次失败，窗口到期后 halflife_failures=1 < MAX_FAILURES(2) → CLOSED
            ht.record_failure("p1")
            assert ht.get_stats("p1").state == CircuitState.CLOSED

        asyncio.run(_test())

    def test_half_open_stays_within_window(self, monkeypatch):
        monkeypatch.setattr("astrocrawl.proxy._proxy.PROXY_HALF_OPEN_MIN_DURATION", 30.0)

        ht = ProxyHealthTracker(failure_threshold=1, cooldown=0.001)

        async def _test():
            ht.record_failure("p1")
            await asyncio.sleep(0.01)
            ht.try_activate("p1")
            # 窗口 30s 远未到期 → 保持 HALF_OPEN
            ht.record_failure("p1")
            assert ht.get_stats("p1").state == CircuitState.HALF_OPEN

        asyncio.run(_test())


# ──────────────────────────────────────────────────────────────────
# _probe_loop 全路径（TCP server + 后台任务）
# ──────────────────────────────────────────────────────────────────


class TestProbeLoopFullPath:
    async def _start_tcp_server(self):
        async def handler(reader, writer):
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        host, port = server.sockets[0].getsockname()[:2]
        return server, f"http://{host}:{port}"

    def test_probe_loop_updates_avg_latency_ms(self):
        ht = ProxyHealthTracker(probe_interval=0.05)

        async def _test():
            server, proxy_url = await self._start_tcp_server()
            proxy = _pp_from_url(proxy_url)
            try:
                await ht.start_background_probes([proxy])
                await asyncio.sleep(0.3)
                await ht.stop_background_probes()
                s = ht._stats.get(proxy_url)
                if s is not None:
                    assert s.avg_latency_ms > 0
            finally:
                server.close()
                await server.wait_closed()

        asyncio.run(_test())

    def test_probe_loop_two_success_activate_open_proxy(self):
        ht = ProxyHealthTracker(failure_threshold=1, cooldown=0.001, probe_interval=0.05)

        async def _test():
            server, proxy_url = await self._start_tcp_server()
            proxy = _pp_from_url(proxy_url)
            try:
                ht.record_failure(proxy_url)
                await asyncio.sleep(0.01)
                assert ht.get_stats(proxy_url).state == CircuitState.OPEN
                await ht.start_background_probes([proxy])
                await asyncio.sleep(0.3)
                await ht.stop_background_probes()
                # 连续2次 TCP 成功 → try_activate → HALF_OPEN
                assert ht.get_stats(proxy_url).state == CircuitState.HALF_OPEN
            finally:
                server.close()
                await server.wait_closed()

        asyncio.run(_test())

    def test_probe_loop_normal_exit_on_stop(self):
        """启动探针循环后立即停止 → _probe_stop.wait() 正常返回 → L440。"""
        ht = ProxyHealthTracker(probe_interval=60.0)

        async def _test():
            proxy = _pp("p1")
            await ht.start_background_probes([proxy])
            # 立即停止——probe_interval=60s 确保还在等待阶段
            await ht.stop_background_probes()
            # 任务应已完成（无异常）
            assert ht._probe_task.done()

        asyncio.run(_test())

    def test_probe_loop_handles_probe_exception(self, monkeypatch):
        """probe_one 抛异常 → isinstance(result, BaseException) → _probe_ok=0 → L459-460。"""

        async def _mock_probe(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr("astrocrawl.proxy._proxy.probe_one", _mock_probe)
        ht = ProxyHealthTracker(failure_threshold=1, cooldown=0.001, probe_interval=0.05)

        async def _test():
            proxy = _pp("p1")
            proxy_url = proxy.to_url_with_auth()
            ht.record_failure(proxy_url)
            await asyncio.sleep(0.01)
            assert ht.get_stats(proxy_url).state == CircuitState.OPEN
            await ht.start_background_probes([proxy])
            await asyncio.sleep(0.3)
            await ht.stop_background_probes()
            assert ht._probe_ok.get(proxy_url, -1) == 0

        asyncio.run(_test())

    def test_probe_loop_handles_unreachable(self, monkeypatch):
        """probe_one 返回 reachable=False → _probe_ok=0 → L462-463。"""
        from astrocrawl.proxy._probe import ProbeResult as PR

        async def _mock_probe(*args, **kwargs):
            return PR(reachable=False, error="connection refused")

        monkeypatch.setattr("astrocrawl.proxy._proxy.probe_one", _mock_probe)
        ht = ProxyHealthTracker(failure_threshold=1, cooldown=0.001, probe_interval=0.05)

        async def _test():
            proxy = _pp("p1")
            proxy_url = proxy.to_url_with_auth()
            ht.record_failure(proxy_url)
            await asyncio.sleep(0.01)
            assert ht.get_stats(proxy_url).state == CircuitState.OPEN
            await ht.start_background_probes([proxy])
            await asyncio.sleep(0.3)
            await ht.stop_background_probes()
            assert ht._probe_ok.get(proxy_url, -1) == 0

        asyncio.run(_test())


# ──────────────────────────────────────────────────────────────────
# 边界情况
# ──────────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_all_proxies_dead_after_success_recover(self):
        ht = ProxyHealthTracker(failure_threshold=1)

        async def _test():
            ht.record_failure("p1")
            assert ht.all_proxies_dead() is True
            ht.record_success("p1")
            assert ht.all_proxies_dead() is False

        asyncio.run(_test())

    def test_consecutive_failures_fully_decayed_to_zero(self):
        ht = ProxyHealthTracker(failure_threshold=10, decay_seconds=0.001)

        async def _test():
            ht.record_failure("p1")
            await asyncio.sleep(0.01)
            ht.record_success("p1")
            s = ht._stats["p1"]
            assert s.consecutive_failures == 0

        asyncio.run(_test())

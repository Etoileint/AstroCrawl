"""HealthMonitor 统一健康监控调度器测试。

对标 Spring HealthEndpoint + K8s kubelet：
- HealthCheckSpec 注册 + 独立定时器调度
- A/B/C 分级 (RESTART/ALERT/REPORT)
- 主动检查 + 被动指示器聚合
- get_health() / get_health_sync() / get_health_report()
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from astrocrawl.health import Health
from astrocrawl.health_monitor import CheckOnUnhealthy, HealthCheckSpec, HealthMonitor

# ═══════════════════════════════════════════════════════════════════════
# CheckOnUnhealthy + HealthCheckSpec
# ═══════════════════════════════════════════════════════════════════════


class TestCheckOnUnhealthy:
    def test_enum_values(self):
        assert CheckOnUnhealthy.RESTART.value == "restart"
        assert CheckOnUnhealthy.ALERT.value == "alert"
        assert CheckOnUnhealthy.REPORT.value == "report"

    def test_health_check_spec_construction(self):
        async def _check() -> Health:
            return Health("UP")

        spec = HealthCheckSpec(
            name="test",
            interval=30.0,
            on_unhealthy=CheckOnUnhealthy.ALERT,
            check=_check,
        )
        assert spec.name == "test"
        assert spec.interval == 30.0
        assert spec.on_unhealthy == CheckOnUnhealthy.ALERT
        assert spec.repair is None


# ═══════════════════════════════════════════════════════════════════════
# register / register_passive / unregister
# ═══════════════════════════════════════════════════════════════════════


class TestHealthMonitorRegister:
    async def _check_up(self) -> Health:
        return Health("UP")

    def test_register_spec_adds_to_specs(self):
        monitor = HealthMonitor()
        spec = HealthCheckSpec(
            name="test",
            interval=30.0,
            on_unhealthy=CheckOnUnhealthy.ALERT,
            check=self._check_up,
        )
        monitor.register(spec)
        assert "test" in monitor._specs
        assert monitor._specs["test"] is spec

    def test_register_sets_initial_last_result(self):
        monitor = HealthMonitor()
        spec = HealthCheckSpec(
            name="test",
            interval=30.0,
            on_unhealthy=CheckOnUnhealthy.ALERT,
            check=self._check_up,
        )
        monitor.register(spec)
        result = monitor._last_results["test"]
        assert result.status == "UP"
        assert result.message == "尚未执行"

    def test_register_passive_adds_to_indicators(self):
        monitor = HealthMonitor()

        class _Indicator:
            def get_health(self) -> Health:
                return Health("UP")

        indicator = _Indicator()
        monitor.register_passive("passive1", indicator)
        # 通过公开接口验证：被动指示器出现在 get_health() 的组件中
        health = monitor.get_health_sync()
        assert "passive1" in health.details["components"]

    def test_unregister_removes_from_all_dicts(self):
        monitor = HealthMonitor()
        spec = HealthCheckSpec(
            name="test",
            interval=30.0,
            on_unhealthy=CheckOnUnhealthy.ALERT,
            check=self._check_up,
        )
        monitor.register(spec)

        class _P:
            def get_health(self) -> Health:
                return Health("UP")

        passive_obj = _P()
        monitor.register_passive("passive1", passive_obj)

        monitor.unregister("test")
        # 通过公共接口验证：被动指示器仍在，主动 spec 已移除
        health = monitor.get_health_sync()
        comps = health.details["components"]
        assert "test" not in comps
        assert "passive1" in comps

    def test_unregister_nonexistent_noop(self):
        monitor = HealthMonitor()
        # 不存在 name 不抛异常
        monitor.unregister("nonexistent")


# ═══════════════════════════════════════════════════════════════════════
# start / stop 生命周期
# ═══════════════════════════════════════════════════════════════════════


class TestHealthMonitorLifecycle:
    async def _check_up(self) -> Health:
        return Health("UP")

    async def test_start_creates_tasks(self):
        monitor = HealthMonitor()
        spec = HealthCheckSpec(
            name="h1",
            interval=3600.0,
            on_unhealthy=CheckOnUnhealthy.ALERT,
            check=self._check_up,
        )
        monitor.register(spec)
        await monitor.start()
        try:
            assert len(monitor._tasks) == 1
            assert monitor._tasks[0].get_name() == "HealthCheck-h1"
        finally:
            await monitor.stop()

    async def test_start_idempotent(self):
        monitor = HealthMonitor()
        spec = HealthCheckSpec(
            name="h1",
            interval=3600.0,
            on_unhealthy=CheckOnUnhealthy.ALERT,
            check=self._check_up,
        )
        monitor.register(spec)
        await monitor.start()
        try:
            task_count = len(monitor._tasks)
            await monitor.start()  # 第二次应直接返回
            assert len(monitor._tasks) == task_count
        finally:
            await monitor.stop()

    async def test_stop_cancels_all_tasks(self):
        monitor = HealthMonitor()
        spec = HealthCheckSpec(
            name="h1",
            interval=3600.0,
            on_unhealthy=CheckOnUnhealthy.ALERT,
            check=self._check_up,
        )
        monitor.register(spec)
        await monitor.start()
        await monitor.stop()
        assert len(monitor._tasks) == 0

    async def test_stop_idempotent(self):
        monitor = HealthMonitor()
        spec = HealthCheckSpec(
            name="h1",
            interval=3600.0,
            on_unhealthy=CheckOnUnhealthy.ALERT,
            check=self._check_up,
        )
        monitor.register(spec)
        await monitor.start()
        await monitor.stop()
        # 第二次 stop 不报错
        await monitor.stop()

    async def test_restart_after_stop(self):
        monitor = HealthMonitor()
        spec = HealthCheckSpec(
            name="h1",
            interval=3600.0,
            on_unhealthy=CheckOnUnhealthy.ALERT,
            check=self._check_up,
        )
        monitor.register(spec)
        await monitor.start()
        await monitor.stop()
        # 重新启动
        await monitor.start()
        try:
            assert len(monitor._tasks) == 1
        finally:
            await monitor.stop()

    async def test_register_after_start_no_task(self):
        """start() 后 register() 新 spec — 无后台 task 创建。"""
        monitor = HealthMonitor()
        spec1 = HealthCheckSpec(
            name="h1",
            interval=3600.0,
            on_unhealthy=CheckOnUnhealthy.ALERT,
            check=self._check_up,
        )
        monitor.register(spec1)
        await monitor.start()
        try:
            task_count = len(monitor._tasks)
            spec2 = HealthCheckSpec(
                name="h2",
                interval=3600.0,
                on_unhealthy=CheckOnUnhealthy.ALERT,
                check=self._check_up,
            )
            monitor.register(spec2)
            assert len(monitor._tasks) == task_count
        finally:
            await monitor.stop()


# ═══════════════════════════════════════════════════════════════════════
# _execute_check — A/B/C 分级 + 异常路径
# ═══════════════════════════════════════════════════════════════════════


class TestHealthMonitorExecuteCheck:
    async def test_check_up_result_stored(self):
        async def _check() -> Health:
            return Health("UP", "all good")

        spec = HealthCheckSpec(
            name="test",
            interval=30.0,
            on_unhealthy=CheckOnUnhealthy.ALERT,
            check=_check,
        )
        monitor = HealthMonitor()
        await monitor._execute_check("test", spec)
        assert monitor._last_results["test"].status == "UP"
        assert monitor._last_results["test"].message == "all good"

    async def test_check_down_result_stored(self):
        async def _check() -> Health:
            return Health("DOWN", "dead")

        spec = HealthCheckSpec(
            name="test",
            interval=30.0,
            on_unhealthy=CheckOnUnhealthy.ALERT,
            check=_check,
        )
        monitor = HealthMonitor()
        await monitor._execute_check("test", spec)
        assert monitor._last_results["test"].status == "DOWN"

    async def test_check_exception_stored_as_down(self):
        async def _check() -> Health:
            raise RuntimeError("unexpected failure")

        spec = HealthCheckSpec(
            name="test",
            interval=30.0,
            on_unhealthy=CheckOnUnhealthy.ALERT,
            check=_check,
        )
        monitor = HealthMonitor()
        await monitor._execute_check("test", spec)
        result = monitor._last_results["test"]
        assert result.status == "DOWN"
        assert "unexpected failure" in result.message

    async def test_check_cancelled_error_propagates(self):
        async def _check() -> Health:
            raise asyncio.CancelledError()

        spec = HealthCheckSpec(
            name="test",
            interval=30.0,
            on_unhealthy=CheckOnUnhealthy.ALERT,
            check=_check,
        )
        monitor = HealthMonitor()
        with pytest.raises(asyncio.CancelledError):
            await monitor._execute_check("test", spec)

    async def test_restart_runs_repair_on_non_up(self):
        repair_called = False

        async def _check() -> Health:
            return Health("DOWN", "broken")

        async def _repair() -> None:
            nonlocal repair_called
            repair_called = True

        spec = HealthCheckSpec(
            name="test",
            interval=30.0,
            on_unhealthy=CheckOnUnhealthy.RESTART,
            check=_check,
            repair=_repair,
        )
        monitor = HealthMonitor()
        await monitor._execute_check("test", spec)
        assert repair_called is True

    async def test_restart_no_repair_skips_action(self, caplog):
        async def _check() -> Health:
            return Health("DOWN", "broken")

        spec = HealthCheckSpec(
            name="test",
            interval=30.0,
            on_unhealthy=CheckOnUnhealthy.RESTART,
            check=_check,
            repair=None,
        )
        monitor = HealthMonitor()
        with caplog.at_level(logging.WARNING, logger="astrocrawl.health"):
            await monitor._execute_check("test", spec)
        # RESTART 无 repair → 不执行修复，不记录修复错误
        assert monitor._last_results["test"].status == "DOWN"

    async def test_alert_logs_warning_on_non_up(self, caplog):
        async def _check() -> Health:
            return Health("DOWN", "something wrong")

        spec = HealthCheckSpec(
            name="test",
            interval=30.0,
            on_unhealthy=CheckOnUnhealthy.ALERT,
            check=_check,
        )
        monitor = HealthMonitor()
        with caplog.at_level(logging.WARNING, logger="astrocrawl.health"):
            await monitor._execute_check("test", spec)
        assert "event=health_alert" in caplog.text
        assert "name=test" in caplog.text
        assert "status=DOWN" in caplog.text

    async def test_report_no_side_effect_on_non_up(self, caplog):
        """REPORT + 非 UP → 无日志、无修复，仅存储结果。"""

        async def _check() -> Health:
            return Health("DOWN", "informational")

        spec = HealthCheckSpec(
            name="test",
            interval=30.0,
            on_unhealthy=CheckOnUnhealthy.REPORT,
            check=_check,
        )
        monitor = HealthMonitor()
        with caplog.at_level(logging.WARNING, logger="astrocrawl.health"):
            await monitor._execute_check("test", spec)
        # 无 WARNING 日志
        assert "HealthCheck test:" not in caplog.text
        assert monitor._last_results["test"].status == "DOWN"

    async def test_repair_exception_logged(self, caplog):
        async def _check() -> Health:
            return Health("DOWN")

        async def _repair() -> None:
            raise RuntimeError("repair failed")

        spec = HealthCheckSpec(
            name="test",
            interval=30.0,
            on_unhealthy=CheckOnUnhealthy.RESTART,
            check=_check,
            repair=_repair,
        )
        monitor = HealthMonitor()
        with caplog.at_level(logging.ERROR, logger="astrocrawl.health"):
            await monitor._execute_check("test", spec)
        assert "event=health_repair_failed" in caplog.text
        assert "repair failed" in caplog.text

    async def test_repair_cancelled_error_propagates(self):
        """repair() 抛 CancelledError → 不被 except Exception 捕获，向上传播。"""

        async def _check() -> Health:
            return Health("DOWN")

        async def _repair() -> None:
            raise asyncio.CancelledError()

        spec = HealthCheckSpec(
            name="test",
            interval=30.0,
            on_unhealthy=CheckOnUnhealthy.RESTART,
            check=_check,
            repair=_repair,
        )
        monitor = HealthMonitor()
        with pytest.raises(asyncio.CancelledError):
            await monitor._execute_check("test", spec)


# ═══════════════════════════════════════════════════════════════════════
# get_health() — 主动 + 被动聚合
# ═══════════════════════════════════════════════════════════════════════


class TestHealthMonitorGetHealth:
    async def test_all_active_up_returns_up(self):
        async def _check_up() -> Health:
            return Health("UP")

        monitor = HealthMonitor()
        spec = HealthCheckSpec(
            name="check1",
            interval=30.0,
            on_unhealthy=CheckOnUnhealthy.ALERT,
            check=_check_up,
        )
        monitor.register(spec)
        # 模拟已执行过检查
        monitor._last_results["check1"] = Health("UP")
        result = await monitor.get_health()
        assert result.status == "UP"

    async def test_one_active_down_returns_down(self):
        async def _check_up() -> Health:
            return Health("UP")

        monitor = HealthMonitor()
        spec = HealthCheckSpec(
            name="check1",
            interval=30.0,
            on_unhealthy=CheckOnUnhealthy.ALERT,
            check=_check_up,
        )
        monitor.register(spec)
        monitor._last_results["check1"] = Health("DOWN", "dead")
        result = await monitor.get_health()
        assert result.status == "DOWN"

    async def test_mixed_active_and_passive(self):
        async def _check_up() -> Health:
            return Health("UP")

        monitor = HealthMonitor()
        spec = HealthCheckSpec(
            name="active1",
            interval=30.0,
            on_unhealthy=CheckOnUnhealthy.ALERT,
            check=_check_up,
        )
        monitor.register(spec)
        monitor._last_results["active1"] = Health("UP")

        class _Passive:
            def get_health(self) -> Health:
                return Health("DEGRADED", "slow")

        monitor.register_passive("passive1", _Passive())
        result = await monitor.get_health()
        assert result.status == "DEGRADED"

    async def test_passive_indicator_sync_call(self):
        """被动指示器同步调用 — 不经过 await，直接返回 Health。"""
        monitor = HealthMonitor()

        class _Fast:
            def get_health(self) -> Health:
                return Health("DEGRADED", "slow response")

        monitor.register_passive("fast", _Fast())
        result = await monitor.get_health()
        assert result.status == "DEGRADED"
        fast_health = result.details["components"]["fast"]
        assert isinstance(fast_health, Health)
        assert fast_health.status == "DEGRADED"

    async def test_passive_indicator_exception(self):
        monitor = HealthMonitor()

        class _Failing:
            def get_health(self) -> Health:
                raise RuntimeError("boom")

        monitor.register_passive("fail", _Failing())
        result = await monitor.get_health()
        assert result.status == "DOWN"
        fail_health = result.details["components"]["fail"]
        assert isinstance(fail_health, Health)
        assert fail_health.status == "DOWN"
        assert "boom" in fail_health.message


# ═══════════════════════════════════════════════════════════════════════
# get_health_sync()
# ═══════════════════════════════════════════════════════════════════════


class TestHealthMonitorGetHealthSync:
    async def _check_up(self) -> Health:
        return Health("UP")

    def test_sync_snapshot_reflects_cached_results(self):
        monitor = HealthMonitor()
        spec = HealthCheckSpec(
            name="active1",
            interval=30.0,
            on_unhealthy=CheckOnUnhealthy.ALERT,
            check=self._check_up,
        )
        monitor.register(spec)
        monitor._last_results["active1"] = Health("DEGRADED", "slow response")
        result = monitor.get_health_sync()
        assert result.status == "DEGRADED"

    def test_sync_calls_passive_get_health(self):
        """get_health_sync() 直接调用被动指示器的 get_health()。"""

        class _Degraded:
            def get_health(self) -> Health:
                return Health("DEGRADED", "degraded for test")

        monitor = HealthMonitor()
        monitor.register_passive("p", _Degraded())
        result = monitor.get_health_sync()
        assert result.status == "DEGRADED"
        passive_result = result.details["components"]["p"]
        assert isinstance(passive_result, Health)
        assert passive_result.status == "DEGRADED"

    def test_sync_passive_exception_becomes_down(self):
        """被动指示器抛异常时 get_health_sync() 报告 DOWN。"""

        class _Broken:
            def get_health(self) -> Health:
                raise RuntimeError("boom")

        monitor = HealthMonitor()
        monitor.register_passive("b", _Broken())
        result = monitor.get_health_sync()
        assert result.status == "DOWN"

    def test_sync_passive_down_status(self):
        """被动指示器正常返回 DOWN 时 get_health_sync() 报告 DOWN（非异常路径）。"""

        class _Down:
            def get_health(self) -> Health:
                return Health("DOWN", "critical failure")

        monitor = HealthMonitor()
        monitor.register_passive("d", _Down())
        result = monitor.get_health_sync()
        assert result.status == "DOWN"
        comp = result.details["components"]["d"]
        assert comp.status == "DOWN"
        assert "critical failure" in comp.message


# ═══════════════════════════════════════════════════════════════════════
# get_health_report()
# ═══════════════════════════════════════════════════════════════════════


class TestHealthMonitorGetHealthReport:
    async def _check_up(self) -> Health:
        return Health("UP")

    def test_report_structure(self):
        monitor = HealthMonitor()
        report = monitor.get_health_report()
        assert "status" in report
        assert "uptime_seconds" in report
        assert "components" in report
        assert report["status"] == "UP"
        assert isinstance(report["uptime_seconds"], float)
        assert isinstance(report["components"], dict)

    def test_uptime_seconds_increases(self):
        import time

        monitor = HealthMonitor()
        report1 = monitor.get_health_report()
        time.sleep(0.1)
        report2 = monitor.get_health_report()
        assert report2["uptime_seconds"] >= report1["uptime_seconds"]

    def test_report_empty_no_components(self):
        monitor = HealthMonitor()
        report = monitor.get_health_report()
        assert report["components"] == {}

    def test_report_reflects_passive_health(self):
        """get_health_report() 反映被动指示器真实健康状态 — on_fatal() 关键路径。"""
        monitor = HealthMonitor()

        class _Bad:
            def get_health(self) -> Health:
                return Health("DOWN", "component dead")

        monitor.register_passive("critical", _Bad())
        report = monitor.get_health_report()
        assert report["status"] == "DOWN"
        comp = report["components"]["critical"]
        assert comp["status"] == "DOWN"


# ═══════════════════════════════════════════════════════════════════════
# _per_timer_run 调度循环
# ═══════════════════════════════════════════════════════════════════════


class TestHealthMonitorScheduling:
    """_per_timer_run 定时器调度行为：自动点火、stop 中断、循环退出。"""

    async def test_per_timer_runs_check_automatically(self):
        """start() 后 _per_timer_run 立即点火执行 check（无初始延迟）。"""
        monitor = HealthMonitor()
        call_count = 0

        async def _check() -> Health:
            nonlocal call_count
            call_count += 1
            return Health("UP")

        spec = HealthCheckSpec(name="t", interval=3600.0, on_unhealthy=CheckOnUnhealthy.ALERT, check=_check)
        monitor.register(spec)
        await monitor.start()
        await asyncio.sleep(0.05)
        await monitor.stop()
        assert call_count >= 1

    async def test_stop_during_check_execution_cancels(self):
        """stop() 在 check 阻塞期间取消任务，不启动下一轮循环。"""
        monitor = HealthMonitor()
        entered = asyncio.Event()
        call_count = 0

        async def _check() -> Health:
            nonlocal call_count
            call_count += 1
            entered.set()
            # 阻塞直到被 cancel
            await asyncio.Event().wait()
            return Health("UP")

        spec = HealthCheckSpec(name="t", interval=3600.0, on_unhealthy=CheckOnUnhealthy.ALERT, check=_check)
        monitor.register(spec)
        await monitor.start()
        await entered.wait()
        await monitor.stop()
        assert call_count == 1

    async def test_stop_during_wait_interval_exits(self):
        """stop() 在等待间隔期间被调用，通过 _stop Event 立即退出循环。"""
        monitor = HealthMonitor()
        call_count = 0

        async def _check() -> Health:
            nonlocal call_count
            call_count += 1
            return Health("UP")

        # 短间隔确保首轮快速完成
        spec = HealthCheckSpec(name="t", interval=0.1, on_unhealthy=CheckOnUnhealthy.ALERT, check=_check)
        monitor.register(spec)
        await monitor.start()
        await asyncio.sleep(0.02)
        await monitor.stop()
        assert call_count == 1


# ═══════════════════════════════════════════════════════════════════════
# 主动/被动同名冲突
# ═══════════════════════════════════════════════════════════════════════


class TestHealthMonitorNameCollision:
    """主动 spec 和被动 indicator 同名时，被动覆盖主动（L141-145 在 L138-139 之后执行）。"""

    async def test_get_health_passive_overwrites_active_same_name(self):
        """get_health() — 被动结果覆盖同名的主动缓存结果。"""
        monitor = HealthMonitor()

        async def _check() -> Health:
            return Health("DEGRADED", "from active check")

        spec = HealthCheckSpec(name="dup", interval=3600.0, on_unhealthy=CheckOnUnhealthy.ALERT, check=_check)
        monitor.register(spec)
        monitor._last_results["dup"] = Health("DEGRADED", "cached degraded")

        class _Passive:
            def get_health(self) -> Health:
                return Health("UP", "fresh from passive")

        monitor.register_passive("dup", _Passive())
        result = await monitor.get_health()
        dup_health = result.details["components"]["dup"]
        assert dup_health.status == "UP"
        assert "fresh from passive" in dup_health.message

    def test_get_health_sync_passive_overwrites_active_degraded(self):
        """get_health_sync() — 被动真实状态覆盖主动 DEGRADED 缓存。"""
        monitor = HealthMonitor()

        class _Passive:
            def get_health(self) -> Health:
                return Health("UP", "passive ok")

        spec = HealthCheckSpec(
            name="dup",
            interval=30.0,
            on_unhealthy=CheckOnUnhealthy.ALERT,
            check=lambda: Health("UP"),
        )
        monitor.register(spec)
        monitor._last_results["dup"] = Health("DEGRADED", "slow but alive")
        monitor.register_passive("dup", _Passive())
        result = monitor.get_health_sync()
        dup_health = result.details["components"]["dup"]
        assert dup_health.status == "UP"


# ═══════════════════════════════════════════════════════════════════════
# 端到端集成
# ═══════════════════════════════════════════════════════════════════════


class TestHealthMonitorIntegration:
    """完整生命周期链路：register → start → 自动调度 → get_health → stop。"""

    async def test_full_lifecycle_register_start_query_stop(self):
        """register → start → 定时器自动执行 check → get_health 反映结果 → stop。"""
        monitor = HealthMonitor()
        check_count = 0

        async def _check() -> Health:
            nonlocal check_count
            check_count += 1
            return Health("UP", f"check #{check_count}")

        spec = HealthCheckSpec(name="e2e", interval=3600.0, on_unhealthy=CheckOnUnhealthy.ALERT, check=_check)
        monitor.register(spec)
        await monitor.start()
        await asyncio.sleep(0.05)
        health = await monitor.get_health()
        await monitor.stop()

        assert check_count >= 1
        assert "e2e" in health.details["components"]
        assert health.details["components"]["e2e"].status == "UP"


# ═══════════════════════════════════════════════════════════════════════
# DEGRADED × A/B/C 三级分类
# ═══════════════════════════════════════════════════════════════════════


class TestHealthMonitorDegradedStatus:
    """DEGRADED 作为不同于 DOWN 的状态，应对 A/B/C 三级分类正确响应。"""

    async def test_degraded_with_alert_logs(self, caplog):
        """DEGRADED + ALERT → 记录 warning（与 DOWN 相同处理）。"""

        async def _check() -> Health:
            return Health("DEGRADED", "sluggish")

        spec = HealthCheckSpec(name="t", interval=30.0, on_unhealthy=CheckOnUnhealthy.ALERT, check=_check)
        monitor = HealthMonitor()
        with caplog.at_level(logging.WARNING, logger="astrocrawl.health"):
            await monitor._execute_check("t", spec)
        assert "event=health_alert" in caplog.text
        assert "status=DEGRADED" in caplog.text

    async def test_degraded_with_restart_runs_repair(self):
        """DEGRADED + RESTART + repair → 执行修复动作。"""
        repaired = False

        async def _check() -> Health:
            return Health("DEGRADED", "needs repair")

        async def _repair() -> None:
            nonlocal repaired
            repaired = True

        spec = HealthCheckSpec(
            name="t",
            interval=30.0,
            on_unhealthy=CheckOnUnhealthy.RESTART,
            check=_check,
            repair=_repair,
        )
        monitor = HealthMonitor()
        await monitor._execute_check("t", spec)
        assert repaired

    async def test_degraded_with_report_silent(self, caplog):
        """DEGRADED + REPORT → 无日志、无动作，仅存储结果。"""

        async def _check() -> Health:
            return Health("DEGRADED", "just fyi")

        spec = HealthCheckSpec(name="t", interval=30.0, on_unhealthy=CheckOnUnhealthy.REPORT, check=_check)
        monitor = HealthMonitor()
        with caplog.at_level(logging.WARNING, logger="astrocrawl.health"):
            await monitor._execute_check("t", spec)
        assert monitor._last_results["t"].status == "DEGRADED"
        assert "health_alert" not in caplog.text
        assert "health_repair" not in caplog.text


# ═══════════════════════════════════════════════════════════════════════
# 边界用例
# ═══════════════════════════════════════════════════════════════════════


class TestHealthMonitorEdgeCases:
    """低概率边界路径：空 specs、重复注册、stop-before-start。"""

    async def test_get_health_empty_no_registrations(self):
        """无任何注册时 get_health() 返回 UP。"""
        monitor = HealthMonitor()
        result = await monitor.get_health()
        assert result.status == "UP"

    def test_get_health_sync_empty_no_registrations(self):
        """无任何注册时 get_health_sync() 返回 UP。"""
        monitor = HealthMonitor()
        result = monitor.get_health_sync()
        assert result.status == "UP"

    def test_register_duplicate_name_overwrites_silently(self):
        """同名 register 不抛异常，使用第二次的初始值。"""
        monitor = HealthMonitor()
        spec1 = HealthCheckSpec(
            name="a", interval=30.0, on_unhealthy=CheckOnUnhealthy.ALERT, check=lambda: Health("UP")
        )
        spec2 = HealthCheckSpec(
            name="a", interval=60.0, on_unhealthy=CheckOnUnhealthy.REPORT, check=lambda: Health("UP")
        )
        monitor.register(spec1)
        monitor.register(spec2)
        result = monitor.get_health_sync()
        assert "a" in result.details["components"]

    async def test_stop_without_start_no_error(self):
        """未 start 时直接 stop 不抛异常。"""
        monitor = HealthMonitor()
        await monitor.stop()

    async def test_start_with_zero_specs_no_error(self):
        """无注册 spec 时 start() 合法——不创建 task，get_health 返回 UP。"""
        monitor = HealthMonitor()
        await monitor.start()
        try:
            result = await monitor.get_health()
            assert result.status == "UP"
        finally:
            await monitor.stop()

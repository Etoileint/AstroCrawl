"""HealthMonitor — 统一健康监控调度器。

对标 Spring HealthEndpoint + K8s kubelet 的融合：
- 被动报告：get_health() → Health（快速，无副作用）
- 主动探测：HealthCheckSpec.check() → Health（可能慢，可能有副作用）
- A/B/C 分类通过 CheckOnUnhealthy 枚举代码化

不负责 HTTP 端点（CrawlDiagnostics）、Task dump（TaskDumper）。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable, Optional

from astrocrawl.health import Health, HealthChecked, health_to_report

_log = logging.getLogger("astrocrawl.health")


# ═══════════════════════════════════════════════════════════════════════
# CheckOnUnhealthy — A/B/C 分级代码化
# ═══════════════════════════════════════════════════════════════════════


class CheckOnUnhealthy(Enum):
    """对标 K8s restartPolicy + Envoy outlier ejection 的融合。"""

    RESTART = "restart"  # A 类：执行 repair 修复动作
    ALERT = "alert"  # B 类：仅日志告警
    REPORT = "report"  # C 类：仅反映在 get_health() 中


@dataclass(frozen=True)
class HealthCheckSpec:
    """一个主动健康检查的规格。对标 K8s Probe + Spring @Scheduled。"""

    name: str
    interval: float
    on_unhealthy: CheckOnUnhealthy
    check: Callable[[], Awaitable[Health]]
    repair: Optional[Callable[[], Awaitable[None]]] = None


# ═══════════════════════════════════════════════════════════════════════
# HealthMonitor — 统一调度器
# ═══════════════════════════════════════════════════════════════════════


class HealthMonitor:
    """统一健康监控调度器。对标 Spring HealthEndpoint + K8s kubelet。

    一个后台协程管理所有 HealthCheckSpec 的调度。
    同时聚合被动指示器（替代旧的 HealthAggregator）。
    不负责 HTTP 端点或 Task dump。
    """

    def __init__(self) -> None:
        self._specs: dict[str, HealthCheckSpec] = {}
        self._last_results: dict[str, Health] = {}
        self._stop: asyncio.Event = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self._passive_indicators: dict[str, HealthChecked] = {}
        self._start_time = time.time()

    def register(self, spec: HealthCheckSpec) -> None:
        self._specs[spec.name] = spec
        self._last_results[spec.name] = Health("UP", "尚未执行")

    def register_passive(self, name: str, indicator: HealthChecked) -> None:
        """注册被动健康指示器（HTTP /health 会查询，但 Monitor 不主动调度）。"""
        self._passive_indicators[name] = indicator

    def unregister(self, name: str) -> None:
        """注销健康指示器（主动或被动）。"""
        self._specs.pop(name, None)
        self._last_results.pop(name, None)
        self._passive_indicators.pop(name, None)

    async def start(self) -> None:
        """启动所有 HealthCheckSpec 的独立定时器。幂等——重复调用无副作用。"""
        if self._tasks:
            return
        self._stop.clear()
        for name, spec in self._specs.items():
            self._tasks.append(asyncio.create_task(self._per_timer_run(name, spec), name=f"HealthCheck-{name}"))

    async def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        for t in self._tasks:
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()

    async def _per_timer_run(self, name: str, spec: HealthCheckSpec) -> None:
        """每个 HealthCheckSpec 的独立定时器。对标 K8s kubelet per-probe timer。"""
        while not self._stop.is_set():
            await self._execute_check(name, spec)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=spec.interval)
            except asyncio.TimeoutError:
                pass

    async def _execute_check(self, name: str, spec: HealthCheckSpec) -> None:
        try:
            result = await spec.check()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            result = Health("DOWN", str(e))
        self._last_results[name] = result

        if result.status == "UP":
            return

        if spec.on_unhealthy == CheckOnUnhealthy.RESTART and spec.repair:
            _log.warning("event=health_repair name=%s status=%s", name, result.status)
            try:
                await spec.repair()
            except Exception as e:
                _log.error("event=health_repair_failed name=%s error=%s", name, e)
        elif spec.on_unhealthy == CheckOnUnhealthy.ALERT:
            _log.warning("event=health_alert name=%s status=%s message=%s", name, result.status, result.message)

    async def get_health(self) -> Health:
        """异步聚合所有主动检查 + 被动指示器的健康状态。

        对标 Spring Actuator HealthEndpoint.invoke() → HealthAggregator.aggregate()。
        被动指示器同步调用（HealthChecked 协议：快速返回，无副作用）。
        """
        components: dict[str, Health] = {}
        # 主动检查结果（同步快照，无需超时）
        for name in self._specs:
            components[name] = self._last_results.get(name, Health("UP"))
        # 被动指示器（同步查询 — HealthChecked 协议：快速、无副作用）
        for name, indicator in self._passive_indicators.items():
            try:
                components[name] = indicator.get_health()
            except Exception as e:
                components[name] = Health("DOWN", str(e))
        return Health.aggregate(components)

    def get_health_sync(self) -> Health:
        """同步聚合（仅主动检查 + 缓存的被动结果，不触发异步查询）。

        供同步上下文使用，可能落后于实时状态。
        """
        components: dict[str, Health] = {}
        for name in self._specs:
            components[name] = self._last_results.get(name, Health("UP"))
        for name, indicator in self._passive_indicators.items():
            try:
                components[name] = indicator.get_health()
            except Exception as e:
                components[name] = Health("DOWN", str(e))
        return Health.aggregate(components)

    def get_health_report(self) -> dict:
        """返回与旧 AggregateHealth.as_dict() 兼容的健康报告格式。

        供 HTTP /health 端点使用。仅使用同步快照以避免阻塞 HTTP handler。
        """
        health = self.get_health_sync()
        report = health_to_report(health)
        report["uptime_seconds"] = time.time() - self._start_time
        return report

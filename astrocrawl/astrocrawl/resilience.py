"""Fuse — 两状态熔断器（Erlang/OTP Supervisor 哲学）。

CLOSED → OPEN（窗口内失败超过阈值）
OPEN 后不自动恢复，不设 HALF_OPEN 探测状态。
"blown = dead"：熔断后由上层决定如何恢复。

区别于 _proxy.py 中的 CircuitState / ProxyHealthTracker 三级状态机
（CLOSED → OPEN → HALF_OPEN → CLOSED，Resilience4j 模式）。
"""

from __future__ import annotations

import time
from typing import Awaitable, Callable, Optional

from astrobase import LogfmtLogger
from astrocrawl.health import Health

_log = LogfmtLogger("astrocrawl.resilience")


class Fuse:
    """可复用两状态熔断器。被 WorkerSupervisor 持有。

    CLOSED → OPEN（窗口内失败超过阈值）
    状态转换时自动触发 on_open 回调（对标 Resilience4j——
    调用方无需检查返回值并手动触发回调）。
    OPEN 后不自动恢复。
    """

    def __init__(
        self,
        name: str,
        max_failures: int = 10,
        within_seconds: float = 60.0,
        on_open: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> None:
        self.name = name
        self._max_failures = max_failures
        self._within_seconds = within_seconds
        self._on_open = on_open
        self._death_times: list[float] = []
        self._is_open = False

    @property
    def max_failures(self) -> int:
        return self._max_failures

    @property
    def within_seconds(self) -> float:
        return self._within_seconds

    @property
    def is_open(self) -> bool:
        """熔断器当前是否已打开。"""
        return self._is_open

    async def record_failure(self) -> bool:
        """记录一次失败。返回 True 表示熔断器已打开。

        状态转换时自动触发 on_open 回调——调用方只需检查返回值决定后续行为，
        无需手动调用 on_open()。
        """
        if self._is_open:
            return True
        now = time.time()
        self._death_times.append(now)
        cutoff = now - self._within_seconds
        self._death_times = [t for t in self._death_times if t > cutoff]
        if len(self._death_times) > self._max_failures:
            self._is_open = True
            await self._on_open_impl()
            return True
        return False

    async def _on_open_impl(self) -> None:
        """熔断回调实现。"""
        _log.critical(
            "fuse_open",
            name=self.name,
            failures=self._max_failures,
            window=self._within_seconds,
        )
        if self._on_open:
            try:
                await self._on_open(f"{self.name} 熔断")
            except Exception:
                _log.exception("fuse_callback_error", name=self.name)

    def _window_filtered_count(self) -> int:
        """只读计数——滑动窗口内的失败次数（不修改 _death_times）。

        满足 HealthChecked 协议约束：快速、无副作用。
        """
        if not self._death_times:
            return 0
        cutoff = time.time() - self._within_seconds
        return sum(1 for t in self._death_times if t > cutoff)

    def get_health(self) -> Health:
        """熔断器自身健康状态（只读，窗口内失败计数）。"""
        recent = self._window_filtered_count()
        if self._is_open:
            return Health("DOWN", f"熔断器 {self.name} 已打开", {"failures": recent, "max": self._max_failures})
        if recent > 0:
            return Health("DEGRADED", f"{recent} 次失败", {"failures": recent, "max": self._max_failures})
        return Health("UP")

"""代理管理：ProxyManager（选择器）+ ProxyHealthTracker（三级断路器状态机）。

ProxyManager — CLOSED > HALF_OPEN > OPEN 三级轮询代理选择。
ProxyHealthTracker — 每个代理独立的 Circuit Breaker 状态机 + 后台 TCP 探测。

故障记录在滑动窗口内计数，超过 decay_seconds 的故障自动过期。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Deque, Dict, List, Optional

from astrocrawl._constants import (
    PROXY_COOLDOWN,
    PROXY_COOLDOWN_MAX,
    PROXY_DECAY_SECONDS,
    PROXY_FAILURE_THRESHOLD,
    PROXY_HALF_OPEN_MAX_FAILURES,
    PROXY_HALF_OPEN_MIN_DURATION,
    PROXY_PROBE_INTERVAL,
    PROXY_PROBE_TIMEOUT,
    PROXY_SCORE_SUCCESS_DECAY,
    PROXY_SCORE_WINDOW,
)
from astrocrawl.proxy._config import ParsedProxy
from astrocrawl.proxy._hook import ProxyHook
from astrocrawl.proxy._probe import probe_one

# ═══════════════════════════════════════════════════════════════════════
# ProxyManager — 三级轮询代理选择器
# ═══════════════════════════════════════════════════════════════════════


class ProxyManager:
    """代理选择器，三级轮询：CLOSED > HALF_OPEN > OPEN。"""

    def __init__(
        self,
        proxies: List[ParsedProxy],
        health_tracker: ProxyHealthTracker | None = None,
    ):
        self._proxies = list(proxies)
        self._index = 0
        self._lock = threading.Lock()
        self._log = logging.getLogger("astrocrawl.proxy")
        # SWRR state
        self._cw: Dict[str, int] = {}
        self._swrr_initialized = False
        self._total_weight = sum(p.weight for p in self._proxies)

        if health_tracker is not None:
            self._health = health_tracker
        else:
            self._health = ProxyHealthTracker(
                failure_threshold=PROXY_FAILURE_THRESHOLD,
                cooldown=PROXY_COOLDOWN,
            )

    async def get_proxy(
        self,
        prefer_different_than: Optional[str] = None,
    ) -> Optional[str]:
        """CLOSED > HALF_OPEN > OPEN 三级轮询 + Nginx 平滑加权轮询（SWRR）。"""
        with self._lock:
            if not self._proxies:
                return None
            parsed_to_url = {p: p.to_url_with_auth() for p in self._proxies}
            snapshot = self._health.get_all_stats()
            closed = [
                p
                for p in self._proxies
                if (url := parsed_to_url[p]) not in snapshot or snapshot[url].state == CircuitState.CLOSED
            ]
            half_open = [
                p
                for p in self._proxies
                if (url := parsed_to_url[p]) in snapshot and snapshot[url].state == CircuitState.HALF_OPEN
            ]
            open_proxies = [
                p
                for p in self._proxies
                if (url := parsed_to_url[p]) in snapshot and snapshot[url].state == CircuitState.OPEN
            ]
            candidates = closed or half_open or open_proxies
            if prefer_different_than and len(candidates) > 1:
                alt = [p for p in candidates if parsed_to_url[p] != prefer_different_than]
                if alt:
                    candidates = alt
            # Fast path: all weights equal → simple round-robin
            weights = {p.weight for p in self._proxies}
            if len(weights) == 1:
                selected = candidates[self._index % len(candidates)]
                self._index = (self._index + 1) % len(candidates)
            else:
                # SWRR: smooth weighted round-robin (Nginx)
                if not self._swrr_initialized:
                    for p in self._proxies:
                        self._cw[parsed_to_url[p]] = 0
                    self._swrr_initialized = True
                for p in self._proxies:
                    self._cw[parsed_to_url[p]] += p.weight
                selected = max(candidates, key=lambda p: self._cw[parsed_to_url[p]])
                self._cw[parsed_to_url[selected]] -= self._total_weight

            url = parsed_to_url[selected]
            s = snapshot.get(url)
            if s is not None:
                s.total_selections += 1
                s.last_selected_at = time.monotonic()
            return url

    async def mark_failure(self, proxy_url: str, weight: int = 1) -> CircuitState:
        """记录故障并返回更新后的断路器状态。weight: 超时=3，普通=1。"""
        return self._health.record_failure(proxy_url, weight=weight)

    async def mark_success(self, proxy_url: str) -> None:
        """记录成功，重置断路器。"""
        self._health.record_success(proxy_url)

    @property
    def health(self):
        return self._health

    def healthy_proxies_in_pool(self) -> List[str]:
        """返回代理池中所有健康的代理（含未使用过的）。批量加锁。"""
        snapshot = self._health.get_all_stats()
        result: List[str] = []
        for p in self._proxies:
            url = p.to_url_with_auth()
            if url not in snapshot or snapshot[url].is_available:
                result.append(url)
        return result

    @property
    def proxies(self) -> List[str]:
        return [p.to_url_with_auth() for p in self._proxies]


# ═══════════════════════════════════════════════════════════════════════
# ProxyHealthTracker — 三级断路器状态机
# ═══════════════════════════════════════════════════════════════════════

"""代理健康追踪器：Circuit Breaker 模式 + 滑动窗口 + 衰减。

每个代理独立维护一个有限状态机：
  CLOSED → consecutive_failures >= failure_threshold    → OPEN
  OPEN   → cooldown 到期                                → HALF_OPEN
  HALF_OPEN → 记录成功                                   → CLOSED (reset)
  HALF_OPEN → 考察窗口内（<15s）记录失败                   → 保持 HALF_OPEN
  HALF_OPEN → 窗口结束后失败数 >= 2                       → OPEN (cooldown × 1.5)
  HALF_OPEN → 窗口结束后失败数 < 2                        → CLOSED (通过考察)

故障记录在滑动窗口内计数，超过 decay_seconds 的故障自动过期。
后台 TCP 探测主动发现代理恢复，不依赖爬虫请求。

"""

# 结果传递模式：修改后在 _lock 内发布不可变快照，GUI 线程从快照读取（无需锁）。


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


_MAX_LATENCY_PENALTY = 0.15


@dataclass
class ProxyStats:
    consecutive_failures: int = 0
    total_failures: int = 0
    total_successes: int = 0
    state: CircuitState = CircuitState.CLOSED
    state_changed_at: float = 0.0
    cooldown_until: float = 0.0
    last_failure_at: float = 0.0
    last_success_at: float = 0.0
    failure_timestamps: Deque[float] = field(default_factory=deque)
    avg_latency_ms: float = 0.0  # HTTP 探测延迟 EWMA
    halflife_start: float = 0.0  # HALF_OPEN 窗口起点
    halflife_failures: int = 0  # 窗口内失败计数
    halflife_requests: int = 0  # 窗口内总请求数
    total_selections: int = 0  # get_proxy() 返回该代理的总次数（对标 Envoy counters）
    last_selected_at: float = 0.0  # 最后一次被选中的时间戳

    @property
    def is_available(self) -> bool:
        return self.health_score > 0.0

    @property
    def health_score(self) -> float:
        """0.0-1.0 综合健康分数。1.0=完全健康，0.0=不可路由。"""
        if self.state == CircuitState.OPEN:
            return 0.0
        base = 1.0
        if self.state == CircuitState.HALF_OPEN:
            base = 0.3  # 恢复中，低信任
        # 近期故障密度惩罚
        now = time.monotonic()
        recent = sum(1 for t in self.failure_timestamps if now - t <= PROXY_SCORE_WINDOW)
        base = max(0.0, base - recent * 0.15)
        # 连续延迟惩罚：0ms→0, PROXY_PROBE_TIMEOUT→_MAX_LATENCY_PENALTY
        if self.avg_latency_ms > 0:
            normalized = self.avg_latency_ms / (PROXY_PROBE_TIMEOUT * 1000)
            penalty = min(_MAX_LATENCY_PENALTY, normalized * _MAX_LATENCY_PENALTY)
            base -= penalty
        # 近期成功奖励 (线性衰减)
        if self.total_successes > 0 and self.state == CircuitState.CLOSED:
            time_since = now - self.last_success_at
            chance = max(0, 0.15 - (time_since / PROXY_SCORE_SUCCESS_DECAY) * 0.15)
            base += chance
        return max(0.0, min(1.0, base))


class ProxyHealthTracker:
    """代理健康追踪器的单一真源。

    由 ProxyManager 在选择代理时查询，由 _fetch_with_retry 在故障/成功时调用。
    ContextPool 在 acquire() 时通过 is_available() 检查槽位代理的即时状态。
    """

    def __init__(
        self,
        failure_threshold: int = PROXY_FAILURE_THRESHOLD,
        cooldown: float = PROXY_COOLDOWN,
        decay_seconds: float = PROXY_DECAY_SECONDS,
        probe_interval: float = PROXY_PROBE_INTERVAL,
        hook: ProxyHook | None = None,
    ):
        self._failure_threshold = failure_threshold
        self._cooldown = cooldown
        self._decay_seconds = decay_seconds
        self._probe_interval = probe_interval
        self._stats: Dict[str, ProxyStats] = {}
        self._lock = threading.Lock()
        self._snapshot: Dict[str, ProxyStats] = {}
        self._log = logging.getLogger("astrocrawl.proxy.health")
        self._probe_ok: Dict[str, int] = {}
        self._probe_task: Optional[asyncio.Task] = None
        self._probe_stop = asyncio.Event()
        self.recovery_event = asyncio.Event()  # 代理恢复时唤醒等待者
        from astrocrawl.proxy._hook import LoggingProxyHook

        self._hook = hook or LoggingProxyHook()

    # ── 公共接口 ────────────────────────────────────────

    def record_failure(self, proxy_url: str, weight: int = 1) -> CircuitState:
        """记录一次代理故障。返回故障后该代理的断路器状态。
        weight: 故障权重。超时=3（立即OPEN），普通=1。"""
        now = time.monotonic()
        with self._lock:
            s = self._ensure_stats(proxy_url)
            s.consecutive_failures += weight
            s.total_failures += weight
            s.last_failure_at = now
            s.failure_timestamps.append(now)
            self._decay_stale(s, now)

            if s.state == CircuitState.HALF_OPEN:
                s.halflife_failures += 1
                s.halflife_requests += 1
                window_elapsed = now - s.halflife_start
                if window_elapsed >= PROXY_HALF_OPEN_MIN_DURATION:
                    if s.halflife_failures >= PROXY_HALF_OPEN_MAX_FAILURES:
                        s.state = CircuitState.OPEN
                        cd = min(self._cooldown * 1.5, PROXY_COOLDOWN_MAX)
                        s.cooldown_until = now + cd
                        s.state_changed_at = now
                        self._hook.on_circuit_open(proxy_url)
                    else:
                        # 失败不足 → 恢复 CLOSED（窗口内通过考察）
                        s.state = CircuitState.CLOSED
                        s.consecutive_failures = 0
                        s.state_changed_at = now
                        s.cooldown_until = 0.0
                        self._hook.on_circuit_recover(proxy_url)
                else:
                    # 窗口未到期，继续观察
                    self._snapshot = dict(self._stats)
                    return s.state
            elif s.state == CircuitState.CLOSED:
                if s.consecutive_failures >= self._failure_threshold:
                    s.state = CircuitState.OPEN
                    s.cooldown_until = now + self._cooldown
                    s.state_changed_at = now
                    self._hook.on_circuit_open(proxy_url)
            self._snapshot = dict(self._stats)
            return s.state

    def record_success(self, proxy_url: str, *, set_recovery: bool = True) -> None:
        """记录一次代理成功。将断路器重置为 CLOSED。
        set_recovery=False: 仅更新计数器，不 set recovery_event（QThread 路径安全隔离）。"""
        now = time.monotonic()
        with self._lock:
            s = self._ensure_stats(proxy_url)
            s.consecutive_failures = 0
            s.total_successes += 1
            s.last_success_at = now
            if s.state == CircuitState.HALF_OPEN:
                s.halflife_requests += 1
            if s.state != CircuitState.CLOSED:
                self._hook.on_circuit_recover(proxy_url)
                s.state = CircuitState.CLOSED
                s.state_changed_at = now
                s.cooldown_until = 0.0
                s.halflife_failures = 0
                s.halflife_requests = 0
                if set_recovery:
                    self.recovery_event.set()  # 等待者负责 clear()
            self._decay_stale(s, now)
            self._snapshot = dict(self._stats)

    def get_health_score(self, proxy_url: str) -> float:
        """返回 0.0-1.0 健康分数。未知代理默认 1.0。从快照读取，无需锁。"""
        s = self._snapshot.get(proxy_url)
        return s.health_score if s else 1.0

    def is_available(self, proxy_url: str) -> bool:
        """纯查询：代理是否可用。从快照读取，无需锁。"""
        s = self._snapshot.get(proxy_url)
        return s.health_score > 0.0 if s else True

    def get_stats(self, proxy_url: str) -> Optional[ProxyStats]:
        """返回代理统计快照。从快照读取，无需锁。"""
        return self._snapshot.get(proxy_url)

    def get_all_stats(self) -> Dict[str, ProxyStats]:
        """返回所有代理统计快照。从快照读取，无需锁。"""
        return dict(self._snapshot)

    def try_activate(self, proxy_url: str) -> bool:
        """尝试将 OPEN 代理转换为 HALF_OPEN（如果冷却到期）。
        返回代理现在是否可用。若转换成功则设置 recovery_event。"""
        s = self._stats.get(proxy_url)
        if s is None or s.state != CircuitState.OPEN:
            return True
        if time.monotonic() < s.cooldown_until:
            return False
        with self._lock:
            now = time.monotonic()
            if s.state == CircuitState.OPEN and now >= s.cooldown_until:
                s.state = CircuitState.HALF_OPEN
                s.state_changed_at = now
                s.consecutive_failures = 0
                s.halflife_start = now
                s.halflife_failures = 0
                s.halflife_requests = 0
                self._hook.on_circuit_recover(proxy_url)
                self._snapshot = dict(self._stats)
                self.recovery_event.set()  # 等待者负责 clear()
                return True
        return s.state != CircuitState.OPEN

    def healthy_proxies(self) -> List[str]:
        """返回当前可用的代理列表。线程安全。"""
        snapshot = self.get_all_stats()
        return [url for url, s in snapshot.items() if s.is_available]

    def all_proxies_dead(self) -> bool:
        """全部已知代理 health_score == 0.0（全部 OPEN）。快照读取，无需锁。"""
        snapshot = self.get_all_stats()
        if not snapshot:
            return False
        return all(s.health_score == 0.0 for s in snapshot.values())

    # ── 后台 TCP 探测 ────────────────────────────────────

    async def start_background_probes(self, proxies: List[ParsedProxy]) -> asyncio.Task:
        """启动后台 TCP 探测任务，周期性验证代理可连通性。"""
        if self._probe_task is not None and not self._probe_task.done():
            return self._probe_task
        self._probe_stop.clear()
        self._probe_ok.clear()
        self._probe_task = asyncio.create_task(self._probe_loop(proxies))
        return self._probe_task

    async def stop_background_probes(self) -> None:
        self._probe_stop.set()
        if self._probe_task and not self._probe_task.done():
            self._probe_task.cancel()
            try:
                await self._probe_task
            except asyncio.CancelledError:
                pass

    async def _probe_loop(self, proxies: List[ParsedProxy]) -> None:
        while not self._probe_stop.is_set():
            try:
                await asyncio.wait_for(
                    self._probe_stop.wait(),
                    timeout=self._probe_interval,
                )
                return
            except asyncio.TimeoutError:
                pass
            # 并发探测所有代理（避免死代理串行超时拖慢活代理检测）
            results = await asyncio.gather(
                *[probe_one(p, timeout=PROXY_PROBE_TIMEOUT) for p in proxies],
                return_exceptions=True,
            )
            for proxy, result in zip(proxies, results):
                if self._probe_stop.is_set():
                    return
                proxy_url = proxy.to_url_with_auth()
                s = self._stats.get(proxy_url)
                if s is None:
                    continue
                if s.state != CircuitState.OPEN:
                    self._probe_ok.pop(proxy_url, None)
                    continue
                if isinstance(result, BaseException):
                    self._probe_ok[proxy_url] = 0
                    continue
                if not result.reachable:
                    self._probe_ok[proxy_url] = 0
                    continue
                # TCP 可达 — 更新延迟 EWMA + 探测计数器
                if result.latency_ms is not None:
                    s.avg_latency_ms = s.avg_latency_ms * 0.7 + result.latency_ms * 0.3
                count = self._probe_ok.get(proxy_url, 0) + 1
                self._probe_ok[proxy_url] = count
                # 连续 2 次主动探测成功 → HALF_OPEN 门控（对齐业界健康阈值）
                if count >= 2:
                    self.try_activate(proxy_url)
                    self._probe_ok[proxy_url] = 0

    # ── 内部 ────────────────────────────────────────────

    def _ensure_stats(self, proxy_url: str) -> ProxyStats:
        # 调用者已持有 _lock
        s = self._stats.get(proxy_url)
        if s is None:
            s = ProxyStats(state_changed_at=time.monotonic())
            self._stats[proxy_url] = s
        return s

    def _decay_stale(self, s: ProxyStats, now: float) -> None:
        # 调用者已持有 _lock
        cutoff = now - self._decay_seconds
        while s.failure_timestamps and s.failure_timestamps[0] < cutoff:
            s.failure_timestamps.popleft()

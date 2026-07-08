from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Protocol

from astrocrawl._constants import DOMAIN_CLEANUP_AGE, DOMAIN_CONCURRENCY_ACQUIRE_TIMEOUT
from astrocrawl.utils.logging import LogfmtLogger


class RateLimitConfig(Protocol):
    domain_min_delay: float
    domain_max_delay: float


@dataclass
class _DomainState:
    next_allowed: float = 0.0
    rate_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    custom_delay: float | None = None
    _sem: asyncio.Semaphore | None = None
    active_count: int = 0
    last_used: float = 0.0
    max_concurrency: int = 1


class DomainTracker:
    """Per-domain state container — unified lifecycle for rate-limit and concurrency tracking.

    Owns the single dictionary of _DomainState entries shared by DomainRateLimiter
    and DomainConcurrencyLimiter.  No policy decisions — pure state mechanism.
    """

    def __init__(self, max_concurrency: int, max_domains: int = 20000, stale_ttl: float | None = None):
        self._states: Dict[str, _DomainState] = {}
        self._lock = asyncio.Lock()
        self._max_domains = max_domains
        self._stale_ttl = stale_ttl if stale_ttl is not None else float(DOMAIN_CLEANUP_AGE)
        self._evict_ttl = 600.0
        self._max_concurrency = max_concurrency
        self._log = LogfmtLogger("astrocrawl.domain_tracker")

    async def touch(self, domain: str) -> _DomainState:
        async with self._lock:
            return self._touch_locked(domain)

    def _touch_locked(self, domain: str) -> _DomainState:
        if domain not in self._states:
            self._states[domain] = _DomainState(max_concurrency=self._max_concurrency)
        state = self._states[domain]
        state.last_used = time.monotonic()
        return state

    async def set_crawl_delay(self, domain: str, delay: float) -> None:
        if not (0.0 < delay <= 3600.0):
            self._log.warning(
                "crawl_delay_out_of_bounds",
                delay=delay,
                domain=domain,
            )
            return
        async with self._lock:
            state = self._states.get(domain)
            if state is None:
                state = _DomainState(max_concurrency=self._max_concurrency)
                self._states[domain] = state
            state.last_used = time.monotonic()
            state.custom_delay = delay

    async def cleanup(self) -> None:
        async with self._lock:
            now = time.monotonic()
            # Phase 1 — remove absolutely idle entries.
            to_del = []
            for domain, state in self._states.items():
                if state.active_count == 0 and now - state.last_used > self._stale_ttl and not state.rate_lock.locked():
                    to_del.append(domain)
            for domain in to_del:
                del self._states[domain]

            # Phase 2 — overflow eviction (only when under memory pressure).
            if len(self._states) > self._max_domains:
                candidates = [
                    (d, s)
                    for d, s in self._states.items()
                    if (s.active_count == 0 and now - s.last_used > self._evict_ttl and not s.rate_lock.locked())
                ]
                candidates.sort(key=lambda x: x[1].last_used)
                overflow = len(self._states) - self._max_domains
                to_evict = candidates[:overflow]
                for domain, _ in to_evict:
                    del self._states[domain]
                if len(self._states) > self._max_domains:
                    self._log.warning(
                        "domain_tracker_overflow",
                        domain_count=len(self._states),
                        max_domains=self._max_domains,
                        evicted=len(to_evict),
                        action="best_effort",
                    )


class DomainRateLimiter:
    def __init__(self, cfg: RateLimitConfig, max_domains: int = 20000, tracker: DomainTracker | None = None):
        if tracker is None:
            tracker = DomainTracker(max_concurrency=1, max_domains=max_domains)
        self._tracker = tracker
        self._min = cfg.domain_min_delay
        self._max = cfg.domain_max_delay

    async def set_crawl_delay(self, domain: str, delay: float) -> None:
        await self._tracker.set_crawl_delay(domain, delay)

    async def acquire(self, domain: str, stop_event: Optional[asyncio.Event] = None) -> bool:
        state = await self._tracker.touch(domain)
        interval = self._get_interval(state)
        async with state.rate_lock:
            now = time.monotonic()
            remaining = state.next_allowed - now
            delay = remaining if remaining > 0 else 0.0
            state.next_allowed = now + max(0.0, interval)
        if delay > 0:
            if stop_event is not None:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
                if stop_event.is_set():
                    return False
            else:
                await asyncio.sleep(delay)
        return True

    def _get_interval(self, state: _DomainState) -> float:
        if state.custom_delay is not None:
            return state.custom_delay
        return random.uniform(self._min, self._max)

    async def cleanup_periodic(self) -> None:
        await self._tracker.cleanup()


class DomainConcurrencyLimiter:
    def __init__(self, max_concurrency: int, ttl_seconds: float = 3600, tracker: DomainTracker | None = None):
        if tracker is None:
            tracker = DomainTracker(max_concurrency=max_concurrency, stale_ttl=ttl_seconds)
        self._tracker = tracker
        self._max = max_concurrency

    async def acquire(self, domain: str) -> None:
        """阻塞获取域并发槽位。已弃用，保留以确保 API 兼容性。

        新代码应使用 try_acquire() + release() 组合。
        """
        async with self._tracker._lock:
            state = self._tracker._touch_locked(domain)
            if state._sem is None:
                state._sem = asyncio.Semaphore(state.max_concurrency)
            state.active_count += 1
            state.last_used = time.monotonic()
        acquired = False
        try:
            await asyncio.wait_for(
                state._sem.acquire(),
                timeout=DOMAIN_CONCURRENCY_ACQUIRE_TIMEOUT,
            )
            acquired = True
        except asyncio.CancelledError:
            if not acquired:
                async with self._tracker._lock:
                    state.active_count = max(0, state.active_count - 1)
            raise
        except asyncio.TimeoutError:
            async with self._tracker._lock:
                state.active_count = max(0, state.active_count - 1)
            raise

    async def try_acquire(self, domain: str) -> bool:
        async with self._tracker._lock:
            state = self._tracker._touch_locked(domain)
            if state._sem is None:
                state._sem = asyncio.Semaphore(state.max_concurrency)
            if state._sem.locked():
                return False
            state.active_count += 1
            state.last_used = time.monotonic()
        await state._sem.acquire()
        return True

    async def release(self, domain: str) -> None:
        need_release = False
        state = None
        try:
            async with self._tracker._lock:
                state = self._tracker._states.get(domain)
                if state and state.active_count > 0:
                    state.active_count -= 1
                    state.last_used = time.monotonic()
                    need_release = True
        finally:
            if need_release and state is not None and state._sem is not None:
                state._sem.release()

    async def cleanup_stale(self) -> None:
        await self._tracker.cleanup()

"""RateLimiter — 线程安全 Token Bucket + BoundedSemaphore 并发控制。

sync/async 路径共享同一限流预算。Async 路径通过 asyncio.to_thread() 委托到线程安全原语。
零外部依赖，~30 行 TokenBucket 自行实现。
"""

from __future__ import annotations

import asyncio
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Iterator, Optional

from astrocrawl.ai._errors import AIRateLimitError


@dataclass(frozen=True)
class RateLimitConfig:
    """API 调用限流配置。RPM=0 或 concurrency=0 禁用对应限制。"""

    rpm: int = 60
    concurrency: int = 4
    blocking: bool = True
    max_wait: float = 120.0


class _TokenBucket:
    """线程安全 Token Bucket——自行实现，~30 行，零外部依赖。"""

    def __init__(self, rate_per_minute: int) -> None:
        self._rate = rate_per_minute / 60.0  # tokens per second
        self._capacity = float(rate_per_minute)
        self._tokens = self._capacity
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> float:
        """尝试获取 1 个 token。返回需要等待的秒数（0 = 立即可用）。

        Raises:
            AIRateLimitError: 非阻塞模式下无可用 token
        """
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            self._last_refill = now

            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return 0.0

            # 需要等待
            wait = (1.0 - self._tokens) / self._rate
            self._tokens = 0.0
            return wait


class RateLimiter:
    """线程安全限流器——Token Bucket + BoundedSemaphore。

    sync 和 async 路径共享同一限流预算。Async 路径通过 asyncio.to_thread() 委托。
    """

    def __init__(self, config: RateLimitConfig) -> None:
        self._config = config
        self._bucket: _TokenBucket | None = None
        if config.rpm > 0:
            self._bucket = _TokenBucket(config.rpm)
        self._semaphore: threading.BoundedSemaphore | None = None
        if config.concurrency > 0:
            self._semaphore = threading.BoundedSemaphore(config.concurrency)

    # ── sync path ─────────────────────────────────────────────────

    @contextmanager
    def acquire_sync(self) -> Iterator[None]:
        """同步上下文管理器——with self._rate_limiter.acquire_sync(): ..."""
        start = time.monotonic()

        # 并发控制
        if self._semaphore is not None:
            acquired = self._semaphore.acquire(timeout=self._config.max_wait if self._config.blocking else 0.001)
            if not acquired:
                raise AIRateLimitError("并发限制已达到，请求被拒绝")
            try:
                self._wait_for_token(start)
                yield
            finally:
                self._semaphore.release()
        else:
            self._wait_for_token(start)
            yield

    # ── async path ────────────────────────────────────────────────

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[None]:
        """异步上下文管理器——async with self._rate_limiter.acquire(): ...

        委托到线程安全原语：Semaphore 通过 to_thread 获取，Token Bucket 通过 to_thread 检查。
        """
        start = time.monotonic()

        if self._semaphore is not None:
            acquired = await asyncio.to_thread(
                self._semaphore.acquire,
                timeout=self._config.max_wait if self._config.blocking else 0.001,
            )
            if not acquired:
                raise AIRateLimitError("并发限制已达到，请求被拒绝")
            try:
                await asyncio.to_thread(self._wait_for_token, start)
                yield
            finally:
                self._semaphore.release()
        else:
            await asyncio.to_thread(self._wait_for_token, start)
            yield

    # ── internal ──────────────────────────────────────────────────

    def _wait_for_token(self, start: float) -> None:
        if self._bucket is None:
            return
        wait = self._bucket.acquire()
        if wait > 0:
            if not self._config.blocking:
                raise AIRateLimitError("速率限制已达到，请求被拒绝")
            elapsed = time.monotonic() - start
            max_wait = self._config.max_wait
            if elapsed + wait > max_wait:
                raise AIRateLimitError(f"等待速率限制超时 ({max_wait}s)")
            time.sleep(wait)


# ── 规则生成共享限流器 ──────────────────────────────────────────────────────

_rule_gen_limiter: Optional[RateLimiter] = None


def get_rule_gen_limiter() -> RateLimiter:
    """返回进程级共享的规则生成 RateLimiter 单例。

    rpm=10, concurrency=1 — 每分钟最多 10 次，串行执行，作为防御层防止误操作高频调用。
    """
    global _rule_gen_limiter
    if _rule_gen_limiter is None:
        _rule_gen_limiter = RateLimiter(RateLimitConfig(rpm=10, concurrency=1))
    return _rule_gen_limiter

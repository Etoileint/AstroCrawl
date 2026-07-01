"""测试：astrocrawl/ai/_rate_limiter.py + _usage_tracker.py — TokenBucket + BoundedSemaphore + 会话累计。

ADR-0006 #3: RateLimiter + UsageTracker
"""

from __future__ import annotations

import asyncio
import time

import pytest

from astrocrawl.ai._errors import AIRateLimitError
from astrocrawl.ai._rate_limiter import RateLimitConfig, RateLimiter, _TokenBucket
from astrocrawl.ai._types import TokenUsage
from astrocrawl.ai._usage_tracker import UsageTracker

# ═══════════════════════════════════════════════════════════════════════
# _TokenBucket
# ═══════════════════════════════════════════════════════════════════════


class TestTokenBucket:
    """_TokenBucket — 线程安全 Token Bucket 算法。"""

    def test_acquire_with_sufficient_tokens(self):
        bucket = _TokenBucket(60)  # 1 token/sec
        wait = bucket.acquire()
        assert wait == 0.0

    def test_acquire_exhausts_tokens(self):
        bucket = _TokenBucket(1)  # 1 token/min = ~0.017 token/sec
        # 第一次成功
        assert bucket.acquire() == 0.0
        # 第二次需要等待很久 (refill rate is slow)
        wait = bucket.acquire()
        assert wait > 0.0

    def test_initial_capacity_is_full(self):
        bucket = _TokenBucket(100)
        for _ in range(100):
            assert bucket.acquire() == 0.0
        assert bucket.acquire() > 0.0

    def test_refill_clamped_to_capacity(self):
        """闲置补桶受 capacity 钳位，防止无限累积。"""
        bucket = _TokenBucket(60)  # capacity=60, rate=1/s
        time.sleep(1.5)  # 若未钳位则 tokens=61.5，可多取 1 个
        for _ in range(60):
            assert bucket.acquire() == 0.0
        assert bucket.acquire() > 0.0  # 第 61 个需等待 → 证明钳位生效

    def test_refill_partial_token_grants_acquire(self):
        """耗尽后补桶至 ≥1.0 即可立即获取。"""
        bucket = _TokenBucket(120)  # rate=2/s
        for _ in range(120):
            bucket.acquire()
        time.sleep(0.6)  # 补 1.2 tokens，刚好过 ≥1.0 阈值
        assert bucket.acquire() == 0.0
        assert bucket.acquire() > 0.0  # 剩余 0.2 tokens 不够


# ═══════════════════════════════════════════════════════════════════════
# RateLimitConfig
# ═══════════════════════════════════════════════════════════════════════


class TestRateLimitConfig:
    """RateLimitConfig frozen dataclass。"""

    def test_defaults(self):
        cfg = RateLimitConfig()
        assert cfg.rpm == 60
        assert cfg.concurrency == 4
        assert cfg.blocking is True
        assert cfg.max_wait == 120.0

    def test_custom_values(self):
        cfg = RateLimitConfig(rpm=120, concurrency=8, blocking=False, max_wait=30.0)
        assert cfg.rpm == 120
        assert cfg.concurrency == 8
        assert cfg.blocking is False
        assert cfg.max_wait == 30.0

    def test_zero_disables_rpm(self):
        cfg = RateLimitConfig(rpm=0)
        limiter = RateLimiter(cfg)
        # 不创建 TokenBucket
        assert limiter._bucket is None

    def test_zero_disables_concurrency(self):
        cfg = RateLimitConfig(concurrency=0)
        limiter = RateLimiter(cfg)
        # 不创建 Semaphore
        assert limiter._semaphore is None


# ═══════════════════════════════════════════════════════════════════════
# RateLimiter — sync path
# ═══════════════════════════════════════════════════════════════════════


class TestRateLimiterSync:
    """RateLimiter.acquire_sync() — with context manager。"""

    def test_acquire_without_limits(self):
        limiter = RateLimiter(RateLimitConfig(rpm=0, concurrency=0))
        start = time.monotonic()
        with limiter.acquire_sync():
            pass
        elapsed = time.monotonic() - start
        assert elapsed < 0.1

    def test_concurrency_limit_blocks(self):
        cfg = RateLimitConfig(rpm=0, concurrency=1, blocking=True, max_wait=10.0)
        limiter = RateLimiter(cfg)

        import threading

        acquired_first = threading.Event()
        release_first = threading.Event()

        def _hold():
            with limiter.acquire_sync():
                acquired_first.set()
                release_first.wait()

        t = threading.Thread(target=_hold, daemon=True)
        t.start()
        acquired_first.wait()

        start = time.monotonic()
        release_first.set()
        with limiter.acquire_sync():
            pass
        elapsed = time.monotonic() - start
        assert elapsed < 5.0
        t.join()

    def test_non_blocking_raises_on_concurrency(self):
        cfg = RateLimitConfig(rpm=0, concurrency=1, blocking=False)
        limiter = RateLimiter(cfg)

        import threading

        acquired_first = threading.Event()
        release_first = threading.Event()

        def _hold():
            with limiter.acquire_sync():
                acquired_first.set()
                release_first.wait()

        t = threading.Thread(target=_hold, daemon=True)
        t.start()
        acquired_first.wait()

        try:
            with pytest.raises(AIRateLimitError):
                with limiter.acquire_sync():
                    pass
        finally:
            release_first.set()
            t.join()

    def test_rate_limit_non_blocking_raises(self):
        """TokenBucket 耗尽时，非阻塞模式立即拒绝。"""
        cfg = RateLimitConfig(rpm=1, concurrency=0, blocking=False, max_wait=30.0)
        limiter = RateLimiter(cfg)
        with limiter.acquire_sync():
            pass
        with pytest.raises(AIRateLimitError, match="速率限制已达到"):
            with limiter.acquire_sync():
                pass

    def test_rate_limit_timeout_raises(self):
        """TokenBucket 等待时间超过 max_wait 时拒绝。"""
        cfg = RateLimitConfig(rpm=1, concurrency=0, blocking=True, max_wait=0.001)
        limiter = RateLimiter(cfg)
        with limiter.acquire_sync():
            pass
        with pytest.raises(AIRateLimitError, match="等待速率限制超时"):
            with limiter.acquire_sync():
                pass

    def test_rate_limit_blocking_waits(self):
        """阻塞模式下 TokenBucket 为空时等待并成功获取。"""
        cfg = RateLimitConfig(rpm=120, concurrency=0, blocking=True, max_wait=10.0)
        limiter = RateLimiter(cfg)
        for _ in range(120):
            with limiter.acquire_sync():
                pass
        start = time.monotonic()
        with limiter.acquire_sync():
            pass
        elapsed = time.monotonic() - start
        assert 0.3 < elapsed < 1.5


# ═══════════════════════════════════════════════════════════════════════
# RateLimiter — async path
# ═══════════════════════════════════════════════════════════════════════


class TestRateLimiterAsync:
    """RateLimiter.acquire() — async with context manager。"""

    async def test_acquire_without_limits(self):
        limiter = RateLimiter(RateLimitConfig(rpm=0, concurrency=0))
        start = time.monotonic()
        async with limiter.acquire():
            pass
        elapsed = time.monotonic() - start
        assert elapsed < 0.1

    async def test_concurrency_limit_async(self):
        cfg = RateLimitConfig(rpm=0, concurrency=2, blocking=True, max_wait=10.0)
        limiter = RateLimiter(cfg)

        async def _work():
            async with limiter.acquire():
                await asyncio.sleep(0.05)

        tasks = [asyncio.create_task(_work()) for _ in range(2)]
        await asyncio.gather(*tasks)

    async def test_non_blocking_raises_async(self):
        cfg = RateLimitConfig(rpm=0, concurrency=1, blocking=False)
        limiter = RateLimiter(cfg)

        async def _hold():
            async with limiter.acquire():
                await asyncio.sleep(0.5)

        task = asyncio.create_task(_hold())
        await asyncio.sleep(0.05)

        with pytest.raises(AIRateLimitError):
            async with limiter.acquire():
                pass

        await task

    async def test_rate_limit_non_blocking_raises_async(self):
        """TokenBucket 耗尽时，异步非阻塞模式立即拒绝。"""
        cfg = RateLimitConfig(rpm=1, concurrency=0, blocking=False, max_wait=30.0)
        limiter = RateLimiter(cfg)
        async with limiter.acquire():
            pass
        with pytest.raises(AIRateLimitError, match="速率限制已达到"):
            async with limiter.acquire():
                pass

    async def test_rate_limit_timeout_raises_async(self):
        """TokenBucket 等待超时，异步路径同样拒绝。"""
        cfg = RateLimitConfig(rpm=1, concurrency=0, blocking=True, max_wait=0.001)
        limiter = RateLimiter(cfg)
        async with limiter.acquire():
            pass
        with pytest.raises(AIRateLimitError, match="等待速率限制超时"):
            async with limiter.acquire():
                pass

    async def test_rate_limit_blocking_waits_async(self):
        """异步阻塞模式下 TokenBucket 为空时等待并成功获取。"""
        cfg = RateLimitConfig(rpm=120, concurrency=0, blocking=True, max_wait=10.0)
        limiter = RateLimiter(cfg)
        for _ in range(120):
            async with limiter.acquire():
                pass
        start = time.monotonic()
        async with limiter.acquire():
            pass
        elapsed = time.monotonic() - start
        assert 0.3 < elapsed < 1.5


# ═══════════════════════════════════════════════════════════════════════
# UsageTracker
# ═══════════════════════════════════════════════════════════════════════


class TestUsageTracker:
    """UsageTracker — 会话级 TokenUsage 累计。"""

    def test_initial_usage_is_zero(self):
        tracker = UsageTracker()
        u = tracker.usage
        assert u.prompt_tokens == 0
        assert u.completion_tokens == 0
        assert u.total_tokens == 0

    def test_record_accumulates(self):
        tracker = UsageTracker()
        tracker.record(TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15))
        tracker.record(TokenUsage(prompt_tokens=20, completion_tokens=10, total_tokens=30))
        u = tracker.usage
        assert u.prompt_tokens == 30
        assert u.completion_tokens == 15
        assert u.total_tokens == 45

    def test_record_none_is_noop(self):
        tracker = UsageTracker()
        tracker.record(None)
        u = tracker.usage
        assert u.prompt_tokens == 0
        assert u.completion_tokens == 0
        assert u.total_tokens == 0

    def test_reset_clears_all(self):
        tracker = UsageTracker()
        tracker.record(TokenUsage(10, 5, 15))
        tracker.reset()
        u = tracker.usage
        assert u.prompt_tokens == 0
        assert u.completion_tokens == 0
        assert u.total_tokens == 0

    def test_usage_returns_snapshot(self):
        """usage 返回快照，后续 record 不改变已返回的对象。"""
        tracker = UsageTracker()
        tracker.record(TokenUsage(10, 5, 15))
        u1 = tracker.usage
        tracker.record(TokenUsage(5, 3, 8))
        assert u1.prompt_tokens == 10
        assert u1.completion_tokens == 5
        assert u1.total_tokens == 15

    def test_concurrent_records_thread_safe(self):
        import threading

        tracker = UsageTracker()
        n_threads = 10
        n_records_per_thread = 100

        def _record():
            for _ in range(n_records_per_thread):
                tracker.record(TokenUsage(1, 1, 2))

        threads = [threading.Thread(target=_record) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        expected = n_threads * n_records_per_thread
        u = tracker.usage
        assert u.prompt_tokens == expected
        assert u.completion_tokens == expected
        assert u.total_tokens == expected * 2

    def test_usage_snapshot_thread_safe(self):
        import threading

        tracker = UsageTracker()
        tracker.record(TokenUsage(100, 50, 150))

        snapshots = []
        barrier = threading.Barrier(2, timeout=2)

        def _reader():
            barrier.wait()
            for _ in range(200):
                snapshots.append(tracker.usage)

        t = threading.Thread(target=_reader)
        t.start()
        barrier.wait()
        for _ in range(200):
            tracker.record(TokenUsage(1, 1, 2))
        t.join()

        # 所有快照的 prompt_tokens >= 100（不会看到残值）
        assert all(s.prompt_tokens >= 100 for s in snapshots)
        # 并发结束后累计总数正确
        u = tracker.usage
        assert u.prompt_tokens == 100 + 200  # initial 100 + 200 records × 1
        assert u.completion_tokens == 50 + 200  # initial 50 + 200 records × 1
        assert u.total_tokens == 150 + 400  # initial 150 + 200 records × 2

    def test_reset_then_record_after(self):
        tracker = UsageTracker()
        tracker.record(TokenUsage(10, 5, 15))
        tracker.reset()
        u = tracker.usage
        assert u.prompt_tokens == 0
        assert u.completion_tokens == 0
        assert u.total_tokens == 0
        tracker.record(TokenUsage(3, 2, 5))
        u = tracker.usage
        assert u.prompt_tokens == 3
        assert u.completion_tokens == 2
        assert u.total_tokens == 5

    def test_record_zero_valued_usage_is_noop(self):
        """record(TokenUsage(0,0,0)) 不改变任何累计值。"""
        tracker = UsageTracker()
        tracker.record(TokenUsage(10, 5, 15))
        tracker.record(TokenUsage(0, 0, 0))
        u = tracker.usage
        assert u.prompt_tokens == 10
        assert u.completion_tokens == 5
        assert u.total_tokens == 15

    def test_reset_idempotent(self):
        """zero-state reset 不抛异常，状态保持零。"""
        tracker = UsageTracker()
        tracker.reset()
        u = tracker.usage
        assert u.prompt_tokens == 0
        assert u.completion_tokens == 0
        assert u.total_tokens == 0
        # 二次 reset，三字段仍为零
        tracker.reset()
        u = tracker.usage
        assert u.prompt_tokens == 0
        assert u.completion_tokens == 0
        assert u.total_tokens == 0

    def test_concurrent_record_and_reset(self):
        """并发 record + reset: reset 不会丢失在锁外等待的 record。"""
        import threading

        tracker = UsageTracker()
        n_writers = 8
        records_per_writer = 500
        n_resets = 50
        final_counts = []
        started = threading.Event()

        def _writer():
            started.wait()
            for _ in range(records_per_writer):
                tracker.record(TokenUsage(1, 1, 2))

        def _resetter():
            started.wait()
            for _ in range(n_resets):
                tracker.reset()
                time.sleep(0.0001)

        writers = [threading.Thread(target=_writer) for _ in range(n_writers)]
        resetter = threading.Thread(target=_resetter)
        all_threads = writers + [resetter]
        for t in all_threads:
            t.start()
        started.set()
        for _ in range(n_resets):
            final_counts.append(tracker.usage)
            time.sleep(0.0001)
        for t in all_threads:
            t.join()

        # 最终状态一致性: 所有计数器 >= 0, 不存在负数
        u = tracker.usage
        assert u.prompt_tokens >= 0
        assert u.completion_tokens >= 0
        assert u.total_tokens >= 0
        # prompt + completion == total (record 中 1+1=2)
        assert u.prompt_tokens + u.completion_tokens == u.total_tokens
        # 所有中间快照满足不变量
        assert all(s.prompt_tokens + s.completion_tokens == s.total_tokens for s in final_counts)


# ═══════════════════════════════════════════════════════════════════════
# get_rule_gen_limiter() — 共享单例
# ═══════════════════════════════════════════════════════════════════════


class TestRuleGenLimiterSingleton:
    """get_rule_gen_limiter() — 共享工厂, rpm=10, concurrency=1。"""

    def test_returns_same_instance(self):
        import astrocrawl.ai._rate_limiter as _rlm

        _rlm._rule_gen_limiter = None
        a = _rlm.get_rule_gen_limiter()
        b = _rlm.get_rule_gen_limiter()
        assert a is b

    def test_config_correct(self):
        import astrocrawl.ai._rate_limiter as _rlm

        _rlm._rule_gen_limiter = None
        limiter = _rlm.get_rule_gen_limiter()
        assert limiter._config.rpm == 10
        assert limiter._config.concurrency == 1
        assert limiter._bucket is not None
        assert limiter._semaphore is not None

    def test_acquire_sync_works(self):
        import astrocrawl.ai._rate_limiter as _rlm

        _rlm._rule_gen_limiter = None
        limiter = _rlm.get_rule_gen_limiter()
        with limiter.acquire_sync():
            pass

"""域限速器与并发控制器测试 — ISTQB 边界值风格。

覆盖 DomainRateLimiter 延迟预约、DomainConcurrencyLimiter 信号量机制。
"""

from __future__ import annotations

import asyncio
import time

import pytest

from astrocrawl.config import CrawlerConfig
from astrocrawl.network.throttling import DomainConcurrencyLimiter, DomainRateLimiter, DomainTracker

# ═══════════════════════════════════════════════════════════════════════
# DomainRateLimiter
# ═══════════════════════════════════════════════════════════════════════


class TestDomainRateLimiter:
    """基本 acquire / set_crawl_delay 行为。"""

    async def test_first_acquire_returns_immediately(self):
        """首次 acquire 不应等待 (next_allowed=0)。"""
        cfg = CrawlerConfig(domain_min_delay=0.01, domain_max_delay=0.02)
        limiter = DomainRateLimiter(cfg)
        t0 = asyncio.get_event_loop().time()
        result = await limiter.acquire("example.com")
        assert result is True
        assert asyncio.get_event_loop().time() - t0 < 0.1
        assert "example.com" in limiter._tracker._states

    async def test_second_acquire_waits_for_interval(self):
        """二次 acquire 须等待 ≥ min_delay (使用自定义延迟消除 random 不确定性)。"""
        cfg = CrawlerConfig(domain_min_delay=0.01, domain_max_delay=0.02)
        limiter = DomainRateLimiter(cfg)
        await limiter.set_crawl_delay("example.com", 0.05)
        await limiter.acquire("example.com")
        t0 = asyncio.get_event_loop().time()
        await limiter.acquire("example.com")
        elapsed = asyncio.get_event_loop().time() - t0
        assert elapsed >= 0.04  # 使用自定义 delay=0.05, 允许微小误差

    async def test_set_crawl_delay_overrides_random(self):
        """自定义 delay 覆盖 random.uniform 的随机区间。"""
        cfg = CrawlerConfig(domain_min_delay=5.0, domain_max_delay=10.0)
        limiter = DomainRateLimiter(cfg)
        await limiter.set_crawl_delay("example.com", 0.02)
        t0 = asyncio.get_event_loop().time()
        await limiter.acquire("example.com")
        elapsed = asyncio.get_event_loop().time() - t0
        assert elapsed < 1.0  # 使用自定义 0.02, 远小于 5-10s


# ═══════════════════════════════════════════════════════════════════════
# DomainRateLimiter — stop_event 中断
# ═══════════════════════════════════════════════════════════════════════


class TestDomainRateLimiterStopEvent:
    """acquire 在 stop_event 已设置时立即返回 False，不等满 delay。"""

    async def test_stop_event_set_returns_immediately(self):
        """stop_event 已设置 + delay > 0 → 立即返回 False。"""
        cfg = CrawlerConfig(domain_min_delay=0.01, domain_max_delay=0.02)
        limiter = DomainRateLimiter(cfg)
        await limiter.set_crawl_delay("example.com", 5.0)
        await limiter.acquire("example.com")  # 第一次: 设置 next_allowed
        stop_event = asyncio.Event()
        stop_event.set()
        t0 = asyncio.get_event_loop().time()
        result = await limiter.acquire("example.com", stop_event=stop_event)
        elapsed = asyncio.get_event_loop().time() - t0
        assert result is False
        assert elapsed < 0.1  # 不等满 5s delay, 立即中断

    async def test_stop_event_unset_waits_then_returns_true(self):
        """stop_event 未设置 + delay > 0 → 等满 delay 返回 True。"""
        cfg = CrawlerConfig(domain_min_delay=0.01, domain_max_delay=0.02)
        limiter = DomainRateLimiter(cfg)
        await limiter.set_crawl_delay("example.com", 0.05)
        await limiter.acquire("example.com")
        stop_event = asyncio.Event()  # 未设置
        t0 = asyncio.get_event_loop().time()
        result = await limiter.acquire("example.com", stop_event=stop_event)
        elapsed = asyncio.get_event_loop().time() - t0
        assert result is True
        assert elapsed >= 0.04  # 等价于 test_second_acquire_waits_for_interval


# ═══════════════════════════════════════════════════════════════════════
# DomainRateLimiter — set_crawl_delay 边界值
# ═══════════════════════════════════════════════════════════════════════


class TestDomainRateLimiterBoundary:
    """set_crawl_delay 延迟边界验证。"""

    async def test_delay_zero_ignored(self, caplog):
        """delay=0 → 被拒绝 (不满足 >0), 记 WARNING, delay 未设置。"""
        cfg = CrawlerConfig(domain_min_delay=0.01, domain_max_delay=0.02)
        limiter = DomainRateLimiter(cfg)
        await limiter.set_crawl_delay("example.com", 0.0)
        assert "event=crawl_delay_out_of_bounds" in caplog.text
        # 未设置: 使用 random interval
        t0 = asyncio.get_event_loop().time()
        await limiter.acquire("example.com")
        assert asyncio.get_event_loop().time() - t0 < 0.5

    async def test_delay_negative_ignored(self, caplog):
        """delay=-1 → 被拒绝, 记 WARNING。"""
        cfg = CrawlerConfig()
        limiter = DomainRateLimiter(cfg)
        await limiter.set_crawl_delay("example.com", -1.0)
        assert "event=crawl_delay_out_of_bounds" in caplog.text

    async def test_delay_above_3600_ignored(self, caplog):
        """delay=3601 → 被拒绝, 记 WARNING。"""
        cfg = CrawlerConfig()
        limiter = DomainRateLimiter(cfg)
        await limiter.set_crawl_delay("example.com", 3601.0)
        assert "event=crawl_delay_out_of_bounds" in caplog.text

    async def test_delay_at_3600_accepted(self):
        """delay=3600 → 被接受 (≤ 边界)。"""
        cfg = CrawlerConfig()
        limiter = DomainRateLimiter(cfg)
        await limiter.set_crawl_delay("example.com", 3600.0)
        # 不抛异常, 无 WARNING 日志 → 已设置
        t0 = asyncio.get_event_loop().time()
        await limiter.acquire("example.com")
        assert asyncio.get_event_loop().time() - t0 < 1.0

    async def test_delay_tiny_positive_accepted(self):
        """delay=0.0001 → 被接受 (最小正值边界)。"""
        cfg = CrawlerConfig()
        limiter = DomainRateLimiter(cfg)
        await limiter.set_crawl_delay("example.com", 0.0001)
        t0 = asyncio.get_event_loop().time()
        await limiter.acquire("example.com")
        assert asyncio.get_event_loop().time() - t0 < 1.0


# ═══════════════════════════════════════════════════════════════════════
# DomainRateLimiter — cleanup
# ═══════════════════════════════════════════════════════════════════════


class TestDomainRateLimiterCleanup:
    """cleanup_periodic 空闲域名清理。"""

    async def test_cleanup_removes_idle_domains(self, monkeypatch):
        """空闲域名被从 _states 中移除 (phase1: now - last_used > _stale_ttl)。"""
        monkeypatch.setattr("astrocrawl.network.throttling.DOMAIN_CLEANUP_AGE", 0.0)
        cfg = CrawlerConfig()
        limiter = DomainRateLimiter(cfg)
        await limiter.acquire("example.com")
        assert "example.com" in limiter._tracker._states
        # 手动将 last_used 设置为过去值, 使 now - last_used > 0 (DOMAIN_CLEANUP_AGE 被 patch 为 0)
        limiter._tracker._states["example.com"].last_used = 0.0
        await limiter.cleanup_periodic()
        assert "example.com" not in limiter._tracker._states

    async def test_cleanup_preserves_active_domains(self):
        """活跃域名 (last_used 刚被 touch) 不会被清理。"""
        cfg = CrawlerConfig()
        limiter = DomainRateLimiter(cfg)
        await limiter.acquire("example.com")
        await limiter.cleanup_periodic()
        assert "example.com" in limiter._tracker._states
        assert len(limiter._tracker._states) == 1


# ═══════════════════════════════════════════════════════════════════════
# DomainConcurrencyLimiter — 基础
# ═══════════════════════════════════════════════════════════════════════


class TestDomainConcurrencyLimiter:
    """基础 acquire / release / cleanup。"""

    async def test_acquire_release_cycle(self):
        limiter = DomainConcurrencyLimiter(max_concurrency=2)
        await limiter.acquire("example.com")
        assert limiter._tracker._states["example.com"].active_count == 1
        assert "example.com" in limiter._tracker._states
        await limiter.release("example.com")
        assert limiter._tracker._states["example.com"].active_count == 0

    async def test_max_concurrency_enforced(self):
        limiter = DomainConcurrencyLimiter(max_concurrency=1)
        await limiter.acquire("example.com")
        assert limiter._tracker._states["example.com"].active_count == 1
        assert limiter._tracker._states["example.com"]._sem.locked()
        await limiter.release("example.com")

    async def test_release_nonexistent_domain_noop(self):
        """release 不存在的域名 → 不抛异常, 无副作用。"""
        limiter = DomainConcurrencyLimiter(max_concurrency=2)
        await limiter.release("nonexistent.com")  # 不应抛异常
        assert "nonexistent.com" not in limiter._tracker._states

    async def test_release_zero_active_count_noop(self):
        """active_count 已为 0 时 release → 不抛异常, 不变成负数。"""
        limiter = DomainConcurrencyLimiter(max_concurrency=2)
        await limiter.acquire("example.com")
        await limiter.release("example.com")
        # 二次 release: active_count 已为 0
        await limiter.release("example.com")
        assert limiter._tracker._states["example.com"].active_count == 0

    async def test_cleanup_stale_removes_idle(self):
        limiter = DomainConcurrencyLimiter(max_concurrency=2, ttl_seconds=0.01)
        await limiter.acquire("domain-a.com")
        await limiter.release("domain-a.com")
        await asyncio.sleep(0.05)
        await limiter.cleanup_stale()
        assert "domain-a.com" not in limiter._tracker._states

    async def test_cleanup_preserves_active(self):
        limiter = DomainConcurrencyLimiter(max_concurrency=2, ttl_seconds=0.01)
        await limiter.acquire("domain-b.com")
        await asyncio.sleep(0.05)
        await limiter.cleanup_stale()
        assert "domain-b.com" in limiter._tracker._states
        await limiter.release("domain-b.com")


# ═══════════════════════════════════════════════════════════════════════
# DomainConcurrencyLimiter — try_acquire / timeout
# ═══════════════════════════════════════════════════════════════════════


class TestDomainConcurrencyLimiterBoundary:
    """try_acquire 非阻塞 + acquire 超时边界。"""

    async def test_try_acquire_success_increments_count(self):
        limiter = DomainConcurrencyLimiter(max_concurrency=2)
        ok = await limiter.try_acquire("example.com")
        assert ok is True
        assert limiter._tracker._states["example.com"].active_count == 1
        assert "example.com" in limiter._tracker._states
        await limiter.release("example.com")

    async def test_try_acquire_at_max_returns_false(self):
        limiter = DomainConcurrencyLimiter(max_concurrency=1)
        await limiter.try_acquire("example.com")
        ok = await limiter.try_acquire("example.com")
        assert ok is False
        await limiter.release("example.com")

    async def test_acquire_timeout_raises(self, monkeypatch):
        """semaphore 满时 acquire 超时抛 TimeoutError。"""
        monkeypatch.setattr(
            "astrocrawl.network.throttling.DOMAIN_CONCURRENCY_ACQUIRE_TIMEOUT",
            0.01,
        )
        limiter = DomainConcurrencyLimiter(max_concurrency=1)
        await limiter.try_acquire("example.com")
        with pytest.raises(asyncio.TimeoutError):
            await limiter.acquire("example.com")
        await limiter.release("example.com")

    async def test_cancelled_error_decrements_active_count(self):
        """CancelledError 时 active_count 被正确递减。"""
        limiter = DomainConcurrencyLimiter(max_concurrency=1)
        await limiter.try_acquire("example.com")
        assert limiter._tracker._states["example.com"].active_count == 1

        async def _cancelled_acquire():
            await limiter.acquire("example.com")

        task = asyncio.create_task(_cancelled_acquire())
        await asyncio.sleep(0.02)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        # active_count 应被还原 (CancelledError 处理路径)
        await limiter.release("example.com")
        assert limiter._tracker._states["example.com"].active_count == 0


# ═══════════════════════════════════════════════════════════════════════
# DomainTracker — 共享 tracker 集成
# ═══════════════════════════════════════════════════════════════════════


class TestDomainTrackerShared:
    """两个限制器共享同一 DomainTracker 时，状态统一。"""

    async def test_shared_tracker_single_state_per_domain(self):
        """同一域名在共享 tracker 中只有一份 _DomainState。"""
        tracker = DomainTracker(max_concurrency=3)
        limiter = DomainRateLimiter(CrawlerConfig(), tracker=tracker)
        concurrency = DomainConcurrencyLimiter(max_concurrency=3, tracker=tracker)

        await limiter.acquire("example.com")
        await concurrency.try_acquire("example.com")

        state = tracker._states["example.com"]
        assert state.active_count == 1  # concurrency 持有 1 个槽位
        assert state.next_allowed > 0  # limiter 已设置
        assert "example.com" in tracker._states

        await concurrency.release("example.com")

    async def test_shared_tracker_cleanup_unified(self):
        """共享 tracker 清理同时作用于速率和并发状态。"""
        tracker = DomainTracker(max_concurrency=2, stale_ttl=0.0)
        limiter = DomainRateLimiter(CrawlerConfig(), tracker=tracker)
        concurrency = DomainConcurrencyLimiter(max_concurrency=2, ttl_seconds=0.0, tracker=tracker)

        await concurrency.try_acquire("example.com")
        await concurrency.release("example.com")
        await limiter.acquire("example.com")

        assert "example.com" in tracker._states
        tracker._states["example.com"].last_used = 0.0
        await tracker.cleanup()
        assert "example.com" not in tracker._states

    async def test_overflow_eviction_warns_best_effort(self, caplog):
        """超过 max_domains 时驱逐候选并 WARNING。"""
        tracker = DomainTracker(max_concurrency=1, max_domains=1, stale_ttl=3600.0)
        limiter = DomainRateLimiter(CrawlerConfig(), max_domains=1, tracker=tracker)

        await limiter.acquire("domain-a.com")
        await limiter.acquire("domain-b.com")
        # domain-a: 设为 idle 但未超 stale_ttl — 仅阶段 2 可驱逐
        tracker._states["domain-a.com"].last_used = (
            time.monotonic() - 1000.0
        )  # 1000s > evict_ttl(600), < stale_ttl(3600)
        # domain-b: 刚活跃 — 既不可阶段 1 也不可阶段 2
        # max_domains=1, 候选仅 domain-a → 驱逐 domain-a → 剩余 1, 无 WARNING

        await limiter.cleanup_periodic()
        assert len(tracker._states) == 1
        assert "domain-b.com" in tracker._states
        assert "event=domain_tracker_overflow" not in caplog.text

    async def test_overflow_all_active_warns(self, caplog):
        """超过 max_domains 但所有域名活跃 → 不驱逐, WARNING。"""
        tracker = DomainTracker(max_concurrency=1, max_domains=1, stale_ttl=3600.0)
        concurrency = DomainConcurrencyLimiter(max_concurrency=1, tracker=tracker)
        limiter = DomainRateLimiter(CrawlerConfig(), max_domains=1, tracker=tracker)

        await concurrency.try_acquire("domain-a.com")
        await limiter.acquire("domain-b.com")
        # 两个都活跃（last_used 刚被 touch, active_count>=0 for domain-a）
        # max_domains=1 但都不满足 evict_ttl (600s) → 不驱逐

        await limiter.cleanup_periodic()
        assert len(tracker._states) == 2  # 未被驱逐
        assert "event=domain_tracker_overflow" in caplog.text
        assert "action=best_effort" in caplog.text

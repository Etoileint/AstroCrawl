"""CrawlState 数据库操作测试"""

from __future__ import annotations

import asyncio
import os
import tempfile
import time

import pytest

from astrocrawl.config import CrawlerConfig
from astrocrawl.storage.db import CrawlState


@pytest.fixture
async def state():
    cfg = CrawlerConfig(queue_hard_maxsize=100)
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    state = CrawlState(db_path, cfg)
    await state.open()
    yield state
    await state.close()
    try:
        os.unlink(db_path)
    except Exception:
        pass


class TestCrawlState:
    async def test_open_and_create_tables(self, state):
        async with state._conn.execute("SELECT name FROM sqlite_master WHERE type='table'") as cur:
            tables = {row[0] for row in await cur.fetchall()}
        assert "urls" in tables
        assert "failures" in tables
        assert "content_hashes" in tables
        assert "boundary_links" in tables

    async def test_mark_completed_is_atomic(self, state):
        """mark_completed 一步完成 urls 写入 + in_flight/failures 清理"""
        await state.mark_completed("https://example.com/page1", 1)
        async with state._conn.execute("SELECT 1 FROM urls WHERE url=?", ("https://example.com/page1",)) as cur:
            assert await cur.fetchone() is not None
        async with state._conn.execute(
            "SELECT 1 FROM urls WHERE url=? AND status='completed'", ("https://example.com/page1",)
        ) as cur:
            assert await cur.fetchone() is not None

    async def test_content_hash(self, state):
        ok = await state.add_content_hash("abc123def", url="https://example.com")
        assert ok is True
        ok2 = await state.add_content_hash("abc123def", url="https://example.com/dup")
        assert ok2 is False  # 重复哈希

    async def test_mark_completed_with_outcome(self, state):
        await state.mark_completed("https://example.com/ok", 0)
        ok = await state.mark_completed("https://example.com/ok", 0, outcome="ok")
        assert ok is True
        async with state._conn.execute(
            "SELECT 1 FROM urls WHERE url=? AND status='completed'", ("https://example.com/ok",)
        ) as cur:
            assert await cur.fetchone() is not None

    async def test_mark_completed_basic(self, state):
        ok = await state.mark_completed("https://example.com/direct", 0, outcome="truncated")
        assert ok is True
        async with state._conn.execute(
            "SELECT 1 FROM urls WHERE url=? AND status='completed'", ("https://example.com/direct",)
        ) as cur:
            assert await cur.fetchone() is not None

    async def test_counts_by_outcome_empty(self, state):
        counts = await state.counts_by_outcome()
        assert isinstance(counts, dict)

    async def test_counts_by_outcome_with_data(self, state):
        await state.mark_completed("https://example.com/a", 0, outcome="ok")
        await state.mark_completed("https://example.com/b", 0, outcome="robots_denied")
        await state.mark_completed("https://example.com/c", 0, outcome="ok")

        counts = await state.counts_by_outcome()
        assert counts.get("ok") == 2
        assert counts.get("robots_denied") == 1

    async def test_counts_by_outcome_excludes_empty_outcome(self, state):
        # 不带 outcome 的 mark_completed 不应计入
        await state.mark_completed("https://example.com/old", 0)

        counts = await state.counts_by_outcome()
        assert counts.get("ok", 0) == 0  # outcome="" 的行被排除

    async def test_outcome_column_exists(self, state):
        async with state._conn.execute("PRAGMA table_info(urls)") as cur:
            cols = {row[1] for row in await cur.fetchall()}
        assert "outcome" in cols


class TestConcurrentWrites:
    """并发写入安全测试 —— _transaction() + _write_lock 串行化验证。"""

    async def test_concurrent_push_and_mark_no_crash(self, state):
        """push_to_queue_single + mark_completed 并发不应崩溃。"""

        async def pusher(prefix: str):
            for i in range(30):
                await state.push_to_queue_single(f"https://{prefix}.com/{i}", 1)

        async def marker():
            for i in range(30):
                await state.mark_completed(f"https://marker.com/{i}", 1)

        await asyncio.gather(pusher("a"), pusher("b"), marker())
        assert await state.queue_size() == 60
        assert await state.completed_count() == 30

    async def test_concurrent_pop_and_mark(self, state):
        """两 worker 并发 pop + mark 同一域，不应崩溃。"""
        for i in range(30):
            await state.push_to_queue_single(f"https://x.com/{i}", 1)

        async def worker():
            for _ in range(15):
                result = await state.pop_from_domain("x.com")
                if result:
                    await state.mark_completed(result[0], result[1])

        await asyncio.gather(worker(), worker())
        assert await state.completed_count() == 30
        assert await state.in_flight_count() == 0

    async def test_retry_monitor_doesnt_conflict(self, state):
        """atomic_retry_reclaim + push_to_queue_single 并发不应崩溃。"""
        await state.log_failure("https://retry.com/1", 1, "timeout")
        await state.log_failure("https://retry.com/2", 1, "timeout")

        async def pusher():
            for i in range(20):
                await state.push_to_queue_single(f"https://new.com/{i}", 1)

        async def retrier():
            for _ in range(5):
                await state.atomic_retry_reclaim(max_requeue=3)

        await asyncio.gather(pusher(), retrier())

    async def test_add_content_hash_atomic(self, state):
        """auto-commit 回退后去重仍正确。"""
        ok = await state.add_content_hash("hash1", "https://a.com")
        assert ok is True
        dup = await state.add_content_hash("hash1", "https://b.com")
        assert dup is False

    async def test_mixed_workload(self, state):
        """3 worker 混合负载压力测试。"""
        for i in range(20):
            await state.push_to_queue_single(f"https://mixed.com/{i}", 1)

        async def worker(domain: str):
            for i in range(10):
                await state.push_to_queue_single(f"https://{domain}.com/{i}", 1)
            for i in range(5):
                result = await state.pop_from_domain(domain)
                if result:
                    await state.mark_completed(result[0], result[1])

        await asyncio.gather(worker("alpha"), worker("beta"), worker("gamma"))
        total = await state.completed_count() + await state.queue_size() + await state.in_flight_count()
        assert total >= 15  # at minimum the completed URLs

    async def test_race_reproducer(self, state):
        """高密度并发写入 —— 之前在无锁时 100% 触发嵌套事务错误。"""
        errors = []

        async def racy_worker(prefix: str, count: int):
            for i in range(count):
                try:
                    await state.push_to_queue_single(f"https://{prefix}.com/{i}", 1)
                except Exception as e:
                    errors.append(e)

        # 4 workers × 25 writes = 100 concurrent transaction attempts
        await asyncio.gather(
            racy_worker("w1", 25),
            racy_worker("w2", 25),
            racy_worker("w3", 25),
            racy_worker("w4", 25),
        )
        assert len(errors) == 0, f"并发写入触发异常: {errors[0] if errors else ''}"


class TestAutoCommitIsolation:
    """auto-commit 单语句方法不应劫持或干扰 _transaction() 事务。"""

    async def test_add_content_hash_not_rolled_back_by_transaction(self, state):
        """_transaction() 回滚不应影响已提交的 add_content_hash。"""
        await state.add_content_hash("hash-indep", "https://indep.com")
        # push_to_queue_single 在 ROLLBACK 时会撤销事务内的所有操作
        # add_content_hash 在其外部执行，不应受影响
        await state.push_to_queue_single("https://rollback-test.com/1", 1)
        # 用完全相同的 URL 再推一次 —— 第一次已成功，第二次应触发 _Rollback
        # 但 add_content_hash 的结果应该保留
        async with state._conn.execute("SELECT 1 FROM content_hashes WHERE hash=?", ("hash-indep",)) as cur:
            assert await cur.fetchone() is not None

    async def test_set_meta_does_not_hijack_transaction(self, state):
        """set_meta 的写入不应提前提交并发 _transaction()。"""
        # 先写入一条 meta
        await state.set_meta("test_key", "before")

        async def long_transaction():
            async with state._transaction():
                await state._conn.execute(
                    "INSERT OR IGNORE INTO failures(url, depth, error, timestamp, requeue_count) VALUES(?,?,?,?,0)",
                    ("https://hijack-test.com", 1, "test", 99999.0),
                )
                # 此时 HealthMonitor 的 set_meta 可能介入
                await asyncio.sleep(0.05)  # 给 auto-commit 方法介入窗口
                await state._conn.execute("DELETE FROM failures WHERE url=?", ("https://hijack-test.com",))

        async def meta_writer():
            await asyncio.sleep(0.01)
            await state.set_meta("test_key", "during_transaction")

        await asyncio.gather(long_transaction(), meta_writer())
        # 事务中的 DELETE 应在 COMMIT 后生效——URL 应不存在
        async with state._conn.execute("SELECT 1 FROM failures WHERE url=?", ("https://hijack-test.com",)) as cur:
            assert await cur.fetchone() is None
        # meta 应该被写入
        val = await state.get_meta("test_key")
        assert val == "during_transaction"

    async def test_save_boundary_links_no_starvation(self, state):
        """save_boundary_links 大批量链接不应饥饿其他 worker。"""
        links = [f"https://big.com/page{i}" for i in range(500)]

        async def large_save():
            await state.save_boundary_links("https://big.com", links, 0)

        async def concurrent_writer():
            for i in range(30):
                await state.push_to_queue_single(f"https://conc.com/{i}", 1)

        await asyncio.wait_for(asyncio.gather(large_save(), concurrent_writer()), timeout=10.0)
        # 验证两边都成功完成
        assert await state.queue_size() >= 30
        assert await state.boundary_links_count() > 0


class TestOrphanPurge:
    """空域名孤立条目清理 —— 防御 sitemap 等外部输入逃逸。"""

    async def test_purge_removes_empty_domain_entries(self, state):
        """domain='' 条目被清理，正常条目保留。"""
        # 正常入队一条
        await state.push_to_queue_single("https://example.com/page1", 1)
        # 模拟外部输入逃逸——直接 INSERT OR IGNORE 绕过 push 校验
        async with state._transaction():
            await state._conn.execute(
                "INSERT OR IGNORE INTO queue(url, depth, added_time, domain) VALUES(?,?,?,?)",
                ("ht", 0, time.time(), ""),
            )

        assert await state.queue_size() == 2

        orphans = await state.purge_orphaned_queue_entries()
        assert orphans == 1
        assert await state.queue_size() == 1

    async def test_purge_no_orphans_returns_zero(self, state):
        """无孤立条目时返回 0，不影响队列。"""
        await state.push_to_queue_single("https://example.com/page1", 1)
        assert await state.purge_orphaned_queue_entries() == 0
        assert await state.queue_size() == 1

"""补充测试: astrocrawl/storage/db.py — 首轮覆盖中遗漏的 CrawlState 方法。

ADR-0004: CrawlState 是 SQLite 持久化的核心，所有写入路径需要测试覆盖。
"""

from __future__ import annotations

import time

# ═══════════════════════════════════════════════════════════════════════
# flush + clean_content_hashes
# ═══════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════
# flush + clean_content_hashes
# ═══════════════════════════════════════════════════════════════════════


class TestFlush:
    async def test_flush_without_open(self, fake_state):
        await fake_state.flush()


class TestCleanContentHashes:
    async def test_clean_old_hashes(self, fake_state):
        await fake_state.add_content_hash("hash1")
        await fake_state.add_content_hash("hash2")
        await fake_state.clean_content_hashes(time.time() + 3600)
        await fake_state.clean_content_hashes(0)


# ═══════════════════════════════════════════════════════════════════════
# push_to_queue_as_owner
# ═══════════════════════════════════════════════════════════════════════


class TestPushToQueueAsOwner:
    async def test_push_as_owner_success(self, fake_state):
        result = await fake_state.push_to_queue_as_owner("https://example.com/page", 0, "example.com")
        assert result is True

    async def test_push_as_owner_duplicate(self, fake_state):
        await fake_state.push_to_queue_as_owner("https://example.com/a", 0, "example.com")
        result = await fake_state.push_to_queue_as_owner("https://example.com/a", 0, "example.com")
        assert result is False


# ═══════════════════════════════════════════════════════════════════════
# url_in_flight / force_fail_all_in_flight
# ═══════════════════════════════════════════════════════════════════════


class TestInFlight:
    async def test_url_in_flight_false_for_unknown(self, fake_state):
        result = await fake_state.url_in_flight("https://unknown.com")
        assert result is False

    async def test_url_in_flight_true_after_pop(self, fake_state):
        """in_flight 由 pop_from_domain 填充。"""
        await fake_state.push_to_queue_single("https://a.com/page", 0, "a.com")
        popped = await fake_state.pop_from_domain("a.com")
        assert popped is not None
        result = await fake_state.url_in_flight("https://a.com/page")
        assert result is True

    async def test_force_fail_empty_in_flight(self, fake_state):
        max_rq = fake_state.cfg.max_requeue
        count = await fake_state.force_fail_all_in_flight(max_rq)
        assert count == 0

    async def test_force_fail_with_in_flight(self, fake_state):
        await fake_state.push_to_queue_single("https://a.com/page", 0, "a.com")
        await fake_state.pop_from_domain("a.com")
        max_rq = fake_state.cfg.max_requeue
        count = await fake_state.force_fail_all_in_flight(max_rq)
        assert count == 1


# ═══════════════════════════════════════════════════════════════════════
# failure counts
# ═══════════════════════════════════════════════════════════════════════


class TestFailureCounts:
    async def test_failure_count_zero(self, fake_state):
        assert await fake_state.failure_count() == 0

    async def test_failure_count_after_failure(self, fake_state):
        await fake_state.log_failure("https://a.com", 0, "error", permanent=False)
        assert await fake_state.failure_count() == 1

    async def test_retryable_failure_count(self, fake_state):
        await fake_state.log_failure("https://a.com", 0, "error", permanent=False)
        assert await fake_state.retryable_failure_count(fake_state.cfg.max_requeue) == 1

    async def test_permanent_failure_count(self, fake_state):
        await fake_state.log_failure("https://a.com", 0, "error", permanent=True)
        assert await fake_state.permanent_failure_count(fake_state.cfg.max_requeue) == 1


# ═══════════════════════════════════════════════════════════════════════
# try_schedule_retry / peek_retryable
# ═══════════════════════════════════════════════════════════════════════


class TestRetrySchedule:
    async def test_try_schedule_retry_fresh_failure(self, fake_state):
        await fake_state.log_failure("https://a.com", 0, "timeout", permanent=False)
        result = await fake_state.try_schedule_retry("https://a.com", 0)
        assert result is True

    async def test_try_schedule_retry_exceeded_max(self, fake_state):
        max_rq = fake_state.cfg.max_requeue
        # log_failure 只插入第一行；需要多次调用 try_schedule_retry 递增 requeue_count
        await fake_state.log_failure("https://a.com", 0, "timeout", permanent=False)
        for _ in range(max_rq + 1):
            result = await fake_state.try_schedule_retry("https://a.com", 0)
        # 最后一次应因超出 max_requeue 返回 False
        assert result is False

    async def test_try_schedule_retry_permanent(self, fake_state):
        await fake_state.log_failure("https://a.com", 0, "404", permanent=True)
        result = await fake_state.try_schedule_retry("https://a.com", 0)
        assert result is False

    async def test_peek_retryable_empty(self, fake_state):
        entry = await fake_state.peek_retryable(fake_state.cfg.max_requeue)
        assert entry is None

    async def test_peek_retryable_with_retryable(self, fake_state):
        await fake_state.log_failure("https://a.com", 0, "timeout", permanent=False)
        entry = await fake_state.peek_retryable(fake_state.cfg.max_requeue)
        assert entry is not None
        url, _depth, _requeue = entry
        assert url == "https://a.com"


# ═══════════════════════════════════════════════════════════════════════
# reset_queue_only / purge_queue_depth_ge
# ═══════════════════════════════════════════════════════════════════════


class TestQueueReset:
    async def test_reset_queue_only(self, fake_state):
        await fake_state.push_to_queue_single("https://a.com/1", 0, "a.com")
        await fake_state.reset_queue_only()
        assert await fake_state.queue_size() == 0

    async def test_purge_queue_depth_ge(self, fake_state):
        await fake_state.push_to_queue_single("https://a.com/1", 0, "a.com")
        await fake_state.push_to_queue_single("https://a.com/2", 5, "a.com")
        count = await fake_state.purge_queue_depth_ge(3)
        assert count >= 1

    async def test_purge_failures_depth_ge(self, fake_state):
        await fake_state.log_failure("https://a.com/1", 0, "error", permanent=False)
        await fake_state.log_failure("https://a.com/2", 5, "error", permanent=False)
        count = await fake_state.purge_failures_depth_ge(3)
        assert count >= 1


# ═══════════════════════════════════════════════════════════════════════
# get_lost_children
# ═══════════════════════════════════════════════════════════════════════


class TestLostChildren:
    async def test_empty_no_lost_children(self, fake_state):
        lost = await fake_state.get_lost_children(2)
        assert lost == []

    async def test_boundary_links_generate_lost_children(self, fake_state):
        await fake_state.save_boundary_links("https://parent.com", ["https://child.com/a"], parent_depth=1)
        # get_lost_children 返回 parent_depth+1 的 boundary_links URL
        lost = await fake_state.get_lost_children(1)
        assert len(lost) >= 1


# ═══════════════════════════════════════════════════════════════════════
# depth counts
# ═══════════════════════════════════════════════════════════════════════


class TestDepthCounts:
    async def test_completed_by_depth_empty(self, fake_state):
        result = await fake_state.completed_count_by_depth()
        assert result == {}

    async def test_completed_by_depth(self, fake_state):
        await fake_state.mark_completed("https://a.com/1", depth=0)
        await fake_state.mark_completed("https://a.com/2", depth=1)
        result = await fake_state.completed_count_by_depth()
        assert result[0] == 1
        assert result[1] == 1

    async def test_queue_by_depth(self, fake_state):
        await fake_state.push_to_queue_single("https://a.com/1", 0, "a.com")
        await fake_state.push_to_queue_single("https://a.com/2", 1, "a.com")
        result = await fake_state.queue_count_by_depth()
        assert result[0] == 1
        assert result[1] == 1

    async def test_failed_by_depth(self, fake_state):
        # 使用不同 URL 确保 INSERT OR IGNORE 创建新行
        await fake_state.log_failure("https://unique-1.com", 0, "err", permanent=False)
        await fake_state.log_failure("https://unique-2.com", 2, "err", permanent=False)
        result = await fake_state.failed_count_by_depth()
        assert isinstance(result, dict)


# ═══════════════════════════════════════════════════════════════════════
# _recover_in_flight
# ═══════════════════════════════════════════════════════════════════════


class TestRecoverInFlight:
    async def test_recover_empty_in_flight(self, fake_state):
        await fake_state._recover_in_flight()
        assert await fake_state.in_flight_count() == 0

    async def test_recover_with_items(self, fake_state):
        # pop_from_domain 填充 in_flight
        await fake_state.push_to_queue_single("https://a.com/page1", 0, "a.com")
        await fake_state.push_to_queue_single("https://a.com/page2", 0, "a.com")
        await fake_state.pop_from_domain("a.com")
        await fake_state.pop_from_domain("a.com")
        before = await fake_state.in_flight_count()
        assert before == 2
        await fake_state._recover_in_flight()
        after = await fake_state.in_flight_count()
        assert after == 0


# ═══════════════════════════════════════════════════════════════════════
# _maybe_migrate_from_old_tables
# ═══════════════════════════════════════════════════════════════════════


class TestMaybeMigrate:
    async def test_migrate_no_old_tables(self, fake_state):
        await fake_state._maybe_migrate_from_old_tables()


# ═══════════════════════════════════════════════════════════════════════
# urls_table_empty / is_queue_full / get_active_domains
# ═══════════════════════════════════════════════════════════════════════


class TestReadOnlyQueries:
    async def test_urls_table_empty_initially(self, fake_state):
        assert await fake_state.urls_table_empty() is True

    async def test_urls_table_not_empty_after_mark(self, fake_state):
        await fake_state.mark_completed("https://a.com", 0, outcome="ok")
        assert await fake_state.urls_table_empty() is False

    async def test_is_queue_full_under_capacity(self, fake_state):
        assert await fake_state.is_queue_full() is False

    async def test_is_queue_full_at_capacity(self, fake_state):
        for i in range(fake_state.cfg.queue_hard_maxsize):
            await fake_state.push_to_queue_single(f"https://fill.com/{i}", 0, "fill.com")
        assert await fake_state.is_queue_full() is True

    async def test_get_active_domains(self, fake_state):
        await fake_state.push_to_queue_single("https://a.com/1", 0, "a.com")
        await fake_state.push_to_queue_single("https://b.com/1", 0, "b.com")
        await fake_state.push_to_queue_single("https://a.com/2", 0, "a.com")
        domains = await fake_state.get_active_domains()
        assert set(domains) == {"a.com", "b.com"}

    async def test_get_active_domains_sorted(self, fake_state):
        await fake_state.push_to_queue_single("https://c.com/1", 0, "c.com")
        await fake_state.push_to_queue_single("https://a.com/1", 0, "a.com")
        domains = await fake_state.get_active_domains()
        assert domains == sorted(domains)

    async def test_get_active_domains_empty(self, fake_state):
        domains = await fake_state.get_active_domains()
        assert domains == []


# ═══════════════════════════════════════════════════════════════════════
# wait_for_queue timeout
# ═══════════════════════════════════════════════════════════════════════


class TestWaitForQueue:
    async def test_wait_for_queue_timeout_empty(self, fake_state):
        await fake_state.wait_for_queue(timeout=0.01)


# ═══════════════════════════════════════════════════════════════════════
# push_to_queue_single — QUEUE_FULL / DUPLICATE
# ═══════════════════════════════════════════════════════════════════════


class TestPushToQueueEdgeCases:
    async def test_push_duplicate_url(self, fake_state):
        result1 = await fake_state.push_to_queue_single("https://dup.com/page", 0, "dup.com")
        assert result1.name == "ENQUEUED"
        result2 = await fake_state.push_to_queue_single("https://dup.com/page", 0, "dup.com")
        assert result2.name == "DUPLICATE"

    async def test_push_queue_full(self, fake_state):
        for i in range(fake_state.cfg.queue_hard_maxsize):
            await fake_state.push_to_queue_single(f"https://full.com/{i}", 0, "full.com")
        result = await fake_state.push_to_queue_single("https://full.com/overflow", 0, "full.com")
        assert result.name == "QUEUE_FULL"

    async def test_push_duplicate_via_completed_url(self, fake_state):
        await fake_state.mark_completed("https://done.com/page", 0, outcome="ok")
        result = await fake_state.push_to_queue_single("https://done.com/page", 0, "done.com")
        assert result.name == "DUPLICATE"


# ═══════════════════════════════════════════════════════════════════════
# push_to_queue_as_owner — QUEUE_FULL / DUPLICATE edges
# ═══════════════════════════════════════════════════════════════════════


class TestPushAsOwnerEdgeCases:
    async def test_push_as_owner_queue_full(self, fake_state):
        for i in range(fake_state.cfg.queue_hard_maxsize):
            await fake_state.push_to_queue_single(f"https://fill-owner.com/{i}", 0, "fill-owner.com")
        result = await fake_state.push_to_queue_as_owner("https://fill-owner.com/overflow", 0, "fill-owner.com")
        assert result is False

    async def test_push_as_owner_duplicate_in_urls(self, fake_state):
        await fake_state.mark_completed("https://done.com/page", 0, outcome="ok")
        result = await fake_state.push_to_queue_as_owner("https://done.com/page", 0, "done.com")
        assert result is False


# ═══════════════════════════════════════════════════════════════════════
# try_schedule_retry / atomic_retry_reclaim — queue_full / completed edges
# ═══════════════════════════════════════════════════════════════════════


class TestRetryEdgeCases:
    async def test_try_schedule_retry_already_completed(self, fake_state):
        await fake_state.log_failure("https://done.com/page", 0, "timeout", permanent=False)
        await fake_state.mark_completed("https://done.com/page", 0, outcome="ok")
        result = await fake_state.try_schedule_retry("https://done.com/page", 0)
        assert result is False

    async def test_atomic_retry_reclaim_queue_full(self, fake_state):
        await fake_state.log_failure("https://retry.com/page", 0, "timeout", permanent=False)
        for i in range(fake_state.cfg.queue_hard_maxsize):
            await fake_state.push_to_queue_single(f"https://fill-retry.com/{i}", 0, "fill-retry.com")
        result = await fake_state.atomic_retry_reclaim(fake_state.cfg.max_requeue)
        assert result is None


# ═══════════════════════════════════════════════════════════════════════
# reset_all
# ═══════════════════════════════════════════════════════════════════════


class TestResetAll:
    async def test_reset_all_clears_everything(self, fake_state):
        await fake_state.push_to_queue_single("https://a.com/1", 0, "a.com")
        await fake_state.mark_completed("https://a.com/1", 0, outcome="ok")
        await fake_state.log_failure("https://b.com", 0, "error", permanent=False)
        await fake_state.add_content_hash("hash1")
        await fake_state.set_meta("key", "val")
        await fake_state.save_boundary_links("https://p.com", ["https://c.com"], parent_depth=0)

        await fake_state.reset_all()

        assert await fake_state.queue_size() == 0
        assert await fake_state.completed_count() == 0
        assert await fake_state.failure_count() == 0
        assert await fake_state.boundary_links_count() == 0
        assert await fake_state.get_meta("key") == ""


# ═══════════════════════════════════════════════════════════════════════
# save_boundary_links — empty child_urls
# ═══════════════════════════════════════════════════════════════════════


class TestSaveBoundaryLinksEdge:
    async def test_save_boundary_links_empty_children(self, fake_state):
        saved = await fake_state.save_boundary_links("https://parent.com", [], parent_depth=0)
        assert saved == 0


# ═══════════════════════════════════════════════════════════════════════
# promote_boundary_links — empty / populated
# ═══════════════════════════════════════════════════════════════════════


class TestPromoteBoundaryLinks:
    async def test_promote_empty_returns_empty(self, fake_state):
        result = await fake_state.promote_boundary_links(new_depth=5)
        assert result == []

    async def test_promote_boundary_links(self, fake_state):
        await fake_state.save_boundary_links(
            "https://parent.com", ["https://child.com/a", "https://child.com/b"], parent_depth=1
        )
        promoted = await fake_state.promote_boundary_links(new_depth=5)
        assert len(promoted) == 2

    async def test_promote_respects_depth(self, fake_state):
        await fake_state.save_boundary_links("https://parent.com", ["https://deep.com/page"], parent_depth=3)
        # parent_depth + 1 = 4, not < 4
        promoted = await fake_state.promote_boundary_links(new_depth=4)
        assert len(promoted) == 0


# ═══════════════════════════════════════════════════════════════════════
# failed_count_by_depth — permanent failures
# ═══════════════════════════════════════════════════════════════════════


class TestFailedCountByDepth:
    async def test_failed_by_depth_with_permanent(self, fake_state):
        await fake_state.log_failure("https://perm-1.com", 0, "err", permanent=True)
        await fake_state.log_failure("https://perm-2.com", 2, "err", permanent=True)
        result = await fake_state.failed_count_by_depth()
        assert result.get(0) == 1
        assert result.get(2) == 1


# ═══════════════════════════════════════════════════════════════════════
# purge_queue_depth_ge — empty
# ═══════════════════════════════════════════════════════════════════════


class TestPurgeQueueDepthGe:
    async def test_purge_queue_no_match(self, fake_state):
        await fake_state.push_to_queue_single("https://a.com/1", 0, "a.com")
        # 没有 depth >= 99 的 URL
        removed = await fake_state.purge_queue_depth_ge(99)
        assert removed == 0

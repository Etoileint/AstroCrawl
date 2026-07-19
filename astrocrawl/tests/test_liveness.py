"""LivenessTracker 测试 — 对标 Erlang heartbeat + timeout 机制。

时间不可 monkeypatch (CPython C 函数)。采用灰盒方式直接操作 _heartbeats 字典。
"""

from __future__ import annotations

import time

from astrocrawl.crawler.liveness import LivenessTracker


class TestInit:
    def test_sets_heartbeats_for_count(self):
        tracker = LivenessTracker(3, timeout=10.0)
        assert list(tracker._heartbeats.keys()) == [0, 1, 2]
        now = time.monotonic()
        for ts in tracker._heartbeats.values():
            assert now - ts < 1.0

    def test_count_zero_empty_dict(self):
        tracker = LivenessTracker(0, timeout=10.0)
        assert tracker._heartbeats == {}


class TestHeartbeat:
    def test_updates_timestamp(self):
        tracker = LivenessTracker(1, timeout=10.0)
        old_ts = tracker._heartbeats[0]
        time.sleep(0.001)
        tracker.heartbeat(0)
        assert tracker._heartbeats[0] > old_ts

    def test_nonexistent_creates_entry(self):
        tracker = LivenessTracker(1, timeout=10.0)
        tracker.heartbeat(99)
        assert 99 in tracker._heartbeats
        assert tracker.alive_count == 2  # 0 + 99

    def test_restores_freshness_after_stale(self):
        tracker = LivenessTracker(1, timeout=1.0)
        tracker._heartbeats[0] = time.monotonic() - 2.0
        assert tracker.stale_count == 1
        tracker.heartbeat(0)
        assert tracker.stale_count == 0
        assert tracker.alive_count == 1


class TestRemove:
    def test_deletes_entry(self):
        tracker = LivenessTracker(2, timeout=10.0)
        tracker.remove(0)
        assert 0 not in tracker._heartbeats
        assert tracker.alive_count == 1

    def test_nonexistent_noop(self):
        tracker = LivenessTracker(1, timeout=10.0)
        tracker.remove(99)  # 不抛异常


class TestAliveCount:
    def test_all_fresh(self):
        tracker = LivenessTracker(3, timeout=10.0)
        assert tracker.alive_count == 3

    def test_all_stale(self):
        tracker = LivenessTracker(3, timeout=1.0)
        for i in range(3):
            tracker._heartbeats[i] = time.monotonic() - 2.0
        assert tracker.alive_count == 0

    def test_mixed(self):
        tracker = LivenessTracker(3, timeout=1.0)
        tracker._heartbeats[0] = time.monotonic() - 2.0  # stale
        tracker._heartbeats[1] = time.monotonic()  # fresh
        tracker._heartbeats[2] = time.monotonic() - 2.0  # stale
        assert tracker.alive_count == 1

    def test_empty_tracker(self):
        tracker = LivenessTracker(0, timeout=1.0)
        assert tracker.alive_count == 0


class TestBoundary:
    """alive_count 使用 <= (inclusive), stale_count/all_stale 使用 > (exclusive)。

    注意: time.monotonic() 在设置 _heartbeats 和属性求值之间会推进，
    因此不能精确设到 timeout 边界。使用小 epsilon 保证语义正确。
    """

    def test_exactly_at_timeout_not_stale(self):
        """now - ts <= timeout → alive; now - ts > timeout → stale。
        设 ts 为 timeout - epsilon 前，确保属性求值时仍在 timeout 内。"""
        tracker = LivenessTracker(1, timeout=1.0)
        now = time.monotonic()
        tracker._heartbeats[0] = now - 1.0 + 0.05  # 0.95s ago, within 1.0s
        assert tracker.stale_count == 0
        assert tracker.alive_count == 1
        assert tracker.all_stale is False

    def test_just_beyond_timeout_is_stale(self):
        tracker = LivenessTracker(1, timeout=1.0)
        tracker._heartbeats[0] = time.monotonic() - 1.1  # 1.1s ago, beyond 1.0s
        assert tracker.stale_count == 1
        assert tracker.alive_count == 0


class TestAllStale:
    def test_empty_tracker_false(self):
        """空追踪器 → all_stale=False (空 ≠ 全部停滞)。"""
        tracker = LivenessTracker(0, timeout=1.0)
        assert tracker.all_stale is False

    def test_all_fresh_false(self):
        tracker = LivenessTracker(2, timeout=10.0)
        assert tracker.all_stale is False

    def test_all_expired_true(self):
        tracker = LivenessTracker(2, timeout=0.1)
        for i in range(2):
            tracker._heartbeats[i] = time.monotonic() - 1.0
        assert tracker.all_stale is True

    def test_mixed_false(self):
        tracker = LivenessTracker(2, timeout=1.0)
        tracker._heartbeats[0] = time.monotonic() - 2.0  # stale
        tracker._heartbeats[1] = time.monotonic()  # fresh
        assert tracker.all_stale is False

    def test_single_expired_true(self):
        """N=1 边界: 唯一 Worker 停滞 → all_stale=True。"""
        tracker = LivenessTracker(1, timeout=0.1)
        tracker._heartbeats[0] = time.monotonic() - 1.0
        assert tracker.all_stale is True

    def test_single_fresh_false(self):
        """N=1 边界: 唯一 Worker 活跃 → all_stale=False。"""
        tracker = LivenessTracker(1, timeout=10.0)
        assert tracker.all_stale is False


class TestStaleCount:
    def test_boundary_values(self):
        tracker = LivenessTracker(3, timeout=1.0)
        assert tracker.stale_count == 0
        tracker._heartbeats[0] = time.monotonic() - 2.0
        assert tracker.stale_count == 1
        tracker._heartbeats[1] = time.monotonic() - 2.0
        assert tracker.stale_count == 2
        tracker._heartbeats[2] = time.monotonic() - 2.0
        assert tracker.stale_count == 3

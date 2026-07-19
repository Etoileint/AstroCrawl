"""Fuse 熔断器测试。

两状态熔断器 (CLOSED → OPEN)，对标 Erlang/OTP Supervisor 哲学：
- 窗口内失败数超过阈值 → OPEN
- OPEN 后不自动恢复
- on_open 回调自动触发，异常被捕获不传播
"""

from __future__ import annotations

import asyncio
import logging
import time

from astrocrawl.resilience import Fuse


class _CallbackError(Exception):
    pass


# ═══════════════════════════════════════════════════════════════════════
# Fuse.__init__ + 属性
# ═══════════════════════════════════════════════════════════════════════


class TestFuseInit:
    def test_default_parameters(self):
        fuse = Fuse("default")
        assert fuse.max_failures == 10
        assert fuse.within_seconds == 60.0
        assert fuse.is_open is False

    def test_custom_parameters(self):
        fuse = Fuse("custom", max_failures=5, within_seconds=30.0)
        assert fuse.max_failures == 5
        assert fuse.within_seconds == 30.0
        assert fuse.name == "custom"

    def test_initial_state_closed(self):
        fuse = Fuse("test")
        assert fuse.is_open is False
        assert fuse._death_times == []

    def test_name_property(self):
        fuse = Fuse("my_fuse")
        assert fuse.name == "my_fuse"


# ═══════════════════════════════════════════════════════════════════════
# Fuse.record_failure() — 核心状态机 + 滑动窗口
# ═══════════════════════════════════════════════════════════════════════


class TestFuseRecordFailure:
    async def test_failures_above_max_opens_fuse(self):
        """max=2 → 需要 3 次失败 (len=3 > 2)。"""
        fuse = Fuse("test", max_failures=2, within_seconds=3600.0)
        assert await fuse.record_failure() is False
        assert await fuse.record_failure() is False
        assert fuse.is_open is False
        opened = await fuse.record_failure()
        assert opened is True
        assert fuse.is_open is True

    async def test_failures_equal_max_does_not_open(self):
        """max=2 → 恰好 2 次失败不触发 (len=2 不 > 2)。"""
        fuse = Fuse("test", max_failures=2, within_seconds=3600.0)
        await fuse.record_failure()
        await fuse.record_failure()
        assert fuse.is_open is False

    async def test_max_failures_zero_opens_immediately(self):
        """max=0 → 首次失败即熔断 (len=1 > 0)。"""
        fuse = Fuse("test", max_failures=0, within_seconds=3600.0)
        opened = await fuse.record_failure()
        assert opened is True
        assert fuse.is_open is True

    async def test_window_pruning_removes_expired_entries(self):
        """窗口外失败不计入计数。"""
        fuse = Fuse("test", max_failures=2, within_seconds=0.05)
        await fuse.record_failure()
        await fuse.record_failure()
        assert len(fuse._death_times) == 2
        # 等待窗口过期
        await asyncio.sleep(0.1)
        # 再次 record: 旧条目被裁剪，新条目追加，窗口内只有 1 条
        await fuse.record_failure()
        assert fuse.is_open is False
        assert len(fuse._death_times) == 1

    def test_entry_exactly_at_boundary_removed(self):
        """t > cutoff 是严格大于——恰好在 cutoff 的条目被移除。"""
        fuse = Fuse("test", max_failures=5, within_seconds=10.0)
        fuse._death_times = [100.0]
        now = 110.0
        cutoff = now - fuse.within_seconds  # = 100.0
        kept = [t for t in fuse._death_times if t > cutoff]
        assert kept == []

    async def test_record_failure_after_open_returns_true(self):
        """已 OPEN 后再 record → 立即返回 True，无副作用。"""
        fuse = Fuse("test", max_failures=0, within_seconds=3600.0)
        await fuse.record_failure()  # opens
        assert fuse.is_open is True
        # 再次 record
        opened = await fuse.record_failure()
        assert opened is True
        assert fuse.is_open is True

    async def test_record_failure_after_open_no_side_effect(self):
        """已 OPEN 后 record_failure 不追加 death_times，不重触发回调。"""
        call_count = 0

        async def _cb(_msg: str) -> None:
            nonlocal call_count
            call_count += 1

        fuse = Fuse("test", max_failures=0, within_seconds=3600.0, on_open=_cb)
        await fuse.record_failure()
        assert call_count == 1
        death_count_after_open = len(fuse._death_times)
        # 再次 record: 不应追加 death_times 也不应触发回调
        await fuse.record_failure()
        assert call_count == 1
        assert len(fuse._death_times) == death_count_after_open

    async def test_record_failure_returns_false_when_not_open(self):
        fuse = Fuse("test", max_failures=10, within_seconds=3600.0)
        result = await fuse.record_failure()
        assert result is False

    def test_all_entries_in_window_preserved(self):
        """窗口内条目全部保留。"""
        fuse = Fuse("test", max_failures=10, within_seconds=3600.0)
        fuse._death_times = [100.0, 200.0, 300.0]
        now = 500.0
        cutoff = now - fuse.within_seconds  # = -3100.0
        kept = [t for t in fuse._death_times if t > cutoff]
        assert len(kept) == 3


# ═══════════════════════════════════════════════════════════════════════
# Fuse.get_health()
# ═══════════════════════════════════════════════════════════════════════


class TestFuseGetHealth:
    def test_get_health_up(self):
        fuse = Fuse("test")
        h = fuse.get_health()
        assert h.status == "UP"

    def test_get_health_degraded(self):
        fuse = Fuse("test", max_failures=10, within_seconds=3600.0)
        fuse._death_times = [time.time() - 60.0]
        h = fuse.get_health()
        assert h.status == "DEGRADED"
        assert "1 次失败" in h.message

    def test_get_health_down(self):
        fuse = Fuse("test", max_failures=10, within_seconds=3600.0)
        fuse._is_open = True
        now = time.time()
        fuse._death_times = [now - 60.0] * 11
        h = fuse.get_health()
        assert h.status == "DOWN"
        assert "熔断器 test 已打开" in h.message
        assert h.details["failures"] == 11

    def test_get_health_window_decay_restores_up(self):
        """窗口外条目被 get_health 只读过滤——不依赖 record_failure 裁剪。"""
        fuse = Fuse("test", max_failures=10, within_seconds=3600.0)
        fuse._death_times = [100.0]  # epoch 时间戳，远在窗口外
        h = fuse.get_health()
        assert h.status == "UP"
        assert h.details.get("failures", 0) == 0
        # 验证 _death_times 未被修改（get_health 只读语义）
        assert fuse._death_times == [100.0]

    def test_get_health_mixed_window(self):
        """窗口内/外混合条目 → 只计窗口内数目，_death_times 不被修改。"""
        fuse = Fuse("test", max_failures=10, within_seconds=3600.0)
        nowish = time.time()
        fuse._death_times = [nowish - 4000.0, nowish - 500.0, nowish - 100.0]
        h = fuse.get_health()
        assert h.status == "DEGRADED"
        assert h.details["failures"] == 2
        assert len(fuse._death_times) == 3  # 只读，原始列表不变

    def test_window_filtered_count_boundary_excluded(self):
        """t == cutoff 被排除（严格大于），与 record_failure 裁剪语义一致。"""
        fuse = Fuse("test", max_failures=10, within_seconds=10.0)
        now = time.time()
        # now-10.0 恰好在边界；now-5.0 有 5s 缓冲确保不受两次 time.time() 微秒差影响
        fuse._death_times = [now - 10.0, now - 5.0]
        count = fuse._window_filtered_count()
        assert count == 1  # 仅 now-5.0 在窗口内（严格大于 cutoff）
        assert len(fuse._death_times) == 2  # 原始列表不变

    def test_get_health_details_structure(self):
        fuse = Fuse("test", max_failures=5, within_seconds=3600.0)
        now = time.time()
        fuse._death_times = [now - 100.0, now - 50.0]
        h = fuse.get_health()
        assert h.details["failures"] == 2
        assert h.details["max"] == 5


# ═══════════════════════════════════════════════════════════════════════
# Fuse on_open 回调
# ═══════════════════════════════════════════════════════════════════════


class TestFuseOnOpenCallback:
    """保留原有 2 个测试 + 新增 3 个。"""

    async def test_fuse_callback_exception_logged(self, caplog):
        """Fuse 回调异常不再静默——通过 record_failure 的公开 API 触发。"""
        called = False

        async def _failing_callback(_msg: str) -> None:
            nonlocal called
            called = True
            raise _CallbackError("callback exploded")

        fuse = Fuse(
            name="test_fuse",
            max_failures=0,  # 首次 record_failure 即熔断
            within_seconds=3600.0,
            on_open=_failing_callback,
        )

        with caplog.at_level(logging.ERROR, logger="astrocrawl.resilience"):
            opened = await fuse.record_failure()

        assert opened
        assert fuse.is_open
        assert called
        assert "event=fuse_callback_error" in caplog.text
        assert "test_fuse" in caplog.text

    async def test_fuse_callback_success_no_error_log(self, caplog):
        """回调成功时不存在 error 级别日志。"""
        called = False

        async def _good_callback(_msg: str) -> None:
            nonlocal called
            called = True

        fuse = Fuse(name="ok_fuse", max_failures=0, within_seconds=3600.0, on_open=_good_callback)

        with caplog.at_level(logging.ERROR, logger="astrocrawl.resilience"):
            await fuse.record_failure()

        assert fuse.is_open
        assert called
        assert "event=fuse_callback_error" not in caplog.text

    async def test_callback_not_set_no_error(self, caplog):
        """on_open=None → 正常熔断，无回调错误日志。"""
        fuse = Fuse("test", max_failures=0, within_seconds=3600.0)

        with caplog.at_level(logging.ERROR, logger="astrocrawl.resilience"):
            opened = await fuse.record_failure()

        assert opened is True
        assert fuse.is_open is True
        assert "event=fuse_callback_error" not in caplog.text

    async def test_callback_called_exactly_once(self):
        """多次 record_failure 只触发一次 on_open。"""
        call_count = 0

        async def _cb(_msg: str) -> None:
            nonlocal call_count
            call_count += 1

        fuse = Fuse("test", max_failures=0, within_seconds=3600.0, on_open=_cb)
        await fuse.record_failure()
        assert call_count == 1
        # 再次 record: 已 OPEN，不应触发回调
        await fuse.record_failure()
        await fuse.record_failure()
        assert call_count == 1

    async def test_callback_receives_name_in_message(self):
        """回调参数为 f'{name} 熔断'。"""
        received: str | None = None

        async def _cb(msg: str) -> None:
            nonlocal received
            received = msg

        fuse = Fuse("my_component", max_failures=0, within_seconds=3600.0, on_open=_cb)
        await fuse.record_failure()
        assert received == "my_component 熔断"

"""WorkerSupervisor 测试 — 对标 Erlang/OTP Supervisor Behaviour。

测试 OTP one_for_one 重启策略、Fuse 熔断边界、Worker 生命周期管理。
使用 asyncio.Event 协调 Worker 死亡时机，避免竞态。
Worker 通过 died set 区分初代/替换：初代死亡一次后替换代永久阻塞。
"""

from __future__ import annotations

import asyncio
import contextlib
import time

import pytest

from astrocrawl.crawler.supervisors import Supervisor, WorkerSupervisor
from astrocrawl.health import Health

# ═══════════════════════════════════════════════════════════════════════
# 辅助: 安全的 supervise 取消
# ═══════════════════════════════════════════════════════════════════════


@contextlib.asynccontextmanager
async def _run_supervise(sv: WorkerSupervisor, stop: asyncio.Event):
    """启动 supervise 并在退出时安全取消。"""
    task = asyncio.create_task(sv.supervise(stop))
    try:
        yield task
    finally:
        stop.set()
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, RuntimeError):
                await task
        # 清理残留 worker
        for _, t in sv._workers:
            if not t.done():
                t.cancel()


def _dying_worker(die: asyncio.Event, died: set, blocker: asyncio.Event):
    """工厂返回 coroutine: 初代 Worker 等 die 后抛异常，替换代永久阻塞。"""

    async def worker(idx):
        if idx in died:
            await blocker.wait()  # 替换代：永久阻塞
        else:
            died.add(idx)
            await die.wait()
            raise RuntimeError(f"Worker-{idx} simulated death")

    return worker


# ═══════════════════════════════════════════════════════════════════════
# Supervisor 基类
# ═══════════════════════════════════════════════════════════════════════


class TestSupervisorBase:
    def test_fuse_property(self):
        sv = Supervisor("test", max_restarts=5, within_seconds=30.0)
        assert sv.fuse.max_failures == 5
        assert sv.fuse.within_seconds == 30.0
        assert sv.fuse.is_open is False

    def test_get_health_delegates_to_fuse(self):
        sv = Supervisor("test")
        health = sv.get_health()
        assert isinstance(health, Health)
        assert health.status == "UP"

    def test_fuse_initial_state_closed(self):
        sv = Supervisor("test")
        assert sv.fuse.is_open is False


# ═══════════════════════════════════════════════════════════════════════
# WorkerSupervisor.start()
# ═══════════════════════════════════════════════════════════════════════


class TestStart:
    async def test_creates_n_tasks(self):
        sv = WorkerSupervisor()
        blocker = asyncio.Event()

        async def worker(idx):
            await blocker.wait()

        await sv.start(3, worker)
        assert len(sv._workers) == 3
        for i, (idx, task) in enumerate(sv._workers):
            assert idx == i
            assert isinstance(task, asyncio.Task)
        for _, t in sv._workers:
            t.cancel()

    async def test_task_names_include_worker_idx(self):
        sv = WorkerSupervisor()
        blocker = asyncio.Event()

        async def worker(idx):
            await blocker.wait()

        await sv.start(2, worker)
        names = [t.get_name() for _, t in sv._workers]
        assert "Worker-0" in names
        assert "Worker-1" in names
        for _, t in sv._workers:
            t.cancel()

    async def test_tasks_returns_task_objects(self):
        sv = WorkerSupervisor()
        blocker = asyncio.Event()

        async def worker(idx):
            await blocker.wait()

        await sv.start(2, worker)
        tasks = sv.tasks
        assert len(tasks) == 2
        assert all(isinstance(t, asyncio.Task) for t in tasks)
        for _, t in sv._workers:
            t.cancel()


# ═══════════════════════════════════════════════════════════════════════
# WorkerSupervisor.supervise() 正常路径
# ═══════════════════════════════════════════════════════════════════════


class TestSuperviseNormal:
    async def test_stop_event_exits(self):
        sv = WorkerSupervisor()
        blocker = asyncio.Event()
        stop = asyncio.Event()

        async def worker(idx):
            await blocker.wait()

        await sv.start(2, worker)
        async with _run_supervise(sv, stop) as _sv_task:
            await asyncio.sleep(0.1)
            assert not _sv_task.done()
        # context manager 退出时 stop.set() + cancel

    async def test_empty_workers_sleeps(self):
        """start 未调用 → _workers 为空 → supervise 循环 sleep，不 crash。"""
        sv = WorkerSupervisor()
        stop = asyncio.Event()
        sv_task = asyncio.create_task(sv.supervise(stop))
        await asyncio.sleep(0.2)
        assert not sv_task.done()
        stop.set()
        await asyncio.wait_for(sv_task, timeout=2.0)

    async def test_no_death_when_all_healthy(self):
        sv = WorkerSupervisor()
        stop = asyncio.Event()

        async def worker(idx):
            while not stop.is_set():
                await asyncio.sleep(0.01)

        await sv.start(2, worker)
        async with _run_supervise(sv, stop) as _sv_task:
            await asyncio.sleep(0.2)
            assert len(sv._workers) == 2  # 无 Worker 被移除


# ═══════════════════════════════════════════════════════════════════════
# WorkerSupervisor 死亡恢复
# ═══════════════════════════════════════════════════════════════════════


class TestDeathRecovery:
    async def test_one_for_one_replacement(self):
        die = asyncio.Event()
        died: set[int] = set()
        blocker = asyncio.Event()
        sv = WorkerSupervisor(max_restarts=10, within_seconds=60.0)
        stop = asyncio.Event()

        await sv.start(2, _dying_worker(die, died, blocker))

        async with _run_supervise(sv, stop) as _sv_task:
            die.set()  # 初代两个 worker 同时死亡
            await asyncio.sleep(0.3)
            assert len(sv._workers) == 2  # 两个都已被替换
            assert all(isinstance(t, asyncio.Task) for _, t in sv._workers)

    async def test_replacement_preserves_idx(self):
        die = asyncio.Event()
        died: set[int] = set()
        blocker = asyncio.Event()
        sv = WorkerSupervisor(max_restarts=10, within_seconds=60.0)
        stop = asyncio.Event()

        await sv.start(3, _dying_worker(die, died, blocker))

        async with _run_supervise(sv, stop) as _sv_task:
            die.set()
            await asyncio.sleep(0.3)
            idxs = sorted(idx for idx, _ in sv._workers)
            assert idxs == [0, 1, 2]  # idx 不变，Worker-2 仍是 Worker-2

    async def test_replacement_uses_same_factory(self):
        die = asyncio.Event()
        died: set[int] = set()
        blocker = asyncio.Event()
        sv = WorkerSupervisor(max_restarts=10, within_seconds=60.0)
        stop = asyncio.Event()

        await sv.start(2, _dying_worker(die, died, blocker))
        await asyncio.sleep(0.05)  # 等 worker 启动
        assert len(died) == 2  # 初代都已在 died 中

        async with _run_supervise(sv, stop) as _sv_task:
            die.set()
            await asyncio.sleep(0.3)
            # 替换的 Worker 调用同一 factory → 阻塞在 blocker.wait()
            assert len(sv._workers) == 2
            assert sv.fuse.is_open is False

    async def test_cancelled_not_replaced(self):
        sv = WorkerSupervisor(max_restarts=5, within_seconds=60.0)
        stop = asyncio.Event()
        cancelled_flag = False

        async def worker(idx):
            nonlocal cancelled_flag
            if idx == 0:
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cancelled_flag = True
                    raise
            else:
                await asyncio.Event().wait()

        await sv.start(2, worker)
        sv_task = asyncio.create_task(sv.supervise(stop))
        await asyncio.sleep(0.05)
        # Cancel Worker-0
        sv._workers[0][1].cancel()
        await asyncio.sleep(0.3)
        assert cancelled_flag
        remaining_idxs = [idx for idx, _ in sv._workers]
        assert 0 not in remaining_idxs  # 被移除
        assert len(sv._workers) == 1  # 未被替换
        stop.set()
        sv_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sv_task
        for _, t in sv._workers:
            if not t.done():
                t.cancel()

    async def test_normal_exit_not_replaced(self):
        sv = WorkerSupervisor(max_restarts=5, within_seconds=60.0)
        stop = asyncio.Event()

        async def worker(idx):
            return  # 正常退出，无异常

        await sv.start(2, worker)
        sv_task = asyncio.create_task(sv.supervise(stop))
        await asyncio.sleep(0.3)
        assert len(sv._workers) == 0  # 全部正常退出，不替换
        stop.set()
        await asyncio.wait_for(sv_task, timeout=2.0)


# ═══════════════════════════════════════════════════════════════════════
# _factory=None 降级
# ═══════════════════════════════════════════════════════════════════════


class TestFactoryNoneDegradation:
    async def test_factory_none_removes_without_replace(self):
        sv = WorkerSupervisor(max_restarts=10, within_seconds=60.0)
        stop = asyncio.Event()

        async def worker(idx):
            raise RuntimeError("death")

        await sv.start(3, worker)
        sv._factory = None
        sv_task = asyncio.create_task(sv.supervise(stop))
        await asyncio.sleep(0.3)
        assert len(sv._workers) < 3
        stop.set()
        sv_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sv_task
        for _, t in sv._workers:
            if not t.done():
                t.cancel()


# ═══════════════════════════════════════════════════════════════════════
# Fuse 边界行为
# ═══════════════════════════════════════════════════════════════════════


class TestFuseBoundary:
    async def test_max_failures_not_exceeded(self):
        """失败数 == max_failures → 不熔断，继续替换。"""
        die = asyncio.Event()
        died: set[int] = set()
        blocker = asyncio.Event()
        sv = WorkerSupervisor(max_restarts=2, within_seconds=60.0)
        stop = asyncio.Event()

        await sv.start(1, _dying_worker(die, died, blocker))

        async with _run_supervise(sv, stop) as _sv_task:
            die.set()
            await asyncio.sleep(0.3)
            assert died == {0}
            assert sv.fuse.is_open is False

    async def test_max_failures_exceeded_raises_runtime_error(self):
        sv = WorkerSupervisor(max_restarts=1, within_seconds=60.0)
        stop = asyncio.Event()

        async def worker(idx):
            raise RuntimeError("death")

        await sv.start(1, worker)
        sv_task = asyncio.create_task(sv.supervise(stop))
        with pytest.raises(RuntimeError, match="熔断"):
            await sv_task
        assert sv.fuse.is_open is True
        stop.set()

    async def test_window_decay(self):
        """窗口外失败不计入熔断计数。"""
        sv = WorkerSupervisor(max_restarts=2, within_seconds=0.01)
        stop = asyncio.Event()
        death_count = 0

        async def worker(idx):
            nonlocal death_count
            death_count += 1
            await asyncio.sleep(0.05)  # 超过 within_seconds=0.01
            raise RuntimeError(f"death #{death_count}")

        await sv.start(1, worker)
        sv_task = asyncio.create_task(sv.supervise(stop))
        await asyncio.sleep(0.3)
        assert sv.fuse.is_open is False
        assert death_count >= 2
        stop.set()
        sv_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, RuntimeError):
            await sv_task
        for _, t in sv._workers:
            if not t.done():
                t.cancel()


# ═══════════════════════════════════════════════════════════════════════
# 多 Worker 同时死亡
# ═══════════════════════════════════════════════════════════════════════


class TestMultipleSimultaneousDeaths:
    async def test_simultaneous_deaths_handled_one_per_cycle(self):
        die = asyncio.Event()
        died: set[int] = set()
        blocker = asyncio.Event()
        sv = WorkerSupervisor(max_restarts=10, within_seconds=60.0)
        stop = asyncio.Event()

        await sv.start(3, _dying_worker(die, died, blocker))

        async with _run_supervise(sv, stop) as _sv_task:
            die.set()
            await asyncio.sleep(0.4)
            assert len(sv._workers) == 3  # 3 个都已替换
            assert len(died) == 3  # 每个 idx 只死一次


# ═══════════════════════════════════════════════════════════════════════
# WorkerSupervisor — HealthChecked 协议验证
# ═══════════════════════════════════════════════════════════════════════


class TestWorkerSupervisorHealth:
    def test_get_health_structure(self):
        """WorkerSupervisor.get_health() 返回正确的 Health——UP 时无额外 details。"""
        sv = WorkerSupervisor(max_restarts=3, within_seconds=60.0)
        h = sv.get_health()
        assert h.status == "UP"
        assert isinstance(h, Health)

    def test_get_health_degraded_after_failure(self):
        """一次失败后 get_health() 返回 DEGRADED。"""
        sv = WorkerSupervisor(max_restarts=3, within_seconds=3600.0)
        sv.fuse._death_times = [time.time() - 60.0]
        h = sv.get_health()
        assert h.status == "DEGRADED"
        assert h.details["failures"] == 1

    def test_get_health_down_when_fuse_open(self):
        """Fuse 打开后 get_health() 返回 DOWN。"""
        sv = WorkerSupervisor(max_restarts=1, within_seconds=3600.0)
        sv.fuse._is_open = True
        h = sv.get_health()
        assert h.status == "DOWN"

    def test_get_health_none_guard(self):
        """验证 _supervisor=None 守卫模式正确——此处测试 Supervisor 可独立创建。"""
        sv = Supervisor("test")
        h = sv.get_health()
        assert isinstance(h, Health)
        assert h.status == "UP"

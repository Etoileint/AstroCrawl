"""ADR-0004 OTP Supervisor Tree — Worker 生命周期管理 + Fuse。

对标 Erlang/OTP Supervisor Behaviour:
- one_for_one: 单个子进程死亡 → 仅重启该子进程
- Fuse: 两状态熔断器（对标 Erlang Supervisor），防快速重启循环
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Awaitable, Callable, List, Optional, Tuple

from astrobasis import LogfmtLogger
from astrocrawl.resilience import Fuse

if TYPE_CHECKING:
    from astrocrawl.health import Health


class Supervisor:
    """Erlang/OTP 风格 Supervisor 基类。

    每个 Supervisor 管理一组子 asyncio.Task，提供:
    - Fuse 熔断保护
    - one_for_one 重启
    - 逆序关闭
    """

    def __init__(
        self,
        name: str,
        max_restarts: int = 10,
        within_seconds: float = 60.0,
        on_fatal: Optional[Callable[[str], Awaitable[None]]] = None,
    ):
        self.name = name
        self._log = LogfmtLogger(f"astrocrawl.supervisor.{name}")
        self._fuse = Fuse(
            name,
            max_restarts,
            within_seconds,
            on_fatal,
        )

    @property
    def fuse(self) -> Fuse:
        return self._fuse

    def get_health(self) -> Health:
        return self._fuse.get_health()


# ═══════════════════════════════════════════════════════════════════════
# WorkerSupervisor — one_for_one 同质 Worker 池
# ═══════════════════════════════════════════════════════════════════════


class WorkerSupervisor(Supervisor):
    """Erlang/OTP one_for_one: 管理 N 个同质 Worker Task。

    单个 Worker 死亡 → 仅替换该 Worker（同一 idx），其他 Worker 不受影响。
    对标 Erlang registered process name——Worker-3 永远是 Worker-3。
    通过 Fuse 防止快速重启循环。
    """

    def __init__(self, max_restarts: int = 10, within_seconds: float = 60.0, on_fatal=None):
        super().__init__("WorkerSupervisor", max_restarts, within_seconds, on_fatal)
        self._workers: List[Tuple[int, asyncio.Task]] = []
        self._factory: Optional[Callable[[int], Awaitable[None]]] = None

    async def start(self, concurrency: int, factory: Callable[[int], Awaitable[None]]) -> None:
        """启动 N 个 Worker Task。factory(idx) → coroutine。"""
        self._factory = factory
        self._workers = [(i, asyncio.create_task(factory(i), name=f"Worker-{i}")) for i in range(concurrency)]  # type: ignore[arg-type]

    @property
    def tasks(self) -> List[asyncio.Task]:
        """返回所有 Worker Task（不含 idx）。"""
        return [t for _, t in self._workers]

    async def supervise(self, stop_event: asyncio.Event) -> None:
        """监控 Worker 池，one_for_one 替换死亡 Worker（保持 idx 不变）。"""
        while not stop_event.is_set():
            if not self._workers:
                await asyncio.sleep(1.0)
                continue
            done, _pending = await asyncio.wait(
                [t for _, t in self._workers],
                return_when=asyncio.FIRST_COMPLETED,
                timeout=5.0,
            )
            for task in done:
                # 找到对应的 idx
                idx = None
                for i, t in self._workers:
                    if t is task:
                        idx = i
                        self._workers.remove((i, t))
                        break
                if idx is None:
                    continue
                if task.cancelled():
                    continue  # 关闭时取消，非故障
                exc = task.exception()
                if exc is None:
                    continue  # 正常退出
                self._log.warning("worker_death", idx=idx, error=exc)
                if await self._fuse.record_failure():
                    raise RuntimeError(
                        f"WorkerSupervisor 熔断: {self._fuse.max_failures} 次/{self._fuse.within_seconds}s"
                    )
                if self._factory is not None:
                    new_task: asyncio.Task[Any] = asyncio.create_task(self._factory(idx), name=f"Worker-{idx}")  # type: ignore[arg-type]
                    self._workers.append((idx, new_task))

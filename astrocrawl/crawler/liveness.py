"""LivenessTracker — 统一心跳追踪器。对标 Erlang heartbeat + timeout 机制。

Worker 用固定的逻辑索引（worker_idx）报告心跳，而非实例身份（id(task)）。
WorkerSupervisor 替换死 Worker 时新 Worker 继承同一 idx，心跳无缝接续。

死亡检测和停滞检测统一为心跳超时——不区分死亡原因。
"""

from __future__ import annotations

import time


class LivenessTracker:
    """统一心跳追踪器。

    对标 Erlang heartbeat + timeout：
    - 死亡检测：心跳停止 → 超时后判定为停滞
    - 停滞检测：同一机制，不需区分死亡原因
    - 正常退出：remove() 清理条目

    不关心谁在心跳——Worker / BrowserPool / ContextPool 任意组件均可注册。
    """

    def __init__(self, count: int, timeout: float) -> None:
        self._timeout = timeout
        self._heartbeats: dict[int, float] = {i: time.monotonic() for i in range(count)}

    def heartbeat(self, idx: int) -> None:
        """Worker idx 报告自己活着。"""
        self._heartbeats[idx] = time.monotonic()

    def remove(self, idx: int) -> None:
        """Worker idx 正常退出，从追踪中移除。"""
        self._heartbeats.pop(idx, None)

    @property
    def alive_count(self) -> int:
        now = time.monotonic()
        return sum(1 for ts in self._heartbeats.values() if now - ts <= self._timeout)

    @property
    def all_stale(self) -> bool:
        """所有追踪中的 Worker 心跳均已过期。"""
        if not self._heartbeats:
            return False
        now = time.monotonic()
        return all(now - ts > self._timeout for ts in self._heartbeats.values())

    @property
    def stale_count(self) -> int:
        now = time.monotonic()
        return sum(1 for ts in self._heartbeats.values() if now - ts > self._timeout)

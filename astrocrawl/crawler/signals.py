from __future__ import annotations

from typing import Any, Optional, Protocol


class CrawlerSignals(Protocol):
    """Worker/Engine 信号接口——消除 Any 返回类型和模块级可变全局。

    实现方式：PySide6 QObject（GUI 模式）或 _StubSignals（CLI/无头模式）。
    """

    layer_progress: Any
    stats_update: Any
    outcome_update: Any
    finished: Any
    error: Any
    pause_state: Any
    worker_state: Any
    rule_matched: Any
    rule_stats_updated: Any


SIGNAL_NAMES: frozenset[str] = frozenset(
    {
        "layer_progress",
        "stats_update",
        "outcome_update",
        "finished",
        "error",
        "pause_state",
        "worker_state",
        "rule_matched",
        "rule_stats_updated",
    }
)


_WorkerSignals: Optional[type[CrawlerSignals]] = None


def _get_worker_signals_class() -> type[CrawlerSignals]:
    global _WorkerSignals
    if _WorkerSignals is not None:
        return _WorkerSignals
    try:
        from PySide6.QtCore import QObject, Signal

        class _RealWorkerSignals(QObject):
            layer_progress = Signal(int, int, int)
            stats_update = Signal(int, int, int)
            outcome_update = Signal(dict)
            finished = Signal(str, dict)
            error = Signal(str)
            pause_state = Signal(bool)
            worker_state = Signal(int, str)
            rule_matched = Signal(str, object)
            rule_stats_updated = Signal(object)

        _WorkerSignals = _RealWorkerSignals
    except ImportError:

        class _StubSignal:
            """精确 Null Object — 对标 Go io.Discard。"""

            def emit(self, *args: Any, **kwargs: Any) -> None:
                pass

            def connect(self, *args: Any, **kwargs: Any) -> None:
                pass

        class _StubSignals:
            layer_progress = _StubSignal()
            stats_update = _StubSignal()
            outcome_update = _StubSignal()
            finished = _StubSignal()
            error = _StubSignal()
            pause_state = _StubSignal()
            worker_state = _StubSignal()
            rule_matched = _StubSignal()
            rule_stats_updated = _StubSignal()

        _WorkerSignals = _StubSignals
    return _WorkerSignals


def create_worker_signals() -> CrawlerSignals:
    """创建 CrawlerSignals 实例。仅在首次调用时加载 PySide6。"""
    cls = _get_worker_signals_class()
    return cls()

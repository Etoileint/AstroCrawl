"""CrawlSession — GUI 与爬虫引擎之间的 MVP 外观（QObject）。

对标 Qt moveToThread Worker 模式 + MVP Presenter：
- View (MainWindow) 只通过 CrawlSession 与引擎交互
- CrawlSession 内部管理 CrawlerThread 和 AsyncCrawler
- 信号/Slot 跨线程自动排队（Qt AutoConnection）
"""

from __future__ import annotations

import warnings
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from PySide6.QtCore import QObject, Signal

from astrocrawl.crawler.engine import AsyncCrawler, create_crawler
from astrocrawl.crawler.signals import SIGNAL_NAMES, create_worker_signals
from astrocrawl.gui.thread import CrawlerThread

if TYPE_CHECKING:
    from pathlib import Path

    from astrocrawl.config import CrawlerConfig, GlobalSettings
    from astrocrawl.proxy import ProxyProfile

# ── 信号注册表：connect/disconnect 的唯一真源 ──
# 新增信号只需在此加一个名字，_wire_signals() 和 dispose() 自动配对

_FORWARDED = tuple(sorted(SIGNAL_NAMES))

_WORKER_TO_SESSION = {
    "layer_progress": "layer_progress",
    "stats_update": "stats_updated",
    "outcome_update": "outcome_updated",
    "finished": "finished",
    "error": "error_occurred",
    "pause_state": "pause_changed",
    "worker_state": "worker_state_changed",
    "rule_matched": "rule_matched",
    "rule_stats_updated": "rule_stats_updated",
}

_SESSION_SIGNALS = (
    "message_logged",
    "layer_progress",
    "stats_updated",
    "outcome_updated",
    "finished",
    "error_occurred",
    "pause_changed",
    "worker_state_changed",
    "rule_matched",
    "rule_stats_updated",
    "session_done",
)


class CrawlSession(QObject):
    """MVP Presenter：GUI 通过此外观与爬虫引擎交互。

    两阶段构造：
    1. __init__(base_config) — 持久偏好（跨爬取会话不变）
    2. start(urls, depth, ...) — 运行时参数（每次点击 Start 不同）

    对标 Qt QProcess / QThread 的两阶段设计。
    """

    # ── 信号（GUI 线程消费） ──

    message_logged = Signal(str)
    layer_progress = Signal(int, int, int)
    stats_updated = Signal(int, int, int)
    outcome_updated = Signal(dict)
    finished = Signal(str, dict)
    error_occurred = Signal(str)
    pause_changed = Signal(bool)
    worker_state_changed = Signal(int, str)
    rule_matched = Signal(str, object)  # S9/N61: rule_name, trace_info
    rule_stats_updated = Signal(object)  # S9/N61: rule stats snapshot
    session_done = Signal()  # 线程完全结束后触发，GUI 处理 UI 重置

    def __init__(self, base_config: CrawlerConfig, global_settings: GlobalSettings, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._base_config = base_config
        self._global_settings = global_settings
        self._crawler: Optional[AsyncCrawler] = None
        self._thread: Optional[CrawlerThread] = None
        self._worker_signals: Any = None

    # ── 两阶段启动 ──

    def start(
        self,
        urls: List[str],
        depth: int,
        concurrency: int,
        output_path: str,
        same_domain_only: bool,
        cfg_overrides: Optional[Dict[str, Any]] = None,
        *,
        proxy_profile: ProxyProfile | None = None,
        proxy_mode_override: str | None = None,
        health_tracker: Any = None,
    ) -> None:
        """第二阶段：运行时参数启动爬取。"""
        cfg = replace(self._base_config, concurrency=concurrency)
        if cfg_overrides:
            cfg = replace(cfg, **cfg_overrides)

        self._worker_signals = create_worker_signals()
        self._wire_signals()

        self._crawler = create_crawler(
            start_urls=urls,
            depth=depth,
            concurrency=cfg.concurrency,
            output_path=output_path,
            same_domain_only=same_domain_only,
            signals=self._worker_signals,
            cfg=cfg,
            global_settings=self._global_settings,
            proxy_profile=proxy_profile,
            proxy_mode_override=proxy_mode_override,
            health_tracker=health_tracker,
        )

        self._thread = CrawlerThread(self._crawler)
        self._thread.finished.connect(self._on_thread_done)
        self._thread.start()

    def _wire_signals(self) -> None:
        """将内部 worker 信号转发到公开信号。遍历 _FORWARDED 注册表。"""
        ws = self._worker_signals
        for name in _FORWARDED:
            getattr(ws, name).connect(getattr(self, _WORKER_TO_SESSION[name]))

    # ── 控制 ──

    def pause(self) -> None:
        if self._thread and self._thread.isRunning():
            self._thread.pause()

    def resume(self) -> None:
        if self._thread and self._thread.isRunning():
            self._thread.resume()

    def stop(self) -> None:
        if self._thread and self._thread.isRunning():
            self._thread.stop()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    # ── 查询 ──

    @property
    def output_path(self) -> Path | None:
        return self._crawler.output_path if self._crawler else None

    @property
    def last_report(self) -> Optional[dict]:
        return self._crawler.last_report if self._crawler else None

    @property
    def proxy_manager(self) -> Any:
        return self._crawler.proxy_manager if self._crawler else None

    @property
    def stopped(self) -> bool:
        """爬虫是否已完成（run 返回）。"""
        return self._crawler is not None and not self.is_running() and self._thread is not None

    # ── 清理 ──

    def dispose(self) -> None:
        """确定性释放所有资源。幂等。仅从 GUI 线程调用。"""
        # Layer 1: 断开 worker→session 转发（修复 _wire_signals 对称性）
        if self._worker_signals is not None:
            ws = self._worker_signals
            self._worker_signals = None
            for name in _FORWARDED:
                try:
                    getattr(ws, name).disconnect()
                except RuntimeError:
                    pass

        # 断开 thread.finished→_on_thread_done（打破 thread→session 引用链）
        if self._thread is not None:
            try:
                self._thread.finished.disconnect()
            except RuntimeError:
                pass

        # Layer 2: 断开所有外部（MainWindow）连接
        for name in _SESSION_SIGNALS:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                try:
                    getattr(self, name).disconnect()
                except RuntimeError:
                    pass

        # Qt 防御性删除
        self.deleteLater()

    def disconnect_signals(self) -> None:
        """Deprecated: 使用 dispose() 替代。"""
        self.dispose()

    def _on_thread_done(self) -> None:
        """线程结束后断开信号并通知 GUI。内部引用保留，确保终态可查询。"""
        self.session_done.emit()  # MainWindow 槽同步执行完毕
        self.dispose()

"""Phase 1 — CrawlSession 状态机 + CrawlerThread 测试。

覆盖:
- S01-S22: CrawlSession 状态机全转换矩阵
- S23-S30: 信号完整性 (_wire_signals 转发验证)
- S31-S34: 信号隔离 (disconnect_signals 后泄漏检测 + rule_match/rule_stats 审计)
- T01-T17: CrawlerThread 线程安全控制 + run() 事件循环生命周期

信号验证使用手动 _SignalCollector，因为 PySide6 版本中 QSignalSpy 不支持下标访问。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.gui


# ═══════════════════════════════════════════════════════════════════════
# 辅助工具
# ═══════════════════════════════════════════════════════════════════════


class _SignalCollector:
    """收集 Signal 发射参数的轻量 spy。

    PySide6 的 QSignalSpy 在当前版本不支持 [index] 下标，
    因此使用手动 connect + list 收集。
    """

    def __init__(self, signal):
        self._calls: list[tuple] = []
        signal.connect(self._slot)

    def _slot(self, *args):
        self._calls.append(args)

    @property
    def count(self) -> int:
        return len(self._calls)

    def args(self, index: int = 0) -> tuple:
        return self._calls[index]

    def first_args(self) -> tuple:
        return self._calls[0]

    def all_args(self) -> list[tuple]:
        return list(self._calls)


def _make_session(monkeypatch, base_config=None):
    """构造 CrawlSession，注入 Fake 依赖。

    返回 (session, mock_thread_cls, mock_create, mock_crawler).
    mock_thread_cls 使用 side_effect 保证每次调用返回新的 MagicMock 实例。
    """
    from astrocrawl.config import CrawlerConfig, GlobalSettings
    from astrocrawl.gui.crawl_session import CrawlSession

    if base_config is None:
        base_config = CrawlerConfig(
            concurrency=1,
            domain_min_delay=0.0,
            domain_max_delay=0.0,
        )

    mock_thread_cls = MagicMock()
    mock_thread_cls.side_effect = lambda *a, **kw: MagicMock()
    mock_crawler = MagicMock()
    mock_crawler.output_path = None
    mock_crawler.last_report = None
    mock_crawler.proxy_manager = None
    mock_create = MagicMock(return_value=mock_crawler)

    monkeypatch.setattr("astrocrawl.gui.crawl_session.CrawlerThread", mock_thread_cls)
    monkeypatch.setattr("astrocrawl.gui.crawl_session.create_crawler", mock_create)

    session = CrawlSession(base_config, GlobalSettings())
    return session, mock_thread_cls, mock_create, mock_crawler


def _start_session(session):
    """用默认参数启动 session。"""
    session.start(
        urls=["https://example.com"],
        depth=2,
        concurrency=2,
        output_path="/tmp/test.jsonl",
        same_domain_only=True,
    )


def _get_thread_mock(session, mock_thread_cls):
    """获取当前 session 对应的 mock_thread 实例。

    因为 mock_thread_cls.side_effect 每次创建新 mock，
    需要通过 session._thread 引用获取正确的 mock。
    """
    return session._thread


# ═══════════════════════════════════════════════════════════════════════
# S01-S08: 初始状态与 no-op 操作
# ═══════════════════════════════════════════════════════════════════════


class TestCrawlSessionInitialState:
    def test_is_running_false_after_construction(self, monkeypatch):
        session, _, _, _ = _make_session(monkeypatch)
        assert not session.is_running()

    def test_output_path_none_after_construction(self, monkeypatch):
        session, _, _, _ = _make_session(monkeypatch)
        assert session.output_path is None

    def test_last_report_none_after_construction(self, monkeypatch):
        session, _, _, _ = _make_session(monkeypatch)
        assert session.last_report is None

    def test_proxy_manager_none_after_construction(self, monkeypatch):
        session, _, _, _ = _make_session(monkeypatch)
        assert session.proxy_manager is None

    def test_stopped_false_after_construction(self, monkeypatch):
        session, _, _, _ = _make_session(monkeypatch)
        assert not session.stopped

    def test_pause_noop_before_start(self, monkeypatch):
        session, _, _, _ = _make_session(monkeypatch)
        session.pause()
        assert not session.is_running()

    def test_resume_noop_before_start(self, monkeypatch):
        session, _, _, _ = _make_session(monkeypatch)
        session.resume()
        assert not session.is_running()

    def test_stop_noop_before_start(self, monkeypatch):
        session, _, _, _ = _make_session(monkeypatch)
        session.stop()
        assert not session.is_running()


# ═══════════════════════════════════════════════════════════════════════
# S07-S08: start() 启动
# ═══════════════════════════════════════════════════════════════════════


class TestCrawlSessionStart:
    def test_start_creates_internal_objects(self, monkeypatch):
        session, _, mock_create, mock_crawler = _make_session(monkeypatch)
        _start_session(session)

        assert session._crawler is mock_crawler
        assert session._thread is not None
        assert session._worker_signals is not None
        assert session.is_running()

    def test_start_calls_create_crawler_with_correct_args(self, monkeypatch):
        session, _, mock_create, _ = _make_session(monkeypatch)
        _start_session(session)

        mock_create.assert_called_once()
        kwargs = mock_create.call_args.kwargs
        assert kwargs["start_urls"] == ["https://example.com"]
        assert kwargs["depth"] == 2
        assert kwargs["concurrency"] == 2
        assert kwargs["output_path"] == "/tmp/test.jsonl"
        assert kwargs["same_domain_only"] is True

    def test_start_creates_and_starts_thread(self, monkeypatch):
        session, mock_thread_cls, _, _ = _make_session(monkeypatch)
        _start_session(session)

        mock_thread_cls.assert_called_once()
        mock_thread = _get_thread_mock(session, mock_thread_cls)
        mock_thread.start.assert_called_once()

    def test_repeat_start_creates_new_thread_and_crawler(self, monkeypatch):
        session, mock_thread_cls, mock_create, _ = _make_session(monkeypatch)
        _start_session(session)
        old_thread = session._thread
        old_crawler = session._crawler

        # 确保第二次 create_crawler 调用返回新的 mock_crawler
        mock_create.side_effect = lambda *a, **kw: MagicMock()

        session.start(
            urls=["https://new.com"],
            depth=3,
            concurrency=4,
            output_path="/tmp/new.jsonl",
            same_domain_only=False,
        )

        assert session._thread is not old_thread
        assert session._crawler is not old_crawler
        assert mock_create.call_count == 2


# ═══════════════════════════════════════════════════════════════════════
# S09-S14: RUNNING 和 PAUSED 状态下的控制
# ═══════════════════════════════════════════════════════════════════════


class TestCrawlSessionRunningOperations:
    def test_pause_delegates_to_thread(self, monkeypatch):
        session, mock_thread_cls, _, _ = _make_session(monkeypatch)
        _start_session(session)
        mock_thread = _get_thread_mock(session, mock_thread_cls)
        mock_thread.isRunning.return_value = True

        session.pause()
        mock_thread.pause.assert_called_once()

    def test_stop_delegates_to_thread(self, monkeypatch):
        session, mock_thread_cls, _, _ = _make_session(monkeypatch)
        _start_session(session)
        mock_thread = _get_thread_mock(session, mock_thread_cls)
        mock_thread.isRunning.return_value = True

        session.stop()
        mock_thread.stop.assert_called_once()

    def test_resume_delegates_to_thread(self, monkeypatch):
        session, mock_thread_cls, _, _ = _make_session(monkeypatch)
        _start_session(session)
        mock_thread = _get_thread_mock(session, mock_thread_cls)
        mock_thread.isRunning.return_value = True

        session.resume()
        mock_thread.resume.assert_called_once()

    def test_pause_twice_delegates_twice(self, monkeypatch):
        session, mock_thread_cls, _, _ = _make_session(monkeypatch)
        _start_session(session)
        mock_thread = _get_thread_mock(session, mock_thread_cls)
        mock_thread.isRunning.return_value = True

        session.pause()
        session.pause()
        assert mock_thread.pause.call_count == 2

    def test_stop_after_pause_delegates_both(self, monkeypatch):
        session, mock_thread_cls, _, _ = _make_session(monkeypatch)
        _start_session(session)
        mock_thread = _get_thread_mock(session, mock_thread_cls)
        mock_thread.isRunning.return_value = True

        session.pause()
        session.stop()
        mock_thread.pause.assert_called_once()
        mock_thread.stop.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════
# S15-S17: _on_thread_done 后的 DONE 状态
# ═══════════════════════════════════════════════════════════════════════


class TestCrawlSessionDoneState:
    def test_is_running_false_after_thread_done(self, monkeypatch):
        session, mock_thread_cls, _, _ = _make_session(monkeypatch)
        _start_session(session)
        mock_thread = _get_thread_mock(session, mock_thread_cls)
        mock_thread.isRunning.return_value = False
        session._on_thread_done()

        assert not session.is_running()

    def test_control_operations_noop_after_thread_done(self, monkeypatch):
        session, mock_thread_cls, _, _ = _make_session(monkeypatch)
        _start_session(session)
        session._on_thread_done()

        session.pause()
        session.resume()
        session.stop()

    def test_restart_after_done_creates_new_session(self, monkeypatch):
        session, mock_thread_cls, mock_create, _ = _make_session(monkeypatch)
        _start_session(session)
        session._on_thread_done()

        _start_session(session)
        assert session.is_running()
        assert mock_create.call_count == 2


# ═══════════════════════════════════════════════════════════════════════
# S18-S19: disconnect_signals 与 _on_thread_done
# ═══════════════════════════════════════════════════════════════════════


class TestCrawlSessionThreadDone:
    def test_on_thread_done_preserves_refs_and_signals_disconnected(self, monkeypatch):
        """_on_thread_done 后 _thread/_crawler 引用保留，信号已断开，stopped 正确。"""
        session, mock_thread_cls, _, _ = _make_session(monkeypatch)
        _start_session(session)
        ws = session._worker_signals
        mock_thread = _get_thread_mock(session, mock_thread_cls)
        mock_thread.isRunning.return_value = False

        session._on_thread_done()

        # 引用保留（dispose 不再擦除记忆）
        assert session._thread is not None
        assert session._crawler is not None
        # is_running 返回 False（线程已终止）
        assert not session.is_running()
        # stopped 正确返回 True（会话已完成）
        assert session.stopped
        # Layer 1 信号已断开
        collector = _SignalCollector(session.message_logged)
        ws.error.emit("after done")
        assert collector.count == 0

    def test_on_thread_done_emits_session_done(self, monkeypatch):
        session, mock_thread_cls, _, _ = _make_session(monkeypatch)
        _start_session(session)
        collector = _SignalCollector(session.session_done)

        session._on_thread_done()

        assert collector.count == 1


# ═══════════════════════════════════════════════════════════════════════
# S20-S21: start() 的 cfg_overrides merge
# ═══════════════════════════════════════════════════════════════════════


class TestCrawlSessionConfigMerge:
    def test_start_without_overrides_uses_base_config(self, monkeypatch):
        from astrocrawl.config import CrawlerConfig

        base = CrawlerConfig(
            concurrency=1,
            domain_min_delay=0.0,
            domain_max_delay=0.0,
            use_sitemap=False,
        )
        session, _, mock_create, _ = _make_session(monkeypatch, base_config=base)

        session.start(
            urls=["https://example.com"],
            depth=2,
            concurrency=5,
            output_path="/tmp/test.jsonl",
            same_domain_only=True,
        )

        passed_cfg = mock_create.call_args.kwargs["cfg"]
        assert passed_cfg.concurrency == 5
        assert not passed_cfg.use_sitemap

    def test_start_with_overrides_merges_into_config(self, monkeypatch):
        from astrocrawl.config import CrawlerConfig

        base = CrawlerConfig(
            concurrency=1,
            domain_min_delay=0.0,
            domain_max_delay=0.0,
        )
        session, _, mock_create, _ = _make_session(monkeypatch, base_config=base)

        session.start(
            urls=["https://example.com"],
            depth=2,
            concurrency=5,
            output_path="/tmp/test.jsonl",
            same_domain_only=True,
            cfg_overrides={"use_sitemap": True, "max_total_pages": 100},
        )

        passed_cfg = mock_create.call_args.kwargs["cfg"]
        assert passed_cfg.concurrency == 5
        assert passed_cfg.use_sitemap is True
        assert passed_cfg.max_total_pages == 100

    def test_start_without_overrides_param_no_third_merge(self, monkeypatch):
        session, _, mock_create, _ = _make_session(monkeypatch)

        session.start(
            urls=["https://example.com"],
            depth=1,
            concurrency=1,
            output_path="/tmp/test.jsonl",
            same_domain_only=False,
        )

        passed_cfg = mock_create.call_args.kwargs["cfg"]
        assert passed_cfg.concurrency == 1


# ═══════════════════════════════════════════════════════════════════════
# S22: proxy_health_snapshot 委托
# ═══════════════════════════════════════════════════════════════════════


# get_proxy_health_snapshot removed from CrawlSession (ADR-0010 Phase 3.5b)
# — health bar now holds ProxyHealthTracker reference from ProxySession construction, no polling needed.


# ═══════════════════════════════════════════════════════════════════════
# S23-S30: 信号完整性 — _wire_signals 转发
# ═══════════════════════════════════════════════════════════════════════


class TestSignalForwarding:
    def test_error_signal_forwarded(self, monkeypatch):
        session, _, _, _ = _make_session(monkeypatch)
        _start_session(session)
        collector = _SignalCollector(session.error_occurred)

        session._worker_signals.error.emit("test error")
        assert collector.count == 1
        assert collector.first_args() == ("test error",)

    def test_layer_progress_forwarded(self, monkeypatch):
        session, _, _, _ = _make_session(monkeypatch)
        _start_session(session)
        collector = _SignalCollector(session.layer_progress)

        session._worker_signals.layer_progress.emit(0, 5, 10)
        assert collector.count == 1
        assert collector.first_args() == (0, 5, 10)

    def test_stats_update_forwarded(self, monkeypatch):
        session, _, _, _ = _make_session(monkeypatch)
        _start_session(session)
        collector = _SignalCollector(session.stats_updated)

        session._worker_signals.stats_update.emit(5, 10, 100)
        assert collector.count == 1
        assert collector.first_args() == (5, 10, 100)

    def test_outcome_update_forwarded(self, monkeypatch):
        session, _, _, _ = _make_session(monkeypatch)
        _start_session(session)
        collector = _SignalCollector(session.outcome_updated)

        data = {"ok": 1}
        session._worker_signals.outcome_update.emit(data)
        assert collector.count == 1
        assert collector.first_args() == (data,)

    def test_finished_forwarded(self, monkeypatch):
        session, _, _, _ = _make_session(monkeypatch)
        _start_session(session)
        collector = _SignalCollector(session.finished)

        stats = {"ok": 5}
        session._worker_signals.finished.emit("/out.jsonl", stats)
        assert collector.count == 1
        assert collector.first_args() == ("/out.jsonl", stats)

    def test_pause_state_forwarded(self, monkeypatch):
        session, _, _, _ = _make_session(monkeypatch)
        _start_session(session)
        collector = _SignalCollector(session.pause_changed)

        session._worker_signals.pause_state.emit(True)
        assert collector.count == 1
        assert collector.first_args() == (True,)

    def test_worker_state_forwarded(self, monkeypatch):
        session, _, _, _ = _make_session(monkeypatch)
        _start_session(session)
        collector = _SignalCollector(session.worker_state_changed)

        session._worker_signals.worker_state.emit(0, "working")
        assert collector.count == 1
        assert collector.first_args() == (0, "working")


# ═══════════════════════════════════════════════════════════════════════
# S31-S34: 信号隔离 — disconnect 与 rule_match/rule_stats 审计
# ═══════════════════════════════════════════════════════════════════════


class TestSignalDisconnection:
    def test_no_signal_after_disconnect(self, monkeypatch):
        session, _, _, _ = _make_session(monkeypatch)
        _start_session(session)
        ws = session._worker_signals

        session.dispose()
        collector = _SignalCollector(session.message_logged)
        ws.error.emit("after disconnect")
        assert collector.count == 0

    def test_dispose_is_idempotent(self, monkeypatch):
        session, _, _, _ = _make_session(monkeypatch)
        _start_session(session)

        session.dispose()
        assert session._worker_signals is None
        session.dispose()

    def test_dispose_before_start_is_safe(self, monkeypatch):
        session, _, _, _ = _make_session(monkeypatch)
        session.dispose()

    def test_dispose_disconnects_worker_state(self, monkeypatch):
        """dispose 应断开所有转发信号，包括 worker_state。"""
        session, _, _, _ = _make_session(monkeypatch)
        _start_session(session)
        ws = session._worker_signals

        session.dispose()
        collector = _SignalCollector(session.worker_state_changed)
        ws.worker_state.emit(0, "working")
        assert collector.count == 0


class TestRuleMatchSignalsAudit:
    """rule_match / rule_stats 信号的可达性审计。

    S9 流水线已实现：_RealWorkerSignals (signals.py:59-60) 和 _StubSignals (signals.py:83-84)
    均包含 rule_matched / rule_stats_updated，_wire_signals() 可正常转发。
    """

    def test_rule_match_signal_exists_on_session(self):
        """CrawlSession 上 rule_matched Signal 存在且可连接。"""
        from astrocrawl.config import CrawlerConfig, GlobalSettings
        from astrocrawl.gui.crawl_session import CrawlSession

        cfg = CrawlerConfig(concurrency=1, domain_min_delay=0.0, domain_max_delay=0.0)
        session = CrawlSession(cfg, GlobalSettings())

        assert hasattr(session, "rule_matched")
        collector = _SignalCollector(session.rule_matched)
        session.rule_matched.emit("test_rule", {"info": 1})
        assert collector.count == 1

    def test_rule_stats_signal_exists_on_session(self):
        """CrawlSession 上 rule_stats_updated Signal 存在且可连接。"""
        from astrocrawl.config import CrawlerConfig, GlobalSettings
        from astrocrawl.gui.crawl_session import CrawlSession

        cfg = CrawlerConfig(concurrency=1, domain_min_delay=0.0, domain_max_delay=0.0)
        session = CrawlSession(cfg, GlobalSettings())

        assert hasattr(session, "rule_stats_updated")
        collector = _SignalCollector(session.rule_stats_updated)
        session.rule_stats_updated.emit({"count": 5})
        assert collector.count == 1

    def test_worker_signals_has_rule_match(self, monkeypatch):
        """_RealWorkerSignals 包含 rule_matched — S9 流水线已实现。"""
        session, _, _, _ = _make_session(monkeypatch)
        _start_session(session)

        assert hasattr(session._worker_signals, "rule_matched"), (
            "worker_signals 应包含 rule_matched 属性 — S9 信号流水线已实现"
        )

    def test_worker_signals_has_rule_stats(self, monkeypatch):
        """_RealWorkerSignals 包含 rule_stats_updated — S9 流水线已实现。"""
        session, _, _, _ = _make_session(monkeypatch)
        _start_session(session)

        assert hasattr(session._worker_signals, "rule_stats_updated"), (
            "worker_signals 应包含 rule_stats_updated 属性 — S9 信号流水线已实现"
        )


# ═══════════════════════════════════════════════════════════════════════
# T01-T10: CrawlerThread 线程安全控制
# ═══════════════════════════════════════════════════════════════════════


class TestCrawlerThreadControl:
    @staticmethod
    def _make_thread():
        from astrocrawl.gui.thread import CrawlerThread

        mock_crawler = MagicMock()
        mock_loop = MagicMock()
        mock_loop.is_running.return_value = True

        thread = CrawlerThread(mock_crawler)
        thread._loop = mock_loop
        return thread, mock_crawler, mock_loop

    def test_stop_calls_request_stop_threadsafe(self):
        thread, mock_crawler, mock_loop = self._make_thread()
        thread.stop()
        mock_loop.call_soon_threadsafe.assert_called_once_with(
            mock_crawler.request_stop,
        )

    def test_pause_calls_request_pause_threadsafe(self):
        thread, mock_crawler, mock_loop = self._make_thread()
        thread.pause()
        mock_loop.call_soon_threadsafe.assert_called_once_with(
            mock_crawler.request_pause,
        )

    def test_resume_calls_request_resume_threadsafe(self):
        thread, mock_crawler, mock_loop = self._make_thread()
        thread.resume()
        mock_loop.call_soon_threadsafe.assert_called_once_with(
            mock_crawler.request_resume,
        )

    def test_stop_noop_when_loop_none(self):
        from astrocrawl.gui.thread import CrawlerThread

        mock_crawler = MagicMock()
        thread = CrawlerThread(mock_crawler)
        thread._loop = None

        thread.stop()

    def test_stop_noop_when_loop_not_running(self):
        from astrocrawl.gui.thread import CrawlerThread

        mock_crawler = MagicMock()
        mock_loop = MagicMock()
        mock_loop.is_running.return_value = False

        thread = CrawlerThread(mock_crawler)
        thread._loop = mock_loop

        thread.stop()
        mock_loop.call_soon_threadsafe.assert_not_called()

    def test_pause_noop_when_loop_none(self):
        from astrocrawl.gui.thread import CrawlerThread

        mock_crawler = MagicMock()
        thread = CrawlerThread(mock_crawler)
        thread._loop = None

        thread.pause()

    def test_pause_noop_when_loop_not_running(self):
        from astrocrawl.gui.thread import CrawlerThread

        mock_crawler = MagicMock()
        mock_loop = MagicMock()
        mock_loop.is_running.return_value = False

        thread = CrawlerThread(mock_crawler)
        thread._loop = mock_loop

        thread.pause()
        mock_loop.call_soon_threadsafe.assert_not_called()

    def test_resume_noop_when_loop_none(self):
        from astrocrawl.gui.thread import CrawlerThread

        mock_crawler = MagicMock()
        thread = CrawlerThread(mock_crawler)
        thread._loop = None

        thread.resume()

    def test_resume_noop_when_loop_not_running(self):
        from astrocrawl.gui.thread import CrawlerThread

        mock_crawler = MagicMock()
        mock_loop = MagicMock()
        mock_loop.is_running.return_value = False

        thread = CrawlerThread(mock_crawler)
        thread._loop = mock_loop

        thread.resume()
        mock_loop.call_soon_threadsafe.assert_not_called()

    def test_crawler_property_returns_passed_crawler(self):
        from astrocrawl.gui.thread import CrawlerThread

        mock_crawler = MagicMock()
        thread = CrawlerThread(mock_crawler)
        assert thread.crawler is mock_crawler


# T11-T17: CrawlerThread run() 事件循环生命周期
# ═══════════════════════════════════════════════════════════════════════


class TestCrawlerThreadRun:
    """run() 直接调用测试（非 QThread.start()）——在测试线程中验证事件循环完整生命周期。"""

    @staticmethod
    def _make_async_run(behavior: str = "success"):
        """构造 AsyncCrawler mock，其 run() 为 AsyncMock（可控行为 + 可断言 call_count）。"""
        mock = MagicMock()

        if behavior == "success":
            mock.run = AsyncMock()
        elif behavior == "cancel":
            mock.run = AsyncMock(side_effect=asyncio.CancelledError())
        elif behavior == "error":
            mock.run = AsyncMock(side_effect=RuntimeError("test-error-from-crawler"))

        return mock

    def test_run_happy_path_creates_and_cleans_loop(self):
        from astrocrawl.gui.thread import CrawlerThread

        mock_crawler = self._make_async_run("success")
        thread = CrawlerThread(mock_crawler)

        thread.run()

        assert thread._loop is None
        assert mock_crawler.run.call_count == 1  # type: ignore[attr-defined]

    def test_run_sets_loop_during_execution(self):
        from astrocrawl.gui.thread import CrawlerThread

        captured_loop = []

        async def _capture_loop():
            captured_loop.append(asyncio.get_running_loop())

        mock = MagicMock()
        mock.run = _capture_loop
        thread = CrawlerThread(mock)

        thread.run()

        assert len(captured_loop) == 1
        assert captured_loop[0] is not None
        assert captured_loop[0].is_closed()

    def test_run_cancelled_error_graceful(self):
        from astrocrawl.gui.thread import CrawlerThread

        mock_crawler = self._make_async_run("cancel")
        thread = CrawlerThread(mock_crawler)

        thread.run()

        assert thread._loop is None

    def test_run_exception_emits_signal(self):
        from astrocrawl.gui.thread import CrawlerThread

        mock_crawler = self._make_async_run("error")
        mock_signals = MagicMock()
        mock_crawler.signals = mock_signals
        thread = CrawlerThread(mock_crawler)

        thread.run()

        mock_signals.error.emit.assert_called_once()
        args = mock_signals.error.emit.call_args[0]
        assert "test-error-from-crawler" in str(args[0])

    def test_run_exception_no_signal_when_signals_none(self):
        from astrocrawl.gui.thread import CrawlerThread

        mock_crawler = self._make_async_run("error")
        mock_crawler.signals = None
        thread = CrawlerThread(mock_crawler)

        thread.run()

        assert thread._loop is None

    def test_run_cleanup_handles_pending_tasks(self):
        from astrocrawl.gui.thread import CrawlerThread

        async def _with_pending():
            loop = asyncio.get_running_loop()
            loop.create_task(asyncio.sleep(3600))

        mock = MagicMock()
        mock.run = _with_pending
        mock.signals = None
        thread = CrawlerThread(mock)

        thread.run()

        assert thread._loop is None

    def test_run_clears_loop_on_finally(self):
        from astrocrawl.gui.thread import CrawlerThread

        mock_crawler = self._make_async_run("cancel")
        thread = CrawlerThread(mock_crawler)

        assert thread._loop is None
        thread.run()
        assert thread._loop is None

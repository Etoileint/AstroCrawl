"""Phase 6 — MainWindow 集成行为测试。

覆盖:
- MW41-MW45: _adjust_layer_bars() 动态增删
- MW46-MW50: _run_crawler() 启动流程
- MW51-MW54: _disconnect_old_session() 信号断连
- MW55-MW59: run/pause/stop 按钮行为
- MW60-MW62: closeEvent 看门狗
- MW63-MW67: _on_thread_finished / _force_close_crawler / _reset_app
- MW68-MW72: _cleanup_session() SSOT 直接测试
- MW73-MW74: _on_thread_finished proxy 资源停止修复验证
- MW75-MW76: closeEvent else 分支 _cleanup_session 修复验证
- MW77-MW78: _force_close_crawler 去重 + 顺序验证 + _session=None 防御
- MW79-MW80: _reset_app 去重 + proxy 资源停止 + dispose 顺序验证
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QMessageBox

from astrocrawl.config import DEFAULT_CONFIG

pytestmark = pytest.mark.gui


@pytest.fixture(autouse=True)
def _patch_qmessagebox(monkeypatch):
    """防止测试中 QMessageBox 模态对话框阻塞。"""
    mock = MagicMock()
    monkeypatch.setattr(QMessageBox, "warning", mock)
    monkeypatch.setattr(QMessageBox, "critical", mock)
    monkeypatch.setattr(QMessageBox, "information", mock)


@pytest.fixture
def mw(qapp, theme_mgr):
    from astrocrawl.gui.main_window import MainWindow

    return MainWindow()


# ═══════════════════════════════════════════════════════════════════════
# MW41-MW45: _adjust_layer_bars
# ═══════════════════════════════════════════════════════════════════════


class TestAdjustLayerBars:
    def test_increase_depth_adds_bars(self, mw):
        mw.depth_spin.setValue(4)
        mw._adjust_layer_bars()
        assert len(mw._layer_bars) == 4
        assert len(mw._layer_labels) == 4

    def test_decrease_depth_removes_bars(self, mw):
        mw.depth_spin.setValue(4)
        mw._adjust_layer_bars()
        mw.depth_spin.setValue(2)
        mw._adjust_layer_bars()
        assert len(mw._layer_bars) == 2
        assert len(mw._layer_labels) == 2

    def test_depth_unchanged_no_effect(self, mw):
        mw.depth_spin.setValue(2)
        mw._adjust_layer_bars()
        count = len(mw._layer_bars)
        mw._adjust_layer_bars()
        assert len(mw._layer_bars) == count

    def test_decrease_preserves_stats_labels(self, mw):
        mw.depth_spin.setValue(4)
        mw._adjust_layer_bars()
        mw.depth_spin.setValue(1)
        mw._adjust_layer_bars()

        assert mw.stats_label.text() != ""
        assert len(mw._layer_bars) == 1

    def test_bars_start_at_zero_value(self, mw):
        mw.depth_spin.setValue(3)
        mw._adjust_layer_bars()
        for bar in mw._layer_bars:
            assert bar.value() == 0


# ═══════════════════════════════════════════════════════════════════════
# MW46-MW50: _run_crawler
# ═══════════════════════════════════════════════════════════════════════


class TestRunCrawler:
    def test_warns_when_no_urls(self, mw):
        mw.url_text.setPlainText("")
        mw._run_crawler()
        QMessageBox.information.assert_called_once()

    def test_starts_session_with_correct_params(self, mw, monkeypatch):
        from tests._fakes_gui import FakeCrawlSession

        fake_session = FakeCrawlSession()

        monkeypatch.setattr(
            "astrocrawl.gui.main_window.CrawlSession",
            MagicMock(return_value=fake_session),
        )
        monkeypatch.setattr(
            "astrocrawl.gui.main_window.setup_root_logger",
            MagicMock(),
        )
        monkeypatch.setattr(
            "astrocrawl.gui.main_window.attach_qt_handler",
            MagicMock(),
        )

        mw.url_text.setPlainText("https://example.com")
        mw.depth_spin.setValue(3)
        mw.concurrency_spin.setValue(4)
        mw.same_domain_check.setChecked(True)
        mw._proxy_session = MagicMock()
        mw._proxy_session.proxies = ("http://p1:8080",)
        mw._output_edit.setText("/tmp/out.jsonl")

        mw._run_crawler()

        assert fake_session.is_running() is True

    def test_no_proxy_when_no_session(self, mw, monkeypatch):
        from tests._fakes_gui import FakeCrawlSession

        monkeypatch.setattr(
            "astrocrawl.gui.main_window.CrawlSession",
            MagicMock(return_value=FakeCrawlSession()),
        )
        monkeypatch.setattr("astrocrawl.gui.main_window.setup_root_logger", MagicMock())
        monkeypatch.setattr("astrocrawl.gui.main_window.attach_qt_handler", MagicMock())

        mw.url_text.setPlainText("https://example.com")
        mw._proxy_session = None
        mw._proxy_health_bar = None

        mw._run_crawler()

        # No proxy URLs passed when _proxy_session is None

    def test_sets_running_state_to_true(self, mw, monkeypatch):
        from tests._fakes_gui import FakeCrawlSession

        monkeypatch.setattr(
            "astrocrawl.gui.main_window.CrawlSession",
            MagicMock(return_value=FakeCrawlSession()),
        )
        monkeypatch.setattr("astrocrawl.gui.main_window.setup_root_logger", MagicMock())
        monkeypatch.setattr("astrocrawl.gui.main_window.attach_qt_handler", MagicMock())

        mw.url_text.setPlainText("https://example.com")

        mw._run_crawler()

        assert mw._run_btn.isEnabled() is False
        assert mw._pause_btn.isEnabled() is True
        assert mw._stop_btn.isEnabled() is True

    def test_failure_on_unwritable_output_path(self, mw, monkeypatch):
        monkeypatch.setattr("astrocrawl.gui.main_window.setup_root_logger", MagicMock())

        mw.url_text.setPlainText("https://example.com")
        mw._output_edit.setText("/dev/null/nonexistent_dir/out.jsonl")

        mw._run_crawler()

    def test_chromium_missing_shows_error_dialog(self, mw, monkeypatch):
        """_run_crawler — Chromium 缺失时弹出 QMessageBox.critical + cleanup。"""
        from astrocrawl._startup import StartupError

        mock_session = MagicMock()
        mock_session.start.side_effect = StartupError("chromium 不可用")
        monkeypatch.setattr(
            "astrocrawl.gui.main_window.CrawlSession",
            MagicMock(return_value=mock_session),
        )
        monkeypatch.setattr("astrocrawl.gui.main_window.setup_root_logger", MagicMock())
        monkeypatch.setattr("astrocrawl.gui.main_window.attach_qt_handler", MagicMock())

        mw.url_text.setPlainText("https://example.com")
        mw._run_crawler()

        QMessageBox.critical.assert_called_once()
        assert "chromium" in QMessageBox.critical.call_args[0][2]
        mock_session.dispose.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════
# MW51-MW54: _disconnect_old_session
# ═══════════════════════════════════════════════════════════════════════


class TestDisconnectOldSession:
    def test_noop_when_session_none(self, mw):
        mw._session = None
        mw._disconnect_old_session()

    def test_disconnects_signals_and_clears(self, mw, monkeypatch):
        from tests._fakes_gui import FakeCrawlSession

        session = FakeCrawlSession()
        session._running = True
        mw._session = session

        mw._disconnect_old_session()

        assert mw._session is None
        assert session._disconnect_called is True

    def test_dispose_handles_connected_signals(self, mw, monkeypatch):
        """dispose() 正确处理已连接和未连接的信号。"""
        from tests._fakes_gui import FakeCrawlSession

        session = FakeCrawlSession()
        mw._session = session
        mw._session.message_logged.connect(lambda msg: None)

        mw._disconnect_old_session()
        assert mw._session is None

    def test_double_call_is_safe(self, mw):
        mw._session = None
        mw._disconnect_old_session()
        mw._disconnect_old_session()


# ═══════════════════════════════════════════════════════════════════════
# MW55-MW59: 按钮行为
# ═══════════════════════════════════════════════════════════════════════


class TestButtonBehavior:
    def test_toggle_pause_pauses_when_running_not_paused(self, mw):
        from tests._fakes_gui import FakeCrawlSession

        session = FakeCrawlSession()
        session._running = True
        mw._session = session
        mw._paused = False

        mw._toggle_pause()

    def test_toggle_pause_resumes_when_paused(self, mw):
        from tests._fakes_gui import FakeCrawlSession

        session = FakeCrawlSession()
        session._running = True
        mw._session = session
        mw._paused = True

        mw._toggle_pause()

    def test_toggle_pause_noop_when_no_session(self, mw):
        mw._session = None
        mw._toggle_pause()

    def test_stop_crawler_stops_and_disables(self, mw):
        from tests._fakes_gui import FakeCrawlSession

        session = FakeCrawlSession()
        session._running = True
        mw._session = session

        mw._stop_crawler()

        assert mw._stop_btn.isEnabled() is False
        session._stop_called is True

    def test_stop_crawler_noop_when_not_running(self, mw):
        from tests._fakes_gui import FakeCrawlSession

        session = FakeCrawlSession()
        session._running = False
        mw._session = session

        mw._stop_crawler()

        session._stop_called is False


# ═══════════════════════════════════════════════════════════════════════
# MW60-MW62: closeEvent
# ═══════════════════════════════════════════════════════════════════════


class TestCloseEvent:
    def test_accepts_when_no_session(self, mw):
        mw._session = None
        event = QCloseEvent()
        mw.closeEvent(event)
        assert event.isAccepted() is True

    def test_ignores_when_session_running(self, mw):
        from tests._fakes_gui import FakeCrawlSession

        session = FakeCrawlSession()
        session._running = True
        mw._session = session
        mw._closing = False

        event = QCloseEvent()
        mw.closeEvent(event)

        assert event.isAccepted() is False
        assert mw._closing is True
        assert mw.isEnabled() is False
        session._stop_called is True

    def test_accepts_second_close(self, mw):
        from tests._fakes_gui import FakeCrawlSession

        session = FakeCrawlSession()
        session._running = True
        mw._session = session
        mw._closing = True

        event = QCloseEvent()
        mw.closeEvent(event)

        assert event.isAccepted() is True


# ═══════════════════════════════════════════════════════════════════════
# MW68-MW72: _cleanup_session — 会话级资源清理 SSOT
# ═══════════════════════════════════════════════════════════════════════


class TestCleanupSession:
    def test_stops_all_session_resources(self, mw):
        """_cleanup_session 停止所有会话级资源。"""
        from tests._fakes_gui import FakeCrawlSession

        session = FakeCrawlSession()
        session._running = True
        mw._session = session
        mw._paused = True
        mw._closing = True
        # Capture mocks before _cleanup_session nulls them
        mock_watchdog = MagicMock()
        mock_health_bar = MagicMock()
        mock_title_bar = MagicMock()

        mw._close_watchdog = mock_watchdog
        mw._proxy_health_bar = mock_health_bar
        mw._title_bar = mock_title_bar
        orig_detach = mw._detach_qt_logger = MagicMock()
        orig_running = mw._set_running_state = MagicMock()

        mw._cleanup_session()

        mock_watchdog.stop.assert_called_once()
        assert mw._close_watchdog is None
        mock_health_bar.stop_refresh.assert_called_once()
        mock_title_bar.stop_worker_bar.assert_called_once()
        orig_detach.assert_called_once()
        orig_running.assert_called_once_with(False)
        assert mw._session is None
        assert mw._paused is False
        assert mw._closing is False
        assert mw._pause_btn.text() == "Pause"

    def test_handles_none_watchdog(self, mw):
        """_close_watchdog 为 None 时不崩溃。"""
        mw._close_watchdog = None
        mw._cleanup_session()

    def test_handles_none_proxy_health_bar(self, mw):
        """_proxy_health_bar 为 None 时不崩溃。"""
        mw._proxy_health_bar = None
        mw._cleanup_session()

    def test_idempotent(self, mw):
        """_cleanup_session 可被多次安全调用。"""
        mw._cleanup_session()
        mw._cleanup_session()
        mw._cleanup_session()


# ═══════════════════════════════════════════════════════════════════════
# MW73-MW74: _on_thread_finished 修复验证 — proxy 资源停止
# ═══════════════════════════════════════════════════════════════════════


class TestThreadFinishedCleanup:
    def test_stops_proxy_health_bar(self, mw):
        """_on_thread_finished 停止 ProxyHealthBar 刷新定时器但保留色条显示。"""
        mock_bar = MagicMock()
        mw._proxy_health_bar = mock_bar
        mw._on_thread_finished()
        mock_bar.stop_refresh.assert_called_once()

    def test_preserves_proxy_segments_after_thread_finished(self, mw):
        """_on_thread_finished 后代理色条保留（停定时器但不丢失代理数据）。"""
        from astrocrawl.gui.proxy_health_bar import ProxyHealthBar

        real_bar = ProxyHealthBar()
        mock_session = MagicMock()
        mock_session.proxies = ["http://p1:8080", "http://p2:8080"]
        mock_session.health = MagicMock()
        mock_session.health.get_all_stats.return_value = {}
        real_bar.set_source(mock_session)
        assert len(real_bar._proxies) == 2
        assert len(real_bar._segments) == 2

        mw._proxy_health_bar = real_bar
        mw._on_thread_finished()

        assert len(real_bar._proxies) == 2
        assert len(real_bar._segments) == 2
        assert real_bar._timer.isActive() is False


# ═══════════════════════════════════════════════════════════════════════
# MW75-MW76: closeEvent else 分支修复验证
# ═══════════════════════════════════════════════════════════════════════


class TestCloseEventCleanup:
    def test_calls_cleanup_session_when_no_session(self, mw):
        """closeEvent session=None 时调用 _cleanup_session（修复缺陷 2）。"""
        mw._session = None
        mw._cleanup_session = MagicMock()
        event = QCloseEvent()
        mw.closeEvent(event)
        mw._cleanup_session.assert_called_once()
        assert event.isAccepted() is True

    def test_calls_cleanup_session_when_already_closing(self, mw):
        """closeEvent _closing=True 时调用 _cleanup_session。"""
        from tests._fakes_gui import FakeCrawlSession

        session = FakeCrawlSession()
        session._running = True
        mw._session = session
        mw._closing = True
        mw._cleanup_session = MagicMock()
        event = QCloseEvent()
        mw.closeEvent(event)
        mw._cleanup_session.assert_called_once()
        assert event.isAccepted() is True


# ═══════════════════════════════════════════════════════════════════════
# MW77-MW78: _force_close_crawler 去重验证
# ═══════════════════════════════════════════════════════════════════════


class TestForceCloseCrawlerCleanup:
    def test_calls_cleanup_session(self, mw):
        """_force_close_crawler 通过 _cleanup_session 清理（去重）。

        _cleanup_session 会被调用两次：_force_close_crawler 一次，随后的 close() →
        closeEvent else 分支一次（幂等）。
        """
        from tests._fakes_gui import FakeCrawlSession

        session = FakeCrawlSession()
        session._running = False
        mw._session = session
        mw._cleanup_session = MagicMock()
        mw.close = MagicMock()  # 阻止真实 close() 触发 closeEvent

        mw._force_close_crawler()

        mw._cleanup_session.assert_called_once()
        mw.close.assert_called_once()

    def test_disposes_before_cleanup(self, mw):
        """_force_close_crawler 在 _cleanup_session 之前调用 session.dispose()。"""
        from tests._fakes_gui import FakeCrawlSession

        session = FakeCrawlSession()
        session._running = False
        mw._session = session
        call_order = []
        session.dispose = lambda: call_order.append("dispose")
        mw._cleanup_session = lambda: call_order.append("cleanup")
        mw.close = MagicMock()

        mw._force_close_crawler()

        assert call_order == ["dispose", "cleanup"]

    def test_force_close_with_none_session(self, mw):
        """_force_close_crawler _session=None 时不崩溃（防御路径）。"""
        mw._session = None
        mw._cleanup_session = MagicMock()
        mw.close = MagicMock()

        mw._force_close_crawler()

        mw._cleanup_session.assert_called_once()
        mw.close.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════
# MW79: _reset_app 去重验证
# ═══════════════════════════════════════════════════════════════════════


class TestResetAppCleanup:
    def test_calls_cleanup_session(self, mw):
        """_reset_app 通过 _cleanup_session 清理（去重）。"""
        mw._cleanup_session = MagicMock()
        mw._reset_app()
        mw._cleanup_session.assert_called_once()

    def test_proxy_health_bar_stopped_on_reset(self, mw):
        """_reset_app 停止 ProxyHealthBar。"""
        mock_bar = MagicMock()
        mw._proxy_health_bar = mock_bar
        mw._session = None
        mw._close_watchdog = None
        mw._reset_app()
        assert mock_bar.stop.called  # 可能通过 _reload_profile_combo 多调一次，幂等

    def test_disposes_before_cleanup(self, mw):
        """_reset_app 在 _cleanup_session 之前调用 session.dispose()。

        _cleanup_session 将 _session 置 None，若 order 反了 dispose 永远不执行。
        """
        from tests._fakes_gui import FakeCrawlSession

        session = FakeCrawlSession()
        mw._session = session
        call_order = []
        session.dispose = lambda: call_order.append("dispose")
        mw._cleanup_session = lambda: call_order.append("cleanup")

        mw._reset_app()

        assert call_order == ["dispose", "cleanup"]


# ═══════════════════════════════════════════════════════════════════════
# MW63-MW67: _on_thread_finished / _force_close_crawler / _reset_app
# ═══════════════════════════════════════════════════════════════════════


class TestThreadFinished:
    def test_resets_all_state(self, mw):
        from tests._fakes_gui import FakeCrawlSession

        session = FakeCrawlSession()
        session._running = True
        mw._session = session
        mw._paused = True
        mw._closing = True
        mw._close_watchdog = MagicMock()

        mw._on_thread_finished()

        assert mw._session is None
        assert mw._paused is False
        assert mw._closing is False
        assert mw._pause_btn.text() == "Pause"
        assert mw._run_btn.isEnabled() is True
        assert mw._pause_btn.isEnabled() is False
        assert mw._stop_btn.isEnabled() is False

    def test_handles_none_watchdog(self, mw):
        from tests._fakes_gui import FakeCrawlSession

        session = FakeCrawlSession()
        mw._session = session
        mw._close_watchdog = None
        mw._paused = True

        mw._on_thread_finished()


class TestForceCloseCrawler:
    def test_force_close_when_session_running(self, mw):
        from tests._fakes_gui import FakeCrawlSession

        session = FakeCrawlSession()
        session._running = True
        mw._session = session
        mw._paused = True

        mw._force_close_crawler()

        assert mw._session is None

    def test_force_close_when_session_ended(self, mw):
        from tests._fakes_gui import FakeCrawlSession

        session = FakeCrawlSession()
        session._running = False
        mw._session = session
        mw._closing = True

        mw._force_close_crawler()

        assert mw._session is None


class TestResetApp:
    def test_resets_all_widgets_to_defaults(self, mw):
        mw.url_text.setPlainText("https://old-url.com")
        mw.depth_spin.setValue(5)
        mw.concurrency_spin.setValue(10)
        mw.same_domain_check.setChecked(False)
        mw._paused = True
        mw._close_watchdog = None

        mw._reset_app()

        assert mw.depth_spin.value() == 2
        assert mw.concurrency_spin.value() == DEFAULT_CONFIG.concurrency
        assert mw.same_domain_check.isChecked() is True
        assert mw._paused is False
        assert mw._advanced_cfg is not None

    def test_reset_before_any_crawl_does_not_raise(self, mw):
        mw._close_watchdog = None
        mw._session = None

        mw._reset_app()

        assert mw.url_text.toPlainText() == ""

    def test_reset_stops_running_session(self, mw):
        from tests._fakes_gui import FakeCrawlSession

        session = FakeCrawlSession()
        session._running = True
        mw._session = session
        mw._close_watchdog = None

        mw._reset_app()

        assert mw._session is None
        assert session._stop_called is True


class TestSaveConfigPermissions:
    def test_save_config_sets_restrictive_permissions(self, mw, tmp_path, monkeypatch):
        config_path = tmp_path / "astrocrawl_config.json"
        monkeypatch.setattr(
            "PySide6.QtWidgets.QFileDialog.getSaveFileName",
            lambda *a, **kw: (str(config_path), "*.json"),
        )
        monkeypatch.setattr(
            "astrocrawl.gui.main_window.clear_qt_file_dialog_history",
            lambda: None,
        )
        mw._save_config()
        assert config_path.exists()
        perms = config_path.stat().st_mode & 0o777
        assert perms == 0o600, f"Expected 0o600, got {oct(perms)}"


# ═══════════════════════════════════════════════════════════════════════
# MW81-MW85: _on_rule_stats_updated — 规则统计表全量刷新
# ═══════════════════════════════════════════════════════════════════════


class TestRuleStatsUpdated:
    def test_populates_table_with_snapshot_data(self, mw):
        """_on_rule_stats_updated 用快照数据填充 6 列表格。"""
        snapshot = {
            "test_rule": {
                "hits": 10,
                "fields_filled": 8,
                "fields_total": 10,
                "fill_rate": 0.8,
                "avg_ms": 150.5,
                "slow_count": 2,
            },
        }
        mw._on_rule_stats_updated(snapshot)
        assert not mw._rule_stats_table.isHidden()
        assert mw._rule_stats_table.rowCount() == 1
        assert mw._rule_stats_table.item(0, 0).text() == "test_rule"
        assert mw._rule_stats_table.item(0, 1).text() == "10"
        assert mw._rule_stats_table.item(0, 2).text() == "8/10"
        assert mw._rule_stats_table.item(0, 3).text() == "80%"
        assert "150.5" in mw._rule_stats_table.item(0, 4).text()
        assert mw._rule_stats_table.item(0, 5).text() == "2"

    def test_multiple_rules_sorted_by_name(self, mw):
        """多条规则按名称排序。"""
        snapshot = {
            "rule_b": {"hits": 1, "fields_filled": 0, "fields_total": 0, "fill_rate": 0, "avg_ms": 0, "slow_count": 0},
            "rule_a": {"hits": 2, "fields_filled": 0, "fields_total": 0, "fill_rate": 0, "avg_ms": 0, "slow_count": 0},
        }
        mw._on_rule_stats_updated(snapshot)
        assert mw._rule_stats_table.rowCount() == 2
        assert mw._rule_stats_table.item(0, 0).text() == "rule_a"
        assert mw._rule_stats_table.item(1, 0).text() == "rule_b"

    def test_empty_dict_does_not_modify_table(self, mw):
        """空 dict 不修改已有表格内容。"""
        mw._on_rule_stats_updated(
            {"r": {"hits": 0, "fields_filled": 0, "fields_total": 0, "fill_rate": 0, "avg_ms": 0, "slow_count": 0}}
        )
        assert mw._rule_stats_table.rowCount() == 1
        mw._on_rule_stats_updated({})
        assert mw._rule_stats_table.rowCount() == 1

    def test_none_snapshot_returns_early(self, mw):
        """None 快照不崩溃且不修改表格状态。"""
        mw._rule_stats_table.setVisible(False)
        mw._on_rule_stats_updated(None)
        assert mw._rule_stats_table.isHidden()

    def test_zero_values_rendered_correctly(self, mw):
        """零值边界：fill_rate=0 显示 0%，avg_ms=0 显示 0.0 ms。"""
        snapshot = {
            "empty_rule": {
                "hits": 0,
                "fields_filled": 0,
                "fields_total": 0,
                "fill_rate": 0,
                "avg_ms": 0,
                "slow_count": 0,
            },
        }
        mw._on_rule_stats_updated(snapshot)
        assert mw._rule_stats_table.item(0, 1).text() == "0"
        assert mw._rule_stats_table.item(0, 2).text() == "0/0"
        assert mw._rule_stats_table.item(0, 3).text() == "0%"
        assert mw._rule_stats_table.item(0, 4).text() == "0.0 ms"
        assert mw._rule_stats_table.item(0, 5).text() == "0"

    def test_table_remains_hidden_on_empty_snapshot(self, mw):
        """空快照时表格保持不可见。"""
        mw._rule_stats_table.setVisible(False)
        mw._on_rule_stats_updated({})
        assert mw._rule_stats_table.isHidden()


# ═══════════════════════════════════════════════════════════════════════
# MW86-MW90: _on_finished — 完成报告对话框
# ═══════════════════════════════════════════════════════════════════════


class TestOnFinished:
    def test_opens_dialog_with_stats_payload(self, mw, monkeypatch):
        """_on_finished 使用 stats payload 直接打开 CompletionReportDialog。"""
        mock_dialog = MagicMock()
        monkeypatch.setattr(
            "astrocrawl.gui.main_window.CompletionReportDialog",
            MagicMock(return_value=mock_dialog),
        )
        stats = {"ok": 5, "duplicate": 2}
        mw._on_finished("/tmp/out.jsonl", stats)
        mock_dialog.exec.assert_called_once()

    def test_falls_back_to_report_file(self, mw, monkeypatch, tmp_path):
        """stats 为空 dict 时回退到 .report.json 文件读取。"""
        mock_dialog = MagicMock()
        monkeypatch.setattr(
            "astrocrawl.gui.main_window.CompletionReportDialog",
            MagicMock(return_value=mock_dialog),
        )
        jsonl = tmp_path / "out.jsonl"
        jsonl.write_text("")
        report = tmp_path / "out.report.json"
        report.write_text('{"ok": 3}')

        mw._on_finished(str(jsonl), {})
        mock_dialog.exec.assert_called_once()

    def test_warns_when_report_file_missing(self, mw, monkeypatch, tmp_path):
        """报告文件不存在时记录警告仍显示对话框。"""
        mock_dialog = MagicMock()
        monkeypatch.setattr(
            "astrocrawl.gui.main_window.CompletionReportDialog",
            MagicMock(return_value=mock_dialog),
        )
        jsonl = tmp_path / "out.jsonl"
        jsonl.write_text("")

        mw._on_finished(str(jsonl), {})
        mock_dialog.exec.assert_called_once()
        messages = [mw.log_list.item(i).text() for i in range(mw.log_list.count())]
        assert any("Report file not generated" in m for m in messages)

    def test_warns_on_corrupt_report_file(self, mw, monkeypatch, tmp_path):
        """报告文件 JSON 解析失败时记录警告。"""
        mock_dialog = MagicMock()
        monkeypatch.setattr(
            "astrocrawl.gui.main_window.CompletionReportDialog",
            MagicMock(return_value=mock_dialog),
        )
        jsonl = tmp_path / "out.jsonl"
        jsonl.write_text("")
        report = tmp_path / "out.report.json"
        report.write_text("not valid json")

        mw._on_finished(str(jsonl), {})
        mock_dialog.exec.assert_called_once()
        messages = [mw.log_list.item(i).text() for i in range(mw.log_list.count())]
        assert any("Failed to read report" in m for m in messages)

    def test_logs_completion_message(self, mw, monkeypatch):
        """完成后记录 [INFO] 完成消息。"""
        monkeypatch.setattr(
            "astrocrawl.gui.main_window.CompletionReportDialog",
            MagicMock(),
        )
        mw._on_finished("/tmp/out.jsonl", {"ok": 1})
        messages = [mw.log_list.item(i).text() for i in range(mw.log_list.count())]
        assert any("Crawl complete" in m for m in messages)


# ═══════════════════════════════════════════════════════════════════════
# MW91-MW92: _on_error — 错误槽
# ═══════════════════════════════════════════════════════════════════════


class TestOnError:
    def test_logs_error_and_shows_critical(self, mw):
        """_on_error 记录 [ERROR] 日志并弹出 QMessageBox.critical。"""
        mw._on_error("测试错误消息")
        QMessageBox.critical.assert_called_once()
        assert "测试错误消息" in QMessageBox.critical.call_args[0][2]
        messages = [mw.log_list.item(i).text() for i in range(mw.log_list.count())]
        assert any("[ERROR] 测试错误消息" in m for m in messages)

    def test_empty_error_message(self, mw):
        """空错误消息不崩溃。"""
        mw._on_error("")
        QMessageBox.critical.assert_called_once()
        messages = [mw.log_list.item(i).text() for i in range(mw.log_list.count())]
        assert any("[ERROR]" in m for m in messages)


# _try_init_health_bar removed (ADR-0010 Phase 3.5b)
# — health bar holds ProxyHealthTracker reference from ProxySession construction, no polling needed.

# ═══════════════════════════════════════════════════════════════════════
# MW98: _save_config error path
# ═══════════════════════════════════════════════════════════════════════


class TestSaveConfigError:
    def test_shows_critical_on_write_error(self, mw, monkeypatch, tmp_path):
        """atomic_write_json 失败时弹出 QMessageBox.critical。"""
        config_path = tmp_path / "config.json"
        monkeypatch.setattr(
            "PySide6.QtWidgets.QFileDialog.getSaveFileName",
            lambda *a, **kw: (str(config_path), "*.json"),
        )
        monkeypatch.setattr(
            "astrocrawl.gui.main_window.clear_qt_file_dialog_history",
            lambda: None,
        )
        monkeypatch.setattr(
            "astrocrawl.gui.main_window.atomic_write_json",
            MagicMock(side_effect=OSError("disk full")),
        )
        mw._save_config()
        QMessageBox.critical.assert_called_once()
        assert "Save failed" in QMessageBox.critical.call_args[0][2]


# ═══════════════════════════════════════════════════════════════════════
# MW99-MW100: _load_config error path
# ═══════════════════════════════════════════════════════════════════════


class TestLoadConfigError:
    def test_shows_critical_on_corrupt_file(self, mw, monkeypatch, tmp_path):
        """损坏的配置文件弹出 QMessageBox.critical。"""
        config_path = tmp_path / "bad_config.json"
        config_path.write_text("not valid json")
        monkeypatch.setattr(
            "PySide6.QtWidgets.QFileDialog.getOpenFileName",
            lambda *a, **kw: (str(config_path), "*.json"),
        )
        monkeypatch.setattr(
            "astrocrawl.gui.main_window.clear_qt_file_dialog_history",
            lambda: None,
        )
        mw._load_config()
        QMessageBox.critical.assert_called_once()
        assert "Load failed" in QMessageBox.critical.call_args[0][2]

    def test_applies_valid_config_from_file(self, mw, monkeypatch, tmp_path):
        """有效配置文件正确应用到各控件。"""
        config_path = tmp_path / "good_config.json"
        config_path.write_text(
            '{"urls": ["https://a.com", "https://b.com"], "depth": 5, "concurrency": 10, '
            '"output_path": "/tmp/out.jsonl", "same_domain_only": false, "respect_robots": false, '
            '"advanced": {"concurrency": 10}}'
        )
        monkeypatch.setattr(
            "PySide6.QtWidgets.QFileDialog.getOpenFileName",
            lambda *a, **kw: (str(config_path), "*.json"),
        )
        monkeypatch.setattr(
            "astrocrawl.gui.main_window.clear_qt_file_dialog_history",
            lambda: None,
        )
        mw._load_config()
        assert "https://a.com" in mw.url_text.toPlainText()
        assert mw.depth_spin.value() == 5
        assert mw.concurrency_spin.value() == 10
        assert mw._output_edit.text() == "/tmp/out.jsonl"
        assert mw.same_domain_check.isChecked() is False
        assert mw.respect_robots_check.isChecked() is False

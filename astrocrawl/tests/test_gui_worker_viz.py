"""Phase 5 — Worker 可视化组件测试。

覆盖:
- WS01-WS13: WorkerStatusBar 状态 / 动画偏移 / 绘制路径
- PH01-PH09: ProxyHealthBar 色条构建 / 颜色刷新
- TB01-TB05: TitleBar 按钮符号 / 主题入口
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QMessageBox

pytestmark = pytest.mark.gui


@pytest.fixture(autouse=True)
def _patch_qmessagebox(monkeypatch):
    """防止 TitleBar._open_theme_dialog 中潜在的 QMessageBox 阻塞。"""
    mock = MagicMock()
    monkeypatch.setattr(QMessageBox, "warning", mock)
    monkeypatch.setattr(QMessageBox, "critical", mock)
    monkeypatch.setattr(QMessageBox, "information", mock)


# ═══════════════════════════════════════════════════════════════════════
# WS01-WS08: WorkerStatusBar
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def worker_bar(qapp, theme_mgr):
    from astrocrawl.gui.worker_status_bar import WorkerStatusBar

    return WorkerStatusBar()


class TestWorkerStatusBar:
    def test_initial_state(self, worker_bar):
        assert worker_bar._working == set()
        assert worker_bar._anim_offset == 0.0
        assert worker_bar._session is None

    def test_connect_session_stops_old_and_starts_timer(self, worker_bar):
        from tests._fakes_gui import FakeCrawlSession

        session = FakeCrawlSession()

        worker_bar.connect_session(session)

        assert worker_bar._session is session
        assert worker_bar._anim_timer.isActive() is True

    def test_on_worker_state_working_adds_to_set(self, worker_bar):
        worker_bar._on_worker_state(0, "working")
        assert 0 in worker_bar._working

    def test_on_worker_state_idle_removes_from_set(self, worker_bar):
        worker_bar._working = {0, 1}
        worker_bar._on_worker_state(0, "idle")
        assert 0 not in worker_bar._working
        assert 1 in worker_bar._working

    def test_on_worker_state_multi_worker(self, worker_bar):
        worker_bar._on_worker_state(0, "working")
        worker_bar._on_worker_state(1, "working")
        worker_bar._on_worker_state(0, "idle")

        assert worker_bar._working == {1}

    def test_tick_with_workers_increases_anim_offset(self, worker_bar):
        worker_bar._working = {0, 1}
        worker_bar._anim_offset = 0.0

        worker_bar._tick()

        assert worker_bar._anim_offset > 0.0
        expected_speed = 2 * 0.003
        assert worker_bar._anim_offset == pytest.approx(expected_speed % 1.0)

    def test_tick_without_workers_resets_offset(self, worker_bar):
        worker_bar._working = set()
        worker_bar._anim_offset = 0.5

        worker_bar._tick()

        assert worker_bar._anim_offset == 0.0

    def test_stop_clears_all_state(self, worker_bar):
        from tests._fakes_gui import FakeCrawlSession

        session = FakeCrawlSession()
        worker_bar.connect_session(session)
        worker_bar._working = {0, 1}
        worker_bar._anim_offset = 0.3

        worker_bar.stop()

        assert worker_bar._session is None
        assert worker_bar._working == set()
        assert worker_bar._anim_offset == 0.0
        assert worker_bar._anim_timer.isActive() is False

    def test_ws09_paint_disabled_prevents_paint(self, worker_bar):
        """paintEvent 在 _paint_disabled 为 True 时提前返回，不触发 _paint_bar。"""
        from unittest.mock import patch

        worker_bar._paint_disabled = True
        with patch.object(worker_bar, "_paint_bar") as mock_paint:
            worker_bar.repaint()
            mock_paint.assert_not_called()

    def test_ws10_stop_uses_repaint(self, worker_bar):
        """stop() 使用 repaint() 而非 update() 来同步绘制。"""
        from unittest.mock import patch

        worker_bar._anim_timer.start(worker_bar.PULSE_INTERVAL_MS)
        with patch.object(worker_bar, "repaint") as mock_repaint:
            worker_bar.stop()
            mock_repaint.assert_called_once()

    def test_paint_bar_no_session_transparent(self, worker_bar, theme_mgr):
        """_paint_bar 在 _session 为 None 时绘制半透明 disabled 色。"""
        from PySide6.QtGui import QPainter, QPixmap

        worker_bar._session = None
        pixmap = QPixmap(200, 24)
        painter = QPainter(pixmap)
        try:
            worker_bar._paint_bar(painter, 0.0, 200, 24)
        finally:
            painter.end()

    def test_paint_bar_idle_workers_solid_disabled(self, worker_bar, theme_mgr):
        """_paint_bar 在 _working 为空时绘制实色 disabled。"""
        from PySide6.QtGui import QPainter, QPixmap

        worker_bar._session = object()
        worker_bar._working = set()
        pixmap = QPixmap(200, 24)
        painter = QPainter(pixmap)
        try:
            worker_bar._paint_bar(painter, 0.0, 200, 24)
        finally:
            painter.end()

    def test_paint_bar_active_workers_gradient(self, worker_bar, theme_mgr):
        """_paint_bar 在 _working 非空时绘制 4-stop 双周期渐变。"""
        from PySide6.QtGui import QPainter, QPixmap

        worker_bar._session = object()
        worker_bar._working = {0, 1}
        pixmap = QPixmap(200, 24)
        painter = QPainter(pixmap)
        try:
            worker_bar._paint_bar(painter, 0.3, 200, 24)
        finally:
            painter.end()


# ═══════════════════════════════════════════════════════════════════════
# PH01-PH09: ProxyHealthBar
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def health_bar(qapp, theme_mgr):
    from astrocrawl.gui.proxy_health_bar import ProxyHealthBar

    return ProxyHealthBar()


def _mock_session(proxies, health=None):
    """Helper: 构造 mock ProxySession（含 proxies + health 属性）。"""
    s = MagicMock()
    s.proxies = proxies
    s.health = health or MagicMock()
    if health is None:
        s.health.get_all_stats.return_value = {}
    return s


class TestProxyHealthBar:
    def test_set_source_with_proxies_creates_segments(self, health_bar):
        mock_health = MagicMock()
        mock_health.get_all_stats.return_value = {}
        session = _mock_session(["http://p1:8080", "http://p2:8080", "http://p3:8080"], mock_health)

        health_bar.set_source(session)

        assert len(health_bar._segments) == 3
        assert health_bar._timer.isActive() is True

    def test_set_source_empty_creates_placeholder(self, health_bar):
        health_bar.set_source(_mock_session([]))

        assert len(health_bar._segments) == 1
        assert "No Proxy" in health_bar._segments[0].toolTip()
        assert health_bar._timer.isActive() is False

    def test_stop_resets_to_placeholder(self, health_bar):
        mock_health = MagicMock()
        mock_health.get_all_stats.return_value = {}
        health_bar.set_source(_mock_session(["http://p1:8080"], mock_health))

        health_bar.stop()

        assert len(health_bar._segments) == 1
        assert "No Proxy" in health_bar._segments[0].toolTip()
        assert health_bar._timer.isActive() is False

    def test_rebuild_replaces_old_segments(self, health_bar):
        mock_health = MagicMock()
        mock_health.get_all_stats.return_value = {}
        health_bar.set_source(_mock_session(["http://a:8080", "http://b:8080"], mock_health))
        old_segments = list(health_bar._segments)

        health_bar.set_source(_mock_session(["http://c:8080"], mock_health))

        assert len(health_bar._segments) == 1
        assert health_bar._segments[0] not in old_segments

    def test_refresh_updates_segment_colors(self, health_bar):
        mock_stats = MagicMock()
        mock_stats.health_score = 0.2
        mock_health = MagicMock()
        mock_health.get_all_stats.return_value = {"http://p1:8080": mock_stats}

        health_bar.set_source(_mock_session(["http://p1:8080"], mock_health))

        style = health_bar._segments[0].styleSheet()
        assert "background-color" in style

    def test_refresh_no_health_does_not_raise(self, health_bar):
        health_bar._proxies = ["http://p1:8080"]
        health_bar._health = None
        health_bar._segments = [MagicMock()]

        health_bar._refresh()

    def test_refresh_missing_proxy_uses_default_score(self, health_bar):
        mock_health = MagicMock()
        mock_health.get_all_stats.return_value = {}
        health_bar.set_source(_mock_session(["http://p1:8080"], mock_health))

        style = health_bar._segments[0].styleSheet()
        assert "background-color" in style

    def test_tooltip_includes_stats_fields(self, health_bar):
        from astrocrawl.proxy._proxy import CircuitState, ProxyStats

        stats = ProxyStats(
            consecutive_failures=2,
            total_failures=4,
            total_successes=10,
            state=CircuitState.CLOSED,
        )
        mock_health = MagicMock()
        mock_health.get_all_stats.return_value = {"http://p1:8080": stats}
        health_bar.set_source(_mock_session(["http://p1:8080"], mock_health))

        tip = health_bar._segments[0].toolTip()
        assert "Health Score:" in tip
        assert "State: closed" in tip
        assert "Consecutive Failures: 2" in tip
        assert "Successes: 10 / Failures: 4" in tip

    def test_stop_refresh_preserves_segments_and_stops_timer(self, health_bar):
        """stop_refresh 停定时器但保留色条和代理数据（停止爬取不应丢失代理池显示）。"""
        mock_health = MagicMock()
        mock_health.get_all_stats.return_value = {}
        health_bar.set_source(_mock_session(["http://p1:8080", "http://p2:8080"], mock_health))

        health_bar.stop_refresh()

        assert health_bar._timer.isActive() is False
        assert len(health_bar._proxies) == 2
        assert health_bar._health is mock_health
        assert len(health_bar._segments) == 2


# ═══════════════════════════════════════════════════════════════════════
# TB01-TB05: TitleBar
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def title_bar(qapp, theme_mgr):
    from astrocrawl.gui.title_bar import TitleBar

    return TitleBar()


class TestTitleBar:
    def test_update_button_light_mode(self, title_bar, theme_mgr):
        theme_mgr.apply("light", "light", {})
        title_bar._update_button()
        assert title_bar._theme_btn.text() == "☀"

    def test_update_button_dark_mode(self, title_bar, theme_mgr):
        theme_mgr.apply("dark", "dark", {})
        title_bar._update_button()
        assert title_bar._theme_btn.text() == "★"

    def test_update_button_custom_mode(self, title_bar, theme_mgr):
        theme_mgr.apply("custom", "light", {"accent": "#FF0000"})
        title_bar._update_button()
        assert title_bar._theme_btn.text() == "✿"

    def test_stop_worker_bar_delegates(self, title_bar):
        title_bar._worker_bar = MagicMock()
        title_bar.stop_worker_bar()
        title_bar._worker_bar.stop.assert_called_once()

    def test_open_theme_dialog(self, title_bar, monkeypatch, theme_mgr):
        theme_mgr.apply("dark", "dark", {})
        mock_dialog = MagicMock()
        monkeypatch.setattr(
            "astrocrawl.gui.theme_dialog.ThemeDialog",
            MagicMock(return_value=mock_dialog),
        )

        title_bar._open_theme_dialog()

        mock_dialog.exec.assert_called_once()
        assert title_bar._theme_btn.text() == "★"

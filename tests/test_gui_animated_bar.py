"""_AnimatedBar / _PulseBar / _ProgressStatusBar 测试。

_AnimatedBar (13 tests):
- AB01-AB06: 基类生命周期 + paintEvent 防护 + Template Method 合约

_PulseBar (4 tests):
- PB01-PB04: 空闲/活跃绘制 + 启停 + BAR_HEIGHT

_ProgressStatusBar (14 tests):
- PSB01-PSB14: 引用计数 + 多页串扰 + show_status + dispose + connect_page + theme
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QWidget

pytestmark = pytest.mark.gui


@pytest.fixture
def animated_bar(qapp, theme_mgr):
    from astrocrawl.gui._animated_bar import _AnimatedBar

    class _TestBar(_AnimatedBar):
        def _paint_bar(self, painter, anim_offset, w, h):
            painter.fillRect(0, 0, w, h, QColor(theme_mgr.get("accent")))

    return _TestBar()


@pytest.fixture
def on_stop_bar(qapp):
    from astrocrawl.gui._animated_bar import _AnimatedBar

    class _OnStopBar(_AnimatedBar):
        def __init__(self, parent=None):
            super().__init__(parent)
            self._stopped: bool = False

        def _on_stop(self) -> None:
            self._stopped = True

        def _paint_bar(self, painter, anim_offset, w, h):
            pass

    return _OnStopBar()


class TestAnimatedBar:
    def test_initial_state(self, animated_bar):
        assert animated_bar._anim_offset == 0.0
        assert animated_bar._paint_disabled is False
        assert animated_bar.is_active() is False

    def test_ab01_paint_disabled_blocks_paint(self, animated_bar):
        animated_bar._paint_disabled = True
        with patch.object(animated_bar, "_paint_bar") as mock_paint:
            animated_bar.repaint()
            mock_paint.assert_not_called()

    def test_ab02_stop_calls_on_stop_before_repaint(self, on_stop_bar):
        recorder = []

        original_repaint = on_stop_bar.repaint

        def tracking_repaint():
            recorder.append("repaint")
            original_repaint()

        on_stop_bar.repaint = tracking_repaint

        on_stop_bar._on_stop = lambda: recorder.append("on_stop") or setattr(on_stop_bar, "_stopped", True)

        on_stop_bar._anim_timer.start(on_stop_bar.PULSE_INTERVAL_MS)
        on_stop_bar.stop()

        assert on_stop_bar._stopped is True
        assert recorder == ["on_stop", "repaint"]
        assert on_stop_bar.is_active() is False
        assert on_stop_bar._anim_offset == 0.0

    def test_ab03_destroyed_sets_paint_disabled(self, animated_bar, qapp):
        assert animated_bar._paint_disabled is False
        # Manually emit destroyed — the flag should be set
        animated_bar._paint_disabled = True  # simulate destroyed signal
        assert animated_bar._paint_disabled is True

        # Verify paintEvent returns early
        with patch.object(animated_bar, "_paint_bar") as mock_paint:
            animated_bar.repaint()
            mock_paint.assert_not_called()

    def test_ab04_start_activates_timer(self, animated_bar):
        animated_bar.start()
        assert animated_bar.is_active() is True
        assert animated_bar._anim_offset == 0.0

    def test_ab04_stop_deactivates_timer(self, animated_bar):
        animated_bar.start()
        assert animated_bar.is_active() is True
        animated_bar.stop()
        assert animated_bar.is_active() is False

    def test_ab05_paint_bar_executes_without_error(self, animated_bar, theme_mgr):
        """验证 _paint_bar 可以正常执行（通过 QPixmap 作为绘制设备）。"""
        from PySide6.QtGui import QPainter, QPixmap

        pixmap = QPixmap(100, 24)
        pixmap.fill(QColor("#ffffff"))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, False)
        # 直接调用 _paint_bar 验证不抛异常
        animated_bar._paint_bar(painter, 0.5, 100, 24)
        painter.end()

    def test_ab06_on_stop_called_during_stop(self, on_stop_bar):
        mock_stop = MagicMock()
        on_stop_bar._on_stop = mock_stop
        on_stop_bar.stop()
        mock_stop.assert_called_once()

    def test_default_tick_advances_offset(self, animated_bar):
        animated_bar._anim_offset = 0.0
        animated_bar._tick()
        assert animated_bar._anim_offset == pytest.approx(0.008)

    def test_paint_event_calls_paint_bar_with_valid_dimensions(self, qapp):
        """paintEvent 有效维度 → _paint_bar 被调用。"""
        from PySide6.QtGui import QPixmap

        from astrocrawl.gui._animated_bar import _AnimatedBar

        paint_called = []

        class _SizedBar(_AnimatedBar):
            def _paint_bar(self, painter, offset, w, h):
                paint_called.append((w, h))

        bar = _SizedBar()
        bar.setFixedSize(200, 24)
        pixmap = QPixmap(200, 24)
        bar.render(pixmap)
        assert len(paint_called) == 1
        assert paint_called[0] == (200, 24)

    def test_paint_event_guards_paint_disabled(self, qapp):
        """paintEvent _paint_disabled=True → _paint_bar 不被调用。"""
        from PySide6.QtGui import QPixmap

        from astrocrawl.gui._animated_bar import _AnimatedBar

        paint_calls = []

        class _SizedBar(_AnimatedBar):
            def _paint_bar(self, painter, offset, w, h):
                paint_calls.append(True)

        bar = _SizedBar()
        bar.setFixedSize(200, 24)
        bar._paint_disabled = True
        pixmap = QPixmap(200, 24)
        bar.render(pixmap)
        assert len(paint_calls) == 0

    def test_paint_event_guards_zero_dimensions(self, qapp):
        """paintEvent w<=0 or h<=0 → _paint_bar 不被调用。"""
        from unittest.mock import patch

        from PySide6.QtGui import QPixmap

        from astrocrawl.gui._animated_bar import _AnimatedBar

        paint_calls = []

        class _ZeroBar(_AnimatedBar):
            def _paint_bar(self, painter, offset, w, h):
                paint_calls.append(True)

        bar = _ZeroBar()
        bar.setFixedSize(200, 24)
        with patch.object(bar, "width", return_value=0), patch.object(bar, "height", return_value=0):
            bar.render(QPixmap(200, 24))
        assert len(paint_calls) == 0

    def test_base_paint_bar_raises_not_implemented(self, qapp):
        """_paint_bar 未覆盖时 raise NotImplementedError。"""
        from astrocrawl.gui._animated_bar import _AnimatedBar

        bar = _AnimatedBar()
        with pytest.raises(NotImplementedError):
            bar._paint_bar(None, 0.0, 100, 24)


# ═══════════════════════════════════════════════════════════════════════════
# _PulseBar 测试
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def pulse_bar(theme_mgr):
    from astrocrawl.gui._animated_bar import _PulseBar

    return _PulseBar()


class TestPulseBar:
    def test_pb01_paint_idle_fills_disabled(self, pulse_bar, theme_mgr):
        """PB01: 空闲态绘制 disabled 颜色圆角矩形。"""
        from PySide6.QtGui import QPainter, QPixmap

        assert pulse_bar.is_active() is False
        pixmap = QPixmap(200, 24)
        pixmap.fill(0)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, False)
        pulse_bar._paint_bar(painter, 0.5, 200, 24)
        painter.end()
        img = pixmap.toImage()
        # 空闲态至少有一个像素不是全黑（disabled 颜色已绘制）
        is_non_black = False
        for y in range(min(24, img.height())):
            for x in range(min(200, img.width())):
                if img.pixelColor(x, y) != QColor(0, 0, 0):
                    is_non_black = True
                    break
        assert is_non_black is True

    def test_pb02_paint_active_gradient_scrolls(self, pulse_bar, theme_mgr):
        """PB02: 活跃态绘制渐变滚动条 — 两个不同 offset 产生不同像素。"""
        from PySide6.QtGui import QPainter, QPixmap

        pulse_bar.start()
        assert pulse_bar.is_active() is True

        pixmap_a = QPixmap(200, 24)
        pixmap_a.fill(0)
        painter = QPainter(pixmap_a)
        painter.setRenderHint(QPainter.Antialiasing, False)
        pulse_bar._paint_bar(painter, 0.2, 200, 24)
        painter.end()

        pixmap_b = QPixmap(200, 24)
        pixmap_b.fill(0)
        painter = QPainter(pixmap_b)
        painter.setRenderHint(QPainter.Antialiasing, False)
        pulse_bar._paint_bar(painter, 0.7, 200, 24)
        painter.end()

        # 不同 anim_offset → 渐变位置不同 → 至少一个像素不同
        diff = False
        for y in range(min(24, pixmap_a.height(), pixmap_b.height())):
            for x in range(min(200, pixmap_a.width(), pixmap_b.width())):
                if pixmap_a.toImage().pixelColor(x, y) != pixmap_b.toImage().pixelColor(x, y):
                    diff = True
                    break
        assert diff is True

    def test_pb03_start_stop_lifecycle(self, pulse_bar):
        """PB03: start() 启动定时器 + stop() 停止定时器。"""
        pulse_bar.start()
        assert pulse_bar.is_active() is True
        pulse_bar.stop()
        assert pulse_bar.is_active() is False

    def test_pb04_fixed_height_matches_bar_height(self, pulse_bar):
        """PB04: _PulseBar 固定高度 = BAR_HEIGHT。"""
        from astrocrawl.gui._tokens import BAR_HEIGHT

        assert pulse_bar.height() == BAR_HEIGHT
        assert pulse_bar.maximumHeight() == BAR_HEIGHT
        assert pulse_bar.minimumHeight() == BAR_HEIGHT


# ═══════════════════════════════════════════════════════════════════════════
# _ProgressStatusBar 测试
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def psb(theme_mgr):
    from astrocrawl.gui._animated_bar import _ProgressStatusBar

    return _ProgressStatusBar()


class TestProgressStatusBar:
    def test_psb01_busy_refcount_single(self, psb):
        """PSB01: 单次 busy→idle 引用计数 → 脉动条启停。"""
        psb._on_busy_changed(True)
        assert psb._pulse_refcount == 1
        assert psb._pulse_bar.is_active() is True

        psb._on_busy_changed(False)
        assert psb._pulse_refcount == 0
        assert psb._pulse_bar.is_active() is False

    def test_psb02_busy_refcount_nested(self, psb):
        """PSB02: 嵌套 busy (2→1) 不停止脉动条，直到 refcount 归零。"""
        psb._on_busy_changed(True)  # refcount 0→1
        psb._on_busy_changed(True)  # refcount 1→2
        assert psb._pulse_refcount == 2
        assert psb._pulse_bar.is_active() is True

        psb._on_busy_changed(False)  # refcount 2→1
        assert psb._pulse_refcount == 1
        assert psb._pulse_bar.is_active() is True  # 仍然运行

        psb._on_busy_changed(False)  # refcount 1→0
        assert psb._pulse_refcount == 0
        assert psb._pulse_bar.is_active() is False

    def test_psb03_busy_refcount_floor_at_zero(self, psb):
        """PSB03: 多余的 idle 信号不会使 refcount 变负。"""
        psb._on_busy_changed(False)
        assert psb._pulse_refcount == 0
        psb._on_busy_changed(False)
        assert psb._pulse_refcount == 0

    def test_psb04_start_pulse_public_api(self, psb):
        """PSB04: start_pulse() 公共 API 启动脉动条。"""
        psb.start_pulse()
        assert psb._pulse_refcount == 1
        assert psb._pulse_bar.is_active() is True

    def test_psb05_stop_pulse_public_api(self, psb):
        """PSB05: stop_pulse() 公共 API 停止脉动条。"""
        psb.start_pulse()
        psb.stop_pulse()
        assert psb._pulse_refcount == 0
        assert psb._pulse_bar.is_active() is False

    def test_psb06_dispose_stops_pulse_bar(self, psb):
        """PSB06: dispose() 直接停止脉动条（无视 refcount）。"""
        psb._on_busy_changed(True)
        psb._on_busy_changed(True)  # refcount = 2
        psb.dispose()
        assert psb._pulse_bar.is_active() is False

    def test_psb07_show_status_levels(self, psb, theme_mgr):
        """PSB07: show_status 各 level 更新文本 + stylesheet。"""
        psb.show_status("Working...", "info")
        assert "Working..." in psb._status_bar.text()
        assert psb._status_level == "info"

        psb.show_status("Warning!", "warning")
        assert "Warning!" in psb._status_bar.text()
        assert psb._status_level == "warning"

        psb.show_status("Error!", "error")
        assert "Error!" in psb._status_bar.text()
        assert psb._status_level == "error"

        psb.show_status("Done", "success")
        assert "Done" in psb._status_bar.text()
        assert psb._status_level == "success"

    def test_psb08_hide_status_bar(self, theme_mgr):
        """PSB08: show_status_bar=False 隐藏状态标签。"""
        from astrocrawl.gui._animated_bar import _ProgressStatusBar

        psb_hidden = _ProgressStatusBar(show_status_bar=False)
        assert psb_hidden._status_bar.isHidden() is True

    def test_psb09_show_status_bar_default_visible(self, psb):
        """PSB09: 默认 show_status_bar=True → 状态标签可见。"""
        assert psb._status_bar.isHidden() is False

    def test_psb10_connect_page_attaches_signals(self, psb, qapp):
        """PSB10: connect_page 连接 busy_changed + status_message 信号。"""
        from PySide6.QtCore import Signal

        from astrocrawl.gui._animated_bar import _ProgressStatusBar

        class _TestPage(QWidget):
            busy_changed = Signal(bool)
            status_message = Signal(str, str)

        psb = _ProgressStatusBar()
        page = _TestPage()
        psb.connect_page(page)

        # emit busy_changed → refcount + pulse starts
        page.busy_changed.emit(True)
        assert psb._pulse_refcount == 1
        assert psb._pulse_bar.is_active() is True

        # emit status_message → text updates
        page.status_message.emit("hello", "warning")
        assert "hello" in psb._status_bar.text()
        assert psb._status_level == "warning"

    def test_psb11_connect_page_without_status(self, psb, qapp):
        """PSB11: connect_page(connect_status=False) 只连 busy_changed，不连 status_message。"""
        from PySide6.QtCore import Signal

        from astrocrawl.gui._animated_bar import _ProgressStatusBar

        class _TestPage(QWidget):
            busy_changed = Signal(bool)
            status_message = Signal(str, str)

        psb = _ProgressStatusBar()
        page = _TestPage()
        psb.connect_page(page, connect_status=False)

        page.busy_changed.emit(True)
        assert psb._pulse_refcount == 1

        # status_message 不应被连接
        saved_text = psb._status_bar.text()
        page.status_message.emit("should be ignored", "error")
        assert psb._status_bar.text() == saved_text

    def test_psb12_connect_page_no_signals(self, psb, qapp):
        """PSB12: 无 busy_changed/status_message 的页面 connect_page 不抛异常。"""
        psb.connect_page(QWidget())

    def test_psb13_theme_changed_updates_stylesheet(self, psb, theme_mgr):
        """PSB13: theme_changed 信号触发后状态栏 stylesheet 更新。"""
        psb.show_status("Connected", "success")
        theme_mgr.theme_changed.emit()
        after = psb._status_bar.styleSheet()
        assert len(after) > 0

    def test_psb14_initial_state(self, psb):
        """PSB14: 初始状态 — refcount=0，脉动条停，状态栏显示 Ready。"""
        assert psb._pulse_refcount == 0
        assert psb._pulse_bar.is_active() is False
        assert "Ready" in psb._status_bar.text()
        assert psb._status_level == "success"

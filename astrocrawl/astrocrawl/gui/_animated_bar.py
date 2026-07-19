"""脉动动画条基类 — 封装 QTimer + _anim_offset + paintEvent 生命周期防护。

对所有脉动/渐变动画 QWidget 提供统一的 Template Method 合约。
在 WorkerStatusBar 和 _PulseBar 之间提取的共享抽象。

私有约定：对接 astrocrawl.gui._tokens 中的 BAR_HEIGHT / PULSE_ANIM_MS / RADIUS_MD。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath
from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from astrocrawl.gui._style import status_label_style
from astrocrawl.gui._tokens import BAR_HEIGHT, RADIUS_MD, SPACE_SM
from astrocrawl.gui.theme import get_theme_manager


class _AnimatedBar(QWidget):
    """脉动动画条基类 — Template Method 模式。

    stop() 合约：
      stop() 调用 self.repaint()（同步绘制）而非 self.update()（异步 QPA 事件）。
      异步 QPA paint event 在控件析构后投递会导致 QPaintDevice::paintEngine()
      纯虚调用 → SIGABRT。

    子类覆盖：
      - _tick(): 动画步进逻辑（默认固定步长 0.008）
      - _paint_bar(painter, anim_offset, w, h): 实际绘制
      - _on_stop(): 自定义状态清理（在 repaint() 之前调用）
    """

    PULSE_INTERVAL_MS = 33  # ~30fps，对接 _tokens.PULSE_ANIM_MS

    def __init__(self, parent=None):
        super().__init__(parent)
        self._anim_offset: float = 0.0
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._tick)

        self._paint_disabled: bool = False
        self.destroyed.connect(lambda: setattr(self, "_paint_disabled", True))

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    # ── public API ──

    def start(self) -> None:
        self._anim_offset = 0.0
        self._anim_timer.start(self.PULSE_INTERVAL_MS)

    def stop(self) -> None:
        self._anim_timer.stop()
        self._anim_offset = 0.0
        self._on_stop()
        self.repaint()

    def is_active(self) -> bool:
        return bool(self._anim_timer.isActive())

    # ── Template Method hooks ──

    def _tick(self) -> None:
        self._anim_offset = (self._anim_offset + 0.008) % 1.0
        self.update()

    def _on_stop(self) -> None:
        pass

    def paintEvent(self, event) -> None:
        if self._paint_disabled:
            return
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing, False)
            self._paint_bar(painter, self._anim_offset, w, h)
        finally:
            painter.end()

    def _paint_bar(self, painter: QPainter, anim_offset: float, w: int, h: int) -> None:
        raise NotImplementedError


class _PulseBar(_AnimatedBar):
    """脉动忙碌指示器 — 恒速滚动渐变，视觉与 WorkerStatusBar 对齐。

    空闲态显示灰色占位条（``disabled`` token），活跃时显示渐变滚动条。
    start()/stop() 继承自 _AnimatedBar，不额外控制可见性——控件常驻于布局中。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(BAR_HEIGHT)

    def _paint_bar(self, painter, anim_offset, w, h):
        from astrocrawl.gui.theme import get_theme_manager

        theme = get_theme_manager()

        if not self.is_active():
            path = QPainterPath()
            path.addRoundedRect(0, 0, w, h, RADIUS_MD, RADIUS_MD)
            painter.fillPath(path, QColor(theme.get("disabled")))
            return

        scroll = (anim_offset * w * 2) % (w * 2)
        grad = QLinearGradient(-w - scroll, 0, w * 3 - scroll, 0)
        grad.setColorAt(0.0, QColor(theme.get("worker_grad_start")))
        grad.setColorAt(0.25, QColor(theme.get("worker_grad_end")))
        grad.setColorAt(0.5, QColor(theme.get("worker_grad_start")))
        grad.setColorAt(0.75, QColor(theme.get("worker_grad_end")))
        grad.setColorAt(1.0, QColor(theme.get("worker_grad_start")))

        path = QPainterPath()
        path.addRoundedRect(0, 0, w, h, RADIUS_MD, RADIUS_MD)
        painter.fillPath(path, grad)


class _ProgressStatusBar(QWidget):
    """复合进度指示器 — 脉动条 + 状态栏 + 多页面 busy 引用计数协调。

    connect_page(page, connect_status=True) 自动检测并连接子页面信号。
    dispose() 在对话框关闭前显式停止脉动动画（对齐 gui-standards.md §12.3）。

    show_status_bar=False 隐藏状态栏，适用于页面自有状态栏的对话框（如 AdvancedSettingsDialog）。
    """

    def __init__(self, parent=None, *, show_status_bar: bool = True):
        super().__init__(parent)

        self._pulse_bar = _PulseBar()
        self._status_level = "success"
        self._status_bar = QLabel(self.tr("Ready"))
        self._status_bar.setObjectName("status-bar")
        self._status_bar.setFixedHeight(BAR_HEIGHT)
        self._status_bar.setAlignment(Qt.AlignCenter)
        self._status_bar.setVisible(show_status_bar)

        layout = QVBoxLayout(self)
        layout.setSpacing(SPACE_SM)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._pulse_bar)
        layout.addWidget(self._status_bar)

        self._pulse_refcount = 0

        self._theme_mgr = get_theme_manager()
        if self._theme_mgr is not None:
            self._theme_mgr.theme_changed.connect(self._on_theme_changed)
        self._on_theme_changed()

    def connect_page(self, page: QWidget, *, connect_status: bool = True) -> None:
        if hasattr(page, "busy_changed"):
            page.busy_changed.connect(self._on_busy_changed)
        if connect_status and hasattr(page, "status_message"):
            page.status_message.connect(self._show_status)

    def show_status(self, msg: str, level: str = "success") -> None:
        self._show_status(msg, level)

    def start_pulse(self) -> None:
        """直接启动脉动条（复用引用计数，与 connect_page 兼容）。"""
        self._on_busy_changed(True)

    def stop_pulse(self) -> None:
        """直接停止脉动条。"""
        self._on_busy_changed(False)

    def dispose(self) -> None:
        self._pulse_bar.stop()

    @Slot(bool)
    def _on_busy_changed(self, active: bool) -> None:
        if active:
            self._pulse_refcount += 1
            if self._pulse_refcount == 1:
                self._pulse_bar.start()
        else:
            self._pulse_refcount = max(0, self._pulse_refcount - 1)
            if self._pulse_refcount == 0:
                self._pulse_bar.stop()

    def _show_status(self, msg: str, level: str = "success") -> None:
        self._status_level = level
        self._status_bar.setText(msg)
        theme = get_theme_manager()
        color_map = {
            "success": theme.get("success"),
            "warning": theme.get("warning"),
            "error": theme.get("danger"),
            "info": theme.get("window_text"),
        }
        fg = color_map.get(level, theme.get("success"))
        self._status_bar.setStyleSheet(status_label_style(theme.get("input_bg")) + f"color: {fg}; font-weight: bold;")

    def _on_theme_changed(self) -> None:
        if self._theme_mgr is None:
            return
        bg = self._theme_mgr.get("input_bg")
        color_map = {"success": "success", "warning": "warning", "error": "danger", "info": "window_text"}
        fg = self._theme_mgr.get(color_map.get(self._status_level, "success"))
        self._status_bar.setStyleSheet(status_label_style(bg) + f"color: {fg}; font-weight: bold;")

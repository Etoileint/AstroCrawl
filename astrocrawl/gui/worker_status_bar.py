"""Worker 状态脉动渐变条。

按信号推模式跟踪 worker 状态：主线程接收 worker_state(int, str) 信号，
无需轮询。4-stop 双周期渐变无缝滚动，速度随活跃 worker 数变化。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtGui import QColor, QLinearGradient, QPainterPath
from PySide6.QtWidgets import QWidget

from astrocrawl.gui._animated_bar import _AnimatedBar
from astrocrawl.gui._tokens import BAR_HEIGHT, RADIUS_MD


class WorkerStatusBar(_AnimatedBar):
    """脉动渐变状态条 — 24px 高，填充宽度。"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFixedHeight(BAR_HEIGHT)

        self._session = None
        self._working: set[int] = set()

    def connect_session(self, session) -> None:
        self._anim_timer.stop()
        self._anim_offset = 0.0
        self._session = session
        self._working.clear()
        self._anim_timer.start(self.PULSE_INTERVAL_MS)
        self.repaint()

    def _on_stop(self) -> None:
        self._session = None
        self._working.clear()

    def _on_worker_state(self, idx: int, state: str) -> None:
        if state == "working":
            self._working.add(idx)
        else:
            self._working.discard(idx)

    def _tick(self) -> None:
        if self._working:
            speed = len(self._working) * 0.003
            self._anim_offset = (self._anim_offset + speed) % 1.0
        else:
            self._anim_offset = 0.0
        self.update()

    def _paint_bar(self, painter, anim_offset, w, h):
        from astrocrawl.gui.theme import get_theme_manager

        theme = get_theme_manager()

        if not self._working:
            path = QPainterPath()
            path.addRoundedRect(0, 0, w, h, RADIUS_MD, RADIUS_MD)
            painter.fillPath(path, QColor(theme.get("disabled")))
            return

        # 4w 宽渐变，viewport [0,w] 始终在 [w, 3w] 内，不触发 PadSpread
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

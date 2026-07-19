"""代理健康状态显示条。

将代理池均分为色块，颜色按 health_score 从 danger→warning→success 连续过渡。
每 3s 自动刷新，与 ProxyHealthTracker 同步。颜色跟随当前主题。
无代理时显示灰色占位段，控件始终可见。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QFrame, QHBoxLayout, QSizePolicy, QWidget

from astrocrawl._constants import PROXY_HEALTH_BAR_REFRESH
from astrocrawl.gui._tokens import RADIUS_MD

if TYPE_CHECKING:
    from astrocrawl.proxy import ProxySession
    from astrocrawl.proxy._proxy import ProxyHealthTracker


def _score_to_color(score: float, theme) -> str:
    """health_score (0.0-1.0) → 在 danger→warning→success 之间线性插值。"""
    score = max(0.0, min(1.0, score))
    danger_c = QColor(theme.get("danger"))
    warning_c = QColor(theme.get("warning"))
    success_c = QColor(theme.get("success"))

    if score <= 0.5:
        ratio = score * 2
        start, end = danger_c, warning_c
    else:
        ratio = (score - 0.5) * 2
        start, end = warning_c, success_c

    r = int(start.red() + (end.red() - start.red()) * ratio)
    g = int(start.green() + (end.green() - start.green()) * ratio)
    b = int(start.blue() + (end.blue() - start.blue()) * ratio)
    return f"#{r:02x}{g:02x}{b:02x}"


class _PlaceholderSegment(QWidget):
    """占位段 — QPainter 渲染，与 WorkerStatusBar 空闲态完全一致。"""

    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self._theme = theme
        self.setMinimumHeight(8)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setToolTip(self.tr("No Proxy"))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width(), self.height(), RADIUS_MD, RADIUS_MD)
        painter.fillPath(path, QColor(self._theme.get("disabled")))
        painter.end()


class ProxyHealthBar(QWidget):
    """代理健康状态色条。无代理时显示灰色占位，控件始终可见。"""

    REFRESH_SEC = PROXY_HEALTH_BAR_REFRESH

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        from astrocrawl.gui.theme import get_theme_manager

        self._theme = get_theme_manager()
        self._proxies: List[str] = []
        self._health: Optional[ProxyHealthTracker] = None
        self._segments: List[QWidget] = []

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(1)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._rebuild()

    def stop(self) -> None:
        """停止刷新并重置为灰色占位。"""
        self._timer.stop()
        self._proxies.clear()
        self._health = None
        self._rebuild()

    def stop_refresh(self) -> None:
        """停止自动刷新但保留当前色条显示（会话结束时代理池仍在）。"""
        self._timer.stop()

    def set_source(self, session: ProxySession) -> None:
        """设置 ProxySession 引用。内部从 session.proxies 和 session.health 获取数据。
        构造时 stats 为空，未测试代理 score=1.0（绿色段），探针启动后自动变活。"""
        self._proxies = list(session.proxies)
        self._health = session.health
        self._rebuild()
        if self._proxies:
            self._timer.start(int(self.REFRESH_SEC * 1000))
        else:
            self._timer.stop()

    def _rebuild(self) -> None:
        for seg in self._segments:
            self._layout.removeWidget(seg)
            seg.deleteLater()
        self._segments.clear()

        n = len(self._proxies)
        if n == 0:
            seg = _PlaceholderSegment(self._theme)
            self._layout.addWidget(seg)
            self._segments.append(seg)
            return

        for url in self._proxies:
            seg = QFrame()
            seg.setMinimumHeight(8)
            seg.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            seg.setFrameShape(QFrame.StyledPanel)
            seg.setToolTip(url)
            self._layout.addWidget(seg)
            self._segments.append(seg)

        self._refresh()

    def _refresh(self) -> None:
        if not self._proxies:
            return
        border = self._theme.get("border")
        if not self._health:
            for seg in self._segments:
                seg.setStyleSheet(
                    f"QFrame {{ background-color: {self._theme.get('disabled')}; "
                    f"border: 1px solid {border}; border-radius: {RADIUS_MD}px; }}"
                )
            return
        snapshot = self._health.get_all_stats()
        for i, url in enumerate(self._proxies):
            s = snapshot.get(url)
            score = s.health_score if s else 1.0
            color = _score_to_color(score, self._theme)
            self._segments[i].setStyleSheet(
                f"QFrame {{ background-color: {color}; border: 1px solid {border}; border-radius: {RADIUS_MD}px; }}"
            )
            tip = self.tr("{url}\nHealth Score: {score:.2f}").format(url=url, score=score)
            if s:
                tip += (
                    self.tr("\nState: {state}").format(state=s.state.value)
                    + self.tr("\nConsecutive Failures: {n}").format(n=s.consecutive_failures)
                    + self.tr("\nSuccesses: {ok} / Failures: {fail}").format(
                        ok=s.total_successes, fail=s.total_failures
                    )
                )
            self._segments[i].setToolTip(tip)

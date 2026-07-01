"""标题栏容器 — Worker 状态条 + 主题按钮。"""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

from astrocrawl.gui._tokens import BAR_HEIGHT, SPACE_XS
from astrocrawl.gui.worker_status_bar import WorkerStatusBar

_MODE_SYMBOLS = {"light": "☀", "dark": "★", "custom": "✿"}


class TitleBar(QWidget):
    """标题栏：WorkerStatusBar（左，stretch）+ 主题按钮（右，24×24 正方形）。"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_XS)

        self._worker_bar = WorkerStatusBar()
        layout.addWidget(self._worker_bar, 1)

        self._theme_btn = QPushButton()
        self._theme_btn.setObjectName("theme-btn")
        self._theme_btn.setFixedSize(BAR_HEIGHT, BAR_HEIGHT)
        self._theme_btn.clicked.connect(self._open_theme_dialog)
        layout.addWidget(self._theme_btn)

        self._update_button()

        from astrocrawl.gui.theme import get_theme_manager

        get_theme_manager().theme_changed.connect(self._update_button)

    def _update_button(self) -> None:
        from astrocrawl.gui.theme import get_theme_manager

        mode = get_theme_manager().current_mode()
        self._theme_btn.setText(_MODE_SYMBOLS.get(mode, "☀"))

    def _open_theme_dialog(self) -> None:
        from astrocrawl.gui.theme_dialog import ThemeDialog

        dlg = ThemeDialog(self)
        dlg.exec()
        self._update_button()  # 无论确认还是取消都刷新（应用可能已生效）

    def connect_worker_state(self, session) -> None:
        """连接 session 的 worker_state_changed 信号到内部 WorkerStatusBar。"""
        session.worker_state_changed.connect(self._worker_bar._on_worker_state)

    def connect_session(self, session) -> None:
        """将 session 引用传递给内部 WorkerStatusBar 并启动动画。"""
        self._worker_bar.connect_session(session)

    def stop_worker_bar(self) -> None:
        self._worker_bar.stop()

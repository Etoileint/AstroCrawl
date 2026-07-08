from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QCompleter,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from astrocrawl.browser._preview import PreviewFieldParams, PreviewParams, assign_field_colors
from astrocrawl.gui._animated_bar import _ProgressStatusBar
from astrocrawl.gui._preview_session import PreviewSession
from astrocrawl.gui._tokens import SPACE_MD, SPACE_SM, SPACE_XS
from astrocrawl.gui.theme import get_theme_manager
from astrocrawl.rules._schema import FieldRule
from astrocrawl.utils.logging import LogfmtLogger

logger = LogfmtLogger("astrocrawl.gui.preview")


class PreviewPanel(QDialog):
    """规则可视化预览面板 — 非模态 Singleton 工具窗口。"""

    _instance: Optional[PreviewPanel] = None

    @classmethod
    def open(
        cls,
        snapshot: Any = None,
        rule_name: Optional[str] = None,
        test_url: Optional[str] = None,
    ) -> PreviewPanel:
        if cls._instance is not None and cls._instance.isVisible():
            logger.info("preview_panel_reuse")
            if snapshot is not None:
                cls._instance._snapshot = snapshot
                cls._instance._populate_rules()
            cls._instance.raise_()
            cls._instance.activateWindow()
            if rule_name:
                cls._instance.set_rule(rule_name, test_url)
            return cls._instance
        if cls._instance is not None:
            logger.info("preview_panel_replace_orphan")
            old = cls._instance
            cls._instance = None
            old.reject()  # 完整释放 PreviewSession + PreviewThread 资源
        logger.info("preview_panel_create")
        panel = cls(snapshot)
        cls._instance = panel
        if rule_name:
            panel.set_rule(rule_name, test_url)
        panel.start()
        return panel

    def __init__(self, snapshot: Any = None, parent=None):
        super().__init__(parent=None)
        self.setWindowFlags(Qt.Window | Qt.Tool)
        self._snapshot = snapshot
        self._session: Optional[PreviewSession] = None
        self._loading = False
        self._page_rows: dict[int, Any] = {}
        self._selected_rule_name: Optional[str] = None
        self._setup_ui()
        self._theme_mgr = get_theme_manager()
        self._theme_mgr.theme_changed.connect(self._on_theme_changed)
        self._on_theme_changed()
        self._populate_rules()

    def _setup_ui(self) -> None:
        self.setWindowTitle(self.tr("Rule Preview"))
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        layout.setSpacing(SPACE_MD)
        layout.setContentsMargins(SPACE_MD, SPACE_MD, SPACE_MD, SPACE_MD)

        # ── 规则数据模型 ──
        self._source_model = QStandardItemModel()

        # ── QCompleter：Qt 内置的标准可搜索下拉方案 ──
        self._completer = QCompleter()
        self._completer.setModel(self._source_model)
        self._completer.setCompletionMode(QCompleter.PopupCompletion)
        self._completer.setFilterMode(Qt.MatchContains)
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._completer.setCompletionRole(Qt.DisplayRole)
        self._completer.activated.connect(self._on_completer_activated)

        # ── 规则行 ──
        rule_row = QHBoxLayout()
        rule_row.setSpacing(SPACE_SM)
        rule_label = QLabel(self.tr("Rule:"))
        rule_row.addWidget(rule_label, 1)

        self._rule_edit = QLineEdit()
        self._rule_edit.setObjectName("rule-search")
        self._rule_edit.setPlaceholderText(self.tr("Type keywords to filter rules..."))
        self._rule_edit.setCompleter(self._completer)

        dropdown_icon = self.style().standardIcon(QStyle.SP_ArrowDown)
        self._dropdown_action = self._rule_edit.addAction(dropdown_icon, QLineEdit.TrailingPosition)
        self._dropdown_action.triggered.connect(self._show_all_completions)
        rule_row.addWidget(self._rule_edit, 3)

        layout.addLayout(rule_row)

        # ── URL 行 ──
        url_row = QHBoxLayout()
        url_row.setSpacing(SPACE_SM)
        url_label = QLabel(self.tr("URL:"))
        url_row.addWidget(url_label, 1)
        self._url_edit = QLineEdit()
        self._url_edit.setObjectName("preview-url")
        self._url_edit.setPlaceholderText(self.tr("Enter test page URL..."))
        url_row.addWidget(self._url_edit, 2)
        self._go_btn = QPushButton(self.tr("Open Preview Page"))
        self._go_btn.setObjectName("go-btn")
        self._go_btn.clicked.connect(self._on_go)
        url_row.addWidget(self._go_btn, 1)
        layout.addLayout(url_row)

        # ── 分隔线 ──
        line = QLabel()
        line.setFrameStyle(QLabel.HLine | QLabel.Sunken)
        layout.addWidget(line)

        # ── 已打开页面列表 ──
        layout.addWidget(QLabel(self.tr("Opened Pages")))
        self._page_list = QListWidget()
        self._page_list.setObjectName("page-list")
        self._page_list.setAlternatingRowColors(True)
        self._page_list.itemClicked.connect(self._on_page_clicked)
        layout.addWidget(self._page_list, 1)

        # ── 脉动条 + 状态栏（_ProgressStatusBar 复合组件） ──
        self._psb = _ProgressStatusBar()
        self._psb.show_status(self.tr("Ready"))
        layout.addWidget(self._psb)

        self.adjustSize()
        self._lock_size()

    # ── Completer ─────────────────────────────────────────────

    def _show_all_completions(self) -> None:
        self._completer.setCompletionPrefix(self._rule_edit.text())
        cr = self._rule_edit.cursorRect()
        cr.setWidth(self._rule_edit.width())
        self._completer.complete(cr)

    def _on_completer_activated(self, text: str) -> None:
        for row in range(self._source_model.rowCount()):
            item = self._source_model.item(row)
            if item and item.text() == text:
                name = item.data(Qt.UserRole)
                if name:
                    self._selected_rule_name = name
                    self._apply_rule(name)
                break

    # ── Rules ─────────────────────────────────────────────────

    def _populate_rules(self) -> None:
        self._rule_edit.blockSignals(True)
        try:
            self._source_model.clear()
            if self._snapshot and hasattr(self._snapshot, "by_name"):
                for name, rule in sorted(self._snapshot.by_name.items()):
                    display = getattr(rule, "display_name", "") or name
                    item = QStandardItem(display)
                    item.setData(name, Qt.UserRole)
                    self._source_model.appendRow(item)
            self._rule_edit.clear()
            self._selected_rule_name = None
        finally:
            self._rule_edit.blockSignals(False)

    def _apply_rule(self, name: str) -> None:
        if self._snapshot:
            rule = self._snapshot.by_name.get(name)
            if rule:
                test_urls = getattr(rule, "test_urls", []) or []
                if test_urls:
                    url = test_urls[0]
                    if isinstance(url, dict):
                        url = url.get("url", "")
                    self._url_edit.setText(url)

    def set_rule(self, rule_name: str, test_url: Optional[str] = None) -> None:
        self._rule_edit.blockSignals(True)
        try:
            for row in range(self._source_model.rowCount()):
                item = self._source_model.item(row)
                if item and item.data(Qt.UserRole) == rule_name:
                    self._rule_edit.setText(item.text())
                    self._selected_rule_name = rule_name
                    break
        finally:
            self._rule_edit.blockSignals(False)
        self._apply_rule(rule_name)
        if test_url:
            self._url_edit.setText(test_url)

    # ── Go ────────────────────────────────────────────────────

    def _on_go(self) -> None:
        if self._loading:
            return
        if self._session is None:
            return
        url = self._url_edit.text().strip()
        if not url:
            self._psb.show_status(self.tr("Please enter a URL"), "warning")
            return
        params = self._build_params()
        if params is None:
            self._psb.show_status(self.tr("Please select a rule"), "warning")
            return
        self._loading = True
        self._go_btn.setEnabled(False)
        self._psb.start_pulse()
        self._session.open_page(url, params, rule_name=self._selected_rule_name or "")

    def _build_params(self) -> Optional[PreviewParams]:
        name = self._selected_rule_name
        if not name or not self._snapshot:
            return None
        rule = self._snapshot.by_name.get(name)
        if rule is None:
            return None
        fields_dict = getattr(rule, "fields", {}) or {}
        raw_fields: list[dict[str, Any]] = []
        for fname, fcfg in fields_dict.items():
            if isinstance(fcfg, FieldRule):
                entry = {
                    "name": fname,
                    "selector": fcfg.selector,
                    "extract": fcfg.extract,
                    "attr": fcfg.attr,
                    "multiple": fcfg.multiple,
                }
                if fcfg.fallback:
                    entry["fallback"] = [
                        {
                            "selector": fb.selector,
                            "extract": fb.extract,
                            "attr": fb.attr,
                            "multiple": fb.multiple,
                        }
                        for fb in fcfg.fallback
                    ]
                raw_fields.append(entry)
            elif isinstance(fcfg, dict):
                entry = {
                    "name": fname,
                    "selector": fcfg.get("selector", ""),
                    "extract": fcfg.get("extract", "text"),
                    "attr": fcfg.get("attr", ""),
                    "multiple": fcfg.get("multiple", False),
                }
                fb = fcfg.get("fallback", [])
                if fb:
                    entry["fallback"] = [
                        {"selector": fb[i], "extract": "text"} if isinstance(fb[i], str) else fb[i]
                        for i in range(len(fb))
                    ]
                raw_fields.append(entry)
            else:
                raw_fields.append({"name": fname, "selector": str(fcfg), "extract": "text"})

        colored = assign_field_colors(raw_fields)
        field_params = [
            PreviewFieldParams(
                name=f["name"],
                selector=f["selector"],
                extract=f.get("extract", "text"),
                attr=f.get("attr", ""),
                multiple=f.get("multiple", False),
                color=f["color"],
                fallback=f.get("fallback", []),
            )
            for f in colored
        ]
        theme_mgr = get_theme_manager()
        config = theme_mgr.get_config()
        theme_mode = config["base"] if config["mode"] == "custom" else config["mode"]
        return PreviewParams(
            fields=field_params,
            rule_name=name,
            theme_mode=theme_mode,
            theme_tokens=theme_mgr.get_all_tokens(),
        )

    # ── Session callbacks ─────────────────────────────────────

    def _on_page_opened(self, handle: Any) -> None:
        self._psb.stop_pulse()
        item = QListWidgetItem()
        item.setData(Qt.UserRole, handle.page_id)

        row = QWidget()
        row.setObjectName("page-row")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(SPACE_XS, 2, SPACE_SM, 2)
        row_layout.setSpacing(SPACE_SM)

        label_text = self.tr("[{rule}] {url}").format(rule=handle.rule_name or self.tr("Unnamed"), url=handle.url)
        label = QLabel(label_text)
        label.setCursor(Qt.PointingHandCursor)
        row_layout.addWidget(label, 1)

        close_btn = QPushButton("✕")
        close_btn.setObjectName("page-close-btn")
        close_btn.setFixedSize(20, 20)
        close_btn.setFlat(True)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setToolTip(self.tr("Close this page"))
        close_btn.clicked.connect(lambda checked=False, pid=handle.page_id: self._on_close_page_row(pid))
        row_layout.addWidget(close_btn)

        idx = self._page_list.count()
        bg = self._theme_mgr.get("input_bg_alt") if idx % 2 == 1 else self._theme_mgr.get("input_bg")
        p = row.palette()
        p.setColor(QPalette.Window, QColor(bg))
        row.setPalette(p)
        row.setAutoFillBackground(True)

        self._page_list.addItem(item)
        self._page_list.setItemWidget(item, row)
        self._page_rows[handle.page_id] = row

        self._loading = False
        self._go_btn.setEnabled(True)

    def _on_highlight_injected(self, result: Any) -> None:
        self._psb.show_status(
            self.tr("Fields: {matched}/{total} matched").format(matched=result.matched, total=result.total)
            + (self.tr(" (fallback active)") if result.fallback_activated else ""),
            "success",
        )

    def _on_page_closed(self, page_id: int) -> None:
        self._page_rows.pop(page_id, None)
        for i in range(self._page_list.count()):
            item = self._page_list.item(i)
            if item and item.data(Qt.UserRole) == page_id:
                self._page_list.takeItem(i)
                break
        self._refresh_page_row_backgrounds()

    def _on_error(self, msg: str) -> None:
        logger.warning("preview_error", error=msg)
        self._psb.stop_pulse()
        self._psb.show_status(msg, "error")
        self._loading = False
        self._go_btn.setEnabled(True)

    def _on_session_disposed(self) -> None:
        logger.warning("preview_session_disposed")
        self._page_rows.clear()
        self._page_list.clear()
        self._psb.show_status(self.tr("Preview session ended"), "warning")

    def _on_page_clicked(self, item: QListWidgetItem) -> None:
        page_id = item.data(Qt.UserRole)
        if page_id is not None and self._session:
            self._session.activate_page(page_id)

    def _on_close_page_row(self, page_id: int) -> None:
        if self._session:
            self._session.close_page(page_id)

    # ── Theme ──────────────────────────────────────────────────

    def _on_theme_changed(self) -> None:
        if self._theme_mgr is None:
            return
        t = self._theme_mgr
        self._refresh_page_row_backgrounds()
        if self._session:
            config = t.get_config()
            theme_mode = config["base"] if config["mode"] == "custom" else config["mode"]
            self._session.update_theme(theme_mode, t.get_all_tokens())

    def _refresh_page_row_backgrounds(self) -> None:
        for i in range(self._page_list.count()):
            item = self._page_list.item(i)
            if item is None:
                continue
            row = self._page_list.itemWidget(item)
            if row is None:
                continue
            bg = self._theme_mgr.get("input_bg_alt") if i % 2 == 1 else self._theme_mgr.get("input_bg")
            p = row.palette()
            p.setColor(QPalette.Window, QColor(bg))
            row.setPalette(p)

    # ── Lifecycle ─────────────────────────────────────────────

    def _lock_size(self) -> None:
        self.setMaximumWidth(self.width())
        self.setMinimumWidth(self.width())
        screen = self.screen()
        if screen:
            ideal_h = self.height()
            max_h = int(screen.availableGeometry().height() * 0.85)
            self.setMaximumHeight(min(ideal_h, max_h))
            self.setMinimumHeight(min(ideal_h, max_h))
        else:
            self.setMaximumHeight(self.height())
            self.setMinimumHeight(self.height())

    def start(self) -> None:
        config = self._theme_mgr.get_config()
        theme_mode = config["base"] if config["mode"] == "custom" else config["mode"]
        from astrocrawl.utils.preferences import get_preferences

        prefs = get_preferences()
        proxy = prefs.get_parsed_proxy_for("preview")
        self._session = PreviewSession(theme_mode=theme_mode, proxy=proxy)
        self._session.page_opened.connect(self._on_page_opened)
        self._session.page_closed.connect(self._on_page_closed)
        self._session.highlight_injected.connect(self._on_highlight_injected)
        self._session.error_occurred.connect(self._on_error)
        self._session.disposed.connect(self._on_session_disposed)
        self._session.start()
        self.show()
        self.raise_()
        self.activateWindow()

    def reject(self) -> None:
        logger.info("preview_panel_close")
        self._psb.dispose()
        self._page_rows.clear()
        if self._session:
            self._session.dispose()
            self._session = None
        PreviewPanel._instance = None
        super().reject()

    def closeEvent(self, event: Any) -> None:
        self.reject()
        super().closeEvent(event)

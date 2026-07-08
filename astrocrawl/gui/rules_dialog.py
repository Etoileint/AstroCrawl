"""规则管理对话框 — 多页签设计，风格与 AdvancedSettingsDialog 统一。

Tab 1: 规则列表 — 搜索 + 表格 + 开关 + 双击预览/编辑
Tab 2: 自定义 — 粘贴导入 + AI 内联生成
Tab 3: 远程源 — 源列表 + 开关
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Protocol

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from astrocrawl._constants import QLINEEDIT_MAX
from astrocrawl._types import DEFAULT_EXTRACTION_TYPE
from astrocrawl.ai import AIClient, AIConfig, GenerationParams, OutputConstraint
from astrocrawl.config import CrawlerConfig
from astrocrawl.gui._animated_bar import _ProgressStatusBar
from astrocrawl.gui._delegates import CheckboxDelegate, StatusColorDelegate
from astrocrawl.gui._style import (
    ColumnDef,
    centered_checkbox_container,
    configure_table_header,
    create_form_scroll_area,
    create_managed_table,
    monospace_style,
)
from astrocrawl.gui._table_page import _FilterProxy
from astrocrawl.gui._tokens import FONT_MD, SPACE_LG, SPACE_MD, SPACE_SM, SPACE_XS
from astrocrawl.gui.advanced_dialog import AdvancedSettingsDialog
from astrocrawl.gui.theme import get_theme_manager
from astrocrawl.rules import (
    PreprocessTier,
    RuleLifecycle,
    add_source_to_file,
    export_all_rules,
    export_rule_to_file,
    get_assembled_prompt,
    import_rule,
    import_rule_preview,
    list_sources_from_file,
    remove_source_from_file,
    safe_write_rule_file,
    set_rule_enabled,
    update_source_in_file,
    validate_rule,
    validate_source_url,
)
from astrocrawl.rules import clean_markdown_wrapper as _clean_markdown_wrapper
from astrocrawl.utils.logging import LogfmtLogger
from astrocrawl.utils.preferences import clear_qt_file_dialog_history, get_preferences

if TYPE_CHECKING:
    import asyncio

logger = LogfmtLogger("astrocrawl.gui.rules_dialog")


class SnapshotProvider(Protocol):
    def get_snapshot(self) -> Any | None: ...


# ═══════════════════════════════════════════════════════════════════════
# 规则编辑 / 预览对话框
# ═══════════════════════════════════════════════════════════════════════


class RuleEditDialog(QDialog):
    """规则编辑 / 预览对话框。官方预置和远程规则仅可预览。"""

    def __init__(
        self,
        rule_data: dict,
        source: str,
        rule_path: Optional[Path] = None,
        parent=None,
        snapshot=None,
        snapshot_provider: SnapshotProvider | None = None,
    ):
        super().__init__(parent)
        self.setWindowModality(Qt.WindowModal)
        self._data = rule_data
        self._source = source
        self._rule_path = rule_path
        self._readonly = source != "user"
        self._snapshot = snapshot
        self._snapshot_provider = snapshot_provider
        self._setup_ui()

    def _setup_ui(self) -> None:
        title = self.tr("View Rule") if self._readonly else self.tr("Edit Rule")
        self.setWindowTitle(title)

        layout = QVBoxLayout(self)
        layout.setSpacing(SPACE_MD)

        # ── 只读提示 ──
        if self._readonly:
            hint = (
                self.tr("Official preset rule, view-only")
                if self._source == "pip"
                else self.tr("Remote rule, view-only")
            )
            note = QLabel(f"🔒 {hint}")
            note.setStyleSheet(
                f"color: {get_theme_manager().get('disabled')}; font-weight: bold; padding: {SPACE_XS}px 0;"
            )
            layout.addWidget(note)

        # ── 元数据表单 ──
        form = QFormLayout()
        form.setHorizontalSpacing(SPACE_LG)

        self._name_edit = QLineEdit(self._data.get("name", ""))
        self._name_edit.setMaxLength(QLINEEDIT_MAX)
        self._name_edit.setReadOnly(self._readonly)
        form.addRow(self.tr("Name:"), self._name_edit)

        self._name_error = QLabel("")
        self._name_error.setStyleSheet(f"color: {get_theme_manager().get('danger')}; font-size: {FONT_MD}px;")
        self._name_error.hide()
        form.addRow("", self._name_error)

        self._display_edit = QLineEdit(self._data.get("display_name", ""))
        self._display_edit.setMaxLength(QLINEEDIT_MAX)
        self._display_edit.setReadOnly(self._readonly)
        form.addRow(self.tr("Display Name:"), self._display_edit)

        self._desc_edit = QLineEdit(self._data.get("description", ""))
        self._desc_edit.setMaxLength(QLINEEDIT_MAX)
        self._desc_edit.setReadOnly(self._readonly)
        form.addRow(self.tr("Description:"), self._desc_edit)

        tags = ", ".join(self._data.get("tags", [])) if self._data.get("tags") else ""
        self._tags_edit = QLineEdit(tags)
        self._tags_edit.setMaxLength(QLINEEDIT_MAX)
        self._tags_edit.setReadOnly(self._readonly)
        self._tags_edit.setPlaceholderText(self.tr("Comma-separated, e.g.: e-commerce, product-page"))
        form.addRow(self.tr("Tags:"), self._tags_edit)

        self._enabled_cb = QCheckBox(self.tr("Enabled"))
        self._enabled_cb.setChecked(self._data.get("enabled", True))
        self._enabled_cb.setEnabled(not self._readonly)
        form.addRow(self.tr("Status:"), self._enabled_cb)

        version = self._data.get("version", 1)
        fields = self._data.get("fields", {})
        match = self._data.get("match", {})
        domains = ", ".join(match.get("domains", [])) or self.tr("(generic)")
        info_text = self.tr("Version: {v} · Fields: {n} · Domains: {d}").format(v=version, n=len(fields), d=domains)
        info_label = QLabel(info_text)
        info_label.setStyleSheet(f"color: {get_theme_manager().get('disabled')}; padding: {SPACE_XS}px 0;")
        form.addRow(info_label)

        layout.addLayout(form)

        # ── 字段详情表 (纵向响应式) ──
        self._field_names = list(fields.keys())
        if fields:
            fields_group = QGroupBox(self.tr("Fields ({0})").format(len(fields)))
            fields_group_layout = QVBoxLayout(fields_group)
            self._fields_table = QTableWidget()
            self._fields_table.setColumnCount(4)
            self._fields_table.setHorizontalHeaderLabels(
                [self.tr("Field Name"), self.tr("Selector"), self.tr("Extract"), self.tr("Multiple")]
            )
            self._fields_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
            self._fields_table.setColumnWidth(0, 100)
            self._fields_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
            self._fields_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Interactive)
            self._fields_table.setColumnWidth(2, 70)
            self._fields_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Interactive)
            self._fields_table.setColumnWidth(3, 50)
            if self._readonly:
                self._fields_table.setEditTriggers(QTableWidget.NoEditTriggers)
            else:
                self._fields_table.setEditTriggers(QTableWidget.DoubleClicked | QTableWidget.EditKeyPressed)
            self._fields_table.setAlternatingRowColors(True)
            self._fields_table.setRowCount(len(fields))
            for i, (fname, fcfg) in enumerate(fields.items()):
                selector = fcfg.get("selector", "") or ""
                extract = fcfg.get("extract", "text")
                multiple = fcfg.get("multiple", False)

                self._fields_table.setItem(i, 0, QTableWidgetItem(fname))

                self._fields_table.setItem(i, 1, QTableWidgetItem(selector))
                item_ext = QTableWidgetItem(extract)
                item_ext.setTextAlignment(Qt.AlignCenter)
                self._fields_table.setItem(i, 2, item_ext)

                cb = QCheckBox()
                cb.setChecked(multiple)
                if self._readonly:
                    cb.setEnabled(False)
                self._fields_table.setCellWidget(i, 3, centered_checkbox_container(cb))
            fields_group_layout.addWidget(self._fields_table)
            layout.addWidget(fields_group, 1)  # stretch=1 — 纵向响应式
        else:
            self._fields_table = None
            self._field_names = []

        # ── 匹配 & 选项 ──
        detail_layout = QFormLayout()
        detail_layout.setHorizontalSpacing(SPACE_LG)

        scope = match.get("scope", "domain_pattern")
        url_pattern = match.get("url_pattern", "")
        scope_labels = {
            "domain_pattern": self.tr("Domain + Path Pattern"),
            "domain_all": self.tr("All Paths Under Domain"),
            "global_pattern": self.tr("Any Domain + Path Pattern"),
            "any": self.tr("Any Domain + Any Path"),
        }
        detail_layout.addRow(self.tr("Match Scope:"), QLabel(scope_labels.get(scope, scope)))
        detail_layout.addRow(self.tr("URL Pattern:"), QLabel(url_pattern or self.tr("(none)")))

        options = self._data.get("options", {})
        keep_body = self.tr("Yes") if options.get("keep_body_text") else self.tr("No")
        follow_links = self.tr("Yes") if options.get("follow_links") else self.tr("No")
        detail_layout.addRow(self.tr("Keep Body:"), QLabel(keep_body))
        detail_layout.addRow(self.tr("Extract Links:"), QLabel(follow_links))

        test_urls = self._data.get("test_urls", [])
        if test_urls:
            urls = [u if isinstance(u, str) else u.get("url", str(u)) for u in test_urls[:5]]
            detail_layout.addRow(self.tr("Test URL:"), QLabel("\n".join(urls)))

        if url_pattern or options or test_urls:
            detail_group = QGroupBox(self.tr("Match Rules & Options"))
            detail_group.setLayout(detail_layout)
            layout.addWidget(detail_group)

        # ── 按钮 ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(SPACE_MD)
        preview_btn = QPushButton(self.tr("Preview"))
        preview_btn.clicked.connect(self._open_preview)
        btn_row.addWidget(preview_btn, 1)
        if not self._readonly:
            apply_btn = QPushButton(self.tr("Apply"))
            apply_btn.clicked.connect(self._apply)
            btn_row.addWidget(apply_btn, 1)
        close_btn = QPushButton(self.tr("Cancel") if not self._readonly else self.tr("Close"))
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn, 1)
        if not self._readonly:
            confirm_btn = QPushButton(self.tr("OK"))
            confirm_btn.clicked.connect(self._confirm)
            btn_row.addWidget(confirm_btn, 1)
        layout.addLayout(btn_row)

        self.adjustSize()
        ideal_h = self.height()
        self.setMaximumWidth(self.width())
        screen = self.screen()
        if screen:
            max_h = int(screen.availableGeometry().height() * 0.85)
            self.setMaximumHeight(min(ideal_h, max_h))
            self.setMinimumHeight(min(ideal_h, max_h))
        else:
            self.setMaximumHeight(ideal_h)
            self.setMinimumHeight(ideal_h)
        self.setMinimumWidth(self.width())

    def _collect_fields(self) -> dict:
        """从字段表格收集当前字段配置。"""
        if self._fields_table is None:
            result: dict = self._data.get("fields", {})
            return result
        result = {}
        for i in range(self._fields_table.rowCount()):
            fname = self._fields_table.item(i, 0).text().strip()
            if not fname:
                continue
            selector = self._fields_table.item(i, 1).text().strip() if self._fields_table.item(i, 1) else ""
            extract = self._fields_table.item(i, 2).text().strip() if self._fields_table.item(i, 2) else "text"
            cb_widget = self._fields_table.cellWidget(i, 3)
            multiple = False
            if cb_widget:
                cb = cb_widget.findChild(QCheckBox)
                if cb:
                    multiple = cb.isChecked()
            entry = {"selector": selector or None, "extract": extract}
            if multiple:
                entry["multiple"] = True
            # 保留原有其他字段 (fallback, transform 等)
            old = self._data.get("fields", {}).get(fname, {})
            for k in ("fallback", "transform", "attr"):
                if k in old:
                    entry[k] = old[k]
            result[fname] = entry
        return result

    def _save_to_file(self) -> None:
        name = self._name_edit.text().strip()
        if not name:
            self._name_error.setText(self.tr("Name cannot be empty"))
            self._name_error.show()
            return
        self._name_error.hide()
        self._data["name"] = name
        self._data["display_name"] = self._display_edit.text().strip()
        self._data["description"] = self._desc_edit.text().strip()
        tags = [t.strip() for t in self._tags_edit.text().split(",") if t.strip()]
        self._data["tags"] = tags
        self._data["enabled"] = self._enabled_cb.isChecked()
        self._data["fields"] = self._collect_fields()

        path = self._rule_path or (Path.home() / ".astrocrawl" / "rules" / f"{name}.json")
        safe_write_rule_file(path, self._data)

    def _apply(self) -> None:
        self._save_to_file()

    def _confirm(self) -> None:
        self._save_to_file()
        self.accept()

    def _open_preview(self) -> None:
        from astrocrawl.gui._preview_panel import PreviewPanel

        rule_name = self._data.get("name", "")
        test_urls = self._data.get("test_urls", [])
        test_url = ""
        if test_urls:
            first = test_urls[0]
            test_url = first.get("url", "") if isinstance(first, dict) else str(first)
        snap = self._snapshot_provider.get_snapshot() if self._snapshot_provider else self._snapshot
        PreviewPanel.open(snap, rule_name, test_url)


# ═══════════════════════════════════════════════════════════════════════
# 规则验证结果对话框
# ═══════════════════════════════════════════════════════════════════════


class _ValidationResultDialog(QDialog):
    def __init__(self, results: list, parent=None):
        super().__init__(parent)
        self.setWindowModality(Qt.WindowModal)
        self.setWindowTitle(self.tr("Rule Validation Results"))
        self._setup_ui(results)
        self.adjustSize()
        ideal_h = self.height()
        self.setMaximumWidth(self.width())
        screen = self.screen()
        if screen:
            max_h = int(screen.availableGeometry().height() * 0.85)
            self.setMaximumHeight(min(ideal_h, max_h))
            self.setMinimumHeight(min(ideal_h, max_h))
        else:
            self.setMaximumHeight(ideal_h)
            self.setMinimumHeight(ideal_h)
        self.setMinimumWidth(self.width())

    def _setup_ui(self, results: list) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(SPACE_MD)

        theme = get_theme_manager()
        sc = theme.get("success")
        dc = theme.get("danger")
        ic = theme.get("disabled")

        passed = sum(1 for r in results if r.get("status") == "pass")
        failed = sum(1 for r in results if r.get("status") == "fail")
        skipped = sum(1 for r in results if r.get("status") == "skip")

        summary = QLabel(
            self.tr("Total: {0}  |  Passed: {1}  |  Failed: {2}  |  Skipped: {3}").format(
                len(results), passed, failed, skipped
            )
        )
        summary.setStyleSheet(f"font-weight: bold; padding: {SPACE_XS}px 0;")
        layout.addWidget(summary)

        table = QTableWidget()
        table.setColumnCount(4)
        table.setHorizontalHeaderLabels([self.tr("Name"), self.tr("Source"), self.tr("Status"), self.tr("Error Info")])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        table.setColumnWidth(0, 100)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Interactive)
        table.setColumnWidth(1, 60)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Interactive)
        table.setColumnWidth(2, 50)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setRowCount(len(results))

        for i, r in enumerate(results):
            status = r.get("status", "fail")
            name = r.get("name", Path(r.get("path", "")).stem)
            source = r.get("source", "")
            error = r.get("error", "")

            table.setItem(i, 0, QTableWidgetItem(name))
            table.setItem(i, 1, QTableWidgetItem(source))

            if status == "pass":
                status_text = self.tr("✓ Pass")
                color = sc
            elif status == "skip":
                status_text = self.tr("— Skip")
                color = ic
            else:
                status_text = self.tr("✗ Fail")
                color = dc

            status_item = QTableWidgetItem(status_text)
            status_item.setTextAlignment(Qt.AlignCenter)
            status_item.setForeground(QColor(color))
            table.setItem(i, 2, status_item)

            table.setItem(i, 3, QTableWidgetItem(error))

        layout.addWidget(table, 1)

        close_btn = QPushButton(self.tr("Close"))
        close_btn.clicked.connect(self.accept)
        btn_layout = QHBoxLayout()
        btn_layout.addWidget(close_btn, 1)
        layout.addLayout(btn_layout)


# ═══════════════════════════════════════════════════════════════════════
# Tab 1: 规则列表
# ═══════════════════════════════════════════════════════════════════════


class _RuleListModel(QAbstractTableModel):
    """规则列表 Model — 7 列：名称/显示名/标签/来源/版本/状态/启用。"""

    def __init__(self, lifecycle, parent=None):
        super().__init__(parent)
        self._lifecycle = lifecycle
        self._rules: list = []
        self._source_map: dict[str, str] = {}
        self._toggle_overrides: dict[str, bool] = {}

    def load(self) -> None:
        self.beginResetModel()
        self._toggle_overrides.clear()
        snap = self._lifecycle.get_snapshot()
        all_rules = list(snap.by_name.values()) if snap and snap.by_name else []
        self._rules = [r for r in all_rules if getattr(r, "name", "") != DEFAULT_EXTRACTION_TYPE]
        self._source_map = {
            getattr(r, "name", ""): (snap.get_source(getattr(r, "name", "")) or "pip") for r in self._rules
        }
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rules)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else 7

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row, col = index.row(), index.column()
        rule = self._rules[row]
        name = getattr(rule, "name", "")

        if role == Qt.DisplayRole:
            if col == 0:
                return str(name)
            elif col == 1:
                return str(getattr(rule, "display_name", ""))
            elif col == 2:
                tags = getattr(rule, "tags", []) or []
                return ", ".join(tags[:3])
            elif col == 3:
                src = self._source_map.get(name, "pip")
                return {
                    "pip": self.tr("Official Preset"),
                    "remote": self.tr("Remote Source"),
                    "user": self.tr("User Custom"),
                }.get(src, src)
            elif col == 4:
                return str(getattr(rule, "version", ""))
            elif col == 5:
                return self.tr("Enabled") if getattr(rule, "enabled", True) else self.tr("Disabled")
            return None

        if role == Qt.UserRole:
            if col == 3:
                return self._source_map.get(name, "pip")
            if col == 5:
                return "enabled" if getattr(rule, "enabled", True) else "disabled"
            return None

        if role == Qt.CheckStateRole and col == 6:
            rule_name = str(getattr(rule, "name", ""))
            if rule_name in self._toggle_overrides:
                return Qt.Checked.value if self._toggle_overrides[rule_name] else Qt.Unchecked.value
            return Qt.Checked.value if getattr(rule, "enabled", True) else Qt.Unchecked.value
        if role == Qt.TextAlignmentRole and col in (3, 4, 5):
            return int(Qt.AlignCenter)

        return None

    def flags(self, index):
        flags = super().flags(index)
        if index.column() == 6:
            rule = self._rules[index.row()]
            if getattr(rule, "name", "") != DEFAULT_EXTRACTION_TYPE:
                flags |= Qt.ItemIsUserCheckable
        return flags

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return [
                self.tr("Name"),
                self.tr("Display Name"),
                self.tr("Tags"),
                self.tr("Source"),
                self.tr("Version"),
                self.tr("Status"),
                self.tr("Enabled"),
            ][section]
        return None

    def setData(self, index, value, role=Qt.EditRole):
        if role == Qt.CheckStateRole and index.column() == 6:
            name = self.get_name(index.row())
            if name and name != DEFAULT_EXTRACTION_TYPE:
                self._toggle_overrides[name] = value == Qt.Checked.value
                self.dataChanged.emit(index, index, [Qt.CheckStateRole])
                return True
        return False

    def get_rule(self, row):
        if 0 <= row < len(self._rules):
            return self._rules[row]
        return None

    def get_name(self, row):
        r = self.get_rule(row)
        return getattr(r, "name", "") if r else ""


class _ValidateAllWorker(QThread):
    """后台线程：扫描全部规则目录并校验。"""

    finished = Signal(list)

    def __init__(self, cfg, extra_rules_dirs=None, parent=None):
        super().__init__(parent)
        self._cfg = cfg
        self._extra_rules_dirs = extra_rules_dirs
        self._cancel_event = threading.Event()

    def run(self) -> None:
        if self.isInterruptionRequested():
            return
        from astrocrawl.rules import validate_rule_files

        results = validate_rule_files(
            self._cfg,
            extra_rules_dirs=self._extra_rules_dirs,
            cancel_event=self._cancel_event,
        )
        if self.isInterruptionRequested() or self._cancel_event.is_set():
            return
        self.finished.emit(results)

    def cancel(self) -> None:
        self.requestInterruption()
        self._cancel_event.set()


class _RuleTablePage(QWidget):
    busy_changed = Signal(bool)

    def __init__(self, cfg: CrawlerConfig, status_callback=None, parent=None):
        super().__init__(parent)
        self._cfg = cfg
        self._lifecycle: Optional[RuleLifecycle] = None
        self._pending_toggles: dict[str, bool] = {}
        self._validate_worker: Optional[_ValidateAllWorker] = None
        self._theme_mgr = get_theme_manager()
        self._show_status = status_callback or (lambda msg, level="success": None)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, SPACE_MD, 0, 0)
        layout.setSpacing(SPACE_SM)

        # ── 操作按钮行 + 搜索行 (8 列网格精确对齐) ──
        grid = QGridLayout()
        self._reload_btn = QPushButton(self.tr("Hot Reload"))
        self._import_btn = QPushButton(self.tr("Import"))
        self._export_btn = QPushButton(self.tr("Export"))
        self._export_all_btn = QPushButton(self.tr("Export All"))
        self._new_btn = QPushButton(self.tr("New"))
        self._edit_btn = QPushButton(self.tr("Edit"))
        self._delete_btn = QPushButton(self.tr("Delete"))
        self._reset_btn = QPushButton(self.tr("Reset"))
        buttons = (
            self._reload_btn,
            self._import_btn,
            self._export_btn,
            self._export_all_btn,
            self._new_btn,
            self._edit_btn,
            self._delete_btn,
            self._reset_btn,
        )
        for col, b in enumerate(buttons):
            grid.addWidget(b, 0, col)
            grid.setColumnStretch(col, 1)

        self._search_input = QLineEdit()
        self._search_input.setMaxLength(QLINEEDIT_MAX)
        self._search_input.setObjectName("search-input")
        self._search_input.setPlaceholderText(self.tr("Search by rule name, display name, or tags..."))
        self._search_input.setClearButtonEnabled(True)
        self._search_input.textChanged.connect(self._apply_filter)
        grid.addWidget(self._search_input, 1, 0, 1, 3)

        preview_btn = QPushButton(self.tr("Preview"))
        preview_btn.setObjectName("toolbar-preview-btn")
        preview_btn.clicked.connect(self._open_preview)
        grid.addWidget(preview_btn, 1, 3)
        grid.setColumnStretch(3, 1)

        select_all_btn = QPushButton(self.tr("Select All"))
        select_all_btn.setToolTip(self.tr("Enable all currently visible rules"))
        select_all_btn.clicked.connect(self._on_select_all)
        grid.addWidget(select_all_btn, 1, 4)

        deselect_all_btn = QPushButton(self.tr("Deselect All"))
        deselect_all_btn.setToolTip(self.tr("Disable all currently visible rules (except default)"))
        deselect_all_btn.clicked.connect(self._on_deselect_all)
        grid.addWidget(deselect_all_btn, 1, 5)

        self._validate_btn = QPushButton(self.tr("Validate One"))
        self._validate_btn.setToolTip(self.tr("Reload and validate the selected rule from disk"))
        self._validate_btn.clicked.connect(self._on_validate)
        grid.addWidget(self._validate_btn, 1, 6)

        self._validate_all_btn = QPushButton(self.tr("Validate All"))
        self._validate_all_btn.setToolTip(self.tr("Scan all rule directories and detect corrupt files"))
        self._validate_all_btn.clicked.connect(self._on_validate_all)
        grid.addWidget(self._validate_all_btn, 1, 7)

        layout.addLayout(grid)

        # ── 额外规则目录（可折叠） ──
        dirs_section = QVBoxLayout()
        dirs_section.setSpacing(SPACE_SM)

        header = QHBoxLayout()
        header.setSpacing(SPACE_SM)

        self._collapse_btn = QToolButton()
        self._collapse_btn.setObjectName("collapse-btn")
        self._collapse_btn.setArrowType(Qt.RightArrow)
        self._collapse_btn.setAutoRaise(True)
        self._collapse_btn.setCheckable(True)
        collapsed = get_preferences().get_rules_dirs_collapsed()
        self._collapse_btn.setChecked(collapsed)
        self._collapse_btn.toggled.connect(self._on_collapse_toggled)
        header.addWidget(self._collapse_btn)

        header.addWidget(QLabel(self.tr("Extra Rules Directories")))
        header.addStretch()

        self._enable_cb = QCheckBox(self.tr("Enabled"))
        enabled = get_preferences().get_rules_dirs_enabled()
        self._enable_cb.setChecked(enabled)
        self._enable_cb.toggled.connect(self._on_rules_dirs_enabled_toggled)
        header.addWidget(self._enable_cb)

        dirs_section.addLayout(header)

        self._rules_dirs_content = QWidget()
        content_layout = QVBoxLayout(self._rules_dirs_content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(SPACE_SM)
        self._rules_dirs_list = QListWidget()
        content_layout.addWidget(self._rules_dirs_list)
        dirs_btn_row = QHBoxLayout()
        self._add_dir_btn = QPushButton(self.tr("Add Directory"))
        self._add_dir_btn.clicked.connect(self._on_add_rules_dir)
        self._remove_dir_btn = QPushButton(self.tr("Remove"))
        self._remove_dir_btn.clicked.connect(self._on_remove_rules_dir)
        dirs_btn_row.addWidget(self._add_dir_btn, 1)
        dirs_btn_row.addWidget(self._remove_dir_btn, 1)
        content_layout.addLayout(dirs_btn_row)

        self._refresh_rules_dirs()
        self._update_rules_dirs_enabled_ui(enabled)
        self._rules_dirs_content.setVisible(not collapsed)
        dirs_section.addWidget(self._rules_dirs_content)
        layout.addLayout(dirs_section)

        # ── 表格 ──
        self._table = create_managed_table(object_name="rule-table")
        self._table.doubleClicked.connect(self._on_edit_rule)
        layout.addWidget(self._table, 1)

        self._empty_label = QLabel(self.tr("No rules"))
        self._empty_label.setAlignment(Qt.AlignCenter)
        self._empty_label.setStyleSheet(f"color: {self._theme_mgr.get('disabled')}; padding: {SPACE_LG}px;")
        self._empty_label.setVisible(False)
        layout.addWidget(self._empty_label)

        # CheckboxDelegate on col 6 (启用)
        self._cb_delegate = CheckboxDelegate(self)
        self._cb_delegate.toggled.connect(self._on_checkbox_toggled)
        self._table.setItemDelegateForColumn(6, self._cb_delegate)

        # 信号
        if self._theme_mgr is not None:
            self._theme_mgr.theme_changed.connect(self._apply_theme)

        self._reload_btn.clicked.connect(self._on_reload)
        self._import_btn.clicked.connect(self._on_import)
        self._export_btn.clicked.connect(self._on_export)
        self._export_all_btn.clicked.connect(self._on_export_all)
        self._new_btn.clicked.connect(self._on_new_rule)
        self._edit_btn.clicked.connect(self._on_edit_btn)
        self._delete_btn.clicked.connect(self._on_delete)
        self._reset_btn.clicked.connect(self._on_reset)

    def init_lifecycle(self) -> None:
        self._lifecycle = self._create_lifecycle()
        self._lifecycle.initial_load()
        self._rebuild_model()

    def _create_lifecycle(self) -> RuleLifecycle:
        """Create lifecycle reading dirs/enabled from Preferences (SSOT)."""
        prefs = get_preferences()
        return RuleLifecycle(
            self._cfg,
            extra_rules_dirs=prefs.get_rules_dirs(),
            rules_dirs_enabled=prefs.get_rules_dirs_enabled(),
        )

    def _reload(self) -> None:
        if self._lifecycle:
            self._lifecycle.reload()

    def _rebuild_model(self) -> None:
        """Rebuild table model from current lifecycle snapshot. No disk I/O."""
        model = _RuleListModel(self._lifecycle)
        model.load()
        model._toggle_overrides = dict(self._pending_toggles)

        proxy = _FilterProxy((0, 1, 2), self)
        proxy.setSourceModel(model)
        self._table.setModel(proxy)
        configure_table_header(
            self._table,
            [
                ColumnDef(key="name", label=self.tr("Name")),
                ColumnDef(key="display", label=self.tr("Display Name")),
                ColumnDef(key="tags", label=self.tr("Tags")),
                ColumnDef(key="source", label=self.tr("Source"), resize="fixed", width=80),
                ColumnDef(key="version", label=self.tr("Version"), resize="fixed", width=50),
                ColumnDef(key="status", label=self.tr("Status"), resize="fixed", width=60),
                ColumnDef(key="enabled", label=self.tr("Enabled"), resize="fixed", width=50),
            ],
        )
        # StatusColorDelegate on col 5 (状态)
        self._status_delegate = StatusColorDelegate({"enabled": "success", "disabled": "disabled"})
        self._table.setItemDelegateForColumn(5, self._status_delegate)
        has_rules = model.rowCount() > 0
        self._table.setVisible(has_rules)
        self._empty_label.setVisible(not has_rules)
        self._apply_filter()

    def refresh(self) -> None:
        """从磁盘重载规则快照并重建表格模型。"""
        if self._lifecycle:
            self._lifecycle.reload()
        self._rebuild_model()

    def _apply_filter(self) -> None:
        from PySide6.QtCore import QRegularExpression

        proxy = self._table.model()
        if proxy is None:
            return
        keyword = self._search_input.text().strip()
        if not keyword:
            proxy.setFilterRegularExpression(QRegularExpression())
        else:
            # 直接使用 keyword 构建子串匹配 pattern（不用 escape，避免 Unicode 被误转义）
            proxy.setFilterRegularExpression(
                QRegularExpression(keyword, QRegularExpression.PatternOption.CaseInsensitiveOption)
            )

    def _apply_theme(self) -> None:
        """主题变更时刷新模型触发重绘。"""
        self._rebuild_model()

    @property
    def rowCount(self) -> int:
        proxy = self._table.model()
        return proxy.sourceModel().rowCount() if proxy else 0

    def rule_name(self, row: int) -> str:
        proxy = self._table.model()
        return proxy.sourceModel().get_name(row) if proxy else ""

    def _selected_name(self) -> str | None:
        idx = self._table.currentIndex()
        if not idx.isValid():
            return None
        proxy = self._table.model()
        if proxy is None:
            return None
        src_idx = proxy.mapToSource(idx)
        result: str = proxy.sourceModel().get_name(src_idx.row())
        return result

    def get_snapshot(self) -> Any | None:
        if self._lifecycle is None:
            return None
        self._lifecycle.reload()
        return self._lifecycle.get_snapshot()

    def _open_preview(self) -> None:
        from astrocrawl.gui._preview_panel import PreviewPanel

        snap = self._lifecycle.get_snapshot() if self._lifecycle else None
        PreviewPanel.open(snap)

    def _on_new_rule(self) -> None:
        dlg = RuleEditDialog({}, "user", rule_path=None, parent=self, snapshot_provider=self)
        dlg.exec()
        self.refresh()

    def _on_validate(self) -> None:
        from astrocrawl.rules import load_rule_file

        name = self._selected_name()
        if not name:
            QMessageBox.information(self, self.tr("Validate Rule"), self.tr("Please select a rule in the table first."))
            return
        snap = self._lifecycle.get_snapshot() if self._lifecycle else None
        if snap is None:
            QMessageBox.information(self, self.tr("Validate Rule"), self.tr("Rule snapshot is not initialized."))
            return
        rule_path = snap.get_path(name)
        if rule_path is None:
            QMessageBox.information(self, self.tr("Validate Rule"), self.tr("Rule file not found: {0}").format(name))
            return
        source = snap.get_source(name) or "pip"
        try:
            rule = load_rule_file(rule_path, source)
            if rule is not None:
                self._show_status(self.tr("Rule '{0}' validated ({1} fields)").format(name, len(rule.fields)))
            else:
                self._show_status(self.tr("Rule '{0}' validation failed: invalid rule file").format(name), "error")
        except (ValueError, json.JSONDecodeError) as e:
            self._show_status(self.tr("Rule '{0}' validation failed: {1}").format(name, e), "error")

    def _on_validate_all(self) -> None:
        if self._validate_worker is not None:
            return
        self._validate_all_btn.setEnabled(False)
        self._show_status(self.tr("Validating all rules..."))
        self.busy_changed.emit(True)
        self._validate_worker = _ValidateAllWorker(
            self._cfg,
            extra_rules_dirs=get_preferences().get_rules_dirs(),
            parent=self,
        )
        self._validate_worker.finished.connect(self._on_validate_all_done)
        self._validate_worker.start()

    def _on_validate_all_done(self, results: list) -> None:
        self._validate_worker = None
        self.busy_changed.emit(False)
        self._validate_all_btn.setEnabled(True)
        self._show_status(self.tr("Validation complete ({0} files)").format(len(results)))
        _ValidationResultDialog(results, self).exec()

    def _cleanup_worker(self) -> None:
        w = self._validate_worker
        if w is None:
            return
        if not w.isRunning():
            self._validate_worker = None
            self.busy_changed.emit(False)
            self._validate_all_btn.setEnabled(True)
            return
        try:
            w.finished.disconnect()
        except RuntimeError:
            pass
        w.cancel()
        if not w.wait(5000):
            w.terminate()
            w.wait(2000)
        self._validate_worker = None
        self.busy_changed.emit(False)
        self._validate_all_btn.setEnabled(True)

    def _on_collapse_toggled(self, collapsed: bool) -> None:
        self._collapse_btn.setArrowType(Qt.RightArrow if collapsed else Qt.DownArrow)
        self._rules_dirs_content.setVisible(not collapsed)
        get_preferences().set_rules_dirs_collapsed(collapsed)
        # 更新父对话框尺寸锁
        win = self.window()
        if isinstance(win, QDialog):
            win.adjustSize()
            win.setMaximumHeight(win.height())
            win.setMinimumSize(win.width(), win.height())

    def _refresh_rules_dirs(self) -> None:
        self._rules_dirs_list.clear()
        for d in get_preferences().get_rules_dirs():
            self._rules_dirs_list.addItem(d)

    def _on_rules_dirs_enabled_toggled(self, checked: bool) -> None:
        get_preferences().set_rules_dirs_enabled(checked)
        self._update_rules_dirs_enabled_ui(checked)
        self._lifecycle = self._create_lifecycle()
        self._lifecycle.initial_load()
        self._rebuild_model()

    def _update_rules_dirs_enabled_ui(self, enabled: bool) -> None:
        self._add_dir_btn.setEnabled(enabled)
        self._remove_dir_btn.setEnabled(enabled)
        self._rules_dirs_list.setEnabled(enabled)

    def _on_add_rules_dir(self) -> None:
        from astrocrawl.utils.preferences import clear_qt_file_dialog_history, get_preferences

        pm = get_preferences()
        default_dir = pm.get_last_dir("rules_dir", str(Path.home()))
        path = QFileDialog.getExistingDirectory(self, self.tr("Select Rules Directory"), default_dir)
        clear_qt_file_dialog_history()
        if path:
            dirs = pm.get_rules_dirs()
            dirs.append(path)
            pm.set_rules_dirs(dirs)
            pm.add_path("rules_dir", path)
            self._refresh_rules_dirs()
            self._lifecycle = self._create_lifecycle()
            self._lifecycle.initial_load()
            self._rebuild_model()
            self._show_status(self.tr("Rules directory added: {0}").format(path))

    def _on_remove_rules_dir(self) -> None:
        current = self._rules_dirs_list.currentItem()
        if current is None:
            QMessageBox.information(self, self.tr("Remove Directory"), self.tr("Please select a directory first."))
            return
        path = current.text()
        pm = get_preferences()
        dirs = pm.get_rules_dirs()
        dirs.remove(path)
        pm.set_rules_dirs(dirs)
        self._refresh_rules_dirs()
        self._lifecycle = self._create_lifecycle()
        self._lifecycle.initial_load()
        self._rebuild_model()
        self._show_status(self.tr("Rules directory removed: {0}").format(path))

    def _on_checkbox_toggled(self, row: int, checked: bool) -> None:
        proxy = self._table.model()
        if proxy is None:
            return
        proxy_idx = proxy.index(row, 6)
        src_idx = proxy.mapToSource(proxy_idx)
        name = proxy.sourceModel().data(proxy.sourceModel().index(src_idx.row(), 0), Qt.DisplayRole)
        if not name or name == DEFAULT_EXTRACTION_TYPE:
            return
        self._pending_toggles[str(name)] = checked

    def _on_select_all(self) -> None:
        proxy = self._table.model()
        if proxy is None:
            return
        model = proxy.sourceModel()
        for proxy_row in range(proxy.rowCount()):
            src_idx = proxy.mapToSource(proxy.index(proxy_row, 0))
            name = model.get_name(src_idx.row())
            if name and name != DEFAULT_EXTRACTION_TYPE:
                self._pending_toggles[name] = True
        self._rebuild_model()

    def _on_deselect_all(self) -> None:
        proxy = self._table.model()
        if proxy is None:
            return
        model = proxy.sourceModel()
        for proxy_row in range(proxy.rowCount()):
            src_idx = proxy.mapToSource(proxy.index(proxy_row, 0))
            name = model.get_name(src_idx.row())
            if name and name != DEFAULT_EXTRACTION_TYPE:
                self._pending_toggles[name] = False
        self._rebuild_model()

    # ── 暂存提交 ──

    def apply_pending(self) -> None:
        """将暂存的启用/禁用更改写入 rules_state.json 并重载。"""
        if not self._pending_toggles:
            return
        for name, enabled in self._pending_toggles.items():
            set_rule_enabled(name, enabled)
        self._pending_toggles.clear()
        self.refresh()

    def discard_pending(self) -> None:
        """丢弃所有暂存的启用/禁用更改。"""
        self._pending_toggles.clear()

    def _on_edit_btn(self) -> None:
        name = self._selected_name()
        if not name:
            QMessageBox.information(self, self.tr("Edit"), self.tr("Please select a rule first."))
            return
        self._edit_rule_at(self._table.currentIndex())

    def _on_edit_rule(self, index) -> None:
        self._edit_rule_at(index)

    def _edit_rule_at(self, idx) -> None:
        proxy = self._table.model()
        if proxy is None:
            return
        src_idx = proxy.mapToSource(idx) if hasattr(proxy, "mapToSource") else idx
        name = proxy.sourceModel().get_name(src_idx.row())
        if not name:
            return
        snap = self._lifecycle.get_snapshot() if self._lifecycle else None
        rule = snap.by_name.get(name) if snap else None
        if rule is None:
            rule_data: dict = {}
        elif isinstance(rule, dict):
            rule_data = rule
        else:
            from astrocrawl.rules import rule_to_dict

            rule_data = rule_to_dict(rule)
        source = snap.get_source(name) or "pip" if snap else "pip"
        rule_path = snap.get_path(name) if snap else None
        dlg = RuleEditDialog(rule_data, source, rule_path, self, snapshot_provider=self)
        dlg.exec()
        self.refresh()

    def _on_reload(self) -> None:
        self.refresh()
        self._show_status(self.tr("Rule snapshot updated"))

    def _on_import(self) -> None:
        pm = get_preferences()
        start_dir = pm.get_last_dir("rule_import", str(Path.home()))
        path, _ = QFileDialog.getOpenFileName(self, self.tr("Import Rule"), start_dir, self.tr("JSON files (*.json)"))
        clear_qt_file_dialog_history()
        if not path:
            return
        pm.add_path("rule_import", str(Path(path).parent))
        try:
            preview = import_rule_preview(Path(path))
            name = preview["name"]
            reply = QMessageBox(
                QMessageBox.Warning,
                self.tr("Import Preview"),
                self.tr(
                    "Rule Name: {name}\nDisplay Name: {display}\nFields: {n}\nDomains: {domains}\n\nConfirm import?"
                ).format(
                    name=name,
                    display=preview.get("display_name", ""),
                    n=preview["fields_count"],
                    domains=", ".join(preview.get("domains", [])) or self.tr("(generic)"),
                ),
                parent=self,
            )
            import_btn = reply.addButton(self.tr("Import"), QMessageBox.YesRole)
            reply.addButton(self.tr("Cancel"), QMessageBox.NoRole)
            reply.exec()
            if reply.clickedButton() != import_btn:
                return
            dest = Path.home() / ".astrocrawl" / "rules"
            dest.mkdir(parents=True, exist_ok=True)
            import_rule(Path(path), dest, overwrite=True)
            self.refresh()
            self._show_status(self.tr("Rule '{0}' imported").format(name))
        except (OSError, PermissionError) as e:
            QMessageBox.critical(self, self.tr("Import Failed"), self.tr("File operation failed:\n{0}").format(e))
        except Exception as e:
            self._show_status(self.tr("Import failed: {0}").format(e), "error")

    def _on_export(self) -> None:
        if self._lifecycle is None:
            return
        name = self._selected_name()
        if not name:
            QMessageBox.information(self, self.tr("Export"), self.tr("Please select a rule first."))
            return
        if name == DEFAULT_EXTRACTION_TYPE:
            QMessageBox.information(self, self.tr("Export"), self.tr("Default rule cannot be exported."))
            return
        snap = self._lifecycle.get_snapshot()
        rule = snap.by_name.get(name)
        if not rule:
            return
        pm = get_preferences()
        default_dir = pm.get_last_dir("rule_export", str(Path.home()))
        path, _ = QFileDialog.getSaveFileName(
            self, self.tr("Export Rule"), str(Path(default_dir) / f"{name}.json"), "JSON Files (*.json)"
        )
        clear_qt_file_dialog_history()
        if not path:
            return
        export_rule_to_file(rule, Path(path))
        pm.add_path("rule_export", str(Path(path).parent))
        self._show_status(self.tr("Rule exported to: {0}").format(path))

    def _on_export_all(self) -> None:
        if self._lifecycle is None:
            return
        pm = get_preferences()
        start_dir = pm.get_last_dir("rule_export", str(Path.home()))
        d = QFileDialog.getExistingDirectory(self, self.tr("Select Export Directory"), start_dir)
        clear_qt_file_dialog_history()
        if not d:
            return
        snap = self._lifecycle.get_snapshot()
        user_rules = [r for r in snap.by_name.values() if r.name != DEFAULT_EXTRACTION_TYPE]
        try:
            count = export_all_rules(user_rules, Path(d))
        except OSError as e:
            QMessageBox.critical(self, self.tr("Export Failed"), str(e))
            return
        pm.add_path("rule_export", d)
        self._show_status(self.tr("Exported {0} rules to: {1}").format(count, d))

    def _on_delete(self) -> None:
        name = self._selected_name()
        if not name:
            QMessageBox.information(self, self.tr("Delete"), self.tr("Please select a rule first."))
            return
        if name == DEFAULT_EXTRACTION_TYPE:
            QMessageBox.information(self, self.tr("Delete"), self.tr("Default rule cannot be deleted."))
            return
        snap = self._lifecycle.get_snapshot() if self._lifecycle else None
        source = snap.get_source(name) if snap else None
        if source != "user":
            QMessageBox.information(self, self.tr("Delete"), self.tr("Only user custom rules can be deleted."))
            return
        msg = QMessageBox(
            QMessageBox.Warning,
            self.tr("Confirm Delete"),
            self.tr("Delete rule '{0}'?").format(name),
            parent=self,
        )
        del_btn = msg.addButton(self.tr("Delete"), QMessageBox.YesRole)
        msg.addButton(self.tr("Cancel"), QMessageBox.NoRole)
        msg.exec()
        if msg.clickedButton() != del_btn:
            return
        rule_file = snap.get_path(name) if snap else None
        if rule_file and rule_file.exists():
            try:
                rule_file.unlink()
            except OSError as e:
                QMessageBox.critical(self, self.tr("Delete Failed"), str(e))
                return
        self.refresh()

    def _on_reset(self) -> None:
        msg = QMessageBox(
            QMessageBox.Warning,
            self.tr("Confirm Reset"),
            self.tr(
                "This will delete all user custom rules and restore presets.\nThis action cannot be undone. Continue?"
            ),
            parent=self,
        )
        reset_btn = msg.addButton(self.tr("Reset"), QMessageBox.YesRole)
        msg.addButton(self.tr("Cancel"), QMessageBox.NoRole)
        msg.exec()
        if msg.clickedButton() != reset_btn:
            return
        rules_dir = Path.home() / ".astrocrawl" / "rules"
        errors = []
        if rules_dir.is_dir():
            for f in rules_dir.glob("*.json"):
                try:
                    f.unlink()
                except OSError as e:
                    errors.append(f"{f.name}: {e}")
        self.refresh()
        if errors:
            QMessageBox.critical(self, self.tr("Partial Delete Failed"), "\n".join(errors[:5]))
        else:
            self._show_status(self.tr("Restored to preset rules"))


# ═══════════════════════════════════════════════════════════════════════
# ChatML 预览对话框 — Path A/B 共用
# ═══════════════════════════════════════════════════════════════════════


class ChatMLPreviewDialog(QDialog):
    confirmed = Signal()

    def __init__(self, chatml_text: str, token_count: int, is_internal: bool = False, parent=None):
        super().__init__(parent)
        self.setWindowModality(Qt.WindowModal)
        self._chatml_text = chatml_text
        title = self.tr("AI Generation Preview") if is_internal else self.tr("Prompt Preview")
        self.setWindowTitle(title)
        self.setSizeGripEnabled(True)

        layout = QVBoxLayout(self)
        layout.setSpacing(SPACE_MD)

        token_label = QLabel(self.tr("Token count: {0:,}").format(token_count))
        token_label.setStyleSheet(f"font-weight: bold; color: {get_theme_manager().get('disabled')};")
        layout.addWidget(token_label)

        text_edit = QTextEdit()
        text_edit.setPlainText(chatml_text)
        text_edit.setReadOnly(True)
        text_edit.setStyleSheet(monospace_style())
        layout.addWidget(text_edit, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(SPACE_MD)
        if is_internal:
            confirm_btn = QPushButton(self.tr("Confirm Send"))
            confirm_btn.clicked.connect(self._on_confirm)
            cancel_btn = QPushButton(self.tr("Cancel"))
            cancel_btn.clicked.connect(self.reject)
            btn_row.addWidget(cancel_btn, 1)
            btn_row.addWidget(confirm_btn, 1)
        else:
            self._copy_btn = QPushButton(self.tr("Copy Prompt"))
            self._copy_btn.clicked.connect(self._on_copy)
            close_btn = QPushButton(self.tr("Cancel"))
            close_btn.clicked.connect(self.reject)
            btn_row.addWidget(close_btn, 1)
            btn_row.addWidget(self._copy_btn, 1)
        layout.addLayout(btn_row)

        self.adjustSize()
        ideal_h = self.height()
        self.setMaximumWidth(self.width())
        screen = self.screen()
        if screen:
            max_h = int(screen.availableGeometry().height() * 0.85)
            self.setMaximumHeight(min(ideal_h, max_h))
            self.setMinimumHeight(min(ideal_h, max_h))
        else:
            self.setMaximumHeight(ideal_h)
            self.setMinimumHeight(ideal_h)
        self.setMinimumWidth(self.width())

    def _on_copy(self) -> None:
        QApplication.clipboard().setText(self._chatml_text)
        self._copy_btn.setText(self.tr("Copied ✓"))

        QTimer.singleShot(2000, lambda: self._copy_btn.setText(self.tr("Copy Prompt")))

    def _on_confirm(self) -> None:
        self.confirmed.emit()
        self.accept()


# ═══════════════════════════════════════════════════════════════════════
# Tab 2: 自定义 — 统一双路径 (Layout A)
# ═══════════════════════════════════════════════════════════════════════

_TIER_LABELS = {0: "Off (Cleaning)", 1: "Safe Clean (Recommended)", 2: "Strict Clean"}


class _CustomPage(QWidget):
    rule_generated = Signal(dict)
    busy_changed = Signal(bool)
    _AI_MODULE_KEY = "rules_generation"

    def __init__(self, status_callback=None, snapshot_provider: SnapshotProvider | None = None, parent=None):
        super().__init__(parent)
        self._worker: Optional[_AiWorker] = None
        self._last_token_count: int = 0
        self._snapshot_provider = snapshot_provider
        self._show_status = status_callback or (lambda msg, level="success": None)
        self._setup_ui()

    def _refresh_profile_combo(self) -> None:
        prefs = get_preferences()
        profiles = prefs.get_ai_profiles()
        self._ai_profile_combo.blockSignals(True)
        self._ai_profile_combo.clear()
        if not profiles:
            self._ai_profile_combo.addItem(self.tr("Not Configured"))
            self._ai_profile_combo.model().item(0).setEnabled(False)
            self._ai_profile_combo.model().item(0).setSelectable(False)
            self._ai_profile_combo.blockSignals(False)
            return
        for p in profiles:
            self._ai_profile_combo.addItem(p.name)
        last = prefs.get_last_ai_profile(self._AI_MODULE_KEY)
        if last:
            idx = self._ai_profile_combo.findText(last)
            if idx >= 0:
                self._ai_profile_combo.setCurrentIndex(idx)
                self._ai_profile_combo.blockSignals(False)
                return
        active_name = prefs.get_active_profile_name()
        if active_name:
            idx = self._ai_profile_combo.findText(active_name)
            if idx >= 0:
                self._ai_profile_combo.setCurrentIndex(idx)
        self._ai_profile_combo.blockSignals(False)

    def _on_profile_selected(self, name: str) -> None:
        if name and name != self.tr("Not Configured"):
            get_preferences().set_last_ai_profile(self._AI_MODULE_KEY, name)

    def _get_active_ai_profile(self):
        prefs = get_preferences()
        selected = self._ai_profile_combo.currentText()
        if selected:
            profile = prefs.get_ai_profile(selected)
            if profile is not None:
                return profile
        return next((p for p in prefs.get_ai_profiles() if p.enabled), None)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        scroll = create_form_scroll_area()

        inner = QWidget()
        inner.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, SPACE_MD, 0, 0)
        inner_layout.setSpacing(SPACE_MD)

        # ── QGroupBox: 网站信息 ──
        site_group = QGroupBox(self.tr("Site Info"))
        site_layout = QVBoxLayout(site_group)
        site_layout.setSpacing(SPACE_SM)

        url_row = QHBoxLayout()
        url_row.setSpacing(SPACE_SM)
        url_lbl = QLabel(self.tr("Site URL:"))
        url_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._site_url = QLineEdit()
        self._site_url.setMaxLength(QLINEEDIT_MAX)
        self._site_url.setPlaceholderText("https://example.com")
        url_row.addWidget(url_lbl, 1)
        url_row.addWidget(self._site_url, 3)
        site_layout.addLayout(url_row)

        fields_row = QHBoxLayout()
        fields_row.setSpacing(SPACE_SM)
        fields_lbl = QLabel(self.tr("Fields Needed:"))
        fields_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._field_requirements = QLineEdit()
        self._field_requirements.setMaxLength(QLINEEDIT_MAX)
        self._field_requirements.setPlaceholderText(self.tr("title, price, image..."))
        fields_row.addWidget(fields_lbl, 1)
        fields_row.addWidget(self._field_requirements, 3)
        site_layout.addLayout(fields_row)

        tier_row = QHBoxLayout()
        tier_row.setSpacing(SPACE_SM)
        tier_lbl = QLabel(self.tr("Clean Level:"))
        tier_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._tier_combo = QComboBox()
        for t, label in _TIER_LABELS.items():
            self._tier_combo.addItem(self.tr(label), t)
        self._tier_combo.setCurrentIndex(1)
        reset_btn = QPushButton(self.tr("Reset"))
        reset_btn.clicked.connect(self.reset)
        tier_row.addWidget(tier_lbl, 1)
        tier_row.addWidget(self._tier_combo, 2)
        tier_row.addWidget(reset_btn, 1)
        site_layout.addLayout(tier_row)

        fmt_row = QHBoxLayout()
        fmt_row.setSpacing(SPACE_SM)
        fmt_lbl = QLabel(self.tr("Output Format:"))
        fmt_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._output_format_combo = QComboBox()
        self._output_format_combo.addItem(self.tr("Auto (Recommended)"), "auto")
        self._output_format_combo.addItem("JSON Schema", "json_schema")
        self._output_format_combo.addItem(self.tr("JSON Object"), "json_object")
        self._output_format_combo.addItem(self.tr("Off"), "off")
        self._output_format_combo.setCurrentIndex(0)
        fmt_row.addWidget(fmt_lbl, 1)
        fmt_row.addWidget(self._output_format_combo, 3)
        site_layout.addLayout(fmt_row)

        rule_mode_row = QHBoxLayout()
        rule_mode_row.setSpacing(SPACE_SM)
        rule_mode_lbl = QLabel(self.tr("Rule Strategy:"))
        rule_mode_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._rule_mode_combo = QComboBox()
        self._rule_mode_combo.addItem(
            self.tr("By Element Type — Match by element type, reusable across pages (Recommended)"), "type"
        )
        self._rule_mode_combo.addItem(
            self.tr("By DOM Position — Precise positioning by DOM path, requires consistent page structure"), "position"
        )
        self._rule_mode_combo.setCurrentIndex(0)
        rule_mode_row.addWidget(rule_mode_lbl, 1)
        rule_mode_row.addWidget(self._rule_mode_combo, 3)
        site_layout.addLayout(rule_mode_row)

        site_layout.addWidget(QLabel(self.tr("HTML Source:")))
        self._html_input = QTextEdit()
        self._html_input.setPlaceholderText(self.tr("Paste webpage HTML source here..."))
        self._html_input.setFixedHeight(120)
        site_layout.addWidget(self._html_input)

        inner_layout.addWidget(site_group)

        # ── QGroupBox: AI 生成 ──
        ai_group = QGroupBox(self.tr("AI Generation"))
        ai_layout = QVBoxLayout(ai_group)
        ai_layout.setSpacing(SPACE_SM)

        warning = QLabel(
            self.tr(
                "⚠ Before submitting HTML to AI, confirm personal data (name, email, cookies, etc.) has been removed."
            )
        )
        warning.setStyleSheet(f"color: {get_theme_manager().get('warning')}; font-weight: bold;")
        ai_layout.addWidget(warning)

        # ── 模式切换 ──
        mode_row = QHBoxLayout()
        mode_row.setSpacing(SPACE_SM)
        self._external_radio = QRadioButton(self.tr("External AI"))
        self._internal_radio = QRadioButton(self.tr("Internal AI"))
        self._external_radio.setChecked(True)
        self._mode_group = QButtonGroup(self)
        self._mode_group.addButton(self._external_radio, 0)
        self._mode_group.addButton(self._internal_radio, 1)
        self._mode_group.buttonClicked.connect(self._on_mode_changed)
        mode_row.addWidget(self._external_radio, 1)
        mode_row.addWidget(self._internal_radio, 1)
        ai_layout.addLayout(mode_row)

        # ── 动态区域 ──
        self._ext_panel = QWidget()
        ext_layout = QVBoxLayout(self._ext_panel)
        ext_layout.setContentsMargins(0, 0, 0, 0)
        ext_layout.setSpacing(SPACE_SM)
        copy_btn = QPushButton(self.tr("Copy Prompt"))
        copy_btn.clicked.connect(self._on_copy_prompt)
        ext_layout.addWidget(copy_btn)
        ext_layout.addWidget(QLabel(self.tr("Paste AI response (rule JSON):")))
        self._paste_edit = QTextEdit()
        self._paste_edit.setPlaceholderText(self.tr("Paste AI response JSON (markdown code block auto-cleaned)"))
        self._paste_edit.setMaximumHeight(120)
        ext_layout.addWidget(self._paste_edit)
        import_btn = QPushButton(self.tr("Import"))
        import_btn.clicked.connect(self._on_paste_import)
        ext_layout.addWidget(import_btn)

        self._int_panel = QWidget()
        int_layout = QVBoxLayout(self._int_panel)
        int_layout.setContentsMargins(0, 0, 0, 0)
        int_layout.setSpacing(SPACE_SM)

        # C-mode: AI Profile 选择器（ADR-0007 决策 4）
        profile_sel_row = QHBoxLayout()
        profile_sel_row.setSpacing(SPACE_SM)
        profile_lbl = QLabel(self.tr("AI Profile:"))
        profile_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._ai_profile_combo = QComboBox()
        self._ai_profile_combo.setObjectName("ai-profile-combo")
        self._ai_profile_combo.currentTextChanged.connect(self._on_profile_selected)
        ai_settings_btn = QPushButton(self.tr("Configure AI"))
        ai_settings_btn.clicked.connect(self._on_ai_settings)
        profile_sel_row.addWidget(profile_lbl, 1)
        profile_sel_row.addWidget(self._ai_profile_combo, 2)
        profile_sel_row.addWidget(ai_settings_btn, 1)
        int_layout.addLayout(profile_sel_row)

        gen_btn_row = QHBoxLayout()
        gen_btn_row.setSpacing(SPACE_SM)
        self._gen_btn = QPushButton(self.tr("Generate & Import"))
        self._gen_btn.setObjectName("gen-btn")
        self._gen_btn.clicked.connect(self._on_generate)
        gen_btn_row.addWidget(self._gen_btn, 1)
        int_layout.addLayout(gen_btn_row)
        int_layout.addStretch()
        self._int_panel.hide()

        ai_layout.addWidget(self._ext_panel)
        ai_layout.addWidget(self._int_panel)

        inner_layout.addWidget(ai_group)
        inner_layout.addStretch()

        scroll.setWidget(inner)
        layout.addWidget(scroll)

    def reset(self) -> None:
        self._site_url.clear()
        self._field_requirements.clear()
        self._html_input.clear()
        self._paste_edit.clear()
        self._tier_combo.setCurrentIndex(1)
        self._output_format_combo.setCurrentIndex(0)
        self._rule_mode_combo.setCurrentIndex(0)

    def _on_mode_changed(self, btn) -> None:
        if self._mode_group.checkedId() == 0:
            self._ext_panel.show()
            self._int_panel.hide()
        else:
            self._ext_panel.hide()
            self._int_panel.show()
            self._refresh_profile_combo()

    # ── 提示词模板 ──

    def _on_ai_settings(self) -> None:
        AdvancedSettingsDialog(self, open_ai_tab=True).exec()
        self._refresh_profile_combo()

    def _on_copy_prompt(self) -> None:
        url = self._site_url.text().strip()
        html = self._html_input.toPlainText()
        field_reqs = self._field_requirements.text().strip()

        if not html:
            self._show_status(self.tr("Please paste HTML source first"))
            return

        field_list = [f.strip() for f in field_reqs.split(",") if f.strip()] if field_reqs else []
        tier = self._tier_combo.currentData()
        mode = self._rule_mode_combo.currentData()

        try:
            chatml_text, token_count = get_assembled_prompt(url, html, field_list, tier, mode=mode)
        except Exception as e:
            QMessageBox.critical(
                self, self.tr("Failed to build prompt"), self.tr("Failed to build prompt:\n{0}").format(e)
            )
            return

        dlg = ChatMLPreviewDialog(chatml_text, token_count, is_internal=False, parent=self)
        dlg.exec()

    # ── 粘贴导入 ──

    def _on_paste_import(self) -> None:
        text = self._paste_edit.toPlainText().strip()
        if not text:
            self._show_status(self.tr("Please paste rule JSON first"))
            return
        try:
            cleaned = _clean_markdown_wrapper(text)
            data = json.loads(cleaned)
            validate_rule(data)

            name = data.get("name", "imported_rule")
            rule_path = Path.home() / ".astrocrawl" / "rules" / f"{name}.json"
            dlg = RuleEditDialog(data, "user", rule_path, self, snapshot_provider=self._snapshot_provider)
            dlg.exec()
            self.rule_generated.emit(data)
            self._paste_edit.clear()
        except Exception as e:
            self._show_status(self.tr("Import failed: {0}").format(e), "error")

    # ── AI 生成 (Path B) ──

    def _on_generate(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._show_status(self.tr("AI generation in progress, please wait."))
            return

        profile = self._get_active_ai_profile()
        if profile is None or not profile.api_key:
            self._show_status(self.tr("Please click 'Configure AI' to create an AI Profile and fill in the API Key."))
            return

        url = self._site_url.text().strip()
        field_reqs = self._field_requirements.text().strip()
        html = self._html_input.toPlainText()

        if not html:
            self._show_status(self.tr("Please paste HTML source."))
            return

        msg = QMessageBox(
            QMessageBox.Warning,
            self.tr("Privacy Confirmation"),
            self.tr(
                "HTML will be sent to the AI provider via API.\nPlease confirm personal data has been removed from the HTML.\nContinue?"
            ),
            parent=self,
        )
        send_btn = msg.addButton(self.tr("Send"), QMessageBox.YesRole)
        msg.addButton(self.tr("Cancel"), QMessageBox.NoRole)
        msg.exec()
        if msg.clickedButton() != send_btn:
            return

        field_list = [f.strip() for f in field_reqs.split(",") if f.strip()] if field_reqs else []
        tier = self._tier_combo.currentData()
        mode = self._rule_mode_combo.currentData()

        try:
            chatml_text, token_count = get_assembled_prompt(url, html, field_list, tier, mode=mode)
        except Exception as e:
            QMessageBox.critical(
                self, self.tr("Failed to build prompt"), self.tr("Failed to build prompt:\n{0}").format(e)
            )
            return

        self._last_token_count = token_count

        dlg = ChatMLPreviewDialog(chatml_text, token_count, is_internal=True, parent=self)
        dlg.confirmed.connect(self._on_generate_confirmed)
        dlg.exec()

    def _on_generate_confirmed(self) -> None:
        url = self._site_url.text().strip()
        html = self._html_input.toPlainText()
        field_reqs = self._field_requirements.text().strip()
        field_list = [f.strip() for f in field_reqs.split(",") if f.strip()] if field_reqs else []
        tier = self._tier_combo.currentData()
        mode = self._rule_mode_combo.currentData()

        profile = self._get_active_ai_profile()
        if profile is None:
            return
        config = AIConfig.from_profile(profile)
        fmt = self._output_format_combo.currentData()
        if fmt == "off":
            params = GenerationParams(temperature=profile.temperature, max_tokens=profile.max_tokens)
        elif fmt == "json_object":
            params = GenerationParams(
                temperature=profile.temperature,
                max_tokens=profile.max_tokens,
                output=OutputConstraint(format="json_object"),
            )
        else:  # auto / json_schema
            from astrocrawl.rules import RuleSchema

            params = GenerationParams(
                temperature=profile.temperature,
                max_tokens=profile.max_tokens,
                output=OutputConstraint(format="json_schema", schema_model=RuleSchema),
            )

        tokens = self._last_token_count
        self._show_status(self.tr("Preprocessing HTML... ({0} tokens)").format(tokens))
        self._gen_btn.setEnabled(False)
        self.busy_changed.emit(True)

        self._worker = _AiWorker(
            url,
            html,
            field_list,
            PreprocessTier(tier),
            config,
            params,
            model=profile.model,
            tokens=tokens,
            mode=mode,
            parent=self,
        )
        self._worker.generation_progress.connect(self._on_ai_progress)
        self._worker.finished.connect(self._on_generate_result)
        self._worker.error_occurred.connect(self._on_generate_error)
        self._worker.start()

    def _on_ai_progress(self, phase: str, info: dict) -> None:
        if phase == "calling_ai":
            model = info.get("model", "")
            tokens = info.get("tokens", 0)
            self._show_status(self.tr("Calling AI ({0}, {1} tokens)...").format(model, tokens))
        elif phase == "parsing":
            self._show_status(self.tr("AI responded, parsing rule JSON..."))

    @Slot(object)
    def _on_generate_result(self, result: dict) -> None:
        self._worker = None
        self.busy_changed.emit(False)
        self._gen_btn.setEnabled(True)

        try:
            rule = validate_rule(result)
        except Exception as e:
            self._show_status(self.tr("Rule validation failed: {0}").format(e), "error")
            return

        empty_fields = [n for n, f in rule.fields.items() if not f.selector]
        if empty_fields:
            logger.info("ai_generate_empty_selectors", fields=empty_fields)

        name = result.get("name", "ai_generated")
        field_count = len(rule.fields)
        self._show_status(self.tr("Rule '{0}' generated ({1} fields)").format(name, field_count), "success")

        rule_path = Path.home() / ".astrocrawl" / "rules" / f"{name}.json"
        dlg = RuleEditDialog(result, "user", rule_path, self, snapshot_provider=self._snapshot_provider)
        dlg.exec()
        self.rule_generated.emit(result)
        self._html_input.clear()

    @Slot(str)
    def _on_generate_error(self, error: str) -> None:
        self._worker = None
        self.busy_changed.emit(False)
        self._gen_btn.setEnabled(True)
        self._show_status(self.tr("API call failed: {0}").format(error), "error")

    def _cleanup_worker(self) -> None:
        w = self._worker
        if w is None:
            return
        if not w.isRunning():
            self._worker = None
            self.busy_changed.emit(False)
            self._gen_btn.setEnabled(True)
            return
        for sig, slot in (
            (w.generation_progress, self._on_ai_progress),
            (w.finished, self._on_generate_result),
            (w.error_occurred, self._on_generate_error),
        ):
            try:
                sig.disconnect(slot)
            except RuntimeError:
                pass
        w.cancel()
        if not w.wait(30000):
            w.terminate()
            w.wait(2000)
        self._worker = None
        self.busy_changed.emit(False)
        self._gen_btn.setEnabled(True)


class _AiWorker(QThread):
    """后台线程：调用 RuleGenerator.generate_sync 生成规则。"""

    finished = Signal(dict)
    error_occurred = Signal(str)
    generation_progress = Signal(str, object)

    def __init__(self, url, html, field_list, tier, config, params, model="", tokens=0, mode="type", parent=None):
        super().__init__(parent)
        self._url = url
        self._html = html
        self._field_list = field_list
        self._tier = tier
        self._config = config
        self._params = params
        self._model = model
        self._tokens = tokens
        self._mode = mode
        self._cancel_event: threading.Event = threading.Event()
        self._client: AIClient | None = None

    def run(self) -> None:
        try:
            from astrocrawl.ai import AIRateLimitError as _AIRateLimitError
            from astrocrawl.ai import get_rule_gen_limiter as _get_limiter
            from astrocrawl.rules import GenerationCancelled as _GenerationCancelled
            from astrocrawl.rules import RuleGenerator

            with _get_limiter().acquire_sync():
                self.generation_progress.emit(
                    "calling_ai",
                    {
                        "model": self._model,
                        "tokens": self._tokens,
                    },
                )
                prefs = get_preferences()
                proxy_parsed = prefs.get_parsed_proxy_for("ai")
                proxy_url = proxy_parsed.to_url_with_auth() if proxy_parsed else None
                self._client = AIClient(self._config, proxy_url=proxy_url)
                generator = RuleGenerator(self._client)
                result = generator.generate_sync(
                    self._url,
                    self._html,
                    self._field_list,
                    self._params,
                    tier=self._tier,
                    mode=self._mode,
                    cancel_event=self._cancel_event,
                )
                if not self.isInterruptionRequested():
                    self.generation_progress.emit("parsing", {})
                    self.finished.emit(result)
        except _GenerationCancelled:
            return
        except _AIRateLimitError as e:
            self.error_occurred.emit(str(e))
        except Exception as e:
            self.error_occurred.emit(str(e))

    def cancel(self) -> None:
        self._cancel_event.set()
        self.requestInterruption()
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════════════
# 源更新后台线程
# ═══════════════════════════════════════════════════════════════════════


class _SourceUpdateWorker(QThread):
    """后台线程：异步更新远程规则源，逐源报告进度。source_name 非空时仅更新指定源。"""

    finished = Signal(dict)
    source_progress = Signal(str, str)
    error_occurred = Signal(str)

    def __init__(self, cache_dir: Path, parent=None, source_name: str | None = None):
        super().__init__(parent)
        self._cache_dir = cache_dir
        self._source_name = source_name
        self._main_task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def run(self) -> None:
        import asyncio

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            if self.isInterruptionRequested():
                return
            main_task = asyncio.ensure_future(self._update(), loop=loop)
            self._main_task = main_task
            result = loop.run_until_complete(main_task)
            self.finished.emit(result)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.error_occurred.emit(str(e))
        finally:
            try:
                pending = asyncio.all_tasks(loop)
                for t in pending:
                    t.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            finally:
                loop.close()
                asyncio.set_event_loop(None)
                self._loop = None
                self._main_task = None

    def cancel(self) -> None:
        self.requestInterruption()
        if self._main_task is not None and self._loop is not None and self._loop.is_running():
            try:
                self._loop.call_soon_threadsafe(self._main_task.cancel)
            except RuntimeError:
                pass

    async def _update(self) -> dict:
        import aiohttp

        from astrocrawl.rules import SourceManager

        async with aiohttp.ClientSession() as session:
            source_proxy = get_preferences().get_parsed_proxy_for("source")
            source_proxy_url = source_proxy.to_url_with_auth() if source_proxy else None
            mgr = SourceManager(session, self._cache_dir, auto_update=False, proxy_url=source_proxy_url)
            sources = mgr.list_sources()
            if self._source_name:
                sources = [s for s in sources if s.name == self._source_name]
                if not sources:
                    return {"updated": [], "up_to_date": 0, "failed": 0}
            updated: list[str] = []
            up_to_date = 0
            failed = 0
            for src in sources:
                if src.state in ("emergency_disabled", "moved"):
                    continue
                self.source_progress.emit(src.name, "fetching")
                try:
                    r = await mgr.update_source(src.name)
                    if r.get("updated"):
                        updated.append(src.name)
                        self.source_progress.emit(src.name, f"updated:{r.get('rules_downloaded', 0)}")
                    else:
                        up_to_date += 1
                        self.source_progress.emit(src.name, "up_to_date")
                except Exception:
                    failed += 1
                    self.source_progress.emit(src.name, "failed")
            return {"updated": updated, "up_to_date": up_to_date, "failed": failed}


class _SourceValidateWorker(QThread):
    """后台线程：异步验证单个远程源的 manifest 可达性。"""

    finished = Signal(dict)
    error_occurred = Signal(str)

    def __init__(self, name: str, url: str, cache_dir: Path, parent=None):
        super().__init__(parent)
        self._name = name
        self._url = url
        self._cache_dir = cache_dir
        self._main_task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def run(self) -> None:
        import asyncio

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            if self.isInterruptionRequested():
                return
            main_task = asyncio.ensure_future(self._validate(), loop=loop)
            self._main_task = main_task
            result = loop.run_until_complete(main_task)
            self.finished.emit(result)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.error_occurred.emit(str(e))
        finally:
            try:
                pending = asyncio.all_tasks(loop)
                for t in pending:
                    t.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            finally:
                loop.close()
                asyncio.set_event_loop(None)
                self._loop = None
                self._main_task = None

    def cancel(self) -> None:
        self.requestInterruption()
        if self._main_task is not None and self._loop is not None and self._loop.is_running():
            try:
                self._loop.call_soon_threadsafe(self._main_task.cancel)
            except RuntimeError:
                pass

    async def _validate(self) -> dict:
        import aiohttp

        from astrocrawl.rules import SourceManager

        async with aiohttp.ClientSession() as session:
            source_proxy = get_preferences().get_parsed_proxy_for("source")
            source_proxy_url = source_proxy.to_url_with_auth() if source_proxy else None
            mgr = SourceManager(session, self._cache_dir, auto_update=False, proxy_url=source_proxy_url)
            try:
                manifest = await mgr.fetch_manifest(self._name)
                return {"valid": True, "manifest": manifest, "error": None}
            except Exception as e:
                return {"valid": False, "manifest": None, "error": str(e)}


class _SourceValidateAllWorker(QThread):
    """后台线程：异步验证全部远程源的 manifest 可达性。"""

    finished = Signal(dict)
    error_occurred = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._main_task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def run(self) -> None:
        import asyncio

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            if self.isInterruptionRequested():
                return
            main_task = asyncio.ensure_future(self._validate_all(), loop=loop)
            self._main_task = main_task
            result = loop.run_until_complete(main_task)
            self.finished.emit(result)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.error_occurred.emit(str(e))
        finally:
            try:
                pending = asyncio.all_tasks(loop)
                for t in pending:
                    t.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            finally:
                loop.close()
                asyncio.set_event_loop(None)
                self._loop = None
                self._main_task = None

    def cancel(self) -> None:
        self.requestInterruption()
        if self._main_task is not None and self._loop is not None and self._loop.is_running():
            try:
                self._loop.call_soon_threadsafe(self._main_task.cancel)
            except RuntimeError:
                pass

    async def _validate_all(self) -> dict:
        import aiohttp

        from astrocrawl.rules import SourceManager, list_sources_from_file

        sources = list_sources_from_file()
        cache_dir = Path.home() / ".astrocrawl" / "rules_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        passed = 0
        failed = 0
        async with aiohttp.ClientSession() as session:
            source_proxy = get_preferences().get_parsed_proxy_for("source")
            source_proxy_url = source_proxy.to_url_with_auth() if source_proxy else None
            mgr = SourceManager(session, cache_dir, auto_update=False, proxy_url=source_proxy_url)
            for src in sources:
                name = src.get("name", "")
                if not name:
                    continue
                try:
                    await mgr.fetch_manifest(name)
                    passed += 1
                except Exception:
                    failed += 1
        return {"passed": passed, "failed": failed}


# ═══════════════════════════════════════════════════════════════════════
# 源编辑对话框
# ═══════════════════════════════════════════════════════════════════════


class _SourceEditDialog(QDialog):
    """远程源编辑对话框 — 三区布局：基本信息可编辑 + 远程信息只读 + 运行状态只读。"""

    def __init__(self, parent=None, source: dict | None = None):
        super().__init__(parent)
        self.setWindowModality(Qt.WindowModal)
        self._source = source
        self._is_new = source is None
        if self._is_new:
            self.setWindowTitle(self.tr("Add Remote Source"))
        else:
            assert source is not None  # _is_new=False implies source passed
            self.setWindowTitle(self.tr("Edit Source: {0}").format(source.get("name", "")))
        self._setup_ui()
        self.adjustSize()
        ideal_h = self.height()
        self.setMaximumWidth(self.width())
        screen = self.screen()
        if screen:
            max_h = int(screen.availableGeometry().height() * 0.85)
            self.setMaximumHeight(min(ideal_h, max_h))
            self.setMinimumHeight(min(ideal_h, max_h))
        else:
            self.setMaximumHeight(ideal_h)
            self.setMinimumHeight(ideal_h)
        self.setMinimumWidth(self.width())

    def get_data(self) -> dict:
        return {
            "name": self._name_edit.text().strip(),
            "url": self._url_edit.text().strip(),
        }

    def _validate(self) -> bool:
        url = self._url_edit.text().strip()
        name = self._name_edit.text().strip()
        if not url:
            self._error_label.setText(self.tr("URL cannot be empty"))
            return False
        try:
            validate_source_url(url)
        except ValueError as e:
            self._error_label.setText(self.tr("Invalid URL: {0}").format(e))
            return False
        if not name:
            self._error_label.setText(self.tr("Name cannot be empty"))
            return False
        self._error_label.setText("")
        return True

    def _validate_and_accept(self) -> None:
        if self._validate():
            self.accept()

    def _setup_ui(self) -> None:
        from datetime import datetime as _datetime

        src = self._source if self._source is not None else {}
        layout = QVBoxLayout(self)
        layout.setSpacing(SPACE_MD)

        theme = get_theme_manager()
        sc = theme.get("success")
        dc = theme.get("danger")
        wc = theme.get("warning")
        ic = theme.get("disabled")
        state_colors = {"active": sc, "degraded": wc, "emergency_disabled": dc, "moved": ic, "offline": ic}
        state_labels = {
            "active": self.tr("Active"),
            "degraded": self.tr("Degraded"),
            "emergency_disabled": self.tr("Emergency Disabled"),
            "moved": self.tr("Moved"),
            "offline": self.tr("Offline"),
        }

        # ── Section 1: Basic Info (editable) ──
        info_group = QGroupBox(self.tr("Basic Info"))
        info_form = QFormLayout(info_group)
        info_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        info_form.setHorizontalSpacing(SPACE_LG)
        self._name_edit = QLineEdit(src.get("name", ""))
        self._name_edit.setMaxLength(QLINEEDIT_MAX)
        info_form.addRow(self.tr("Name:"), self._name_edit)
        self._url_edit = QLineEdit(src.get("url", ""))
        self._url_edit.setMaxLength(QLINEEDIT_MAX)
        info_form.addRow(self.tr("URL:"), self._url_edit)
        layout.addWidget(info_group)

        # ── Section 2: Remote Info (read-only, from manifest) ──
        if not self._is_new:
            remote_group = QGroupBox(self.tr("Remote Info"))
            remote_form = QFormLayout(remote_group)
            remote_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
            remote_form.setHorizontalSpacing(SPACE_LG)
            remote_form.addRow(self.tr("Title:"), QLabel(src.get("title") or self.tr("—")))
            remote_form.addRow(self.tr("Maintainer:"), QLabel(src.get("maintainer") or self.tr("—")))
            remote_form.addRow(self.tr("Homepage:"), QLabel(src.get("homepage") or self.tr("—")))
            layout.addWidget(remote_group)

        # ── Section 3: Runtime Status (read-only, system-managed) ──
        if not self._is_new:
            state_group = QGroupBox(self.tr("Runtime Status"))
            state_form = QFormLayout(state_group)
            state_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
            state_form.setHorizontalSpacing(SPACE_LG)

            state = src.get("state", "active")
            state_label = QLabel(state_labels.get(state, state))
            state_label.setStyleSheet(f"color: {state_colors.get(state, ic)}; font-weight: bold;")
            state_form.addRow(self.tr("Status:"), state_label)

            rc = src.get("rules_count")
            last_upd = src.get("last_updated", 0)
            state_form.addRow(
                self.tr("Rules:"), QLabel(str(rc) if (rc is not None and last_upd and last_upd > 0) else self.tr("—"))
            )
            ts = (
                _datetime.fromtimestamp(last_upd).strftime("%Y-%m-%d %H:%M:%S")
                if (last_upd and last_upd > 0)
                else self.tr("Never updated")
            )
            state_form.addRow(self.tr("Last Updated:"), QLabel(ts))

            from astrocrawl._constants import SOURCE_DAILY_UPDATE_LIMIT

            daily = src.get("daily_update_count", 0)
            state_form.addRow(self.tr("Today:"), QLabel(f"{daily} / {SOURCE_DAILY_UPDATE_LIMIT}"))

            failures = src.get("consecutive_failures", 0)
            fail_label = QLabel(str(failures))
            if failures > 0:
                fail_label.setStyleSheet(f"color: {dc}; font-weight: bold;")
            state_form.addRow(self.tr("Consecutive Failures:"), fail_label)
            layout.addWidget(state_group)

        # ── 验证错误 ──
        self._error_label = QLabel("")
        self._error_label.setStyleSheet(f"color: {get_theme_manager().get('danger')}; font-size: {FONT_MD}px;")
        self._error_label.setWordWrap(True)
        layout.addWidget(self._error_label)

        # ── Bottom Buttons ──
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(SPACE_SM)
        cancel_btn = QPushButton(self.tr("Cancel"))
        cancel_btn.clicked.connect(self.reject)
        confirm_btn = QPushButton(self.tr("OK"))
        confirm_btn.clicked.connect(self._validate_and_accept)
        confirm_btn.setDefault(True)
        btn_layout.addWidget(cancel_btn, 1)
        btn_layout.addWidget(confirm_btn, 1)
        layout.addLayout(btn_layout)


# ═══════════════════════════════════════════════════════════════════════
# Tab 3: 远程源
# ═══════════════════════════════════════════════════════════════════════


class _SourceListModel(QAbstractTableModel):
    """远程源列表 Model — 5 列：名称/URL/规则数/状态/启用。"""

    def __init__(self, sources: list, parent=None):
        super().__init__(parent)
        self._sources = sources
        self._toggle_overrides: dict[str, bool] = {}

    def load(self, sources: list) -> None:
        self.beginResetModel()
        self._toggle_overrides.clear()
        self._sources = list(sources)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._sources)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else 5

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        src = self._sources[index.row()]
        col = index.column()

        if role == Qt.DisplayRole:
            if col == 0:
                return src.get("name", "")
            elif col == 1:
                return src.get("url", "")
            elif col == 2:
                last_upd = src.get("last_updated", 0)
                rc = src.get("rules_count")
                if rc is not None and last_upd and last_upd > 0:
                    return str(rc)
                return self.tr("—")
            elif col == 3:
                return self._state_label(src)
            return None

        if role == Qt.UserRole and col == 3:
            return src.get("state", "active")

        if role == Qt.CheckStateRole and col == 4:
            name = src.get("name", "")
            if name in self._toggle_overrides:
                return Qt.Checked.value if self._toggle_overrides[name] else Qt.Unchecked.value
            return Qt.Checked.value if src.get("enabled", True) else Qt.Unchecked.value

        if role == Qt.TextAlignmentRole and col in (2, 3):
            return int(Qt.AlignCenter)

        return None

    def flags(self, index):
        flags = super().flags(index)
        if index.column() == 4:
            flags |= Qt.ItemIsUserCheckable
        return flags

    def setData(self, index, value, role=Qt.EditRole):
        if role == Qt.CheckStateRole and index.column() == 4:
            if 0 <= index.row() < len(self._sources):
                name = self._sources[index.row()].get("name", "")
                if name:
                    self._toggle_overrides[name] = value == Qt.Checked.value
                    self.dataChanged.emit(index, index, [Qt.CheckStateRole])
                    return True
        return False

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return [self.tr("Name"), self.tr("URL"), self.tr("Rules"), self.tr("Status"), self.tr("Enabled")][section]
        return None

    def get_source(self, row):
        if 0 <= row < len(self._sources):
            return self._sources[row]
        return {}

    def _state_label(self, src: dict) -> str:
        labels: dict[str, str] = {
            "active": self.tr("Active"),
            "degraded": self.tr("Degraded"),
            "emergency_disabled": self.tr("Emergency Disabled"),
            "moved": self.tr("Moved"),
            "offline": self.tr("Offline"),
        }
        state: str = src.get("state", "active")
        return labels.get(state, state)


class _SourcePage(QWidget):
    sources_updated = Signal(list)
    busy_changed = Signal(bool)

    def __init__(self, status_callback=None, parent=None):
        super().__init__(parent)
        self._pending_toggles: dict[str, bool] = {}
        self._active_worker = None
        self._theme_mgr = get_theme_manager()
        self._show_status = status_callback or (lambda msg, level="success": None)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, SPACE_MD, 0, 0)
        layout.setSpacing(SPACE_MD)

        grid = QGridLayout()
        grid.setSpacing(SPACE_SM)
        self._refresh_btn = QPushButton(self.tr("Reload"))
        self._refresh_btn.clicked.connect(self._on_reload)
        self._add_btn = QPushButton(self.tr("Add Source"))
        self._add_btn.clicked.connect(self._on_add_source)
        self._edit_btn = QPushButton(self.tr("Edit Source"))
        self._edit_btn.clicked.connect(self._on_edit_source)
        self._remove_btn = QPushButton(self.tr("Remove Source"))
        self._remove_btn.clicked.connect(self._on_remove_source)
        self._validate_btn = QPushButton(self.tr("Validate Source"))
        self._validate_btn.clicked.connect(self._on_validate_source)
        self._validate_all_btn = QPushButton(self.tr("Validate All Sources"))
        self._validate_all_btn.clicked.connect(self._on_validate_all)
        self._update_btn = QPushButton(self.tr("Update Source"))
        self._update_btn.clicked.connect(self._on_update_single)
        self._update_all_btn = QPushButton(self.tr("Update All"))
        self._update_all_btn.clicked.connect(self._on_update_all)
        buttons = (
            self._refresh_btn,
            self._add_btn,
            self._edit_btn,
            self._remove_btn,
            self._validate_btn,
            self._validate_all_btn,
            self._update_btn,
            self._update_all_btn,
        )
        for col, b in enumerate(buttons):
            grid.addWidget(b, 0, col)
            grid.setColumnStretch(col, 1)

        self._search_input = QLineEdit()
        self._search_input.setMaxLength(QLINEEDIT_MAX)
        self._search_input.setObjectName("source-search-input")
        self._search_input.setPlaceholderText(self.tr("Search source name or URL..."))
        self._search_input.setClearButtonEnabled(True)
        self._search_input.textChanged.connect(self._apply_filter)
        grid.addWidget(self._search_input, 1, 0, 1, 8)

        if self._theme_mgr is not None:
            self._theme_mgr.theme_changed.connect(self._apply_theme)
        layout.addLayout(grid)

        self._table = create_managed_table(object_name="source-table")
        self._table.doubleClicked.connect(self._on_source_detail)
        layout.addWidget(self._table, 1)

        self._cb_delegate = CheckboxDelegate(self)
        self._cb_delegate.toggled.connect(self._on_checkbox_toggled)
        self._table.setItemDelegateForColumn(4, self._cb_delegate)

        self._empty_label = QLabel(
            self.tr('No remote sources configured.\nClick "Add Source" and enter a Manifest URL.')
        )
        self._empty_label.setAlignment(Qt.AlignCenter)
        self._empty_label.setStyleSheet(f"color: {self._theme_mgr.get('disabled')}; padding: {SPACE_LG}px;")
        layout.addWidget(self._empty_label)

        self._refresh()

    def _refresh(self) -> None:
        sources = list_sources_from_file()
        has_sources = bool(sources)
        self._table.setVisible(has_sources)
        self._empty_label.setVisible(not has_sources)
        self._sync_button_states()

        model = _SourceListModel(sources)
        model._toggle_overrides = dict(self._pending_toggles)
        proxy = _FilterProxy((0, 1), self)
        proxy.setSourceModel(model)
        self._table.setModel(proxy)
        configure_table_header(
            self._table,
            [
                ColumnDef(key="name", label="Name", resize="fixed", width=120),
                ColumnDef(key="url", label="URL"),
                ColumnDef(key="count", label="Rules", resize="fixed", width=50),
                ColumnDef(key="status", label="Status", resize="fixed", width=60),
                ColumnDef(key="enabled", label="Enabled", resize="fixed", width=50),
            ],
        )
        # StatusColorDelegate on col 3 (状态)
        self._status_delegate = StatusColorDelegate(
            {
                "active": "success",
                "degraded": "warning",
                "emergency_disabled": "danger",
                "moved": "disabled",
                "offline": "disabled",
            },
        )
        self._table.setItemDelegateForColumn(3, self._status_delegate)
        self._apply_filter()

    def _apply_filter(self) -> None:
        from PySide6.QtCore import QRegularExpression

        proxy = self._table.model()
        if proxy is None:
            return
        keyword = self._search_input.text().strip()
        if not keyword:
            proxy.setFilterRegularExpression(QRegularExpression())
        else:
            proxy.setFilterRegularExpression(
                QRegularExpression(keyword, QRegularExpression.PatternOption.CaseInsensitiveOption)
            )

    def _on_reload(self) -> None:
        self._refresh()
        self._show_status(self.tr("Source list refreshed"))

    # ── 异步操作模板方法 ──

    def _start_source_operation(self, worker, *, result_handler=None, error_handler=None):
        if self._active_worker is not None:
            return
        self._active_worker = worker
        self.busy_changed.emit(True)
        self._disable_all_buttons()
        worker.finished.connect(lambda r: self._on_operation_done(result_handler, r))
        worker.error_occurred.connect(lambda e: self._on_operation_error(error_handler, e))
        worker.start()

    def _on_operation_done(self, handler, result):
        self._active_worker = None
        self.busy_changed.emit(False)
        self._restore_all_buttons()
        self._sync_button_states()
        if handler:
            handler(result)

    def _on_operation_error(self, handler, error):
        self._active_worker = None
        self.busy_changed.emit(False)
        self._restore_all_buttons()
        self._sync_button_states()
        if handler:
            handler(error)

    def _sync_button_states(self):
        has_sources = bool(list_sources_from_file())
        self._remove_btn.setEnabled(has_sources)
        self._edit_btn.setEnabled(has_sources)
        self._validate_btn.setEnabled(has_sources)
        self._validate_all_btn.setEnabled(has_sources)
        self._update_btn.setEnabled(has_sources)
        self._update_all_btn.setEnabled(has_sources)

    def _disable_all_buttons(self):
        for btn in (
            self._refresh_btn,
            self._add_btn,
            self._edit_btn,
            self._remove_btn,
            self._validate_btn,
            self._validate_all_btn,
            self._update_btn,
            self._update_all_btn,
        ):
            btn.setEnabled(False)

    def _restore_all_buttons(self):
        self._refresh_btn.setEnabled(True)
        self._add_btn.setEnabled(True)
        self._edit_btn.setEnabled(True)
        self._remove_btn.setEnabled(True)
        self._validate_btn.setEnabled(True)
        self._validate_all_btn.setEnabled(True)
        self._update_btn.setEnabled(True)
        self._update_all_btn.setEnabled(True)

    def _cleanup_worker(self) -> None:
        w = self._active_worker
        if w is None:
            return
        if not w.isRunning():
            self._active_worker = None
            self.busy_changed.emit(False)
            self._restore_all_buttons()
            self._sync_button_states()
            return
        for sig_name in ("finished", "error_occurred", "source_progress"):
            try:
                getattr(w, sig_name).disconnect()
            except (AttributeError, RuntimeError):
                pass
        w.cancel()
        if not w.wait(5000):
            w.terminate()
            w.wait(2000)
        self._active_worker = None
        self.busy_changed.emit(False)
        self._restore_all_buttons()
        self._sync_button_states()

    # ── checkbox ──

    def _on_checkbox_toggled(self, row: int, checked: bool) -> None:
        proxy = self._table.model()
        if proxy is None:
            return
        proxy_idx = proxy.index(row, 4)
        src_idx = proxy.mapToSource(proxy_idx)
        model = proxy.sourceModel()
        name = model.data(model.index(src_idx.row(), 0), Qt.DisplayRole)
        if not name:
            return
        self._pending_toggles[str(name)] = checked

    def _selected_source_name(self) -> str | None:
        idx = self._table.currentIndex()
        if not idx.isValid():
            return None
        proxy = self._table.model()
        if proxy is None:
            return None
        src_idx = proxy.mapToSource(idx)
        name = proxy.sourceModel().data(proxy.sourceModel().index(src_idx.row(), 0), Qt.DisplayRole)
        return str(name) if name else None

    def apply_pending(self) -> None:
        """将暂存的启用/禁用变更写入 sources.json。"""
        if not self._pending_toggles:
            return
        for name, enabled in self._pending_toggles.items():
            update_source_in_file(name, enabled=enabled)
        self._pending_toggles.clear()
        self._refresh()

    def discard_pending(self) -> None:
        """丢弃暂存的启用/禁用更改。"""
        self._pending_toggles.clear()

    def _apply_theme(self) -> None:
        """主题变更时刷新空状态标签和表格。"""
        if self._theme_mgr is None:
            return
        self._empty_label.setStyleSheet(f"color: {self._theme_mgr.get('disabled')}; padding: {SPACE_LG}px;")
        self._refresh()
        # Re-apply pending toggles after model rebuild
        proxy = self._table.model()
        if proxy and self._pending_toggles:
            model = proxy.sourceModel()
            model._toggle_overrides = dict(self._pending_toggles)

    def _on_add_source(self) -> None:
        dlg = _SourceEditDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        data = dlg.get_data()
        try:
            add_source_to_file(data["name"], data["url"])
        except ValueError as e:
            self._show_status(self.tr("Add failed: {0}").format(e), "error")
            return
        self._show_status(self.tr("Source '{0}' added").format(data["name"]))
        self._refresh()

    def _on_remove_source(self) -> None:
        name = self._selected_source_name()
        if not name:
            QMessageBox.information(
                self, self.tr("Remove Source"), self.tr("Please select a remote source in the table first.")
            )
            return

        msg = QMessageBox(
            QMessageBox.Warning,
            self.tr("Confirm Removal"),
            self.tr("Remove remote source '{0}'?\nThis action cannot be undone.").format(name),
            parent=self,
        )
        del_btn = msg.addButton(self.tr("Remove"), QMessageBox.YesRole)
        msg.addButton(self.tr("Cancel"), QMessageBox.NoRole)
        msg.exec()
        if msg.clickedButton() != del_btn:
            return

        remove_source_from_file(name)
        self._show_status(self.tr("Source '{0}' removed").format(name))
        self._refresh()

    def _on_edit_source(self) -> None:
        name = self._selected_source_name()
        if not name:
            QMessageBox.information(
                self, self.tr("Edit Source"), self.tr("Please select a remote source in the table first.")
            )
            return
        sources = list_sources_from_file()
        src = next((s for s in sources if s.get("name") == name), None)
        if src is None:
            return
        original_url = src.get("url", "")
        dlg = _SourceEditDialog(self, source=src)
        if dlg.exec() != QDialog.Accepted:
            return
        updated = dlg.get_data()
        new_url = updated["url"]
        updated.pop("name")  # name is the lookup key, not an update field
        update_source_in_file(name, **updated)
        if new_url != original_url:
            import shutil

            cache_dir = Path.home() / ".astrocrawl" / "rules_cache" / name
            if cache_dir.exists():
                shutil.rmtree(cache_dir)
            self._show_status(self.tr("Source '{0}' URL changed, re-downloading...").format(name))
            worker = _SourceUpdateWorker(Path.home() / ".astrocrawl" / "rules_cache", self)
            self._start_source_operation(
                worker,
                result_handler=lambda r: self._on_edit_source_done(name, r),
                error_handler=lambda e: self._show_status(
                    self.tr("Source '{0}' download failed: {1}").format(name, e), "error"
                ),
            )
        else:
            self._refresh()
            self._show_status(self.tr("Source '{0}' updated").format(name))

    def _on_edit_source_done(self, name: str, result: dict) -> None:
        self._refresh()
        self._show_status(self.tr("Source '{0}' updated").format(name), "success")

    def _on_source_detail(self) -> None:
        self._on_edit_source()

    def _on_validate_source(self) -> None:
        name = self._selected_source_name()
        if not name:
            QMessageBox.information(
                self, self.tr("Validate Source"), self.tr("Please select a remote source in the table first.")
            )
            return
        sources = list_sources_from_file()
        src = next((s for s in sources if s.get("name") == name), None)
        if src is None:
            return
        url = src.get("url", "")
        try:
            validate_source_url(url)
        except ValueError as e:
            QMessageBox.information(self, self.tr("Invalid URL"), str(e))
            return
        self._show_status(self.tr("Validating source '{0}'...").format(name))
        worker = _SourceValidateWorker(name, url, Path.home() / ".astrocrawl" / "rules_cache")
        self._start_source_operation(
            worker,
            result_handler=lambda r: self._on_validate_result(name, r),
            error_handler=lambda e: self._show_status(
                self.tr("Source '{0}' validation failed: {1}").format(name, e), "error"
            ),
        )

    def _on_validate_result(self, name: str, result: dict) -> None:
        if result.get("valid"):
            manifest = result.get("manifest", {})
            rc = manifest.get("rules", [])
            self._show_status(
                self.tr("Source '{0}' validated (manifest schema ok, {1} rules)").format(name, len(rc)), "success"
            )
        else:
            self._show_status(
                self.tr("Source '{0}' validation failed: {1}").format(
                    name, result.get("error", self.tr("Unknown error"))
                ),
                "error",
            )

    def _on_update_all(self) -> None:
        cache_dir = Path.home() / ".astrocrawl" / "rules_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        worker = _SourceUpdateWorker(cache_dir, self)
        worker.source_progress.connect(lambda n, s: self._show_status(self._update_progress_label(n, s)))
        self._start_source_operation(
            worker,
            result_handler=self._on_update_finished,
            error_handler=self._on_update_error,
        )

    def _on_update_finished(self, result: dict) -> None:
        updated = result.get("updated", [])
        up_to_date = result.get("up_to_date", 0)
        failed = result.get("failed", 0)
        msg, level = self._compose_update_summary(updated, up_to_date, failed)
        self._show_status(msg, level)
        if updated:
            self.sources_updated.emit(updated)
        self._refresh()

    def _on_update_error(self, msg: str) -> None:
        QMessageBox.critical(self, self.tr("Update Failed"), self.tr("Remote source update failed:\n{0}").format(msg))

    def _on_update_single(self) -> None:
        name = self._selected_source_name()
        if not name:
            QMessageBox.information(
                self, self.tr("Update Source"), self.tr("Please select a remote source in the table first.")
            )
            return
        self._show_status(self.tr("Updating source '{0}'...").format(name))
        cache_dir = Path.home() / ".astrocrawl" / "rules_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        worker = _SourceUpdateWorker(cache_dir, self, source_name=name)
        worker.source_progress.connect(lambda n, s: self._show_status(self._update_progress_label(n, s)))
        self._start_source_operation(
            worker,
            result_handler=lambda r: self._on_update_finished(r),
            error_handler=self._on_update_error,
        )

    def _on_validate_all(self) -> None:
        sources = list_sources_from_file()
        if not sources:
            self._show_status(self.tr("No sources to validate"), "warning")
            return
        self._show_status(self.tr("Validating {0} sources...").format(len(sources)))
        worker = _SourceValidateAllWorker(self)
        self._start_source_operation(
            worker,
            result_handler=self._on_validate_all_finished,
            error_handler=lambda e: self._show_status(self.tr("Validate all failed: {0}").format(e), "error"),
        )

    def _on_validate_all_finished(self, result: dict) -> None:
        passed = result.get("passed", 0)
        failed = result.get("failed", 0)
        total = passed + failed
        if failed:
            self._show_status(
                self.tr("Validate all done: {0}/{1} passed, {2} failed").format(passed, total, failed), "warning"
            )
        else:
            self._show_status(self.tr("Validate all done: {0} sources all passed").format(total), "success")

    def _update_progress_label(self, name: str, status: str) -> str:
        """将 source_progress 信号转换为状态栏可读文本。"""
        if status.startswith("updated:"):
            return str(self.tr("{name} updated ({n} rules)").format(name=name, n=status.split(":")[1]))
        if status == "fetching":
            return str(self.tr("Updating {name}...").format(name=name))
        if status == "up_to_date":
            return str(self.tr("{name} is up to date").format(name=name))
        if status == "failed":
            return str(self.tr("{name} update failed").format(name=name))
        return f"{name}: {status}"

    def _compose_update_summary(self, updated: list, up_to_date: int, failed: int) -> tuple[str, str]:
        """组合更新全部的五态结果消息。返回 (message, level)。"""
        parts = []
        if updated:
            parts.append(self.tr("{n} sources updated").format(n=len(updated)))
        if up_to_date and not updated and not failed:
            parts.append(self.tr("All sources up to date ({n})").format(n=up_to_date))
        elif up_to_date:
            parts.append(self.tr("{n} up to date").format(n=up_to_date))
        if failed:
            parts.append(self.tr("{n} failed").format(n=failed))
        if not parts:
            return self.tr("No remote sources to update"), "success"
        if failed and not updated:
            return ", ".join(parts), "warning"
        if failed:
            return ", ".join(parts), "warning"
        return "，".join(parts), "success"


# ═══════════════════════════════════════════════════════════════════════
# 规则管理对话框
# ═══════════════════════════════════════════════════════════════════════


class RulesDialog(QDialog):
    def __init__(self, parent=None, cfg: Optional[CrawlerConfig] = None):
        super().__init__(parent)
        self.setWindowModality(Qt.WindowModal)
        self.setWindowTitle(self.tr("Rule Management"))
        self._cfg = cfg or CrawlerConfig()

        layout = QVBoxLayout(self)
        layout.setSpacing(SPACE_MD)

        self._tabs = QTabWidget()

        # ── 统一脉动条 + 状态栏（_ProgressStatusBar 复合组件，先于子页面创建以提供 status_callback） ──
        self._psb = _ProgressStatusBar()
        self._psb.show_status(self.tr("Ready"))

        self._rule_page = _RuleTablePage(self._cfg, status_callback=self._psb.show_status)
        self._custom_page = _CustomPage(status_callback=self._psb.show_status, snapshot_provider=self._rule_page)
        self._source_page = _SourcePage(status_callback=self._psb.show_status)

        self._tabs.addTab(self._rule_page, self.tr("Rule List"))
        self._tabs.addTab(self._custom_page, self.tr("Custom"))
        self._tabs.addTab(self._source_page, self.tr("Remote Sources"))

        layout.addWidget(self._tabs, 1)
        layout.addWidget(self._psb)

        self._psb.connect_page(self._rule_page)
        self._psb.connect_page(self._custom_page)
        self._psb.connect_page(self._source_page)

        # ── 底部按钮 ──
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(SPACE_MD)
        refresh_all_btn = QPushButton(self.tr("Refresh All"))
        refresh_all_btn.setToolTip(self.tr("Discard pending changes, reload from disk"))
        refresh_all_btn.clicked.connect(self._on_refresh_all)
        apply_btn = QPushButton(self.tr("Apply"))
        apply_btn.setToolTip(self.tr("Commit pending enable/disable changes"))
        apply_btn.clicked.connect(self._on_apply)
        cancel_btn = QPushButton(self.tr("Cancel"))
        cancel_btn.setToolTip(self.tr("Discard pending changes and close dialog"))
        cancel_btn.clicked.connect(self._on_cancel)
        confirm_btn = QPushButton(self.tr("OK"))
        confirm_btn.setToolTip(self.tr("Commit changes and close dialog"))
        confirm_btn.clicked.connect(self._on_confirm)
        btn_layout.addWidget(refresh_all_btn, 1)
        btn_layout.addWidget(apply_btn, 1)
        btn_layout.addWidget(cancel_btn, 1)
        btn_layout.addWidget(confirm_btn, 1)
        layout.addLayout(btn_layout)

        self._custom_page.rule_generated.connect(self._on_rule_generated)
        self._source_page.sources_updated.connect(self._on_sources_updated)

        self._rule_page.init_lifecycle()

        self._theme_mgr = get_theme_manager()
        if self._theme_mgr is not None:
            self._theme_mgr.theme_changed.connect(self._on_theme_changed)
        self._on_theme_changed()

        self._tabs.setCurrentIndex(0)  # 按 Tab 0（规则列表，内容最多）计算尺寸
        self.adjustSize()
        ideal_h = self.height()
        self.setMaximumWidth(self.width())
        screen = self.screen()
        if screen:
            max_h = int(screen.availableGeometry().height() * 0.85)
            self.setMaximumHeight(min(ideal_h, max_h))
            self.setMinimumHeight(min(ideal_h, max_h))
        else:
            self.setMaximumHeight(ideal_h)
            self.setMinimumHeight(ideal_h)
        self.setMinimumWidth(self.width())

    def _on_theme_changed(self) -> None:
        if self._theme_mgr is None:
            return
        bg = self._theme_mgr.get("input_bg")
        # QPalette on each page widget — avoids QSS disrupting QTableView QPalette rendering
        for i in range(self._tabs.count()):
            page = self._tabs.widget(i)
            if page is not None:
                p = page.palette()
                p.setColor(QPalette.Window, QColor(bg))
                page.setPalette(p)
                page.setAutoFillBackground(True)
                page.setAttribute(Qt.WA_InputMethodEnabled, True)

    def _on_refresh_all(self) -> None:
        self._rule_page.discard_pending()
        self._source_page.discard_pending()
        self._rule_page.refresh()
        self._source_page._refresh()
        self._custom_page.reset()

    def _on_apply(self) -> None:
        self._rule_page.apply_pending()
        self._source_page.apply_pending()

    def _on_confirm(self) -> None:
        self._on_apply()
        self.accept()

    def reject(self) -> None:
        self._psb.dispose()
        self._rule_page._cleanup_worker()
        self._custom_page._cleanup_worker()
        self._source_page._cleanup_worker()
        self._custom_page.reset()
        super().reject()

    def accept(self) -> None:
        self._rule_page._cleanup_worker()
        self._custom_page._cleanup_worker()
        self._source_page._cleanup_worker()
        super().accept()

    def _on_cancel(self) -> None:
        self._rule_page.discard_pending()
        self._source_page.discard_pending()
        self.reject()

    def _on_sources_updated(self, updated_names: list) -> None:
        self._rule_page.refresh()
        self._psb.show_status(self.tr("Updated {0} remote sources, rule list refreshed").format(len(updated_names)))

    def _on_rule_generated(self, rule_data: dict) -> None:
        self._rule_page.refresh()

"""Table Management Page 基类 — Template Method 模式（ADR-0007 决策 1）。

提供 QTableView + QSortFilterProxyModel + 搜索栏 + 按钮网格 + pending toggles
生命周期。子类填空列定义、Model、CRUD 回调即可。
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QAbstractTableModel, QRegularExpression, QSortFilterProxyModel, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QTableView, QVBoxLayout, QWidget

from astrocrawl._constants import QLINEEDIT_MAX
from astrocrawl.gui._style import ColumnDef, configure_table_header, create_managed_table
from astrocrawl.gui._tokens import SPACE_MD, SPACE_SM


class _FilterProxy(QSortFilterProxyModel):
    """QSortFilterProxyModel — 仅搜索 _search_columns 指定的列。

    对标 QTableWidget.setRowHidden() 的模型层替代。
    """

    def __init__(self, search_columns: tuple[int, ...], parent=None):
        super().__init__(parent)
        self._search_columns = search_columns

    def filterAcceptsRow(self, source_row, source_parent):
        pattern = self.filterRegularExpression()
        if pattern is None or not pattern.pattern():
            return True
        keyword = pattern.pattern().lower()
        model = self.sourceModel()
        if model is None:
            return True
        for col in self._search_columns:
            idx = model.index(source_row, col, source_parent)
            if keyword in str(idx.data(Qt.ItemDataRole.DisplayRole) or "").lower():
                return True
        return False


class _TableManagementPage(QWidget):
    """列表管理页基类 — Template Method 模式。

    子类必须实现：
      - _define_columns() -> list[ColumnDef]
      - _create_model() -> QAbstractTableModel
      - _on_add() / _on_edit(row) / _on_remove(row)
      - _apply_toggle(name, enabled)

    子类可选覆盖：
      - _search_columns() -> tuple[int, ...]  （默认 (0, 1)）
      - _empty_text() -> str                   （默认 ""）
      - _extra_buttons() -> list[tuple[str, Callable]]  （默认 []）
    """

    # 子类信号命名约定：finished → 过去分词
    pending_applied = Signal()
    pending_discarded = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._columns: list[ColumnDef] = []
        self._table: QTableView | None = None
        self._proxy: _FilterProxy | None = None
        self._model: QAbstractTableModel | None = None
        self._search: QLineEdit | None = None
        self._empty_label: QLabel | None = None
        self._pending_toggles: dict[str, bool] = {}
        self._setup_ui()

    # ── 抽象方法（子类必须实现）───────────────────────────────────────────

    def _define_columns(self) -> list[ColumnDef]:
        raise NotImplementedError

    def _create_model(self) -> QAbstractTableModel:
        raise NotImplementedError

    def _on_add(self) -> None:
        raise NotImplementedError

    def _on_edit(self, row: int) -> None:
        raise NotImplementedError

    def _on_remove(self, row: int) -> None:
        raise NotImplementedError

    def _apply_toggle(self, name: str, enabled: bool) -> None:
        raise NotImplementedError

    # ── 可选覆盖 ──────────────────────────────────────────────────────────

    def _search_columns(self) -> tuple[int, ...]:
        return (0, 1)

    def _empty_text(self) -> str:
        return ""

    def _extra_buttons(self) -> list[tuple[str, Callable[[], None]]]:
        return []

    # ── 基类提供 ──────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(SPACE_MD)
        layout.setContentsMargins(0, SPACE_MD, 0, 0)

        # ── 工具栏：搜索 + 按钮 ──
        toolbar = QHBoxLayout()
        toolbar.setSpacing(SPACE_SM)

        self._search = QLineEdit()
        self._search.setMaxLength(QLINEEDIT_MAX)
        self._search.setPlaceholderText(self.tr("Search..."))
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_filter)
        toolbar.addWidget(self._search, 1)

        for label, callback in self._extra_buttons():
            btn = QPushButton(label)
            btn.clicked.connect(callback)
            toolbar.addWidget(btn)

        add_btn = QPushButton(self.tr("Add"))
        add_btn.setObjectName("add-btn")
        add_btn.clicked.connect(self._on_add)
        toolbar.addWidget(add_btn)

        self._edit_btn = QPushButton(self.tr("Edit"))
        self._edit_btn.setObjectName("edit-btn")
        self._edit_btn.clicked.connect(self._on_edit_selected)
        toolbar.addWidget(self._edit_btn)

        self._remove_btn = QPushButton(self.tr("Delete"))
        self._remove_btn.setObjectName("remove-btn")
        self._remove_btn.clicked.connect(self._on_remove_selected)
        toolbar.addWidget(self._remove_btn)

        layout.addLayout(toolbar)

        # ── 表格 ──
        self._columns = self._define_columns()
        self._table = create_managed_table()
        self._table.doubleClicked.connect(
            lambda idx: self._on_edit(self._proxy.mapToSource(idx).row()) if self._proxy else None
        )

        self._model = self._create_model()
        self._proxy = _FilterProxy(self._search_columns(), self)
        self._proxy.setSourceModel(self._model)
        self._table.setModel(self._proxy)
        configure_table_header(self._table, self._columns)

        layout.addWidget(self._table, 1)

        # ── 空状态 ──
        self._empty_label = QLabel(self._empty_text())
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setVisible(False)
        layout.addWidget(self._empty_label)

    def _on_edit_selected(self) -> None:
        row = self._selected_source_row()
        if row >= 0:
            self._on_edit(row)

    def _on_remove_selected(self) -> None:
        row = self._selected_source_row()
        if row >= 0:
            self._on_remove(row)

    def _selected_source_row(self) -> int:
        """返回 proxy 当前选中行对应的 source model row。"""
        if self._table is None or self._proxy is None:
            return -1
        idx = self._table.currentIndex()
        if not idx.isValid():
            return -1
        return int(self._proxy.mapToSource(idx).row())

    def _apply_filter(self, keyword: str) -> None:
        if self._proxy is None:
            return
        if not keyword:
            self._proxy.setFilterRegularExpression(QRegularExpression())
        else:
            self._proxy.setFilterRegularExpression(
                QRegularExpression(keyword, QRegularExpression.PatternOption.CaseInsensitiveOption)
            )

    def refresh(self) -> None:
        """Model 通知重载 → proxy invalidate → pending 覆盖。

        子类 Model 实现 ``load()`` 方法后，基类调用此方法触发刷新。
        """
        if self._model is None:
            return
        load_fn = getattr(self._model, "load", None)
        if callable(load_fn):
            load_fn()
        if self._proxy is not None:
            self._proxy.invalidate()

    # ── pending toggles 生命周期 ──────────────────────────────────────────

    def _set_pending(self, name: str, enabled: bool) -> None:
        self._pending_toggles[name] = enabled

    def apply_pending(self) -> None:
        """将 _pending_toggles 写入持久化存储。"""
        for name, enabled in self._pending_toggles.items():
            self._apply_toggle(name, enabled)
        self._pending_toggles.clear()
        self.pending_applied.emit()

    def discard_pending(self) -> None:
        """丢弃所有 pending toggles，Model 重载恢复原始状态。"""
        self._pending_toggles.clear()
        self.refresh()
        self.pending_discarded.emit()

    @property
    def has_pending(self) -> bool:
        return len(self._pending_toggles) > 0

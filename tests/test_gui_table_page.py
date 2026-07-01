"""gui/_table_page.py 测试 — _TableManagementPage + _FilterProxy。

覆盖:
- _FilterProxy: filterAcceptsRow / 搜索空 keyword / case-insensitive / no-match
- _TableManagementPage: 按钮创建 / _selected_source_row / _apply_filter / pending toggles
  / _on_edit_selected / _on_remove_selected / _extra_buttons / apply_pending signal
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QRegularExpression, Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel

from astrocrawl.gui._style import ColumnDef
from astrocrawl.gui._table_page import _FilterProxy, _TableManagementPage

pytestmark = pytest.mark.gui


class TestFilterProxy:
    def test_empty_pattern_accepts_all(self, qapp):
        model = QStandardItemModel(2, 2)
        model.setItem(0, 0, QStandardItem("hello"))
        model.setItem(1, 0, QStandardItem("world"))

        proxy = _FilterProxy((0, 1))
        proxy.setSourceModel(model)
        proxy.setFilterRegularExpression(QRegularExpression())

        assert proxy.rowCount() == 2

    def test_filter_matches_first_column(self, qapp):
        model = QStandardItemModel(2, 2)
        model.setItem(0, 0, QStandardItem("alpha"))
        model.setItem(1, 0, QStandardItem("beta"))

        proxy = _FilterProxy((0, 1))
        proxy.setSourceModel(model)
        escaped = QRegularExpression.escape("alpha")
        proxy.setFilterRegularExpression(
            QRegularExpression(escaped, QRegularExpression.PatternOption.CaseInsensitiveOption)
        )

        assert proxy.rowCount() == 1
        assert proxy.index(0, 0).data(Qt.ItemDataRole.DisplayRole) == "alpha"

    def test_filter_case_insensitive(self, qapp):
        model = QStandardItemModel(2, 2)
        model.setItem(0, 0, QStandardItem("Alpha"))
        model.setItem(1, 0, QStandardItem("BETA"))

        proxy = _FilterProxy((0, 1))
        proxy.setSourceModel(model)
        escaped = QRegularExpression.escape("BETA")
        proxy.setFilterRegularExpression(
            QRegularExpression(escaped, QRegularExpression.PatternOption.CaseInsensitiveOption)
        )

        assert proxy.rowCount() == 1

    def test_filter_no_match_returns_empty(self, qapp):
        model = QStandardItemModel(2, 2)
        model.setItem(0, 0, QStandardItem("alpha"))
        model.setItem(1, 0, QStandardItem("beta"))

        proxy = _FilterProxy((0, 1))
        proxy.setSourceModel(model)
        escaped = QRegularExpression.escape("xyz")
        proxy.setFilterRegularExpression(
            QRegularExpression(escaped, QRegularExpression.PatternOption.CaseInsensitiveOption)
        )

        assert proxy.rowCount() == 0


class _FakeListModel(QStandardItemModel):
    """测试用 Model — 提供 load() 方法供 _TableManagementPage.refresh() 调用。"""

    def __init__(self, columns: list[ColumnDef]):
        super().__init__(0, len(columns))
        for col_idx, col_def in enumerate(columns):
            self.setHeaderData(col_idx, Qt.Orientation.Horizontal, col_def.label)
        self._load_count = 0

    def load(self) -> None:
        self._load_count += 1
        self.removeRows(0, self.rowCount())
        items = [
            ["alpha", "ok"],
            ["beta", "failed"],
        ]
        for row_data in items:
            row = []
            for val in row_data:
                item = QStandardItem(val)
                row.append(item)
            self.appendRow(row)


class _FakeTablePage(_TableManagementPage):
    """测试用子类 — 实现 6 个抽象方法。"""

    def _define_columns(self):
        return [
            ColumnDef(key="name", label="Name"),
            ColumnDef(key="status", label="Status", resize="fixed", width=80),
        ]

    def _create_model(self):
        return _FakeListModel(self._columns)

    def _on_add(self):
        pass

    def _on_edit(self, row):
        pass

    def _on_remove(self, row):
        pass

    def _apply_toggle(self, name, enabled):
        pass


class TestTableManagementPage:
    def test_init_creates_ui(self, qapp):
        page = _FakeTablePage()
        assert page._table is not None
        assert page._proxy is not None
        assert page._model is not None
        assert page._search is not None

    def test_search_columns_default(self, qapp):
        page = _FakeTablePage()
        assert page._search_columns() == (0, 1)

    def test_empty_text_default(self, qapp):
        page = _FakeTablePage()
        assert page._empty_text() == ""

    def test_extra_buttons_default(self, qapp):
        page = _FakeTablePage()
        assert page._extra_buttons() == []

    def test_model_has_data(self, qapp):
        page = _FakeTablePage()
        # _FakeListModel.load is called during _setup_ui → no, it's not
        page.refresh()
        assert page._model.rowCount() == 2

    def test_apply_filter(self, qapp):
        page = _FakeTablePage()
        page.refresh()
        assert page._proxy.rowCount() == 2

        page._apply_filter("alpha")
        assert page._proxy.rowCount() == 1

        page._apply_filter("")
        assert page._proxy.rowCount() == 2

    def test_pending_toggles_lifecycle(self, qapp):
        page = _FakeTablePage()
        assert page.has_pending is False

        page._set_pending("alpha", False)
        assert page.has_pending is True

        page.discard_pending()
        assert page.has_pending is False

    def test_refresh_calls_model_load(self, qapp):
        page = _FakeTablePage()
        assert page._model._load_count == 0
        page.refresh()
        assert page._model._load_count == 1
        page.refresh()
        assert page._model._load_count == 2

    def test_extra_buttons_creates_widgets(self, qapp):
        page = _FakeTablePage()
        orig = page._extra_buttons
        page._extra_buttons = lambda: [("BtnA", lambda: None), ("BtnB", lambda: None)]
        page._setup_ui()  # 重建 UI
        # 仅验证不抛异常 — _extra_buttons 创建的按钮已嵌入 toolbar
        assert page._search is not None
        page._extra_buttons = orig

    def test_edit_selected_no_selection_does_not_call_edit(self, qapp):
        page = _FakeTablePage()
        called = []
        page._on_edit = lambda row: called.append(row)
        page._on_edit_selected()
        assert called == []

    def test_edit_selected_valid_calls_edit(self, qapp):
        page = _FakeTablePage()
        page.refresh()
        page._table.selectRow(0)
        called = []
        page._on_edit = lambda row: called.append(row)
        page._on_edit_selected()
        assert len(called) == 1

    def test_remove_selected_no_selection_does_not_call_remove(self, qapp):
        page = _FakeTablePage()
        called = []
        page._on_remove = lambda row: called.append(row)
        page._on_remove_selected()
        assert called == []

    def test_remove_selected_valid_calls_remove(self, qapp):
        page = _FakeTablePage()
        page.refresh()
        page._table.selectRow(0)
        called = []
        page._on_remove = lambda row: called.append(row)
        page._on_remove_selected()
        assert len(called) == 1

    def test_double_click_with_filter_passes_source_row(self, qapp):
        """B1 回归: 过滤后双击应传递 source row 而非 proxy row。"""
        page = _FakeTablePage()
        page.refresh()
        # source model: [["alpha","ok"], ["beta","failed"]]
        # filter 只显示 "beta" → proxy row 0 对应 source row 1
        page._apply_filter("beta")
        assert page._proxy.rowCount() == 1

        called = []
        page._on_edit = lambda row: called.append(row)
        proxy_idx = page._proxy.index(0, 0)
        page._table.doubleClicked.emit(proxy_idx)
        assert called == [1]  # source row 1, not proxy row 0

    def test_double_click_without_filter_passes_source_row(self, qapp):
        """无过滤时双击 — proxy row ≡ source row。"""
        page = _FakeTablePage()
        page.refresh()
        assert page._proxy.rowCount() == 2

        called = []
        page._on_edit = lambda row: called.append(row)
        proxy_idx = page._proxy.index(1, 0)
        page._table.doubleClicked.emit(proxy_idx)
        assert called == [1]  # 1:1 映射

    def test_apply_pending_emits_signal(self, qapp):
        page = _FakeTablePage()
        emitted = []
        page.pending_applied.connect(lambda: emitted.append(True))
        page._set_pending("alpha", False)
        page.apply_pending()
        assert len(emitted) == 1

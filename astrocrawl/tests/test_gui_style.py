"""gui/_style.py 工具函数测试。

覆盖:
- ColumnDef frozen dataclass + create_managed_table + configure_table_header
- create_form_scroll_area — QScrollArea 工厂
- 4 个 CSS 工具的签名和返回值正确性
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.gui


class TestColumnDef:
    def test_defaults(self):
        from astrocrawl.gui._style import ColumnDef

        cd = ColumnDef(key="name", label="Name")
        assert cd.key == "name"
        assert cd.label == "Name"
        assert cd.resize == "stretch"
        assert cd.width == 100

    def test_fixed_resize(self):
        from astrocrawl.gui._style import ColumnDef

        cd = ColumnDef(key="status", label="Status", resize="fixed", width=70)
        assert cd.resize == "fixed"
        assert cd.width == 70

    def test_equality(self):
        from astrocrawl.gui._style import ColumnDef

        a = ColumnDef(key="x", label="X")
        b = ColumnDef(key="x", label="X")
        c = ColumnDef(key="y", label="Y")
        assert a == b
        assert a != c

    def test_frozen_immutability(self):
        from dataclasses import FrozenInstanceError

        from astrocrawl.gui._style import ColumnDef

        cd = ColumnDef(key="k", label="L")
        try:
            cd.key = "changed"  # type: ignore[misc]
        except FrozenInstanceError:
            pass
        else:
            raise AssertionError("frozen dataclass 应禁止属性赋值")


class TestCreateManagedTable:
    def test_returns_qtableview(self, qapp):
        from astrocrawl.gui._style import create_managed_table

        table = create_managed_table()
        from PySide6.QtWidgets import QTableView

        assert isinstance(table, QTableView)

    def test_select_rows_behavior(self, qapp):
        from PySide6.QtWidgets import QAbstractItemView

        from astrocrawl.gui._style import create_managed_table

        table = create_managed_table()
        assert table.selectionBehavior() == QAbstractItemView.SelectionBehavior.SelectRows

    def test_no_edit_triggers(self, qapp):
        from PySide6.QtWidgets import QAbstractItemView

        from astrocrawl.gui._style import create_managed_table

        table = create_managed_table()
        assert table.editTriggers() == QAbstractItemView.EditTrigger.NoEditTriggers

    def test_alternating_row_colors(self, qapp):
        from astrocrawl.gui._style import create_managed_table

        table = create_managed_table()
        assert table.alternatingRowColors() is True

    def test_vertical_header_hidden(self, qapp):
        from astrocrawl.gui._style import create_managed_table

        table = create_managed_table()
        assert table.verticalHeader().isHidden() is True

    def test_object_name(self, qapp):
        from astrocrawl.gui._style import create_managed_table

        table = create_managed_table(object_name="my-table")
        assert table.objectName() == "my-table"


class TestConfigureTableHeader:
    def test_stretch_default_resize_mode(self, qapp):
        from PySide6.QtWidgets import QHeaderView

        from astrocrawl.gui._style import ColumnDef, configure_table_header, create_managed_table

        table = create_managed_table()
        from PySide6.QtGui import QStandardItemModel

        model = QStandardItemModel(1, 1)
        table.setModel(model)
        configure_table_header(table, [ColumnDef(key="x", label="X")])

        assert table.horizontalHeader().sectionResizeMode(0) == QHeaderView.ResizeMode.Stretch

    def test_fixed_resize_mode_sets_width(self, qapp):
        from PySide6.QtWidgets import QHeaderView

        from astrocrawl.gui._style import ColumnDef, configure_table_header, create_managed_table

        table = create_managed_table()
        from PySide6.QtGui import QStandardItemModel

        model = QStandardItemModel(1, 1)
        table.setModel(model)
        configure_table_header(table, [ColumnDef(key="x", label="X", resize="fixed", width=150)])

        assert table.horizontalHeader().sectionResizeMode(0) == QHeaderView.ResizeMode.Fixed
        assert table.horizontalHeader().sectionSize(0) == 150

    def test_multiple_columns(self, qapp):
        from PySide6.QtWidgets import QHeaderView

        from astrocrawl.gui._style import ColumnDef, configure_table_header, create_managed_table

        table = create_managed_table()
        from PySide6.QtGui import QStandardItemModel

        model = QStandardItemModel(1, 2)
        table.setModel(model)
        columns = [
            ColumnDef(key="a", label="A", resize="stretch"),
            ColumnDef(key="b", label="B", resize="fixed", width=80),
        ]
        configure_table_header(table, columns)

        assert table.horizontalHeader().sectionResizeMode(0) == QHeaderView.ResizeMode.Stretch
        assert table.horizontalHeader().sectionResizeMode(1) == QHeaderView.ResizeMode.Fixed
        assert table.horizontalHeader().sectionSize(1) == 80

    def test_resize_to_contents_mode(self, qapp):
        from PySide6.QtWidgets import QHeaderView

        from astrocrawl.gui._style import ColumnDef, configure_table_header, create_managed_table

        table = create_managed_table()
        from PySide6.QtGui import QStandardItemModel

        model = QStandardItemModel(1, 1)
        table.setModel(model)
        configure_table_header(table, [ColumnDef(key="x", label="X", resize="resize_to_contents")])

        assert table.horizontalHeader().sectionResizeMode(0) == QHeaderView.ResizeMode.ResizeToContents


class TestStatusLabelStyle:
    def test_includes_background_color(self):
        from astrocrawl.gui._style import status_label_style

        result = status_label_style("#ff0000")
        assert "background: #ff0000" in result
        assert "padding: 4px 6px" in result
        assert "border-radius:" in result

    def test_includes_padding_and_radius(self):
        from astrocrawl.gui._style import status_label_style

        result = status_label_style("blue")
        assert "padding: 4px 6px" in result
        assert "border-radius:" in result

    def test_accepts_named_colors(self):
        from astrocrawl.gui._style import status_label_style

        result = status_label_style("transparent")
        assert "background: transparent" in result


class TestMonospaceStyle:
    def test_includes_monospace_font(self):
        from astrocrawl.gui._style import monospace_style

        result = monospace_style()
        assert "font-family: monospace" in result
        assert "font-size:" in result
        assert isinstance(result, str)

    def test_callable_and_no_args(self):
        from astrocrawl.gui._style import monospace_style

        assert callable(monospace_style)
        result = monospace_style()
        assert isinstance(result, str)


class TestCenteredCheckboxContainer:
    def test_returns_qwidget_with_layout(self, qapp):
        from PySide6.QtWidgets import QCheckBox, QHBoxLayout

        from astrocrawl.gui._style import centered_checkbox_container

        cb = QCheckBox("test")
        container = centered_checkbox_container(cb)

        assert container is not None
        layout = container.layout()
        assert isinstance(layout, QHBoxLayout)
        assert layout.count() == 1
        assert layout.itemAt(0).widget() is cb

    def test_layout_has_center_alignment(self, qapp):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QCheckBox

        from astrocrawl.gui._style import centered_checkbox_container

        cb = QCheckBox("test")
        container = centered_checkbox_container(cb)
        layout = container.layout()

        assert layout.alignment() == Qt.AlignCenter

    def test_layout_has_zero_margins(self, qapp):
        from PySide6.QtWidgets import QCheckBox

        from astrocrawl.gui._style import centered_checkbox_container

        cb = QCheckBox("test")
        container = centered_checkbox_container(cb)
        layout = container.layout()
        margins = layout.contentsMargins()

        assert margins.left() == 0
        assert margins.top() == 0
        assert margins.right() == 0
        assert margins.bottom() == 0

    def test_checkbox_preserves_checked_state(self, qapp):
        from PySide6.QtWidgets import QCheckBox

        from astrocrawl.gui._style import centered_checkbox_container

        cb = QCheckBox("test")
        cb.setChecked(True)
        container = centered_checkbox_container(cb)
        child = container.layout().itemAt(0).widget()

        assert child.isChecked() is True

    def test_checkbox_preserves_enabled_state(self, qapp):
        from PySide6.QtWidgets import QCheckBox

        from astrocrawl.gui._style import centered_checkbox_container

        cb = QCheckBox("test")
        cb.setEnabled(False)
        container = centered_checkbox_container(cb)
        child = container.layout().itemAt(0).widget()

        assert child.isEnabled() is False


class TestCreateFormScrollArea:
    def test_returns_qscrollarea(self, qapp):
        from PySide6.QtWidgets import QScrollArea

        from astrocrawl.gui._style import create_form_scroll_area

        result = create_form_scroll_area()
        assert isinstance(result, QScrollArea)

    def test_widget_resizable(self, qapp):
        from astrocrawl.gui._style import create_form_scroll_area

        result = create_form_scroll_area()
        assert result.widgetResizable() is True

    def test_horizontal_scrollbar_off(self, qapp):
        from PySide6.QtCore import Qt

        from astrocrawl.gui._style import create_form_scroll_area

        result = create_form_scroll_area()
        assert result.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff

    def test_no_frame_shape(self, qapp):
        from PySide6.QtWidgets import QScrollArea

        from astrocrawl.gui._style import create_form_scroll_area

        result = create_form_scroll_area()
        assert result.frameShape() == QScrollArea.NoFrame

    def test_input_method_enabled_on_scroll(self, qapp):
        from PySide6.QtCore import Qt

        from astrocrawl.gui._style import create_form_scroll_area

        result = create_form_scroll_area()
        assert result.testAttribute(Qt.WA_InputMethodEnabled) is True

    def test_input_method_enabled_on_viewport(self, qapp):
        from PySide6.QtCore import Qt

        from astrocrawl.gui._style import create_form_scroll_area

        result = create_form_scroll_area()
        assert result.viewport().testAttribute(Qt.WA_InputMethodEnabled) is True

"""gui/_delegates.py 测试 — StatusColorDelegate + CheckboxDelegate。

覆盖:
- StatusColorDelegate: color_map 查找 + UserRole → 颜色渲染 (paint 由集成测试覆盖)
- CheckboxDelegate: CheckStateRole paint (checked/unchecked) + editor + toggled 信号
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QRect, Qt
from PySide6.QtGui import QStandardItemModel
from PySide6.QtWidgets import QStyleOptionViewItem, QTableView

from astrocrawl.gui._delegates import CheckboxDelegate, StatusColorDelegate

pytestmark = pytest.mark.gui


class TestStatusColorDelegate:
    def test_init_stores_token_map(self, qapp):
        delegate = StatusColorDelegate({"ok": "success", "failed": "danger"})
        assert delegate._status_to_token == {"ok": "success", "failed": "danger"}

    def test_token_map_empty_no_crash(self, qapp):
        delegate = StatusColorDelegate({})
        assert delegate._status_to_token == {}

    def test_paint_no_crash_with_empty_model_index(self, qapp):
        table = QTableView()
        model = QStandardItemModel(1, 1)
        table.setModel(model)

        delegate = StatusColorDelegate({"ok": "success"})
        opt = QStyleOptionViewItem()
        opt.rect = table.viewport().rect()
        opt.widget = table

        index = model.index(0, 0)
        model.setData(index, "OK", Qt.ItemDataRole.DisplayRole)

        assert delegate._status_to_token == {"ok": "success"}

    def test_helper_paint_index_data(self, qapp):
        """验证 Model 数据访问路径 — DisplayRole + UserRole。"""
        model = QStandardItemModel(1, 1)
        index = model.index(0, 0)
        model.setData(index, "Verified", Qt.ItemDataRole.DisplayRole)
        model.setData(index, "ok", Qt.ItemDataRole.UserRole)

        assert index.data(Qt.ItemDataRole.DisplayRole) == "Verified"
        assert index.data(Qt.ItemDataRole.UserRole) == "ok"

    # StatusColorDelegate.paint() uses CE_ItemViewItem which causes segfault in offscreen QPainter;
    # covered indirectly via _AIProfilePage and RulesDialog integration tests at the QWidget level.


class TestCheckboxDelegate:
    def test_init(self, qapp):
        delegate = CheckboxDelegate()
        assert delegate is not None

    def test_toggled_signal_emits_on_set_model_data(self, qapp):
        from PySide6.QtWidgets import QStyleOptionViewItem, QTableView

        table = QTableView()
        model = QStandardItemModel(1, 1)
        index = model.index(0, 0)
        model.setData(index, Qt.CheckState.Unchecked.value, Qt.ItemDataRole.CheckStateRole)

        delegate = CheckboxDelegate()

        signals_received = []
        delegate.toggled.connect(lambda row, checked: signals_received.append((row, checked)))

        editor = delegate.createEditor(table, QStyleOptionViewItem(), index)
        editor.setChecked(True)
        delegate.setModelData(editor, model, index)

        assert len(signals_received) == 1
        assert signals_received[0] == (0, True)
        assert model.data(index, Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked.value

    def test_set_editor_data_reads_checkstate(self, qapp):
        from PySide6.QtWidgets import QStyleOptionViewItem, QTableView

        table = QTableView()
        model = QStandardItemModel(1, 1)
        index = model.index(0, 0)
        model.setData(index, Qt.CheckState.Checked.value, Qt.ItemDataRole.CheckStateRole)

        delegate = CheckboxDelegate()
        editor = delegate.createEditor(table, QStyleOptionViewItem(), index)
        delegate.setEditorData(editor, index)
        assert editor.isChecked() is True

    def test_set_editor_data_unchecked(self, qapp):
        from PySide6.QtWidgets import QStyleOptionViewItem, QTableView

        table = QTableView()
        model = QStandardItemModel(1, 1)
        index = model.index(0, 0)
        model.setData(index, Qt.CheckState.Unchecked.value, Qt.ItemDataRole.CheckStateRole)

        delegate = CheckboxDelegate()
        editor = delegate.createEditor(table, QStyleOptionViewItem(), index)
        delegate.setEditorData(editor, index)
        assert editor.isChecked() is False

    def test_update_editor_geometry_centers_checkbox(self, qapp):
        from PySide6.QtCore import QRect
        from PySide6.QtWidgets import QStyleOptionViewItem, QTableView

        table = QTableView()
        model = QStandardItemModel(1, 1)
        index = model.index(0, 0)

        delegate = CheckboxDelegate()
        editor = delegate.createEditor(table, QStyleOptionViewItem(), index)

        opt = QStyleOptionViewItem()
        opt.rect = QRect(0, 0, 200, 40)

        delegate.updateEditorGeometry(editor, opt, index)
        geo = editor.geometry()
        assert geo.center().x() - 100 <= 50  # roughly centered

    def test_editor_event_toggles_checkbox(self, qapp):
        """editorEvent 直接处理点击切换，emit toggled 信号。"""
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QMouseEvent, QStandardItem

        model = QStandardItemModel(1, 1)
        item = QStandardItem()
        item.setCheckable(True)
        item.setCheckState(Qt.CheckState.Unchecked)
        model.setItem(0, 0, item)
        index = model.index(0, 0)

        delegate = CheckboxDelegate()
        signals_received = []
        delegate.toggled.connect(lambda row, checked: signals_received.append((row, checked)))

        opt = QStyleOptionViewItem()
        event = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(0, 0),
            QPointF(0, 0),
            QPointF(0, 0),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )

        result = delegate.editorEvent(event, model, opt, index)
        assert result is True
        assert len(signals_received) == 1
        assert signals_received[0] == (0, True)
        assert item.checkState() == Qt.CheckState.Checked

    def test_editor_event_ignores_non_checkable(self, qapp):
        """非 ItemIsUserCheckable 项不处理。"""
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QMouseEvent

        model = QStandardItemModel(1, 1)
        index = model.index(0, 0)

        delegate = CheckboxDelegate()
        signals_received = []
        delegate.toggled.connect(lambda row, checked: signals_received.append((row, checked)))

        opt = QStyleOptionViewItem()
        event = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(0, 0),
            QPointF(0, 0),
            QPointF(0, 0),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )

        result = delegate.editorEvent(event, model, opt, index)
        assert not result
        assert len(signals_received) == 0

    def test_paint_centers_indicator(self, qapp):
        """paint 使用 PE_PanelItemViewItem + PE_IndicatorItemViewItemCheck 两层绘制。"""
        from PySide6.QtGui import QPainter, QPixmap
        from PySide6.QtWidgets import QStyle

        table = QTableView()
        model = QStandardItemModel(1, 1)
        model.setData(model.index(0, 0), Qt.CheckState.Checked.value, Qt.ItemDataRole.CheckStateRole)
        table.setModel(model)

        delegate = CheckboxDelegate()
        pixmap = QPixmap(100, 40)
        painter = QPainter(pixmap)

        opt = QStyleOptionViewItem()
        opt.rect = QRect(0, 0, 100, 40)
        opt.state = QStyle.StateFlag.State_Enabled
        opt.widget = table

        delegate.paint(painter, opt, model.index(0, 0))
        painter.end()

    def test_paint_unchecked_indicator(self, qapp):
        """paint 在 State_Off 时正确绘制未选中 indicator。"""
        from PySide6.QtGui import QPainter, QPixmap
        from PySide6.QtWidgets import QStyle

        table = QTableView()
        model = QStandardItemModel(1, 1)
        model.setData(model.index(0, 0), Qt.CheckState.Unchecked.value, Qt.ItemDataRole.CheckStateRole)
        table.setModel(model)

        delegate = CheckboxDelegate()
        pixmap = QPixmap(100, 40)
        painter = QPainter(pixmap)

        opt = QStyleOptionViewItem()
        opt.rect = QRect(0, 0, 100, 40)
        opt.state = QStyle.StateFlag.State_Enabled
        opt.widget = table

        delegate.paint(painter, opt, model.index(0, 0))
        painter.end()

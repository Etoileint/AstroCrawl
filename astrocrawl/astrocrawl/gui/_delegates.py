"""Qt 自定义 Delegate — StatusColorDelegate + CheckboxDelegate（ADR-0007）。

每个 Delegate 独立可单元测试，不依赖具体 Model。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QRect, Qt, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QCheckBox, QStyle, QStyledItemDelegate, QStyleOptionButton, QStyleOptionViewItem

from astrocrawl.gui.theme import get_theme_manager


class StatusColorDelegate(QStyledItemDelegate):
    """状态列按颜色渲染文字 — token 化，主题切换自动响应。

    Model 在 ``Qt.UserRole`` 存储状态 key（如 ``"active"`` / ``"degraded"``）。
    Delegate 查 status_to_token map → ``theme.get(token)`` → 实时取色绘制。
    """

    def __init__(self, status_to_token: dict[str, str], parent=None):
        super().__init__(parent)
        self._status_to_token = status_to_token  # {"active": "success", ...}

    def paint(self, painter, option, index):
        painter.save()
        try:
            status_key = index.data(Qt.ItemDataRole.UserRole)
            text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")

            opt = QStyleOptionViewItem(option)
            opt.displayAlignment = Qt.AlignmentFlag.AlignCenter

            widget = option.widget
            style = widget.style() if widget else option.styleObject
            if style is None:
                style = option.widget.style()

            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)

            if status_key and status_key in self._status_to_token:
                token = self._status_to_token[status_key]
                painter.setPen(QColor(get_theme_manager().get(token)))
            else:
                painter.setPen(option.palette.color(QPalette.ColorRole.Text))

            painter.drawText(
                opt.rect.adjusted(4, 0, -4, 0),
                int(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter),
                text,
            )
        finally:
            painter.restore()


class CheckboxDelegate(QStyledItemDelegate):
    """复选框列 Delegate — 自包含 editorEvent + 居中 paint。

    Model 在 ``Qt.CheckStateRole`` 存储 ``Qt.Checked`` / ``Qt.Unchecked``。
    """

    toggled = Signal(int, bool)  # row, checked

    def editorEvent(self, event, model, option, index):
        """直接处理鼠标点击切换，不依赖 editTriggers。"""
        if event.type() == QEvent.Type.MouseButtonRelease:
            if index.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                current = index.data(Qt.ItemDataRole.CheckStateRole)
                new = (
                    Qt.CheckState.Unchecked.value
                    if current == Qt.CheckState.Checked.value
                    else Qt.CheckState.Checked.value
                )
                model.setData(index, new, Qt.ItemDataRole.CheckStateRole)
                self.toggled.emit(index.row(), new == Qt.CheckState.Checked.value)
                return True
        return super().editorEvent(event, model, option, index)

    def paint(self, painter, option, index):
        """分离背景和 indicator 绘制 — 背景覆盖完整单元格，indicator 居中。"""
        painter.save()
        try:
            widget = option.widget
            style = widget.style() if widget else option.styleObject
            if style is None:
                style = option.widget.style()

            # 1. 绘制完整单元格背景（选中高亮、交替行色、焦点框）
            style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, option, painter, widget)

            # 2. 居中绘制 checkbox indicator
            checked = index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked.value
            temp_opt = QStyleOptionViewItem(option)
            temp_opt.features |= QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator
            temp_opt.checkState = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
            temp_opt.text = ""
            indicator_rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemCheckIndicator, temp_opt, widget)
            x = option.rect.x() + (option.rect.width() - indicator_rect.width()) // 2
            y = option.rect.y() + (option.rect.height() - indicator_rect.height()) // 2

            btn_opt = QStyleOptionButton()
            btn_opt.rect = QRect(x, y, indicator_rect.width(), indicator_rect.height())
            btn_opt.state = option.state
            if checked:
                btn_opt.state |= QStyle.StateFlag.State_On
            else:
                btn_opt.state |= QStyle.StateFlag.State_Off
            style.drawPrimitive(QStyle.PrimitiveElement.PE_IndicatorItemViewItemCheck, btn_opt, painter, widget)
        finally:
            painter.restore()

    def createEditor(self, parent, option, index):
        cb = QCheckBox(parent)
        cb.clicked.connect(lambda: self.commitData.emit(cb))
        return cb

    def setEditorData(self, editor, index):
        checked = index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked.value
        editor.setChecked(checked)

    def setModelData(self, editor, model, index):
        checked = editor.isChecked()
        value = Qt.CheckState.Checked.value if checked else Qt.CheckState.Unchecked.value
        model.setData(index, value, Qt.ItemDataRole.CheckStateRole)
        self.toggled.emit(index.row(), checked)

    def updateEditorGeometry(self, editor, option, index):
        size = editor.sizeHint()
        x = option.rect.x() + (option.rect.width() - size.width()) // 2
        y = option.rect.y() + (option.rect.height() - size.height()) // 2
        editor.setGeometry(x, y, size.width(), size.height())

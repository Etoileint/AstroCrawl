"""路由设置页 — _RouteSettingsPage（ADR-0010 Phase 3.5a）。

遍历 PROXY_CONSUMERS 静态字典渲染 consumer→profile→node 分配表。
Profile/Node 列使用 QComboBox 委托，变更即时写入 Preferences。
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QComboBox, QMessageBox, QPushButton, QStyledItemDelegate

from astrocrawl.gui._style import ColumnDef
from astrocrawl.gui._table_page import _TableManagementPage
from astrocrawl.proxy._consumers import PROXY_CONSUMERS
from astrocrawl.utils.preferences import Preferences

# ═══════════════════════════════════════════════════════════════════════════
# ProxyRouteModel
# ═══════════════════════════════════════════════════════════════════════════


class ProxyRouteModel(QAbstractTableModel):
    """消费者→Profile 路由表 Model — 3 列：Consumer / Profile / Node。

    数据源：PROXY_CONSUMERS 静态字典 + Preferences.proxy_last_used。
    """

    _COLUMNS: list[ColumnDef] = [
        ColumnDef(key="consumer", label="Consumer", resize="fixed", width=120),
        ColumnDef(key="profile", label="Profile"),
        ColumnDef(key="node", label="Node"),
    ]

    def __init__(self, prefs: Preferences, parent=None):
        super().__init__(parent)
        self._prefs = prefs
        self._consumers: list[tuple[str, str]] = list(PROXY_CONSUMERS.items())

    def load(self) -> None:
        self.beginResetModel()
        self._consumers = list(PROXY_CONSUMERS.items())
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._consumers)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._COLUMNS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        consumer_key, display_name = self._consumers[index.row()]
        col = index.column()

        if col == 0:
            if role == Qt.ItemDataRole.DisplayRole:
                return self.tr(display_name)
            if role == Qt.ItemDataRole.UserRole:
                return consumer_key
        elif col == 1:
            entry = self._prefs.get_proxy_last_used(consumer_key)
            profile_uuid = entry.get("profile", "") if entry else ""
            if role == Qt.ItemDataRole.DisplayRole:
                if profile_uuid:
                    profile = self._prefs._get_proxy_profile_by_uuid(profile_uuid)
                    if profile:
                        return profile.name
                return self.tr("Direct")
            if role == Qt.ItemDataRole.UserRole:
                return profile_uuid
        elif col == 2:
            entry = self._prefs.get_proxy_last_used(consumer_key)
            node = entry.get("node", "") if entry else ""
            if role == Qt.ItemDataRole.DisplayRole:
                return node
            if role == Qt.ItemDataRole.UserRole:
                return node

        return None

    def setData(self, index: QModelIndex, value, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if not role == Qt.ItemDataRole.EditRole:
            return False
        consumer_key = self._consumers[index.row()][0]
        col = index.column()

        if col == 1:  # Profile — store uuid, reset node to first endpoint
            profile_uuid = str(value) if value else ""
            node = ""
            if profile_uuid:
                profile = self._prefs._get_proxy_profile_by_uuid(profile_uuid)
                if profile and profile.proxies:
                    first = profile.proxies[0]
                    node = f"{first.type.name}:{first.host}:{first.port}"
            self._prefs.set_proxy_last_used(consumer_key, profile_uuid, node)
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.UserRole])
            node_index = self.index(index.row(), 2)
            self.dataChanged.emit(node_index, node_index, [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.UserRole])
            return True
        elif col == 2:  # Node — 格式 TYPE:host:port
            node = str(value) if value else ""
            entry = self._prefs.get_proxy_last_used(consumer_key)
            profile_uuid = entry.get("profile", "") if entry else ""
            self._prefs.set_proxy_last_used(consumer_key, profile_uuid, node)
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.UserRole])
            return True
        return False

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        flags = super().flags(index)
        if index.column() == 1:
            flags |= Qt.ItemFlag.ItemIsEditable
        elif index.column() == 2 and self._node_editable(index.row()):
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def _node_editable(self, row: int) -> bool:
        if row < 0 or row >= len(self._consumers):
            return False
        consumer_key = self._consumers[row][0]
        entry = self._prefs.get_proxy_last_used(consumer_key)
        if not entry:
            return False
        profile_uuid = entry.get("profile", "")
        if not profile_uuid:
            return False
        profile = self._prefs._get_proxy_profile_by_uuid(profile_uuid)
        return bool(profile and profile.proxies)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return [self.tr("Consumer"), self.tr("Profile"), self.tr("Node")][section]
        return None

    def get_consumer_key(self, row: int) -> str | None:
        if 0 <= row < len(self._consumers):
            return self._consumers[row][0]
        return None

    def get_profile_uuid(self, row: int) -> str:
        if 0 <= row < len(self._consumers):
            entry = self._prefs.get_proxy_last_used(self._consumers[row][0])
            return entry.get("profile", "") if entry else ""
        return ""


# ═══════════════════════════════════════════════════════════════════════════
# _ProfileComboDelegate
# ═══════════════════════════════════════════════════════════════════════════


class _ProfileComboDelegate(QStyledItemDelegate):
    """Profile 列 QComboBox 委托 — 显示 profile name，UserRole 存 uuid。"""

    def __init__(self, prefs: Preferences, parent=None):
        super().__init__(parent)
        self._prefs = prefs

    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        combo.addItem(self.tr("Direct"), "")
        for profile in self._prefs.get_proxy_profiles():
            combo.addItem(profile.name, profile.uuid)
        combo.currentIndexChanged.connect(lambda: self.commitData.emit(combo))
        return combo

    def setEditorData(self, editor, index):
        current = index.data(Qt.ItemDataRole.UserRole) or ""
        idx = editor.findData(current)
        if idx >= 0:
            editor.setCurrentIndex(idx)
        else:
            editor.setCurrentIndex(0)

    def setModelData(self, editor, model, index):
        selected = editor.currentData() or ""
        model.setData(index, selected, Qt.ItemDataRole.EditRole)


# ═══════════════════════════════════════════════════════════════════════════
# _NodeComboDelegate
# ═══════════════════════════════════════════════════════════════════════════


class _NodeComboDelegate(QStyledItemDelegate):
    """Node 列 QComboBox 委托 — 读取 Profile uuid，填充该 Profile 的节点列表。

    Profile 未选或无端点时 combo 禁用（灰色不可交互）。
    Profile 有端点时启用并默认选中第一个。
    """

    def __init__(self, prefs: Preferences, parent=None):
        super().__init__(parent)
        self._prefs = prefs

    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)

        profile_uuid = index.sibling(index.row(), 1).data(Qt.ItemDataRole.UserRole) or ""
        if profile_uuid:
            profile = self._prefs._get_proxy_profile_by_uuid(profile_uuid)
            if profile and profile.proxies:
                for spec in profile.proxies:
                    node = f"{spec.type.name}:{spec.host}:{spec.port}"
                    combo.addItem(node, node)

        if combo.count() == 0:
            combo.setEnabled(False)

        combo.currentIndexChanged.connect(lambda: self.commitData.emit(combo))
        return combo

    def setEditorData(self, editor, index):
        if editor.count() == 0:
            return
        current = index.data(Qt.ItemDataRole.UserRole) or ""
        idx = editor.findData(current)
        if idx >= 0:
            editor.setCurrentIndex(idx)
        else:
            editor.setCurrentIndex(0)

    def setModelData(self, editor, model, index):
        selected = editor.currentData() or ""
        model.setData(index, selected, Qt.ItemDataRole.EditRole)


# ═══════════════════════════════════════════════════════════════════════════
# _RouteSettingsPage
# ═══════════════════════════════════════════════════════════════════════════


class _RouteSettingsPage(_TableManagementPage):
    """路由设置页 — Consumer→Profile→Node 分配表。

    Consumer 列只读（来自 PROXY_CONSUMERS），Profile/Node 列使用 QComboBox 委托编辑。
    CRUD 按钮隐藏，提供"恢复默认"按钮将所有 consumer 重置为直连。
    """

    route_changed = Signal()

    def __init__(self, prefs: Preferences, parent=None):
        self._prefs = prefs
        super().__init__(parent)

    # ── _TableManagementPage 抽象方法 ──

    def _define_columns(self) -> list[ColumnDef]:
        return [
            ColumnDef(key="consumer", label=self.tr("Consumer"), resize="fixed", width=120),
            ColumnDef(key="profile", label=self.tr("Profile")),
            ColumnDef(key="node", label=self.tr("Node")),
        ]

    def _create_model(self) -> ProxyRouteModel:
        return ProxyRouteModel(self._prefs, self)

    def _on_add(self) -> None:
        pass

    def _on_edit(self, row: int) -> None:
        pass

    def _on_remove(self, row: int) -> None:
        pass

    def _apply_toggle(self, name: str, enabled: bool) -> None:
        pass

    def _search_columns(self) -> tuple[int, ...]:
        return (0,)

    def _extra_buttons(self) -> list[tuple[str, Callable[[], None]]]:
        return [
            (self.tr("Reset to Default"), self._on_reset_defaults),
        ]

    # ── UI setup overrides ──

    def _setup_ui(self) -> None:
        super()._setup_ui()

        for name in ("add-btn", "edit-btn", "remove-btn"):
            btn = self.findChild(QPushButton, name)
            if btn is not None:
                btn.setVisible(False)

        if self._table is not None:
            self._table.setEditTriggers(QAbstractItemView.EditTrigger.AllEditTriggers)
            self._table.setItemDelegateForColumn(1, _ProfileComboDelegate(self._prefs, self))
            self._table.setItemDelegateForColumn(2, _NodeComboDelegate(self._prefs, self))

            self._open_combo_editors()
            if self._model is not None:
                self._model.dataChanged.connect(self._on_model_data_changed)

    def _open_combo_editors(self) -> None:
        if self._table is None or self._model is None:
            return
        for row in range(self._model.rowCount()):
            for col in (1, 2):
                idx = self._model.index(row, col)
                if self._proxy is not None:
                    idx = self._proxy.mapFromSource(idx)
                if idx.isValid():
                    self._table.openPersistentEditor(idx)

    def refresh(self) -> None:
        super().refresh()
        self._open_combo_editors()

    # ── actions ──

    def _on_model_data_changed(self, topLeft, bottomRight, roles) -> None:
        """Profile 列变更时刷新同行 Node 列的 persistent editor。"""
        if topLeft.column() <= 1 <= bottomRight.column():
            for row in range(topLeft.row(), bottomRight.row() + 1):
                self._refresh_node_editor(row)

    def _refresh_node_editor(self, row: int) -> None:
        if self._table is None or self._model is None:
            return
        idx = self._model.index(row, 2)
        if self._proxy is not None:
            idx = self._proxy.mapFromSource(idx)
        if idx.isValid():
            self._table.closePersistentEditor(idx)
            self._table.openPersistentEditor(idx)

    def _on_reset_defaults(self) -> None:
        msg = QMessageBox.warning(
            self,
            self.tr("Confirm Reset"),
            self.tr("Reset all consumer proxy settings to default (Direct)?"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if msg == QMessageBox.StandardButton.No:
            return

        for consumer_key in PROXY_CONSUMERS:
            self._prefs.set_proxy_last_used(consumer_key, "", "")
        self.refresh()
        self.route_changed.emit()

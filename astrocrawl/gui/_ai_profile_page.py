"""AI Profile 管理页 — _AIProfilePage + AIProfileListModel + AIProfileEditDialog（ADR-0007 决策 2-5）。

列表页遵循 _TableManagementPage Template Method，编辑对话框含 dirty check。
_AIProfilePage 含 Test Connection 按钮 + _FetchModelsWorker 动态模型拉取。
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Callable

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from astrocrawl._constants import QLINEEDIT_MAX, QSPINBOX_MAX
from astrocrawl.ai._profile import AIProfile
from astrocrawl.ai._provider_registry import list_installed_providers
from astrocrawl.gui._delegates import CheckboxDelegate, StatusColorDelegate
from astrocrawl.gui._style import ColumnDef
from astrocrawl.gui._table_page import _TableManagementPage
from astrocrawl.gui._tokens import FONT_MD, SPACE_MD, SPACE_SM
from astrocrawl.gui.theme import get_theme_manager
from astrocrawl.utils.preferences import Preferences

# ═══════════════════════════════════════════════════════════════════════════
# AIProfileListModel
# ═══════════════════════════════════════════════════════════════════════════


class AIProfileListModel(QAbstractTableModel):
    """AI Profile 列表 Model — 5 列：Name / Provider / Model / Status / Enabled。

    UserRole 在 Status 列存储状态 key（ok/failed/untested）。
    CheckStateRole 在 Enabled 列存储复选框状态。
    """

    _COLUMNS: list[ColumnDef] = [
        ColumnDef(key="name", label="Name"),
        ColumnDef(key="provider", label="Provider", resize="fixed", width=100),
        ColumnDef(key="model", label="Model"),
        ColumnDef(key="status", label="Status", resize="fixed", width=80),
        ColumnDef(key="enabled", label="Enabled", resize="fixed", width=70),
    ]

    def __init__(self, prefs: Preferences, parent=None):
        super().__init__(parent)
        self._prefs = prefs
        self._profiles: list[AIProfile] = []
        self._active_profile_name = ""
        self.load()

    def load(self) -> None:
        self.beginResetModel()
        self._profiles = self._prefs.get_ai_profiles()
        self._active_profile_name = self._prefs.get_active_profile_name()
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._profiles)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._COLUMNS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        profile = self._profiles[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                name = profile.name
                return f"☆ {name}" if name == self._active_profile_name else name
            elif col == 1:
                return profile.provider
            elif col == 2:
                return profile.model
            elif col == 3:
                return self._status_display(profile)
            elif col == 4:
                return None
            return None

        if role == Qt.ItemDataRole.UserRole and col == 3:
            return profile.last_test_status or "untested"

        if role == Qt.ItemDataRole.CheckStateRole and col == 4:
            return Qt.CheckState.Checked.value if profile.enabled else Qt.CheckState.Unchecked.value

        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        flags = super().flags(index)
        if index.column() == 4:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        return flags

    def setData(self, index: QModelIndex, value, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if role == Qt.ItemDataRole.CheckStateRole and index.column() == 4:
            enabled = value == Qt.CheckState.Checked.value
            row = index.row()
            if 0 <= row < len(self._profiles):
                self._profiles[row] = replace(self._profiles[row], enabled=enabled)
                self.dataChanged.emit(index, index, [Qt.ItemDataRole.CheckStateRole])
                return True
        return False

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return [self.tr("Name"), self.tr("Provider"), self.tr("Model"), self.tr("Status"), self.tr("Enabled")][
                section
            ]
        return None

    @staticmethod
    def _status_display(profile: AIProfile) -> str:
        if profile.last_test_status == "ok":
            return "Verified"
        if profile.last_test_status == "failed":
            return "Failed"
        return "Untested"

    def get_profile(self, row: int) -> AIProfile | None:
        if 0 <= row < len(self._profiles):
            return self._profiles[row]
        return None

    @property
    def profiles(self) -> list[AIProfile]:
        return list(self._profiles)

    @property
    def active_profile_name(self) -> str:
        return self._active_profile_name


# ═══════════════════════════════════════════════════════════════════════════
# AIProfileEditDialog
# ═══════════════════════════════════════════════════════════════════════════


class AIProfileEditDialog(QDialog):
    """AI Profile 编辑对话框 — 创建/编辑 profile 配置。

    内联红字验证（Name 非空/不重复），Cancel 时 dirty check。
    """

    def __init__(self, parent: QWidget | None, existing_names: list[str], profile: AIProfile | None = None):
        super().__init__(parent)
        self.setWindowModality(Qt.WindowModal)
        self._existing_names = [n for n in existing_names if n != (profile.name if profile else None)]
        self._profile = profile
        self._is_new = profile is None
        self._dirty = False

        title = self.tr("Add AI Profile") if self._is_new else self.tr("Edit AI Profile")
        self.setWindowTitle(title)

        layout = QVBoxLayout(self)
        layout.setSpacing(SPACE_MD)

        # ── _form_row 局部辅助 ──
        def _form_row(label_text: str = "", widget=None, *stretches: int) -> QHBoxLayout:
            row = QHBoxLayout()
            row.setSpacing(SPACE_SM)
            if not label_text:
                row.addWidget(widget, 1)
                return row
            lbl = QLabel(label_text)
            lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            if not stretches:
                row.addWidget(lbl, 1)
                if isinstance(widget, (list, tuple)):
                    row.addWidget(widget[0], 3)
                else:
                    row.addWidget(widget, 3)
                return row
            it = iter(stretches)
            row.addWidget(lbl, next(it))
            if isinstance(widget, (list, tuple)):
                for w, s in zip(widget, it):
                    row.addWidget(w, s)
            else:
                row.addWidget(widget, next(it))
            return row

        # ── Basic Info ──
        basic_group = QGroupBox(self.tr("Basic Info"))
        basic_gl = QVBoxLayout()
        basic_gl.setSpacing(SPACE_SM)

        self._name_edit = QLineEdit()
        self._name_edit.setMaxLength(QLINEEDIT_MAX)
        self._name_edit.setPlaceholderText(self.tr("Enter Profile name"))
        if not self._is_new and profile is not None:
            self._name_edit.setText(profile.name)
            self._name_edit.setReadOnly(True)
            self._name_edit.setToolTip(self.tr("Profile name cannot be changed after creation"))
        self._name_edit.textChanged.connect(self._mark_dirty)
        basic_gl.addLayout(_form_row(self.tr("Name:"), self._name_edit))

        self._name_error = QLabel("")
        self._name_error.setStyleSheet(f"color: {get_theme_manager().get('danger')}; font-size: {FONT_MD}px;")
        self._name_error.hide()
        basic_gl.addLayout(_form_row(widget=self._name_error))

        basic_group.setLayout(basic_gl)
        layout.addWidget(basic_group)

        # ── Connection ──
        conn_group = QGroupBox(self.tr("Connection"))
        conn_gl = QVBoxLayout()
        conn_gl.setSpacing(SPACE_SM)

        self._provider_combo = QComboBox()
        providers = list_installed_providers()
        self._provider_combo.addItems(providers)
        if profile:
            idx = self._provider_combo.findText(profile.provider)
            if idx >= 0:
                self._provider_combo.setCurrentIndex(idx)
        self._provider_combo.currentTextChanged.connect(self._on_provider_changed)
        conn_gl.addLayout(_form_row(self.tr("Provider:"), self._provider_combo))

        self._api_key_edit = QLineEdit()
        self._api_key_edit.setMaxLength(QLINEEDIT_MAX)
        self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key_edit.setPlaceholderText("sk-...")
        if profile:
            self._api_key_edit.setText(profile.api_key)
        self._api_key_edit.textChanged.connect(self._mark_dirty)
        self._toggle_key_btn = QToolButton()
        self._toggle_key_btn.setText("👁")
        self._toggle_key_btn.setCheckable(True)
        self._toggle_key_btn.toggled.connect(self._on_toggle_api_key)
        self._toggle_key_btn.setToolTip(self.tr("Show/Hide API Key"))
        conn_gl.addLayout(_form_row(self.tr("API Key:"), [self._api_key_edit, self._toggle_key_btn], 1, 2, 1))

        self._endpoint_edit = QLineEdit()
        self._endpoint_edit.setMaxLength(QLINEEDIT_MAX)
        self._endpoint_edit.setPlaceholderText(self.tr("Leave blank to use Provider default endpoint"))
        if profile:
            self._endpoint_edit.setText(profile.endpoint)
        self._endpoint_edit.textChanged.connect(self._mark_dirty)
        conn_gl.addLayout(_form_row(self.tr("Endpoint:"), self._endpoint_edit))

        conn_group.setLayout(conn_gl)
        layout.addWidget(conn_group)

        # ── Model & Parameters ──
        model_group = QGroupBox(self.tr("Model & Parameters"))
        model_gl = QVBoxLayout()
        model_gl.setSpacing(SPACE_SM)

        self._model_combo = QComboBox()
        self._model_combo.setEditable(True)
        if profile:
            self._model_combo.setCurrentText(profile.model)
        self._model_combo.currentTextChanged.connect(self._mark_dirty)
        self._refresh_btn = QToolButton()
        self._refresh_btn.setText("↻")
        self._refresh_btn.setToolTip(self.tr("Fetch model list from Provider"))
        model_gl.addLayout(_form_row(self.tr("Model:"), [self._model_combo, self._refresh_btn], 1, 2, 1))

        self._temp_spin = QDoubleSpinBox()
        self._temp_spin.setRange(0.0, 2.0)
        self._temp_spin.setSingleStep(0.1)
        self._temp_spin.setValue(profile.temperature if profile else 0.1)
        self._temp_spin.valueChanged.connect(self._mark_dirty)
        model_gl.addLayout(_form_row(self.tr("Temperature:"), self._temp_spin))

        self._max_tokens_spin = QSpinBox()
        self._max_tokens_spin.setRange(1, QSPINBOX_MAX)
        self._max_tokens_spin.setSingleStep(1024)
        self._max_tokens_spin.setValue(profile.max_tokens if profile else 2048)
        self._max_tokens_spin.valueChanged.connect(self._mark_dirty)
        model_gl.addLayout(_form_row(self.tr("Max Tokens:"), self._max_tokens_spin))

        model_group.setLayout(model_gl)
        layout.addWidget(model_group)

        # ── Buttons ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(SPACE_SM)

        cancel_btn = QPushButton(self.tr("Cancel"))
        cancel_btn.clicked.connect(self._on_cancel)
        commit_btn = QPushButton(self.tr("OK"))
        commit_btn.clicked.connect(self._validate_and_accept)
        commit_btn.setDefault(True)

        btn_row.addWidget(cancel_btn, 1)
        btn_row.addWidget(commit_btn, 1)
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

    # ── properties ──

    @property
    def profile_name(self) -> str:
        return str(self._name_edit.text()).strip()

    def get_profile(self) -> AIProfile:
        p = AIProfile(
            name=self.profile_name,
            provider=self._provider_combo.currentText(),
            model=self._model_combo.currentText().strip(),
            api_key=self._api_key_edit.text(),
            endpoint=self._endpoint_edit.text().strip(),
            temperature=self._temp_spin.value(),
            max_tokens=self._max_tokens_spin.value(),
        )
        if self._profile is not None:
            p = replace(
                p,
                enabled=self._profile.enabled,
                last_test_status=self._profile.last_test_status,
                last_test_time=self._profile.last_test_time,
            )
        return p

    def get_connection_info(self) -> tuple[str, str, str]:
        """返回 (provider, base_url, api_key) 供 Refresh 使用。"""
        return (
            self._provider_combo.currentText(),
            self._endpoint_edit.text().strip(),
            self._api_key_edit.text(),
        )

    def on_models_fetched(self, models: list[str]) -> None:
        current = self._model_combo.currentText()
        self._model_combo.clear()
        self._model_combo.addItems(models)
        if current:
            idx = self._model_combo.findText(current)
            if idx >= 0:
                self._model_combo.setCurrentIndex(idx)

    # ── internal ──

    def _mark_dirty(self):
        self._dirty = True

    def _on_provider_changed(self, _text: str):
        self._mark_dirty()

    def _on_toggle_api_key(self, checked: bool):
        self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password)

    def _validate(self) -> bool:
        name = self.profile_name
        if not name:
            self._name_error.setText(self.tr("Name cannot be empty"))
            self._name_error.show()
            return False
        if name in self._existing_names:
            self._name_error.setText(self.tr("Name already exists"))
            self._name_error.show()
            return False
        self._name_error.hide()
        return True

    def _validate_and_accept(self):
        if self._validate():
            self.accept()

    def _on_cancel(self):
        if self._dirty and not self._is_new:
            answer = QMessageBox.question(
                self,
                self.tr("Discard Changes?"),
                self.tr("Unsaved changes will be lost. Discard?"),
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer == QMessageBox.StandardButton.Cancel:
                return
        self.reject()


# ═══════════════════════════════════════════════════════════════════════════
# _FetchModelsWorker
# ═══════════════════════════════════════════════════════════════════════════


class _FetchModelsWorker(QThread):
    """后台线程 — 通过 ``list_models()`` 拉取模型列表。

    ``list_models(base_url, api_key, timeout) -> list[str]`` 约定。
    """

    models_fetched = Signal(list)
    fetch_failed = Signal(str)
    test_ok = Signal(str)  # profile_name
    test_failed = Signal(str, str)  # profile_name, error

    def __init__(self, parent=None):
        super().__init__(parent)
        self._provider = ""
        self._base_url = ""
        self._api_key = ""
        self._timeout = 15.0
        self._profile_name = ""

    def configure_fetch(self, provider: str, base_url: str, api_key: str, timeout: float = 15.0) -> None:
        self._provider = provider
        self._base_url = base_url
        self._api_key = api_key
        self._timeout = timeout
        self._profile_name = ""

    def configure_test(
        self, profile_name: str, provider: str, base_url: str, api_key: str, timeout: float = 15.0
    ) -> None:
        self._profile_name = profile_name
        self._provider = provider
        self._base_url = base_url
        self._api_key = api_key
        self._timeout = timeout

    def run(self) -> None:
        try:
            from astrocrawl.ai._provider_registry import get_list_models_func

            list_models = get_list_models_func(self._provider)
            if list_models is None:
                msg = self.tr("Provider '{0}' does not support model list fetching").format(self._provider)
                if self._profile_name:
                    self.test_failed.emit(self._profile_name, msg)
                else:
                    self.fetch_failed.emit(msg)
                return

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                if asyncio.iscoroutinefunction(list_models):
                    models = loop.run_until_complete(list_models(self._base_url, self._api_key, timeout=self._timeout))
                else:
                    models = list_models(self._base_url, self._api_key, timeout=self._timeout)
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

            self.models_fetched.emit(list(models))
            if self._profile_name:
                self.test_ok.emit(self._profile_name)
        except Exception as e:
            msg = str(e)[:200]
            if self._profile_name:
                self.test_failed.emit(self._profile_name, msg)
            else:
                self.fetch_failed.emit(msg)


# ═══════════════════════════════════════════════════════════════════════════
# _AIProfilePage
# ═══════════════════════════════════════════════════════════════════════════


class _AIProfilePage(_TableManagementPage):
    """AI Profile 列表管理页 — View + CRUD + ☆ 默认标记 + pending toggles + Test Connection。"""

    profile_changed = Signal()
    busy_changed = Signal(bool)
    status_message = Signal(str, str)  # msg, level

    def __init__(self, prefs: Preferences, parent=None):
        self._prefs = prefs
        self._fetching: bool = False
        super().__init__(parent)

    # ── _TableManagementPage 抽象方法 ──

    def _define_columns(self) -> list[ColumnDef]:
        return [
            ColumnDef(key="name", label="Name"),
            ColumnDef(key="provider", label="Provider", resize="fixed", width=100),
            ColumnDef(key="model", label="Model"),
            ColumnDef(key="status", label="Status", resize="fixed", width=80),
            ColumnDef(key="enabled", label="Enabled", resize="fixed", width=70),
        ]

    def _create_model(self) -> AIProfileListModel:
        return AIProfileListModel(self._prefs, self)

    def _on_add(self) -> None:
        model: AIProfileListModel = self._model  # type: ignore[assignment]
        names = [p.name for p in model.profiles]
        dlg = AIProfileEditDialog(self, names)
        self._wire_refresh_button(dlg)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._prefs.save_ai_profile(dlg.get_profile())
            self.refresh()
            self.profile_changed.emit()

    def _on_edit(self, row: int) -> None:
        model: AIProfileListModel = self._model  # type: ignore[assignment]
        profile = model.get_profile(row)
        if profile is None:
            return
        names = [p.name for p in model.profiles]
        dlg = AIProfileEditDialog(self, names, profile)
        self._wire_refresh_button(dlg)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._prefs.save_ai_profile(dlg.get_profile())
            self.refresh()
            self.profile_changed.emit()

    def _wire_refresh_button(self, dlg: AIProfileEditDialog) -> None:
        """连接 Refresh 按钮 — 点击时从对话框公共 API 读取最新输入。"""

        def do_fetch():
            dlg._refresh_btn.setEnabled(False)
            dlg._refresh_btn.setText(self.tr("Fetching..."))
            provider, base_url, api_key = dlg.get_connection_info()

            def on_fetched(models):
                dlg.on_models_fetched(models)
                self.status_message.emit(self.tr("Model list refreshed"), "success")

            def on_failed(msg):
                self.status_message.emit(self.tr("Failed to fetch model list: {0}").format(msg), "error")

            def on_done():
                dlg._refresh_btn.setText("↻")
                dlg._refresh_btn.setEnabled(True)

            self._fetch_models(provider, base_url, api_key, on_fetched, on_done=on_done, on_failed=on_failed)

        dlg._refresh_btn.clicked.connect(do_fetch)

    def _on_remove(self, row: int) -> None:
        model: AIProfileListModel = self._model  # type: ignore[assignment]
        profile = model.get_profile(row)
        if profile is None:
            return
        if profile.name == model.active_profile_name:
            QMessageBox.warning(
                self,
                self.tr("Cannot Delete"),
                self.tr("Please set another Profile as default before deleting this one."),
            )
            return
        msg = QMessageBox(
            QMessageBox.Warning,
            self.tr("Confirm Delete"),
            self.tr('Delete Profile "{0}"?').format(profile.name),
            parent=self,
        )
        del_btn = msg.addButton(self.tr("Delete"), QMessageBox.YesRole)
        msg.addButton(self.tr("Cancel"), QMessageBox.NoRole)
        msg.exec()
        if msg.clickedButton() == del_btn:
            self._prefs.remove_ai_profile(profile.name)
            self.refresh()
            self.profile_changed.emit()

    def _apply_toggle(self, name: str, enabled: bool) -> None:
        model: AIProfileListModel = self._model  # type: ignore[assignment]
        for profile in model.profiles:
            if profile.name == name:
                updated = replace(profile, enabled=enabled)
                self._prefs.save_ai_profile(updated)
                return

    def _search_columns(self) -> tuple[int, ...]:
        return (0, 1, 2)

    def _empty_text(self) -> str:
        return str(self.tr('No AI Profiles — click "Add" to create one'))

    def _extra_buttons(self) -> list[tuple[str, Callable[[], None]]]:
        return [
            (self.tr("☆ Set as Default"), self._on_set_default),
            (self.tr("Test Connection"), self._on_test_connection),
        ]

    # ── UI setup overrides ──

    def _setup_ui(self) -> None:
        super()._setup_ui()

        # StatusColorDelegate on column 3
        delegate = StatusColorDelegate(
            {
                "ok": "success",
                "failed": "danger",
                "untested": "disabled",
            },
        )
        if self._table is not None:
            self._table.setItemDelegateForColumn(3, delegate)

        # CheckboxDelegate on column 4
        cb_delegate = CheckboxDelegate(self)
        cb_delegate.toggled.connect(self._on_checkbox_toggled)
        if self._table is not None:
            self._table.setItemDelegateForColumn(4, cb_delegate)

    # ── actions ──

    def _on_checkbox_toggled(self, row: int, checked: bool) -> None:
        """CheckboxDelegate toggled — 将 proxy row 映射到 source row。"""
        model: AIProfileListModel = self._model  # type: ignore[assignment]
        if self._proxy is not None:
            proxy_idx = self._proxy.index(row, 4)
            src_idx = self._proxy.mapToSource(proxy_idx)
            row = src_idx.row()
        profile = model.get_profile(row)
        if profile is None:
            return
        if checked != profile.enabled:
            self._set_pending(profile.name, checked)

    def _on_set_default(self) -> None:
        row = self._selected_source_row()
        if row < 0:
            return
        model: AIProfileListModel = self._model  # type: ignore[assignment]
        profile = model.get_profile(row)
        if profile is None:
            return
        self._prefs.set_active_ai_profile(profile.name)
        self.refresh()
        self.profile_changed.emit()

    def _fetch_models(
        self,
        provider: str,
        base_url: str,
        api_key: str,
        on_models: Callable[[list[str]], None],
        *,
        on_done: Callable[[], None] | None = None,
        on_failed: Callable[[str], None] | None = None,
    ) -> None:
        if self._fetching:
            return
        self._fetching = True
        self.busy_changed.emit(True)
        worker = _FetchModelsWorker(self)
        worker.configure_fetch(provider, base_url, api_key)
        worker.models_fetched.connect(on_models)
        if on_failed is not None:
            worker.fetch_failed.connect(on_failed)
        else:
            worker.fetch_failed.connect(lambda msg: self.status_message.emit(msg, "error"))
        if on_done is not None:
            worker.finished.connect(on_done)
        worker.finished.connect(lambda: setattr(self, "_fetching", False))
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(lambda: self.busy_changed.emit(False))
        worker.start()

    def _on_test_connection(self) -> None:
        row = self._selected_source_row()
        if row < 0:
            return
        model: AIProfileListModel = self._model  # type: ignore[assignment]
        profile = model.get_profile(row)
        if profile is None:
            return

        if self._fetching:
            self.status_message.emit(self.tr("Connection test in progress, please try again later"), "warning")
            return

        self._fetching = True
        self.busy_changed.emit(True)
        worker = _FetchModelsWorker(self)
        worker.configure_test(
            profile.name,
            profile.provider,
            profile.endpoint,
            profile.api_key,
        )
        worker.test_ok.connect(self._on_test_result)
        worker.test_failed.connect(self._on_test_result_failed)
        worker.fetch_failed.connect(
            lambda msg: self.status_message.emit(self.tr("Test failed: {0}").format(msg), "error")
        )
        worker.finished.connect(lambda: setattr(self, "_fetching", False))
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(lambda: self.busy_changed.emit(False))
        self.status_message.emit(self.tr("Testing {0}...").format(profile.name), "info")
        worker.start()

    def _on_test_result(self, name: str) -> None:
        from datetime import datetime, timezone

        profile = self._prefs.get_ai_profile(name)
        if profile is None:
            return
        from dataclasses import replace

        updated = replace(profile, last_test_status="ok", last_test_time=datetime.now(timezone.utc).isoformat())
        self._prefs.save_ai_profile(updated)
        self.refresh()
        self.status_message.emit(self.tr("'{0}' connection test passed").format(name), "success")

    def _on_test_result_failed(self, name: str, error: str) -> None:
        from datetime import datetime, timezone

        profile = self._prefs.get_ai_profile(name)
        if profile is None:
            return

        updated = replace(profile, last_test_status="failed", last_test_time=datetime.now(timezone.utc).isoformat())
        self._prefs.save_ai_profile(updated)
        self.refresh()
        self.status_message.emit(self.tr("'{0}' connection test failed: {1}").format(name, error), "warning")

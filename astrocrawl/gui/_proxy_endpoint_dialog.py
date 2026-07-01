"""Proxy 端点编辑对话框 — ProxyEndpointEditDialog（ADR-0010 Phase 3.5a）。

扁平表单（无 GroupBox），7 字段对标 FoxyProxy / Clash Verge / Charles Proxy 扁平布局。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
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

from astrocrawl._constants import QLINEEDIT_MAX
from astrocrawl.gui._tokens import FONT_MD, SPACE_MD, SPACE_SM
from astrocrawl.gui.theme import get_theme_manager
from astrocrawl.proxy._config import ProxyEndpointSpec, ProxyType


class ProxyEndpointEditDialog(QDialog):
    """代理端点编辑对话框 — 新建/编辑单个代理端点。

    新建模式：所有字段为空/默认值。
    编辑模式：从 ProxyEndpointSpec 预填所有字段。
    """

    def __init__(
        self,
        parent: QWidget | None,
        endpoint: ProxyEndpointSpec | None = None,
    ):
        super().__init__(parent)
        self.setWindowModality(Qt.WindowModal)
        self._endpoint = endpoint
        self._is_new = endpoint is None
        self._dirty = False

        if self._is_new:
            self.setWindowTitle(self.tr("Add Proxy Endpoint"))
        else:
            self.setWindowTitle(self.tr("Edit Proxy Endpoint"))

        layout = QVBoxLayout(self)
        layout.setSpacing(SPACE_MD)

        # ── 扁平表单（无 GroupBox）──
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        form.setSpacing(SPACE_SM)

        self._label_edit = QLineEdit()
        self._label_edit.setMaxLength(QLINEEDIT_MAX)
        self._label_edit.setPlaceholderText(self.tr("e.g. US-Proxy-1"))
        if not self._is_new and endpoint is not None:
            self._label_edit.setText(endpoint.label)
        self._label_edit.textChanged.connect(self._mark_dirty)
        form.addRow(self.tr("Label"), self._label_edit)

        self._type_combo = QComboBox()
        self._type_combo.addItem("HTTP", int(ProxyType.HTTP))
        self._type_combo.addItem("HTTPS", int(ProxyType.HTTPS))
        self._type_combo.addItem("SOCKS5", int(ProxyType.SOCKS5))
        if not self._is_new and endpoint is not None:
            idx = self._type_combo.findData(int(endpoint.type))
            if idx >= 0:
                self._type_combo.setCurrentIndex(idx)
        self._type_combo.currentIndexChanged.connect(self._mark_dirty)
        form.addRow(self.tr("Type"), self._type_combo)

        self._host_edit = QLineEdit()
        self._host_edit.setMaxLength(QLINEEDIT_MAX)
        self._host_edit.setPlaceholderText(self.tr("e.g. proxy.example.com"))
        if not self._is_new and endpoint is not None:
            self._host_edit.setText(endpoint.host)
        self._host_edit.textChanged.connect(self._mark_dirty)
        form.addRow(self.tr("Host"), self._host_edit)

        self._port_spin = QSpinBox()
        self._port_spin.setRange(1, 65535)
        self._port_spin.setValue(endpoint.port if not self._is_new and endpoint is not None else 8080)
        self._port_spin.valueChanged.connect(self._mark_dirty)
        form.addRow(self.tr("Port"), self._port_spin)

        self._weight_spin = QSpinBox()
        self._weight_spin.setRange(1, 100)
        self._weight_spin.setValue(endpoint.weight if not self._is_new and endpoint is not None else 1)
        self._weight_spin.setToolTip(self.tr("Higher weight for better quality proxies"))
        self._weight_spin.valueChanged.connect(self._mark_dirty)
        form.addRow(self.tr("Weight"), self._weight_spin)

        self._username_edit = QLineEdit()
        self._username_edit.setMaxLength(QLINEEDIT_MAX)
        self._username_edit.setPlaceholderText(self.tr("Optional"))
        if not self._is_new and endpoint is not None:
            self._username_edit.setText(endpoint.username)
        self._username_edit.textChanged.connect(self._mark_dirty)
        form.addRow(self.tr("Username"), self._username_edit)

        self._password_edit = QLineEdit()
        self._password_edit.setMaxLength(QLINEEDIT_MAX)
        self._password_edit.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
        self._password_edit.setPlaceholderText(self.tr("Optional"))
        if not self._is_new and endpoint is not None:
            self._password_edit.setText(endpoint.password)
        self._password_edit.textChanged.connect(self._mark_dirty)

        pwd_row = QHBoxLayout()
        pwd_row.addWidget(self._password_edit)
        self._toggle_pwd_btn = QToolButton()
        self._toggle_pwd_btn.setText("👁")
        self._toggle_pwd_btn.setCheckable(True)
        self._toggle_pwd_btn.toggled.connect(self._on_toggle_password)
        self._toggle_pwd_btn.setToolTip(self.tr("Show/Hide Password"))
        pwd_row.addWidget(self._toggle_pwd_btn)
        form.addRow(self.tr("Password"), pwd_row)

        layout.addLayout(form)

        # ── 验证错误 ──
        self._error_label = QLabel("")
        self._error_label.setStyleSheet(f"color: {get_theme_manager().get('danger')}; font-size: {FONT_MD}px;")
        self._error_label.setWordWrap(True)
        layout.addWidget(self._error_label)

        # ── 按钮行 ──
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

    # ── 公共 API ──

    def get_endpoint(self) -> ProxyEndpointSpec:
        return ProxyEndpointSpec(
            label=self._label_edit.text().strip(),
            type=ProxyType(self._type_combo.currentData()),
            host=self._host_edit.text().strip(),
            port=self._port_spin.value(),
            weight=self._weight_spin.value(),
            username=self._username_edit.text(),
            password=self._password_edit.text(),
        )

    # ── internal ──

    def _mark_dirty(self) -> None:
        self._dirty = True

    def _on_toggle_password(self, checked: bool) -> None:
        if checked:
            self._password_edit.setEchoMode(QLineEdit.EchoMode.Normal)
        else:
            self._password_edit.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)

    def _validate(self) -> bool:
        label = self._label_edit.text().strip()
        host = self._host_edit.text().strip()

        if not label:
            self._error_label.setText(self.tr("Label cannot be empty"))
            return False
        if not host:
            self._error_label.setText(self.tr("Host cannot be empty"))
            return False

        self._error_label.setText("")
        return True

    def _validate_and_accept(self) -> None:
        if self._validate():
            self.accept()

    def _on_cancel(self) -> None:
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

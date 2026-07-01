"""ProxyEndpointEditDialog 测试 — 新建/编辑/验证/dirty check。

对标 tests/test_gui_ai_profile.py 模式。GUI 测试使用 @pytest.mark.gui + QT_QPA_PLATFORM=offscreen。
"""

from __future__ import annotations

import pytest

from astrocrawl.gui._proxy_endpoint_dialog import ProxyEndpointEditDialog
from astrocrawl.proxy._config import ProxyEndpointSpec, ProxyType

pytestmark = pytest.mark.gui


class TestProxyEndpointEditDialog:
    """新建/编辑模式测试。"""

    def test_new_dialog_defaults(self, theme_mgr):
        dlg = ProxyEndpointEditDialog(None)
        assert dlg._is_new is True
        assert dlg._label_edit.text() == ""
        assert dlg._host_edit.text() == ""
        assert dlg._port_spin.value() == 8080
        assert dlg._weight_spin.value() == 1
        assert dlg._username_edit.text() == ""
        assert dlg._password_edit.text() == ""

    def test_edit_dialog_loads_endpoint(self, theme_mgr):
        endpoint = ProxyEndpointSpec(
            label="JP",
            type=ProxyType.SOCKS5,
            host="1.2.3.4",
            port=1080,
            username="user",
            password="pass",
            weight=3,
        )
        dlg = ProxyEndpointEditDialog(None, endpoint)
        assert dlg._is_new is False
        assert dlg._label_edit.text() == "JP"
        assert dlg._host_edit.text() == "1.2.3.4"
        assert dlg._port_spin.value() == 1080
        assert dlg._weight_spin.value() == 3
        assert dlg._username_edit.text() == "user"
        assert dlg._password_edit.text() == "pass"

    def test_confirm_returns_correct_spec(self, theme_mgr):
        dlg = ProxyEndpointEditDialog(None)
        dlg._label_edit.setText("MyProxy")
        dlg._host_edit.setText("proxy.example.com")
        dlg._port_spin.setValue(3128)
        dlg._weight_spin.setValue(5)
        dlg._username_edit.setText("admin")
        dlg._password_edit.setText("s3cret")

        spec = dlg.get_endpoint()
        assert spec.label == "MyProxy"
        assert spec.type == ProxyType.HTTP
        assert spec.host == "proxy.example.com"
        assert spec.port == 3128
        assert spec.weight == 5
        assert spec.username == "admin"
        assert spec.password == "s3cret"

    def test_confirm_with_type_https(self, theme_mgr):
        dlg = ProxyEndpointEditDialog(None)
        dlg._type_combo.setCurrentIndex(1)  # HTTPS
        dlg._host_edit.setText("proxy.example.com")

        spec = dlg.get_endpoint()
        assert spec.type == ProxyType.HTTPS

    def test_confirm_with_type_socks5(self, theme_mgr):
        dlg = ProxyEndpointEditDialog(None)
        dlg._type_combo.setCurrentIndex(2)  # SOCKS5
        dlg._host_edit.setText("proxy.example.com")

        spec = dlg.get_endpoint()
        assert spec.type == ProxyType.SOCKS5

    def test_cancel_does_not_accept(self, theme_mgr):
        dlg = ProxyEndpointEditDialog(None)
        dlg._label_edit.setText("Test")
        dlg._host_edit.setText("proxy.example.com")
        assert dlg._dirty is True

        dlg.reject()
        assert dlg.result() == 0  # Rejected

    def test_validation_empty_label(self, theme_mgr):
        dlg = ProxyEndpointEditDialog(None)
        dlg._label_edit.setText("")
        dlg._host_edit.setText("proxy.example.com")
        assert dlg._validate() is False

    def test_validation_empty_host(self, theme_mgr):
        dlg = ProxyEndpointEditDialog(None)
        dlg._label_edit.setText("Test")
        dlg._host_edit.setText("")
        assert dlg._validate() is False

    def test_validation_passes(self, theme_mgr):
        dlg = ProxyEndpointEditDialog(None)
        dlg._label_edit.setText("Test")
        dlg._host_edit.setText("proxy.example.com")
        assert dlg._validate() is True


class TestProxyEndpointEditDialogDirty:
    """脏检查 — 新建时关闭不警告 / 编辑时取消警告。"""

    def test_new_close_no_warn_when_empty(self, theme_mgr):
        """新建未填任何字段 → 不脏，关闭不警告。"""
        dlg = ProxyEndpointEditDialog(None)
        assert dlg._dirty is False

    def test_new_dirty_after_edit(self, theme_mgr):
        dlg = ProxyEndpointEditDialog(None)
        dlg._label_edit.setText("x")
        assert dlg._dirty is True


class TestProxyEndpointEditDialogPasswordToggle:
    """密码显隐切换。"""

    def test_toggle_password_show(self, theme_mgr):
        from PySide6.QtWidgets import QLineEdit

        dlg = ProxyEndpointEditDialog(None)
        dlg._toggle_pwd_btn.setChecked(True)
        assert dlg._password_edit.echoMode() == QLineEdit.EchoMode.Normal

    def test_toggle_password_hide(self, theme_mgr):
        from PySide6.QtWidgets import QLineEdit

        dlg = ProxyEndpointEditDialog(None)
        dlg._toggle_pwd_btn.setChecked(True)
        dlg._toggle_pwd_btn.setChecked(False)
        assert dlg._password_edit.echoMode() == QLineEdit.EchoMode.PasswordEchoOnEdit


class TestProxyEndpointEditDialogAccept:
    """OK 按钮 — _validate_and_accept 路径。"""

    def test_validate_and_accept_valid(self, theme_mgr, monkeypatch):
        from unittest.mock import MagicMock

        dlg = ProxyEndpointEditDialog(None)
        dlg._label_edit.setText("Test")
        dlg._host_edit.setText("proxy.example.com")
        mock_accept = MagicMock()
        monkeypatch.setattr(dlg, "accept", mock_accept)
        dlg._validate_and_accept()
        mock_accept.assert_called_once()

    def test_validate_and_accept_invalid(self, theme_mgr, monkeypatch):
        from unittest.mock import MagicMock

        dlg = ProxyEndpointEditDialog(None)
        dlg._label_edit.setText("")
        dlg._host_edit.setText("proxy.example.com")
        mock_accept = MagicMock()
        monkeypatch.setattr(dlg, "accept", mock_accept)
        dlg._validate_and_accept()
        mock_accept.assert_not_called()


class TestProxyEndpointEditDialogCancelDirty:
    """_on_cancel dirty check 分支 — 对标 AIProfileEditDialog 模式。"""

    def test_on_cancel_dirty_discard(self, theme_mgr, monkeypatch):
        from unittest.mock import MagicMock

        from PySide6.QtWidgets import QMessageBox

        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *a, **kw: QMessageBox.StandardButton.Discard,
        )
        endpoint = ProxyEndpointSpec(label="JP", host="1.2.3.4")
        dlg = ProxyEndpointEditDialog(None, endpoint)
        dlg._dirty = True
        mock_reject = MagicMock()
        monkeypatch.setattr(dlg, "reject", mock_reject)
        dlg._on_cancel()
        mock_reject.assert_called_once()

    def test_on_cancel_dirty_keep_editing(self, theme_mgr, monkeypatch):
        from unittest.mock import MagicMock

        from PySide6.QtWidgets import QMessageBox

        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *a, **kw: QMessageBox.StandardButton.Cancel,
        )
        endpoint = ProxyEndpointSpec(label="JP", host="1.2.3.4")
        dlg = ProxyEndpointEditDialog(None, endpoint)
        dlg._dirty = True
        mock_reject = MagicMock()
        monkeypatch.setattr(dlg, "reject", mock_reject)
        dlg._on_cancel()
        mock_reject.assert_not_called()

    def test_on_cancel_new_no_prompt(self, theme_mgr, monkeypatch):
        from unittest.mock import MagicMock

        dlg = ProxyEndpointEditDialog(None)
        dlg._label_edit.setText("x")
        dlg._dirty = True
        mock_reject = MagicMock()
        monkeypatch.setattr(dlg, "reject", mock_reject)
        dlg._on_cancel()
        mock_reject.assert_called_once()

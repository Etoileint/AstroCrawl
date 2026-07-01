"""代理 Profile 管理页测试 — ProxyProfileListModel + _ProxyProfilePage + ProxyProfileEditDialog。

对标 tests/test_gui_ai_profile.py 模式。覆盖：Model 数据/状态/消费者渲染、Profile CRUD、默认保护、测试按钮。
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt

from astrocrawl.gui._proxy_profile_page import ProxyProfileEditDialog, ProxyProfileListModel, _ProxyProfilePage
from astrocrawl.proxy._config import ProxyEndpointSpec, ProxyProfile, ProxyType

pytestmark = pytest.mark.gui


class TestProxyProfileListModel:
    """ProxyProfileListModel — rowCount / columnCount / data / header / status / consumers。"""

    def test_initial_load_empty(self, fake_prefs):
        model = ProxyProfileListModel(fake_prefs)
        assert model.rowCount() == 0
        assert model.columnCount() == 4

    def test_initial_load_with_profiles(self, fake_prefs):
        fake_prefs.save_proxy_profile(ProxyProfile(name="test", proxies=(ProxyEndpointSpec(host="1.2.3.4"),)))
        model = ProxyProfileListModel(fake_prefs)
        assert model.rowCount() == 1

    def test_header_labels(self, fake_prefs):
        model = ProxyProfileListModel(fake_prefs)
        assert model.headerData(0, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) == "Name"
        assert model.headerData(1, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) == "Endpoints"
        assert model.headerData(2, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) == "Status"
        assert model.headerData(3, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) == "In Use"

    def test_display_role_name(self, fake_prefs):
        fake_prefs.save_proxy_profile(ProxyProfile(name="my-profile"))
        model = ProxyProfileListModel(fake_prefs)
        idx = model.index(0, 0)
        assert model.data(idx, Qt.ItemDataRole.DisplayRole) == "my-profile"

    def test_display_role_endpoints_count(self, fake_prefs):
        fake_prefs.save_proxy_profile(
            ProxyProfile(
                name="test",
                proxies=(ProxyEndpointSpec(host="1.2.3.4"), ProxyEndpointSpec(host="5.6.7.8")),
            )
        )
        model = ProxyProfileListModel(fake_prefs)
        idx = model.index(0, 1)
        assert "endpoints" in str(model.data(idx, Qt.ItemDataRole.DisplayRole)).lower()

    def test_display_role_status_untested(self, fake_prefs):
        fake_prefs.save_proxy_profile(ProxyProfile(name="test", proxies=(ProxyEndpointSpec(host="1.2.3.4"),)))
        model = ProxyProfileListModel(fake_prefs)
        idx = model.index(0, 2)
        assert model.data(idx, Qt.ItemDataRole.DisplayRole) == "Untested"
        assert model.data(idx, Qt.ItemDataRole.UserRole) == "untested"

    def test_set_test_result_all_reachable(self, fake_prefs):
        fake_prefs.save_proxy_profile(ProxyProfile(name="test", proxies=(ProxyEndpointSpec(host="1.2.3.4"),)))
        model = ProxyProfileListModel(fake_prefs)
        model.set_test_result("test", reachable=1, unreachable=0)
        idx = model.index(0, 2)
        assert model.data(idx, Qt.ItemDataRole.UserRole) == "all_reachable"

    def test_set_test_result_partial(self, fake_prefs):
        fake_prefs.save_proxy_profile(
            ProxyProfile(
                name="test",
                proxies=(
                    ProxyEndpointSpec(host="1.2.3.4"),
                    ProxyEndpointSpec(host="5.6.7.8"),
                ),
            )
        )
        model = ProxyProfileListModel(fake_prefs)
        model.set_test_result("test", reachable=1, unreachable=1)
        idx = model.index(0, 2)
        assert model.data(idx, Qt.ItemDataRole.UserRole) == "partial"

    def test_display_role_consumers(self, fake_prefs):
        profile = ProxyProfile(name="test")
        fake_prefs.save_proxy_profile(profile)
        saved = fake_prefs.get_proxy_profile("test")
        fake_prefs.set_proxy_last_used("preview", saved.uuid, "")
        model = ProxyProfileListModel(fake_prefs)
        idx = model.index(0, 3)
        display = model.data(idx, Qt.ItemDataRole.DisplayRole)
        assert "Rule Preview" in display

    def test_display_role_no_consumers(self, fake_prefs):
        fake_prefs.save_proxy_profile(ProxyProfile(name="test"))
        model = ProxyProfileListModel(fake_prefs)
        idx = model.index(0, 3)
        assert model.data(idx, Qt.ItemDataRole.DisplayRole) == "Unused"

    def test_get_profile_valid(self, fake_prefs):
        fake_prefs.save_proxy_profile(ProxyProfile(name="test"))
        model = ProxyProfileListModel(fake_prefs)
        p = model.get_profile(0)
        assert p is not None
        assert p.name == "test"

    def test_get_profile_invalid(self, fake_prefs):
        model = ProxyProfileListModel(fake_prefs)
        assert model.get_profile(999) is None

    def test_load_cleans_deleted_profiles(self, fake_prefs):
        fake_prefs.save_proxy_profile(ProxyProfile(name="test"))
        model = ProxyProfileListModel(fake_prefs)
        model.set_test_result("test", reachable=1, unreachable=0)
        # 删除 profile
        fake_prefs.remove_proxy_profile("test")
        model.load()
        assert model.rowCount() == 0
        assert model._test_status.get("test") is None


class TestProxyProfileEditDialog:
    """ProxyProfileEditDialog — 新建/编辑端点子表 + bypass 表。"""

    def test_new_dialog_defaults(self, theme_mgr):
        dlg = ProxyProfileEditDialog(None, [])
        assert dlg._is_new is True
        assert dlg._name_edit.text() == ""
        assert dlg._endpoint_table.rowCount() == 0
        assert dlg._bypass_table.rowCount() == 0

    def test_edit_dialog_loads_data(self, theme_mgr):
        profile = ProxyProfile(
            name="test",
            proxies=(ProxyEndpointSpec(label="EP1", host="1.2.3.4", type=ProxyType.HTTP),),
            bypass_domains=(".internal",),
        )
        dlg = ProxyProfileEditDialog(None, [], profile)
        assert dlg._is_new is False
        assert dlg._name_edit.text() == "test"
        assert dlg._endpoint_table.rowCount() == 1
        assert dlg._bypass_table.rowCount() == 1

    def test_get_profile_builds(self, theme_mgr):
        dlg = ProxyProfileEditDialog(None, [])
        dlg._name_edit.setText("my-profile")
        dlg._endpoints = [ProxyEndpointSpec(label="EP", host="1.2.3.4", port=8080)]
        dlg._bypass_domains = [".example.com"]
        dlg._refresh_endpoint_table()
        dlg._refresh_bypass_table()

        profile = dlg.get_profile()
        assert profile.name == "my-profile"
        assert len(profile.proxies) == 1
        assert profile.proxies[0].host == "1.2.3.4"
        assert profile.bypass_domains == (".example.com",)

    def test_add_endpoint(self, theme_mgr, monkeypatch):
        """添加端点 → 打开 ProxyEndpointEditDialog 确认后加入端点表。"""
        from astrocrawl.gui._proxy_profile_page import ProxyEndpointEditDialog as PED

        called = False

        def fake_exec(self):
            nonlocal called
            self._label_edit.setText("NewEP")
            self._host_edit.setText("10.0.0.1")
            called = True
            return PED.DialogCode.Accepted

        monkeypatch.setattr(PED, "exec", fake_exec)

        dlg = ProxyProfileEditDialog(None, [])
        dlg._add_endpoint()
        assert called
        assert len(dlg._endpoints) == 1
        assert dlg._endpoints[0].label == "NewEP"

    def test_remove_endpoint_direct(self, theme_mgr):
        """直接操作内部端点列表来验证删除（跳过 QMessageBox 模态）。"""
        profile = ProxyProfile(
            name="test",
            proxies=(
                ProxyEndpointSpec(label="EP1", host="1.2.3.4"),
                ProxyEndpointSpec(label="EP2", host="5.6.7.8"),
            ),
        )
        dlg = ProxyProfileEditDialog(None, [], profile)
        # 直接模拟删除确认后的行为
        del dlg._endpoints[0]
        dlg._refresh_endpoint_table()
        assert len(dlg._endpoints) == 1
        assert dlg._endpoints[0].label == "EP2"
        assert dlg._endpoint_table.rowCount() == 1

    def test_dirty_set_on_change(self, theme_mgr):
        dlg = ProxyProfileEditDialog(None, [])
        assert dlg._dirty is False
        dlg._name_edit.setText("x")
        assert dlg._dirty is True

    def test_bypass_dialog_empty_rejected(self, theme_mgr):
        from astrocrawl.gui._proxy_profile_page import _BypassDomainDialog

        dlg = _BypassDomainDialog()
        dlg._domain_edit.setText("")
        dlg._validate_and_accept()
        assert dlg._error_label.text() != ""

    def test_bypass_dialog_valid_accepted(self, theme_mgr):
        from astrocrawl.gui._proxy_profile_page import _BypassDomainDialog

        dlg = _BypassDomainDialog()
        dlg._domain_edit.setText("*.example.com")
        dlg._validate_and_accept()
        assert dlg.result() == 1  # QDialog.Accepted
        assert dlg.get_domain() == "*.example.com"

    def test_bypass_dialog_whitespace_trimmed(self, theme_mgr):
        from astrocrawl.gui._proxy_profile_page import _BypassDomainDialog

        dlg = _BypassDomainDialog()
        dlg._domain_edit.setText("  example.com  ")
        assert dlg.get_domain() == "example.com"

    def test_bypass_dialog_enter_default(self, theme_mgr):
        from astrocrawl.gui._proxy_profile_page import _BypassDomainDialog

        dlg = _BypassDomainDialog()
        assert dlg._ok_btn.isDefault() is True


class TestProxyProfilePage:
    """_ProxyProfilePage GUI 测试。"""

    def test_page_creation(self, fake_prefs, theme_mgr):
        page = _ProxyProfilePage(fake_prefs)
        assert page is not None
        assert page._model is not None
        assert page._model.rowCount() == 0

    def test_page_with_profiles(self, fake_prefs, theme_mgr):
        fake_prefs.save_proxy_profile(ProxyProfile(name="test"))
        page = _ProxyProfilePage(fake_prefs)
        assert page._model.rowCount() == 1

    def test_empty_text(self, fake_prefs, theme_mgr):
        page = _ProxyProfilePage(fake_prefs)
        assert "No Proxy Profiles" in page._empty_text()

    def test_search_columns(self, fake_prefs, theme_mgr):
        page = _ProxyProfilePage(fake_prefs)
        assert page._search_columns() == (0,)

    def test_extra_buttons_present(self, fake_prefs, theme_mgr):
        page = _ProxyProfilePage(fake_prefs)
        buttons = page._extra_buttons()
        names = [b[0] for b in buttons]
        assert "☆ Set as Default" in names
        assert "Test Selected" in names

    def test_add_profile(self, fake_prefs, theme_mgr, monkeypatch):
        """点击添加 → 打开编辑对话框 → 确认后保存 + 刷新。"""
        from astrocrawl.gui._proxy_profile_page import ProxyProfileEditDialog as PED

        called = False

        def fake_exec(self):
            nonlocal called
            self._name_edit.setText("new-profile")
            called = True
            return PED.DialogCode.Accepted

        monkeypatch.setattr(PED, "exec", fake_exec)

        page = _ProxyProfilePage(fake_prefs)
        page._on_add()
        assert called
        profile = fake_prefs.get_proxy_profile("new-profile")
        assert profile is not None
        assert profile.name == "new-profile"

    def test_remove_default_succeeds(self, fake_prefs, theme_mgr, monkeypatch):
        """default Profile 可以正常删除。"""
        from unittest.mock import MagicMock

        import PySide6.QtWidgets as qtw

        monkeypatch.setattr(qtw.QDialog, "exec", MagicMock())
        monkeypatch.setattr(qtw.QMessageBox, "warning", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            qtw.QMessageBox,
            "clickedButton",
            lambda self: self.buttons()[0] if self.buttons() else None,
        )

        fake_prefs.save_proxy_profile(ProxyProfile(name="to-delete"))
        page = _ProxyProfilePage(fake_prefs)
        page._on_remove(0)
        assert fake_prefs.get_proxy_profile("to-delete") is None

    def test_set_default(self, fake_prefs, theme_mgr):
        profile = ProxyProfile(name="test")
        fake_prefs.save_proxy_profile(profile)
        saved = fake_prefs.get_proxy_profile("test")
        page = _ProxyProfilePage(fake_prefs)
        page._table.selectRow(0)
        page._on_set_default()
        entry = fake_prefs.get_proxy_last_used("preview")
        assert entry is not None
        assert entry["profile"] == saved.uuid
        assert entry["profile"] == saved.uuid

    def test_refresh_reloads(self, fake_prefs, theme_mgr):
        page = _ProxyProfilePage(fake_prefs)
        assert page._model.rowCount() == 0
        fake_prefs.save_proxy_profile(ProxyProfile(name="added-after"))
        page.refresh()
        assert page._model.rowCount() == 1

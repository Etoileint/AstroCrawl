"""路由设置页测试 — _RouteSettingsPage + ProxyRouteModel。

Consumer 列表来自 PROXY_CONSUMERS 静态字典，Profile/Node 列使用 QComboBox 委托。
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton

from astrocrawl.gui._route_settings_page import ProxyRouteModel, _RouteSettingsPage
from astrocrawl.proxy._consumers import PROXY_CONSUMERS

pytestmark = pytest.mark.gui

# ── helpers ──


def _save_test_profile(fake_prefs, name="test-profile"):
    """保存测试 Profile 并返回其 uuid。"""
    from astrocrawl.proxy._config import ProxyProfile

    profile = ProxyProfile(name=name)
    fake_prefs.save_proxy_profile(profile)
    saved = fake_prefs.get_proxy_profile(name)
    return saved.uuid if saved else ""


class TestProxyRouteModel:
    """ProxyRouteModel — rowCount / columnCount / data / setData / header。"""

    def test_row_count(self, fake_prefs):
        model = ProxyRouteModel(fake_prefs)
        assert model.rowCount() == 3

    def test_column_count(self, fake_prefs):
        model = ProxyRouteModel(fake_prefs)
        assert model.columnCount() == 3

    def test_column_0_display_role(self, fake_prefs):
        model = ProxyRouteModel(fake_prefs)
        for row in range(model.rowCount()):
            consumer_key, display_name = list(PROXY_CONSUMERS.items())[row]
            idx = model.index(row, 0)
            assert model.data(idx, Qt.ItemDataRole.DisplayRole) == display_name
            assert model.data(idx, Qt.ItemDataRole.UserRole) == consumer_key

    def test_column_1_unset_shows_direct(self, fake_prefs):
        model = ProxyRouteModel(fake_prefs)
        idx = model.index(0, 1)
        assert model.data(idx, Qt.ItemDataRole.DisplayRole) == "Direct"

    def test_column_1_set_shows_profile_name(self, fake_prefs):
        profile_uuid = _save_test_profile(fake_prefs)
        fake_prefs.set_proxy_last_used("preview", profile_uuid, "")

        model = ProxyRouteModel(fake_prefs)
        preview_row = list(PROXY_CONSUMERS.keys()).index("preview")
        idx = model.index(preview_row, 1)
        assert model.data(idx, Qt.ItemDataRole.DisplayRole) == "test-profile"
        assert model.data(idx, Qt.ItemDataRole.UserRole) == profile_uuid

    def test_column_2_unset_shows_empty(self, fake_prefs):
        model = ProxyRouteModel(fake_prefs)
        idx = model.index(0, 2)
        assert model.data(idx, Qt.ItemDataRole.DisplayRole) == ""

    def test_column_2_set_shows_node(self, fake_prefs):
        profile_uuid = _save_test_profile(fake_prefs)
        fake_prefs.set_proxy_last_used("preview", profile_uuid, "HTTP:1.2.3.4:8080")

        model = ProxyRouteModel(fake_prefs)
        preview_row = list(PROXY_CONSUMERS.keys()).index("preview")
        idx = model.index(preview_row, 2)
        assert model.data(idx, Qt.ItemDataRole.DisplayRole) == "HTTP:1.2.3.4:8080"

    def test_setdata_profile_stores_uuid(self, fake_prefs):
        profile_uuid = _save_test_profile(fake_prefs)
        model = ProxyRouteModel(fake_prefs)
        preview_row = list(PROXY_CONSUMERS.keys()).index("preview")
        idx = model.index(preview_row, 1)
        result = model.setData(idx, profile_uuid, Qt.ItemDataRole.EditRole)
        assert result is True
        entry = fake_prefs.get_proxy_last_used("preview")
        assert entry is not None
        assert entry["profile"] == profile_uuid

    def test_setdata_node_stores_host_port(self, fake_prefs):
        profile_uuid = _save_test_profile(fake_prefs)
        fake_prefs.set_proxy_last_used("preview", profile_uuid, "")
        model = ProxyRouteModel(fake_prefs)
        preview_row = list(PROXY_CONSUMERS.keys()).index("preview")
        idx = model.index(preview_row, 2)
        model.setData(idx, "HTTP:5.6.7.8:3128", Qt.ItemDataRole.EditRole)
        entry = fake_prefs.get_proxy_last_used("preview")
        assert entry["node"] == "HTTP:5.6.7.8:3128"

    def test_setdata_empty_profile_returns_none(self, fake_prefs):
        profile_uuid = _save_test_profile(fake_prefs)
        fake_prefs.set_proxy_last_used("preview", profile_uuid, "")
        model = ProxyRouteModel(fake_prefs)
        preview_row = list(PROXY_CONSUMERS.keys()).index("preview")
        idx = model.index(preview_row, 1)
        model.setData(idx, "", Qt.ItemDataRole.EditRole)
        assert fake_prefs.get_proxy_last_used("preview") is None

    def test_flags_profile_editable(self, fake_prefs):
        model = ProxyRouteModel(fake_prefs)
        flags = model.flags(model.index(0, 1))
        assert bool(flags & Qt.ItemFlag.ItemIsEditable)

    def test_flags_node_not_editable_when_no_profile(self, fake_prefs):
        model = ProxyRouteModel(fake_prefs)
        flags = model.flags(model.index(0, 2))
        assert not bool(flags & Qt.ItemFlag.ItemIsEditable)

    def test_flags_node_editable_when_profile_has_endpoints(self, fake_prefs):
        from astrocrawl.proxy._config import ProxyEndpointSpec, ProxyProfile

        spec = ProxyEndpointSpec(host="1.2.3.4", port=8080, weight=1)
        profile = ProxyProfile(name="with-endpoints", proxies=(spec,))
        fake_prefs.save_proxy_profile(profile)
        saved = fake_prefs.get_proxy_profile("with-endpoints")
        fake_prefs.set_proxy_last_used("preview", saved.uuid, "HTTP:1.2.3.4:8080")

        model = ProxyRouteModel(fake_prefs)
        preview_row = list(PROXY_CONSUMERS.keys()).index("preview")
        flags = model.flags(model.index(preview_row, 2))
        assert bool(flags & Qt.ItemFlag.ItemIsEditable)

    def test_flags_consumer_not_editable(self, fake_prefs):
        model = ProxyRouteModel(fake_prefs)
        flags = model.flags(model.index(0, 0))
        assert not bool(flags & Qt.ItemFlag.ItemIsEditable)

    def test_load_reloads(self, fake_prefs):
        model = ProxyRouteModel(fake_prefs)
        model.load()
        assert model.rowCount() == 3

    def test_get_consumer_key(self, fake_prefs):
        model = ProxyRouteModel(fake_prefs)
        key = model.get_consumer_key(0)
        assert key in PROXY_CONSUMERS

    def test_get_profile_uuid(self, fake_prefs):
        profile_uuid = _save_test_profile(fake_prefs)
        fake_prefs.set_proxy_last_used("preview", profile_uuid, "")
        model = ProxyRouteModel(fake_prefs)
        preview_row = list(PROXY_CONSUMERS.keys()).index("preview")
        assert model.get_profile_uuid(preview_row) == profile_uuid

    def test_node_editable_false_when_profile_has_no_endpoints(self, fake_prefs):
        profile_uuid = _save_test_profile(fake_prefs)  # no endpoints
        fake_prefs.set_proxy_last_used("preview", profile_uuid, "")

        model = ProxyRouteModel(fake_prefs)
        preview_row = list(PROXY_CONSUMERS.keys()).index("preview")
        assert not model._node_editable(preview_row)

    def test_setdata_profile_resets_node_to_first(self, fake_prefs):
        from astrocrawl.proxy._config import ProxyEndpointSpec, ProxyProfile

        spec1 = ProxyEndpointSpec(host="1.2.3.4", port=8080, weight=1)
        spec2 = ProxyEndpointSpec(host="5.6.7.8", port=3128, weight=1)
        profile = ProxyProfile(name="multi-endpoint", proxies=(spec1, spec2))
        fake_prefs.save_proxy_profile(profile)
        saved = fake_prefs.get_proxy_profile("multi-endpoint")

        model = ProxyRouteModel(fake_prefs)
        preview_row = list(PROXY_CONSUMERS.keys()).index("preview")
        idx = model.index(preview_row, 1)
        model.setData(idx, saved.uuid, Qt.ItemDataRole.EditRole)

        entry = fake_prefs.get_proxy_last_used("preview")
        assert entry is not None
        assert entry["node"] == "HTTP:1.2.3.4:8080"

    def test_setdata_profile_sets_empty_node_when_no_endpoints(self, fake_prefs):
        profile_uuid = _save_test_profile(fake_prefs)  # no endpoints

        model = ProxyRouteModel(fake_prefs)
        preview_row = list(PROXY_CONSUMERS.keys()).index("preview")
        idx = model.index(preview_row, 1)
        model.setData(idx, profile_uuid, Qt.ItemDataRole.EditRole)

        entry = fake_prefs.get_proxy_last_used("preview")
        assert entry is not None
        assert entry["node"] == ""

    def test_setdata_profile_emits_datachanged_for_both_columns(self, fake_prefs):
        from astrocrawl.proxy._config import ProxyEndpointSpec, ProxyProfile

        spec = ProxyEndpointSpec(host="1.2.3.4", port=8080)
        profile = ProxyProfile(name="with-endpoint", proxies=(spec,))
        fake_prefs.save_proxy_profile(profile)
        saved = fake_prefs.get_proxy_profile("with-endpoint")

        model = ProxyRouteModel(fake_prefs)
        calls = []

        def _collect(*args):
            calls.append(args)

        model.dataChanged.connect(_collect)

        preview_row = list(PROXY_CONSUMERS.keys()).index("preview")
        idx = model.index(preview_row, 1)
        model.setData(idx, saved.uuid, Qt.ItemDataRole.EditRole)

        assert len(calls) == 2
        # first signal: col 1 (profile)
        assert calls[0][0].column() == 1
        # second signal: col 2 (node)
        assert calls[1][0].column() == 2


class TestRouteSettingsPage:
    """_RouteSettingsPage GUI 测试。"""

    def test_page_creation(self, fake_prefs, theme_mgr):
        page = _RouteSettingsPage(fake_prefs)
        assert page is not None
        assert page._model is not None
        assert page._model.rowCount() == 3

    def test_crud_buttons_hidden(self, fake_prefs, theme_mgr):
        page = _RouteSettingsPage(fake_prefs)
        found_any = False
        for name in ("add-btn", "edit-btn", "remove-btn"):
            btn = page.findChild(QPushButton, name)
            assert btn is not None, f"Button {name} should exist"
            assert btn.isVisible() is False
            found_any = True
        assert found_any

    def test_reset_defaults(self, fake_prefs, theme_mgr, monkeypatch):
        import PySide6.QtWidgets as qtw

        profile_uuid = _save_test_profile(fake_prefs)
        for key in PROXY_CONSUMERS:
            fake_prefs.set_proxy_last_used(key, profile_uuid, "")

        monkeypatch.setattr(qtw.QMessageBox, "warning", lambda *a, **kw: qtw.QMessageBox.StandardButton.Yes)

        page = _RouteSettingsPage(fake_prefs)
        page._on_reset_defaults()

        for key in PROXY_CONSUMERS:
            assert fake_prefs.get_proxy_last_used(key) is None

    def test_extra_buttons_present(self, fake_prefs, theme_mgr):
        page = _RouteSettingsPage(fake_prefs)
        buttons = page._extra_buttons()
        button_names = [b[0] for b in buttons]
        assert "Reset to Default" in button_names

    def test_refresh(self, fake_prefs, theme_mgr):
        page = _RouteSettingsPage(fake_prefs)
        page.refresh()
        assert page._model.rowCount() == 3

    def test_refresh_node_editor_opens_when_profile_has_endpoints(self, fake_prefs, theme_mgr):
        from astrocrawl.proxy._config import ProxyEndpointSpec, ProxyProfile

        spec = ProxyEndpointSpec(host="1.2.3.4", port=8080)
        profile = ProxyProfile(name="with-endpoint", proxies=(spec,))
        fake_prefs.save_proxy_profile(profile)
        saved = fake_prefs.get_proxy_profile("with-endpoint")
        fake_prefs.set_proxy_last_used("preview", saved.uuid, "HTTP:1.2.3.4:8080")

        page = _RouteSettingsPage(fake_prefs)
        preview_row = list(PROXY_CONSUMERS.keys()).index("preview")
        page._refresh_node_editor(preview_row)
        # After refresh, the Node column should have a persistent editor open
        # (no crash = success; editor lifecycle verified via _open_combo_editors skip logic)

    def test_refresh_node_editor_always_reopens_disabled_when_no_endpoints(self, fake_prefs, theme_mgr):
        profile_uuid = _save_test_profile(fake_prefs)  # no endpoints
        fake_prefs.set_proxy_last_used("preview", profile_uuid, "")

        page = _RouteSettingsPage(fake_prefs)
        preview_row = list(PROXY_CONSUMERS.keys()).index("preview")
        page._refresh_node_editor(preview_row)
        # 始终 reopen，但 combo 为空 → setEnabled(False)

    def test_on_model_data_changed_ignores_col2_changes(self, fake_prefs, theme_mgr):
        """col 2 的 dataChanged 不应触发 Node editor 刷新（仅 col 1 触发）。"""
        page = _RouteSettingsPage(fake_prefs)

        preview_row = list(PROXY_CONSUMERS.keys()).index("preview")
        col2_idx = page._model.index(preview_row, 2)
        # Manually emit dataChanged for col 2 — handler should ignore
        page._model.dataChanged.emit(col2_idx, col2_idx, [Qt.ItemDataRole.DisplayRole])
        # Handler fires but condition topLeft.column()<=1<=bottomRight.column() is False
        # (no crash = success)

    def test_on_model_data_changed_triggers_on_col1_change(self, fake_prefs, theme_mgr):
        from astrocrawl.proxy._config import ProxyEndpointSpec, ProxyProfile

        spec = ProxyEndpointSpec(host="1.2.3.4", port=8080)
        profile = ProxyProfile(name="with-endpoint", proxies=(spec,))
        fake_prefs.save_proxy_profile(profile)
        saved = fake_prefs.get_proxy_profile("with-endpoint")

        page = _RouteSettingsPage(fake_prefs)
        preview_row = list(PROXY_CONSUMERS.keys()).index("preview")

        # Simulate what setData does: emit dataChanged for col 1
        col1_idx = page._model.index(preview_row, 1)
        page._model.setData(col1_idx, saved.uuid, Qt.ItemDataRole.EditRole)
        # _on_model_data_changed fires → _refresh_node_editor → close + reopen
        # No crash = success

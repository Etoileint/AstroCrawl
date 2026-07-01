"""gui/_proxy_endpoint_dialog.py + _proxy_profile_page.py + _route_settings_page.py 测试。

覆盖:
- ProxyEndpointEditDialog: 新建/编辑模式 / 验证 / get_endpoint / dirty check / password toggle
- ProxyProfileListModel: rowCount / columnCount / data roles / status / consumers / set_test_result
- ProxyProfileEditDialog: 端点 CRUD / bypass CRUD / 验证 / dirty check / get_profile
- _ProxyProfilePage: 按钮 / 设为默认 / 删除保护 / 状态栏
- _ProbeWorker: configure / run
- ProxyRouteModel: 行数 / 数据 / setData / flags
- _RouteSettingsPage: 按钮隐藏 / 恢复默认
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton

from astrocrawl.gui._proxy_endpoint_dialog import ProxyEndpointEditDialog
from astrocrawl.gui._proxy_profile_page import (
    ProxyProfileEditDialog,
    ProxyProfileListModel,
    _ProbeWorker,
    _ProxyProfilePage,
)
from astrocrawl.gui._route_settings_page import ProxyRouteModel, _RouteSettingsPage
from astrocrawl.proxy._config import ProxyEndpointSpec, ProxyProfile, ProxyType

pytestmark = pytest.mark.gui


# ═══════════════════════════════════════════════════════════════════════════
# ProxyEndpointEditDialog
# ═══════════════════════════════════════════════════════════════════════════


class TestProxyEndpointEditDialog:
    def test_new_dialog_defaults(self, theme_mgr):
        dlg = ProxyEndpointEditDialog(None)
        assert dlg._is_new is True
        ep = dlg.get_endpoint()
        assert ep.label == ""
        assert ep.type == ProxyType.HTTP
        assert ep.host == ""
        assert ep.port == 8080
        assert ep.weight == 1
        assert ep.username == ""
        assert ep.password == ""

    def test_edit_dialog_loads_endpoint(self, theme_mgr):
        spec = ProxyEndpointSpec(
            label="US-Proxy",
            type=ProxyType.SOCKS5,
            host="us.proxy.com",
            port=1080,
            weight=5,
            username="admin",
            password="s3cret",
        )
        dlg = ProxyEndpointEditDialog(None, spec)
        assert dlg._is_new is False
        ep = dlg.get_endpoint()
        assert ep.label == "US-Proxy"
        assert ep.type == ProxyType.SOCKS5
        assert ep.host == "us.proxy.com"
        assert ep.port == 1080
        assert ep.weight == 5
        assert ep.username == "admin"
        assert ep.password == "s3cret"

    def test_form_fields_prefilled_on_edit(self, theme_mgr):
        spec = ProxyEndpointSpec(label="test", host="h", port=9090, weight=10, username="u")
        dlg = ProxyEndpointEditDialog(None, spec)
        assert dlg._label_edit.text() == "test"
        assert dlg._host_edit.text() == "h"
        assert dlg._port_spin.value() == 9090
        assert dlg._weight_spin.value() == 10
        assert dlg._username_edit.text() == "u"

    def test_validation_empty_label(self, theme_mgr):
        dlg = ProxyEndpointEditDialog(None)
        dlg._label_edit.setText("")
        dlg._host_edit.setText("host.com")
        assert dlg._validate() is False

    def test_validation_empty_host(self, theme_mgr):
        dlg = ProxyEndpointEditDialog(None)
        dlg._label_edit.setText("My Proxy")
        dlg._host_edit.setText("")
        assert dlg._validate() is False

    def test_port_spin_clamped_to_min(self, theme_mgr):
        dlg = ProxyEndpointEditDialog(None)
        dlg._port_spin.setValue(0)
        assert dlg._port_spin.value() == 1  # QSpinBox clamps to min

    def test_validation_passes(self, theme_mgr):
        dlg = ProxyEndpointEditDialog(None)
        dlg._label_edit.setText("My Proxy")
        dlg._host_edit.setText("proxy.example.com")
        dlg._port_spin.setValue(3128)
        assert dlg._validate() is True
        assert dlg._error_label.text() == ""

    def test_get_endpoint_preserves_all_fields(self, theme_mgr):
        dlg = ProxyEndpointEditDialog(None)
        dlg._label_edit.setText("SOCKS5-HQ")
        dlg._type_combo.setCurrentIndex(2)  # SOCKS5
        dlg._host_edit.setText("socks.example.com")
        dlg._port_spin.setValue(1080)
        dlg._weight_spin.setValue(8)
        dlg._username_edit.setText("user1")
        dlg._password_edit.setText("pass1")

        ep = dlg.get_endpoint()
        assert ep.label == "SOCKS5-HQ"
        assert ep.type == ProxyType.SOCKS5
        assert ep.host == "socks.example.com"
        assert ep.port == 1080
        assert ep.weight == 8
        assert ep.username == "user1"
        assert ep.password == "pass1"

    def test_dirty_set_on_change(self, theme_mgr):
        dlg = ProxyEndpointEditDialog(None)
        assert dlg._dirty is False
        dlg._label_edit.setText("x")
        assert dlg._dirty is True

    def test_dirty_set_on_type_change(self, theme_mgr):
        dlg = ProxyEndpointEditDialog(None)
        assert dlg._dirty is False
        dlg._type_combo.setCurrentIndex(1)
        assert dlg._dirty is True

    def test_password_echo_toggle(self, theme_mgr):
        dlg = ProxyEndpointEditDialog(None)
        from PySide6.QtWidgets import QLineEdit

        # Default: PasswordEchoOnEdit
        assert dlg._password_edit.echoMode() == QLineEdit.EchoMode.PasswordEchoOnEdit

        # Toggle to Normal (show password)
        dlg._toggle_pwd_btn.setChecked(True)
        assert dlg._password_edit.echoMode() == QLineEdit.EchoMode.Normal

        # Toggle back
        dlg._toggle_pwd_btn.setChecked(False)
        assert dlg._password_edit.echoMode() == QLineEdit.EchoMode.PasswordEchoOnEdit

    def test_weight_default_is_1(self, theme_mgr):
        dlg = ProxyEndpointEditDialog(None)
        assert dlg._weight_spin.value() == 1
        assert dlg._weight_spin.minimum() == 1
        assert dlg._weight_spin.maximum() == 100

    def test_weight_tooltip_present(self, theme_mgr):
        dlg = ProxyEndpointEditDialog(None)
        assert "weight" in dlg._weight_spin.toolTip().lower()


# ═══════════════════════════════════════════════════════════════════════════
# ProxyProfileListModel
# ═══════════════════════════════════════════════════════════════════════════


class TestProxyProfileListModel:
    def test_initial_load_empty(self, fake_prefs):
        model = ProxyProfileListModel(fake_prefs)
        assert model.rowCount() == 0
        assert model.columnCount() == 4

    def test_header_labels(self, fake_prefs):
        model = ProxyProfileListModel(fake_prefs)
        assert model.headerData(0, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) == "Name"
        assert model.headerData(1, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) == "Endpoints"
        assert model.headerData(2, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) == "Status"
        assert model.headerData(3, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) == "In Use"

    def test_load_with_profiles(self, fake_prefs):
        fake_prefs.save_proxy_profile(ProxyProfile(name="test", proxies=(ProxyEndpointSpec(label="ep1", host="h1"),)))
        model = ProxyProfileListModel(fake_prefs)
        assert model.rowCount() == 1

    def test_name_display(self, fake_prefs):
        fake_prefs.save_proxy_profile(ProxyProfile(name="my-proxy"))
        model = ProxyProfileListModel(fake_prefs)
        idx = model.index(0, 0)
        assert model.data(idx, Qt.ItemDataRole.DisplayRole) == "my-proxy"

    def test_endpoint_count_display(self, fake_prefs):
        fake_prefs.save_proxy_profile(
            ProxyProfile(
                name="multi",
                proxies=(
                    ProxyEndpointSpec(label="a", host="h1"),
                    ProxyEndpointSpec(label="b", host="h2"),
                    ProxyEndpointSpec(label="c", host="h3"),
                ),
            )
        )
        model = ProxyProfileListModel(fake_prefs)
        idx = model.index(0, 1)
        assert "endpoints" in str(model.data(idx, Qt.ItemDataRole.DisplayRole)).lower()

    def test_status_untested_default(self, fake_prefs):
        fake_prefs.save_proxy_profile(ProxyProfile(name="test"))
        model = ProxyProfileListModel(fake_prefs)
        idx = model.index(0, 2)
        assert model.data(idx, Qt.ItemDataRole.UserRole) == "untested"
        assert model.data(idx, Qt.ItemDataRole.DisplayRole) == "Untested"

    def test_set_test_result_all_reachable(self, fake_prefs):
        fake_prefs.save_proxy_profile(ProxyProfile(name="test"))
        model = ProxyProfileListModel(fake_prefs)
        model.set_test_result("test", reachable=3, unreachable=0)
        idx = model.index(0, 2)
        assert model.data(idx, Qt.ItemDataRole.UserRole) == "all_reachable"
        assert model.data(idx, Qt.ItemDataRole.DisplayRole) == "All Reachable"

    def test_set_test_result_partial(self, fake_prefs):
        fake_prefs.save_proxy_profile(ProxyProfile(name="test"))
        model = ProxyProfileListModel(fake_prefs)
        model.set_test_result("test", reachable=2, unreachable=1)
        idx = model.index(0, 2)
        assert model.data(idx, Qt.ItemDataRole.UserRole) == "partial"
        assert "Unreachable" in str(model.data(idx, Qt.ItemDataRole.DisplayRole))

    def test_consumers_display_when_none(self, fake_prefs):
        fake_prefs.save_proxy_profile(ProxyProfile(name="unused"))
        model = ProxyProfileListModel(fake_prefs)
        idx = model.index(0, 3)
        assert model.data(idx, Qt.ItemDataRole.DisplayRole) == "Unused"

    def test_consumers_display_when_in_use(self, fake_prefs):
        profile = ProxyProfile(name="active")
        fake_prefs.save_proxy_profile(profile)
        saved = fake_prefs.get_proxy_profile("active")
        fake_prefs.set_proxy_last_used("preview", saved.uuid, "")
        model = ProxyProfileListModel(fake_prefs)
        idx = model.index(0, 3)
        text = str(model.data(idx, Qt.ItemDataRole.DisplayRole))
        assert "Rule Preview" in text

    def test_get_profile_valid(self, fake_prefs):
        fake_prefs.save_proxy_profile(ProxyProfile(name="test"))
        model = ProxyProfileListModel(fake_prefs)
        p = model.get_profile(0)
        assert p is not None
        assert p.name == "test"

    def test_get_profile_invalid(self, fake_prefs):
        model = ProxyProfileListModel(fake_prefs)
        assert model.get_profile(999) is None

    def test_profiles_property_returns_copy(self, fake_prefs):
        fake_prefs.save_proxy_profile(ProxyProfile(name="a"))
        fake_prefs.save_proxy_profile(ProxyProfile(name="b"))
        model = ProxyProfileListModel(fake_prefs)
        profiles = model.profiles
        assert len(profiles) == 2
        names = [p.name for p in profiles]
        assert "a" in names
        assert "b" in names

    def test_load_reloads_from_prefs(self, fake_prefs):
        fake_prefs.save_proxy_profile(ProxyProfile(name="original"))
        model = ProxyProfileListModel(fake_prefs)
        assert model.rowCount() == 1
        fake_prefs.save_proxy_profile(ProxyProfile(name="added"))
        model.load()
        assert model.rowCount() == 2

    def test_load_cleans_stale_test_status(self, fake_prefs):
        fake_prefs.save_proxy_profile(ProxyProfile(name="keep"))
        model = ProxyProfileListModel(fake_prefs)
        model.set_test_result("keep", 1, 0)
        model.set_test_result("stale", 1, 0)  # stale — not in profiles
        assert model._test_status.get("stale") == "all_reachable"
        model.load()
        assert "stale" not in model._test_status
        assert "keep" in model._test_status


# ═══════════════════════════════════════════════════════════════════════════
# ProxyProfileEditDialog
# ═══════════════════════════════════════════════════════════════════════════


class TestProxyProfileEditDialog:
    def test_new_dialog_defaults(self, theme_mgr):
        dlg = ProxyProfileEditDialog(None, [])
        assert dlg._is_new is True
        assert dlg._endpoints == []
        assert dlg._bypass_domains == []
        assert dlg._name_edit.text() == ""

    def test_edit_dialog_loads_profile(self, theme_mgr):
        profile = ProxyProfile(
            name="prod",
            proxies=(ProxyEndpointSpec(label="ep1", host="h1", type=ProxyType.HTTPS),),
            bypass_domains=("example.com", "test.com"),
        )
        dlg = ProxyProfileEditDialog(None, [], profile)
        assert dlg._is_new is False
        assert dlg._name_edit.text() == "prod"
        assert len(dlg._endpoints) == 1
        assert dlg._endpoints[0].label == "ep1"
        assert len(dlg._bypass_domains) == 2
        assert "example.com" in dlg._bypass_domains

    def test_get_profile_builds_from_fields(self, theme_mgr):
        dlg = ProxyProfileEditDialog(None, [])
        dlg._name_edit.setText("my-profile")
        dlg._endpoints = [ProxyEndpointSpec(label="ep1", host="h1")]
        dlg._bypass_domains = ["bypass.example.com"]

        profile = dlg.get_profile()
        assert profile.name == "my-profile"
        assert len(profile.proxies) == 1
        assert profile.proxies[0].label == "ep1"
        assert profile.bypass_domains == ("bypass.example.com",)

    def test_validation_empty_name(self, theme_mgr):
        dlg = ProxyProfileEditDialog(None, [])
        dlg._name_edit.setText("")
        assert dlg._validate() is False
        assert "Name cannot be empty" in dlg._name_error.text()

    def test_validation_duplicate_name(self, theme_mgr):
        dlg = ProxyProfileEditDialog(None, ["existing"])
        dlg._name_edit.setText("existing")
        assert dlg._validate() is False
        assert "Name already exists" in dlg._name_error.text()

    def test_validation_passes_unique_name(self, theme_mgr):
        dlg = ProxyProfileEditDialog(None, ["existing"])
        dlg._name_edit.setText("new_name")
        assert dlg._validate() is True
        assert dlg._name_error.text() == ""

    def test_dirty_set_on_name_change(self, theme_mgr):
        dlg = ProxyProfileEditDialog(None, [])
        assert dlg._dirty is False
        dlg._name_edit.setText("x")
        assert dlg._dirty is True

    def test_dirty_set_on_add_endpoint(self, theme_mgr):
        dlg = ProxyProfileEditDialog(None, [])
        assert dlg._dirty is False
        dlg._endpoints.append(ProxyEndpointSpec(label="ep", host="h"))
        dlg._mark_dirty()
        assert dlg._dirty is True

    def test_endpoint_table_renders_rows(self, theme_mgr):
        dlg = ProxyProfileEditDialog(None, [])
        dlg._endpoints = [
            ProxyEndpointSpec(label="ep1", type=ProxyType.HTTP, host="h1", port=8080, weight=1),
            ProxyEndpointSpec(label="ep2", type=ProxyType.SOCKS5, host="h2", port=1080, weight=5),
        ]
        dlg._refresh_endpoint_table()
        assert dlg._endpoint_table.rowCount() == 2
        assert dlg._endpoint_table.item(0, 0).text() == "ep1"
        assert dlg._endpoint_table.item(1, 0).text() == "ep2"

    def test_bypass_table_renders_rows(self, theme_mgr):
        dlg = ProxyProfileEditDialog(None, [])
        dlg._bypass_domains = ["example.com", "test.com"]
        dlg._refresh_bypass_table()
        assert dlg._bypass_table.rowCount() == 2
        assert dlg._bypass_table.item(0, 0).text() == "example.com"
        assert dlg._bypass_table.item(1, 0).text() == "test.com"

    def test_remove_endpoint_empty_selection(self, theme_mgr):
        dlg = ProxyProfileEditDialog(None, [])
        dlg._endpoints = [ProxyEndpointSpec(label="ep", host="h")]
        dlg._refresh_endpoint_table()
        # No selection — remove should be no-op
        dlg._remove_endpoint()
        assert len(dlg._endpoints) == 1


# ═══════════════════════════════════════════════════════════════════════════
# _ProbeWorker
# ═══════════════════════════════════════════════════════════════════════════


class TestProbeWorker:
    def test_worker_init_stores_endpoints(self, qapp):
        from astrocrawl.proxy._config import ParsedProxy, ProxyAuth

        ep = ParsedProxy(type=ProxyType.HTTP, host="127.0.0.1", port=9999, auth=ProxyAuth())
        worker = _ProbeWorker([ep])
        assert len(worker._endpoints) == 1
        assert worker._endpoints[0].host == "127.0.0.1"

    def test_worker_empty_endpoints(self, qapp):
        worker = _ProbeWorker([])
        assert len(worker._endpoints) == 0

    def test_worker_run_reachable_endpoint(self, qapp):
        """启动本地 TCP server，验证 _ProbeWorker.run() → single_result(reachable=True)。"""
        import socket
        import threading

        from PySide6.QtCore import QEventLoop

        from astrocrawl.proxy._config import ParsedProxy, ProxyAuth

        # ── 启动一次性 TCP server ──
        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind(("127.0.0.1", 0))
        actual_port = server_sock.getsockname()[1]
        server_sock.listen(1)

        server_ready = threading.Event()
        server_done = threading.Event()

        def serve():
            server_ready.set()
            try:
                for _ in range(10):  # 接受全部 10 次 probe
                    try:
                        conn, _ = server_sock.accept()
                        conn.close()
                    except OSError:
                        break
            finally:
                server_sock.close()
                server_done.set()

        server_thread = threading.Thread(target=serve, daemon=True)
        server_thread.start()
        assert server_ready.wait(timeout=2), "TCP server failed to start"

        # ── 创建 worker + 收集信号 ──
        ep = ParsedProxy(type=ProxyType.HTTP, host="127.0.0.1", port=actual_port, auth=ProxyAuth())
        worker = _ProbeWorker([ep])

        results: list[tuple[str, bool]] = []
        worker.single_result.connect(lambda label, reachable: results.append((label, reachable)))

        # 使用 QEventLoop 等待线程结束——不阻塞信号传递
        loop = QEventLoop()
        worker.finished.connect(loop.quit)
        worker.start()
        loop.exec()  # 处理事件直到 worker 完成

        server_done.wait(timeout=1)

        assert len(results) >= 1, f"Expected >=1 single_result, got {len(results)}"
        assert results[0][0] == f"HTTP:127.0.0.1:{actual_port}"
        assert results[0][1] is True  # reachable

    def test_worker_run_unreachable_endpoint(self, qapp):
        """连接未监听端口，验证 _ProbeWorker.run() → single_result(reachable=False)。"""
        from PySide6.QtCore import QEventLoop

        from astrocrawl.proxy._config import ParsedProxy, ProxyAuth

        ep = ParsedProxy(type=ProxyType.HTTP, host="127.0.0.1", port=19999, auth=ProxyAuth())
        worker = _ProbeWorker([ep])

        results: list[tuple[str, bool]] = []
        worker.single_result.connect(lambda label, reachable: results.append((label, reachable)))

        loop = QEventLoop()
        worker.finished.connect(loop.quit)
        worker.start()
        loop.exec()

        assert len(results) >= 1, f"Expected >=1 single_result, got {len(results)}"
        assert results[0][0] == "HTTP:127.0.0.1:19999"
        assert results[0][1] is False  # unreachable


# ═══════════════════════════════════════════════════════════════════════════
# _ProxyProfilePage
# ═══════════════════════════════════════════════════════════════════════════


class TestProxyProfilePage:
    def test_page_init(self, theme_mgr, fake_prefs):
        page = _ProxyProfilePage(fake_prefs)
        assert page._table is not None
        assert page._proxy is not None
        assert page._model is not None

    def test_search_columns(self, theme_mgr, fake_prefs):
        page = _ProxyProfilePage(fake_prefs)
        assert page._search_columns() == (0,)

    def test_extra_buttons_includes_set_default(self, theme_mgr, fake_prefs):
        page = _ProxyProfilePage(fake_prefs)
        buttons = page._extra_buttons()
        labels = [b[0] for b in buttons]
        assert "☆ Set as Default" in labels

    def test_extra_buttons_includes_test_selected(self, theme_mgr, fake_prefs):
        page = _ProxyProfilePage(fake_prefs)
        buttons = page._extra_buttons()
        labels = [b[0] for b in buttons]
        assert "Test Selected" in labels

    def test_empty_text(self, theme_mgr, fake_prefs):
        page = _ProxyProfilePage(fake_prefs)
        assert "No Proxy Profiles" in page._empty_text()

    def test_set_default_updates_last_used(self, theme_mgr, fake_prefs):
        profile = ProxyProfile(name="my-proxy")
        fake_prefs.save_proxy_profile(profile)
        saved = fake_prefs.get_proxy_profile("my-proxy")
        page = _ProxyProfilePage(fake_prefs)
        page.refresh()
        page._table.selectRow(0)
        page._on_set_default()
        entry = fake_prefs.get_proxy_last_used("preview")
        assert entry is not None
        assert entry["profile"] == saved.uuid

    def test_set_default_emits_signal(self, theme_mgr, fake_prefs):
        fake_prefs.save_proxy_profile(ProxyProfile(name="my-proxy"))
        page = _ProxyProfilePage(fake_prefs)
        page.refresh()
        page._table.selectRow(0)
        emitted = []
        page.profile_changed.connect(lambda: emitted.append(True))
        page._on_set_default()
        assert len(emitted) == 1

    def test_remove_default_succeeds(self, theme_mgr, fake_prefs, monkeypatch):
        fake_prefs.save_proxy_profile(ProxyProfile(name="default"))
        page = _ProxyProfilePage(fake_prefs)
        page.refresh()

        from unittest.mock import MagicMock

        real_qmb = __import__("PySide6.QtWidgets", fromlist=["QMessageBox"]).QMessageBox

        class _FakeDeleteBox:
            def __init__(self, *args, **kwargs):
                self._sentinel = MagicMock()

            def exec(self):
                pass

            def addButton(self, text, role):
                return self._sentinel

            def clickedButton(self):
                return self._sentinel

        _fake = _FakeDeleteBox
        _fake.Warning = real_qmb.Warning
        _fake.YesRole = real_qmb.YesRole
        _fake.NoRole = real_qmb.NoRole

        monkeypatch.setattr(
            "astrocrawl.gui._proxy_profile_page.QMessageBox",
            _fake,
        )
        page._table.selectRow(0)
        page._on_remove(0)
        profiles = fake_prefs.get_proxy_profile_names()
        assert "default" not in profiles

    def test_remove_with_consumer_in_use_warns(self, theme_mgr, fake_prefs, monkeypatch):
        profile = ProxyProfile(name="in-use")
        fake_prefs.save_proxy_profile(profile)
        saved = fake_prefs.get_proxy_profile("in-use")
        fake_prefs.set_proxy_last_used("source", saved.uuid, "")
        page = _ProxyProfilePage(fake_prefs)
        page.refresh()

        monkeypatch.setattr(
            "astrocrawl.gui._proxy_profile_page.QMessageBox.warning",
            lambda *args, **kwargs: None,
        )
        page._table.selectRow(0)
        page._on_remove(0)
        # Profile should still be there
        profiles = fake_prefs.get_proxy_profile_names()
        assert "in-use" in profiles

    def test_remove_profile_succeeds(self, theme_mgr, fake_prefs, monkeypatch):
        fake_prefs.save_proxy_profile(ProxyProfile(name="removable"))
        page = _ProxyProfilePage(fake_prefs)
        page.refresh()

        # Mock QMessageBox to simulate "删除" click — must preserve enum attrs
        from unittest.mock import MagicMock

        real_qmb = __import__("PySide6.QtWidgets", fromlist=["QMessageBox"]).QMessageBox

        class _FakeDeleteBox:
            def __init__(self):
                self._sentinel = MagicMock()

            def exec(self):
                pass

            def addButton(self, text, role):
                return self._sentinel

            def clickedButton(self):
                return self._sentinel

        def _fake_qmessagebox(*args, **kwargs):
            return _FakeDeleteBox()

        # Attach enum attrs so QMessageBox.Warning etc. still work
        _fake_qmessagebox.Warning = real_qmb.Warning
        _fake_qmessagebox.YesRole = real_qmb.YesRole
        _fake_qmessagebox.NoRole = real_qmb.NoRole

        monkeypatch.setattr(
            "astrocrawl.gui._proxy_profile_page.QMessageBox",
            _fake_qmessagebox,
        )
        page._table.selectRow(0)
        page._on_remove(0)

        profiles = fake_prefs.get_proxy_profile_names()
        assert "removable" not in profiles

    def test_status_message_to_psb(self, theme_mgr, fake_prefs):
        from astrocrawl.gui._animated_bar import _ProgressStatusBar

        page = _ProxyProfilePage(fake_prefs)
        psb = _ProgressStatusBar()
        psb.connect_page(page)
        page.status_message.emit("test", "success")
        assert "test" in psb._status_bar.text()
        assert psb._status_bar.isHidden() is False


# ═══════════════════════════════════════════════════════════════════════════
# ProxyRouteModel
# ═══════════════════════════════════════════════════════════════════════════


class TestProxyRouteModel:
    def test_row_count_is_3(self, fake_prefs):
        model = ProxyRouteModel(fake_prefs)
        assert model.rowCount() == 3

    def test_column_count_is_3(self, fake_prefs):
        model = ProxyRouteModel(fake_prefs)
        assert model.columnCount() == 3

    def test_header_labels(self, fake_prefs):
        model = ProxyRouteModel(fake_prefs)
        assert model.headerData(0, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) == "Consumer"
        assert model.headerData(1, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) == "Profile"
        assert model.headerData(2, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) == "Node"

    def test_column_0_shows_translated_consumer_names(self, fake_prefs):
        model = ProxyRouteModel(fake_prefs)
        names = set()
        for row in range(3):
            idx = model.index(row, 0)
            names.add(str(model.data(idx, Qt.ItemDataRole.DisplayRole)))
        assert "AI Calls" in names
        assert "Rule Preview" in names
        assert "Rule Source" in names

    def test_column_0_user_role_returns_consumer_key(self, fake_prefs):
        model = ProxyRouteModel(fake_prefs)
        keys = set()
        for row in range(3):
            idx = model.index(row, 0)
            keys.add(str(model.data(idx, Qt.ItemDataRole.UserRole)))
        assert "ai" in keys
        assert "preview" in keys
        assert "source" in keys

    def test_column_1_default_is_direct(self, fake_prefs):
        model = ProxyRouteModel(fake_prefs)
        idx = model.index(0, 1)
        assert model.data(idx, Qt.ItemDataRole.DisplayRole) == "Direct"
        assert model.data(idx, Qt.ItemDataRole.UserRole) == ""

    def test_column_1_shows_profile_name_when_set(self, fake_prefs):
        profile = ProxyProfile(name="my-proxy")
        fake_prefs.save_proxy_profile(profile)
        saved = fake_prefs.get_proxy_profile("my-proxy")
        fake_prefs.set_proxy_last_used("preview", saved.uuid, "")
        model = ProxyRouteModel(fake_prefs)
        for row in range(3):
            idx = model.index(row, 0)
            if model.data(idx, Qt.ItemDataRole.UserRole) == "preview":
                p_idx = model.index(row, 1)
                assert model.data(p_idx, Qt.ItemDataRole.DisplayRole) == "my-proxy"
                assert model.data(p_idx, Qt.ItemDataRole.UserRole) == saved.uuid
                break
        else:
            pytest.fail("preview row not found")

    def test_set_data_updates_prefs(self, fake_prefs):
        profile = ProxyProfile(name="my-proxy")
        fake_prefs.save_proxy_profile(profile)
        saved = fake_prefs.get_proxy_profile("my-proxy")
        model = ProxyRouteModel(fake_prefs)
        for row in range(3):
            idx = model.index(row, 0)
            if model.data(idx, Qt.ItemDataRole.UserRole) == "preview":
                p_idx = model.index(row, 1)
                model.setData(p_idx, saved.uuid, Qt.ItemDataRole.EditRole)
                break
        entry = fake_prefs.get_proxy_last_used("preview")
        assert entry is not None
        assert entry["profile"] == saved.uuid

    def test_column_1_is_editable(self, fake_prefs):
        model = ProxyRouteModel(fake_prefs)
        idx = model.index(0, 1)
        assert bool(model.flags(idx) & Qt.ItemFlag.ItemIsEditable)

    def test_column_2_not_editable_when_no_profile(self, fake_prefs):
        model = ProxyRouteModel(fake_prefs)
        idx = model.index(0, 2)
        assert not bool(model.flags(idx) & Qt.ItemFlag.ItemIsEditable)

    def test_column_0_is_not_editable(self, fake_prefs):
        model = ProxyRouteModel(fake_prefs)
        idx = model.index(0, 0)
        assert not bool(model.flags(idx) & Qt.ItemFlag.ItemIsEditable)

    def test_get_consumer_key(self, fake_prefs):
        model = ProxyRouteModel(fake_prefs)
        key = model.get_consumer_key(0)
        assert key is not None
        assert key in ("preview", "ai", "source")

    def test_get_consumer_key_invalid(self, fake_prefs):
        model = ProxyRouteModel(fake_prefs)
        assert model.get_consumer_key(999) is None

    def test_load_refreshes_consumers(self, fake_prefs):
        model = ProxyRouteModel(fake_prefs)
        profile = ProxyProfile(name="p")
        fake_prefs.save_proxy_profile(profile)
        saved = fake_prefs.get_proxy_profile("p")
        fake_prefs.set_proxy_last_used("preview", saved.uuid, "")
        model.load()
        for row in range(3):
            idx = model.index(row, 0)
            if model.data(idx, Qt.ItemDataRole.UserRole) == "preview":
                p_idx = model.index(row, 1)
                assert model.data(p_idx, Qt.ItemDataRole.DisplayRole) == "p"
                return
        pytest.fail("preview not found")


# ═══════════════════════════════════════════════════════════════════════════
# _RouteSettingsPage
# ═══════════════════════════════════════════════════════════════════════════


class TestRouteSettingsPage:
    def test_page_init(self, theme_mgr, fake_prefs):
        page = _RouteSettingsPage(fake_prefs)
        assert page._table is not None
        assert page._proxy is not None
        assert page._model is not None

    def test_crud_buttons_hidden(self, theme_mgr, fake_prefs):
        page = _RouteSettingsPage(fake_prefs)
        for name in ("add-btn", "edit-btn", "remove-btn"):
            btn = page.findChild(QPushButton, name)
            if btn is not None:
                assert not btn.isVisible(), f"{name} should be hidden"

    def test_extra_buttons_includes_reset(self, theme_mgr, fake_prefs):
        page = _RouteSettingsPage(fake_prefs)
        buttons = page._extra_buttons()
        labels = [b[0] for b in buttons]
        assert "Reset to Default" in labels

    def test_search_columns(self, theme_mgr, fake_prefs):
        page = _RouteSettingsPage(fake_prefs)
        assert page._search_columns() == (0,)

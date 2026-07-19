"""gui/_ai_profile_page.py 测试 — AIProfileListModel + AIProfileEditDialog + _AIProfilePage + _FetchModelsWorker。

覆盖:
- AIProfileListModel: rowCount / columnCount / data roles / flags / _status_display / setData
- AIProfileEditDialog: 默认值 / 验证 / dirty check / get_profile / on_models_fetched / toggle key / cancel
- _AIProfilePage: 按钮 / pending toggles / default marker / _on_add / _on_edit / checkbox / fetch / test connection
- _FetchModelsWorker: configure_fetch / configure_test / run (all paths)
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt

from astrocrawl.ai._profile import AIProfile
from astrocrawl.gui._ai_profile_page import AIProfileEditDialog, AIProfileListModel, _AIProfilePage

pytestmark = pytest.mark.gui


@pytest.fixture
def seeded_fake_prefs(fake_prefs):
    """FakePreferences with a default AIProfile already saved."""
    fake_prefs.save_ai_profile(AIProfile(name="default"))
    return fake_prefs


# ═══════════════════════════════════════════════════════════════════════════
# AIProfileListModel
# ═══════════════════════════════════════════════════════════════════════════


class TestAIProfileListModel:
    def test_initial_load(self, fake_prefs):
        model = AIProfileListModel(fake_prefs)
        assert model.rowCount() == 0

    def test_initial_load_seeded(self, seeded_fake_prefs):
        model = AIProfileListModel(seeded_fake_prefs)
        assert model.rowCount() == 1
        assert model.columnCount() == 5

    def test_header_labels(self, fake_prefs):
        model = AIProfileListModel(fake_prefs)
        assert model.headerData(0, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) == "Name"
        assert model.headerData(1, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) == "Provider"
        assert model.headerData(2, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) == "Model"

    def test_display_role_name_with_star(self, seeded_fake_prefs):
        seeded_fake_prefs._data["ai_active_profile"] = "default"
        model = AIProfileListModel(seeded_fake_prefs)
        idx = model.index(0, 0)
        assert "☆" in str(model.data(idx, Qt.ItemDataRole.DisplayRole))
        assert "default" in str(model.data(idx, Qt.ItemDataRole.DisplayRole))

    def test_display_role_name_without_star(self, seeded_fake_prefs):
        seeded_fake_prefs._data["ai_profiles"].append(AIProfile(name="other", provider="google").to_dict())
        seeded_fake_prefs._data["ai_active_profile"] = "default"
        model = AIProfileListModel(seeded_fake_prefs)
        idx = model.index(1, 0)
        assert "☆" not in str(model.data(idx, Qt.ItemDataRole.DisplayRole))

    def test_status_user_role(self, seeded_fake_prefs):
        seeded_fake_prefs._data["ai_profiles"][0]["last_test_status"] = "ok"
        model = AIProfileListModel(seeded_fake_prefs)
        idx = model.index(0, 3)
        assert model.data(idx, Qt.ItemDataRole.UserRole) == "ok"

    def test_status_untested_default(self, seeded_fake_prefs):
        model = AIProfileListModel(seeded_fake_prefs)
        idx = model.index(0, 3)
        assert model.data(idx, Qt.ItemDataRole.UserRole) == "untested"

    def test_enabled_checkstate_role(self, seeded_fake_prefs):
        model = AIProfileListModel(seeded_fake_prefs)
        idx = model.index(0, 4)
        assert model.data(idx, Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked.value

    def test_disabled_checkstate_role(self, seeded_fake_prefs):
        seeded_fake_prefs._data["ai_profiles"][0]["enabled"] = False
        model = AIProfileListModel(seeded_fake_prefs)
        idx = model.index(0, 4)
        assert model.data(idx, Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Unchecked.value

    def test_flags_enabled_column(self, seeded_fake_prefs):
        model = AIProfileListModel(seeded_fake_prefs)
        flags = model.flags(model.index(0, 4))
        assert bool(flags & Qt.ItemFlag.ItemIsUserCheckable)

    def test_flags_other_columns_not_checkable(self, seeded_fake_prefs):
        model = AIProfileListModel(seeded_fake_prefs)
        flags = model.flags(model.index(0, 0))
        assert not bool(flags & Qt.ItemFlag.ItemIsUserCheckable)

    def test_setdata_toggles_enabled(self, seeded_fake_prefs):
        model = AIProfileListModel(seeded_fake_prefs)
        idx = model.index(0, 4)
        model.setData(idx, Qt.CheckState.Unchecked.value, Qt.ItemDataRole.CheckStateRole)
        assert model.data(idx, Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Unchecked.value

    def test_get_profile_valid_row(self, seeded_fake_prefs):
        model = AIProfileListModel(seeded_fake_prefs)
        p = model.get_profile(0)
        assert p is not None
        assert p.name == "default"

    def test_get_profile_invalid_row(self, fake_prefs):
        model = AIProfileListModel(fake_prefs)
        assert model.get_profile(999) is None

    def test_load_reloads_from_prefs(self, seeded_fake_prefs):
        model = AIProfileListModel(seeded_fake_prefs)
        seeded_fake_prefs._data["ai_profiles"][0]["model"] = "changed-model"
        model.load()
        idx = model.index(0, 2)
        assert model.data(idx, Qt.ItemDataRole.DisplayRole) == "changed-model"


# ═══════════════════════════════════════════════════════════════════════════
# AIProfileEditDialog
# ═══════════════════════════════════════════════════════════════════════════


class TestAIProfileEditDialog:
    def test_new_dialog_defaults(self, theme_mgr):
        dlg = AIProfileEditDialog(None, [])
        assert dlg._is_new is True
        assert dlg.profile_name == ""
        assert dlg._temp_spin.value() == 0.1
        assert dlg._max_tokens_spin.value() == 2048

    def test_edit_dialog_loads_profile(self, theme_mgr):
        profile = AIProfile(name="prod", provider="google", model="gemini-pro", temperature=0.5, max_tokens=4096)
        dlg = AIProfileEditDialog(None, [], profile)
        assert dlg._is_new is False
        assert dlg.profile_name == "prod"
        assert dlg._temp_spin.value() == 0.5
        assert dlg._max_tokens_spin.value() == 4096

    def test_name_readonly_when_editing(self, theme_mgr):
        profile = AIProfile(name="prod")
        dlg = AIProfileEditDialog(None, [], profile)
        assert dlg._name_edit.isReadOnly() is True

    def test_name_writable_when_new(self, theme_mgr):
        dlg = AIProfileEditDialog(None, [])
        assert dlg._name_edit.isReadOnly() is False

    def test_validation_empty_name(self, theme_mgr):
        dlg = AIProfileEditDialog(None, [])
        dlg._name_edit.setText("")
        assert dlg._validate() is False
        assert "cannot be empty" in dlg._name_error.text()

    def test_validation_duplicate_name(self, theme_mgr):
        dlg = AIProfileEditDialog(None, ["existing"])
        dlg._name_edit.setText("existing")
        assert dlg._validate() is False
        assert "already exists" in dlg._name_error.text()

    def test_validation_passes_unique_name(self, theme_mgr):
        dlg = AIProfileEditDialog(None, ["existing"])
        dlg._name_edit.setText("new_name")
        assert dlg._validate() is True
        assert dlg._name_error.text() == ""

    def test_get_profile_builds_from_fields(self, theme_mgr):
        dlg = AIProfileEditDialog(None, [])
        dlg._name_edit.setText("test")
        dlg._api_key_edit.setText("sk-xxx")
        dlg._temp_spin.setValue(0.8)
        dlg._max_tokens_spin.setValue(8192)

        profile = dlg.get_profile()
        assert profile.name == "test"
        assert profile.api_key == "sk-xxx"
        assert profile.temperature == 0.8
        assert profile.max_tokens == 8192

    def test_dirty_set_on_any_change(self, theme_mgr):
        dlg = AIProfileEditDialog(None, [])
        assert dlg._dirty is False
        dlg._name_edit.setText("x")
        assert dlg._dirty is True

    def test_on_models_fetched(self, theme_mgr):
        dlg = AIProfileEditDialog(None, [])
        dlg.on_models_fetched(["gpt-4o", "gpt-4o-mini"])
        assert dlg._model_combo.count() == 2


# ═══════════════════════════════════════════════════════════════════════════
# _AIProfilePage
# ═══════════════════════════════════════════════════════════════════════════


class TestAIProfilePage:
    def test_page_init(self, theme_mgr, fake_prefs):
        page = _AIProfilePage(fake_prefs)
        assert page._table is not None
        assert page._proxy is not None
        assert page._model is not None

    def test_search_columns(self, theme_mgr, fake_prefs):
        page = _AIProfilePage(fake_prefs)
        assert page._search_columns() == (0, 1, 2)

    def test_extra_buttons_includes_set_default(self, theme_mgr, fake_prefs):
        page = _AIProfilePage(fake_prefs)
        buttons = page._extra_buttons()
        labels = [b[0] for b in buttons]
        assert "☆ Set as Default" in labels

    def test_set_default_updates_active(self, theme_mgr, seeded_fake_prefs):
        seeded_fake_prefs._data["ai_profiles"].append(AIProfile(name="prod", provider="google").to_dict())
        page = _AIProfilePage(seeded_fake_prefs)
        page.refresh()
        page._table.selectRow(1)
        page._on_set_default()
        assert seeded_fake_prefs._data["ai_active_profile"] == "prod"

    def test_apply_toggle_writes_to_prefs(self, theme_mgr, seeded_fake_prefs):
        page = _AIProfilePage(seeded_fake_prefs)
        page._apply_toggle("default", False)
        prof = seeded_fake_prefs.get_ai_profile("default")
        assert prof is not None
        assert prof.enabled is False

    def test_pending_and_apply(self, theme_mgr, seeded_fake_prefs):
        page = _AIProfilePage(seeded_fake_prefs)
        page._set_pending("default", False)
        assert page.has_pending is True
        page.apply_pending()
        assert page.has_pending is False

    def test_extra_buttons_includes_test_connection(self, theme_mgr, fake_prefs):
        page = _AIProfilePage(fake_prefs)
        buttons = page._extra_buttons()
        labels = [b[0] for b in buttons]
        assert "Test Connection" in labels

    def test_test_connection_updates_profile_status(self, theme_mgr, seeded_fake_prefs, monkeypatch):
        from unittest.mock import MagicMock

        seeded_fake_prefs._data["ai_profiles"][0] = AIProfile(
            name="default", provider="openai", api_key="sk-test"
        ).to_dict()
        page = _AIProfilePage(seeded_fake_prefs)
        page.refresh()

        # Mock worker
        mock_worker = MagicMock()
        mock_worker.isRunning.return_value = False
        monkeypatch.setattr(
            "astrocrawl.gui._ai_profile_page._FetchModelsWorker",
            lambda parent: mock_worker,
        )

        page._table.selectRow(0)
        page._on_test_connection()

        mock_worker.configure_test.assert_called_once()
        assert mock_worker.start.called

    def test_test_connection_result_ok(self, theme_mgr, fake_prefs):
        from astrocrawl.ai._profile import AIProfile

        fake_prefs.save_ai_profile(AIProfile(name="test", provider="openai", api_key="sk"))
        page = _AIProfilePage(fake_prefs)
        page.refresh()

        page._on_test_result("test")
        updated = fake_prefs.get_ai_profile("test")
        assert updated is not None
        assert updated.last_test_status == "ok"
        assert updated.last_test_time is not None

    def test_test_connection_result_failed(self, theme_mgr, fake_prefs):
        from astrocrawl.ai._profile import AIProfile

        fake_prefs.save_ai_profile(AIProfile(name="test", provider="openai", api_key="sk"))
        page = _AIProfilePage(fake_prefs)
        page.refresh()

        page._on_test_result_failed("test", "Connection refused")
        updated = fake_prefs.get_ai_profile("test")
        assert updated is not None
        assert updated.last_test_status == "failed"
        assert updated.last_test_time is not None


# ═══════════════════════════════════════════════════════════════════════════
# _FetchModelsWorker
# ═══════════════════════════════════════════════════════════════════════════


class TestFetchModelsWorker:
    def test_configure_fetch(self, qapp):
        from astrocrawl.gui._ai_profile_page import _FetchModelsWorker

        worker = _FetchModelsWorker()
        worker.configure_fetch("openai", "https://api.openai.com/v1", "sk-test")
        assert worker._provider == "openai"

    def test_configure_test(self, qapp):
        from astrocrawl.gui._ai_profile_page import _FetchModelsWorker

        worker = _FetchModelsWorker()
        worker.configure_test("default", "openai", "", "sk-test")
        assert worker._profile_name == "default"
        assert worker._provider == "openai"

    def test_run_no_provider_emits_failed(self, qapp):
        from astrocrawl.gui._ai_profile_page import _FetchModelsWorker

        worker = _FetchModelsWorker()
        worker.configure_fetch("nonexistent", "", "")

        errors = []
        worker.fetch_failed.connect(lambda msg: errors.append(msg))
        worker.run()

        assert len(errors) == 1
        assert "does not support" in errors[0]

    def test_run_models_fetched(self, qapp, monkeypatch):
        from astrocrawl.gui._ai_profile_page import _FetchModelsWorker

        fake_models = ["gpt-4o", "gpt-4o-mini"]

        def _fake_list_models(base_url, api_key, timeout):
            return fake_models

        monkeypatch.setattr(
            "astrocrawl.ai._provider_registry.get_list_models_func",
            lambda name: _fake_list_models,
        )
        worker = _FetchModelsWorker()
        worker.configure_fetch("openai", "", "")
        results = []
        worker.models_fetched.connect(results.append)
        worker.run()
        assert results == [fake_models]

    def test_run_fetch_failed_on_error(self, qapp, monkeypatch):
        from astrocrawl.gui._ai_profile_page import _FetchModelsWorker

        def _raise_auth(*_args, **_kwargs):
            raise Exception("auth failed")

        monkeypatch.setattr(
            "astrocrawl.ai._provider_registry.get_list_models_func",
            lambda name: _raise_auth,
        )
        worker = _FetchModelsWorker()
        worker.configure_fetch("openai", "", "")
        errors = []
        worker.fetch_failed.connect(errors.append)
        worker.run()
        assert len(errors) == 1
        assert "auth" in errors[0]

    def test_run_test_ok(self, qapp, monkeypatch):
        from astrocrawl.gui._ai_profile_page import _FetchModelsWorker

        def _return_models(base_url, api_key, timeout):
            return ["m1"]

        monkeypatch.setattr(
            "astrocrawl.ai._provider_registry.get_list_models_func",
            lambda name: _return_models,
        )
        worker = _FetchModelsWorker()
        worker.configure_test("test-profile", "openai", "", "sk-test")
        names = []
        worker.test_ok.connect(names.append)
        worker.run()
        assert names == ["test-profile"]

    def test_run_test_failed(self, qapp, monkeypatch):
        from astrocrawl.gui._ai_profile_page import _FetchModelsWorker

        def _raise_auth(*_args, **_kwargs):
            raise Exception("auth")

        monkeypatch.setattr(
            "astrocrawl.ai._provider_registry.get_list_models_func",
            lambda name: _raise_auth,
        )
        worker = _FetchModelsWorker()
        worker.configure_test("test-profile", "openai", "", "sk")
        failures = []
        worker.test_failed.connect(lambda n, e: failures.append((n, e)))
        worker.run()
        assert failures == [("test-profile", "auth")]

    def test_run_list_models_none_emits_test_failed(self, qapp, monkeypatch):
        from astrocrawl.gui._ai_profile_page import _FetchModelsWorker

        monkeypatch.setattr(
            "astrocrawl.ai._provider_registry.get_list_models_func",
            lambda name: None,
        )
        worker = _FetchModelsWorker()
        worker.configure_test("p", "unknown", "", "")
        failures = []
        worker.test_failed.connect(lambda n, e: failures.append((n, e)))
        worker.run()
        assert len(failures) == 1
        assert "does not support" in failures[0][1]


# ═══════════════════════════════════════════════════════════════════════════
# _AIProfilePage — 删除流程
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _patch_dialogs(monkeypatch):
    """全局 patch QMessageBox + QDialog.exec 防止模态阻塞。"""
    from unittest.mock import MagicMock

    from PySide6.QtWidgets import QDialog, QMessageBox

    mock = MagicMock()
    for method in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, method, mock)
    monkeypatch.setattr(QDialog, "exec", MagicMock())
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda self: self.buttons()[0] if self.buttons() else None)


class TestAIProfilePageDelete:
    def test_on_remove_confirms_and_deletes(self, theme_mgr, seeded_fake_prefs):
        seeded_fake_prefs.save_ai_profile(AIProfile(name="extra", provider="openai"))
        page = _AIProfilePage(seeded_fake_prefs)
        page.refresh()
        page._table.selectRow(1)
        page._on_remove(1)
        assert seeded_fake_prefs.get_ai_profile("extra") is None

    def test_on_remove_cancels_without_deleting(self, theme_mgr, seeded_fake_prefs, monkeypatch):
        from PySide6.QtWidgets import QMessageBox

        monkeypatch.setattr(
            QMessageBox,
            "clickedButton",
            lambda self: self.buttons()[1] if len(self.buttons()) > 1 else None,
        )
        seeded_fake_prefs.save_ai_profile(AIProfile(name="extra", provider="openai"))
        page = _AIProfilePage(seeded_fake_prefs)
        page.refresh()
        page._table.selectRow(1)
        page._on_remove(1)
        assert seeded_fake_prefs.get_ai_profile("extra") is not None

    def test_on_remove_non_active_works(self, theme_mgr, seeded_fake_prefs):
        seeded_fake_prefs.save_ai_profile(AIProfile(name="secondary", provider="openai"))
        seeded_fake_prefs.set_active_ai_profile("secondary")
        page = _AIProfilePage(seeded_fake_prefs)
        page.refresh()
        page._table.selectRow(0)
        page._on_remove(0)
        assert seeded_fake_prefs.get_ai_profile("default") is None

    def test_on_remove_active_blocked(self, theme_mgr, seeded_fake_prefs):
        seeded_fake_prefs.save_ai_profile(AIProfile(name="secondary", provider="openai"))
        seeded_fake_prefs.set_active_ai_profile("secondary")
        page = _AIProfilePage(seeded_fake_prefs)
        page.refresh()
        for i in range(page._model.rowCount()):
            if page._model.get_profile(i).name == "secondary":
                page._table.selectRow(i)
                page._on_remove(i)
                break
        assert seeded_fake_prefs.get_ai_profile("secondary") is not None


# ═══════════════════════════════════════════════════════════════════════════
# _AIProfilePage — status_message 状态栏
# ═══════════════════════════════════════════════════════════════════════════


class TestStatusMessage:
    def test_status_message_updates_bar(self, theme_mgr, fake_prefs):
        from astrocrawl.gui._animated_bar import _ProgressStatusBar

        page = _AIProfilePage(fake_prefs)
        psb = _ProgressStatusBar()
        psb.connect_page(page)
        page.status_message.emit("连接成功", "success")
        assert "连接成功" in psb._status_bar.text()

    def test_status_bar_persistent(self, theme_mgr, fake_prefs):
        from astrocrawl.gui._animated_bar import _ProgressStatusBar

        page = _AIProfilePage(fake_prefs)
        psb = _ProgressStatusBar()
        psb.connect_page(page)
        page.status_message.emit("info test", "info")
        assert "info test" in psb._status_bar.text()
        assert psb._status_bar.isHidden() is False


# ═══════════════════════════════════════════════════════════════════════════
# AIProfileListModel — 补充
# ═══════════════════════════════════════════════════════════════════════════


class TestAIProfileListModelExtra:
    def test_status_display_verified(self):
        p = AIProfile(name="a", last_test_status="ok")
        assert AIProfileListModel._status_display(p) == "Verified"

    def test_status_display_failed(self):
        p = AIProfile(name="a", last_test_status="failed")
        assert AIProfileListModel._status_display(p) == "Failed"

    def test_status_display_untested(self):
        p = AIProfile(name="a")
        assert AIProfileListModel._status_display(p) == "Untested"

    def test_setdata_wrong_column(self, seeded_fake_prefs):
        model = AIProfileListModel(seeded_fake_prefs)
        result = model.setData(model.index(0, 0), True, Qt.ItemDataRole.CheckStateRole)
        assert result is False

    def test_display_role_provider_and_model(self, seeded_fake_prefs):
        seeded_fake_prefs._data["ai_profiles"][0]["provider"] = "openai"
        seeded_fake_prefs._data["ai_profiles"][0]["model"] = "gpt-4o"
        model = AIProfileListModel(seeded_fake_prefs)
        assert model.data(model.index(0, 1), Qt.ItemDataRole.DisplayRole) == "openai"
        assert model.data(model.index(0, 2), Qt.ItemDataRole.DisplayRole) == "gpt-4o"


# ═══════════════════════════════════════════════════════════════════════════
# AIProfileEditDialog — 补充
# ═══════════════════════════════════════════════════════════════════════════


class TestAIProfileEditDialogExtra:
    def test_toggle_api_key_reveals(self, theme_mgr):
        dlg = AIProfileEditDialog(None, [])
        dlg._on_toggle_api_key(True)
        assert dlg._api_key_edit.echoMode() == dlg._api_key_edit.EchoMode.Normal

    def test_toggle_api_key_hides(self, theme_mgr):
        dlg = AIProfileEditDialog(None, [])
        dlg._on_toggle_api_key(True)
        dlg._on_toggle_api_key(False)
        assert dlg._api_key_edit.echoMode() == dlg._api_key_edit.EchoMode.Password

    def test_provider_changed_marks_dirty(self, theme_mgr):
        dlg = AIProfileEditDialog(None, [])
        dlg._dirty = False
        dlg._on_provider_changed("openai")
        assert dlg._dirty is True

    def test_validate_and_accept_on_valid(self, theme_mgr, monkeypatch):
        from unittest.mock import MagicMock

        dlg = AIProfileEditDialog(None, [])
        dlg._name_edit.setText("valid_name")
        mock_accept = MagicMock()
        monkeypatch.setattr(dlg, "accept", mock_accept)
        dlg._validate_and_accept()
        mock_accept.assert_called_once()

    def test_validate_and_accept_on_invalid(self, theme_mgr, monkeypatch):
        from unittest.mock import MagicMock

        dlg = AIProfileEditDialog(None, [])
        dlg._name_edit.setText("")
        mock_accept = MagicMock()
        monkeypatch.setattr(dlg, "accept", mock_accept)
        dlg._validate_and_accept()
        mock_accept.assert_not_called()

    def test_on_cancel_dirty_confirm_discard(self, theme_mgr, monkeypatch):
        from unittest.mock import MagicMock

        from PySide6.QtWidgets import QMessageBox

        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *a, **kw: QMessageBox.StandardButton.Discard,
        )
        # 编辑模式（_is_new=False）——dirty check 会触发警告
        dlg = AIProfileEditDialog(None, [], AIProfile(name="test"))
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
        # 编辑模式（_is_new=False）——dirty check 会触发警告，用户选择取消
        dlg = AIProfileEditDialog(None, [], AIProfile(name="test"))
        dlg._dirty = True
        mock_reject = MagicMock()
        monkeypatch.setattr(dlg, "reject", mock_reject)
        dlg._on_cancel()
        mock_reject.assert_not_called()

    def test_on_cancel_clean_no_prompt(self, theme_mgr, monkeypatch):
        from unittest.mock import MagicMock

        dlg = AIProfileEditDialog(None, [])
        dlg._dirty = False
        mock_reject = MagicMock()
        monkeypatch.setattr(dlg, "reject", mock_reject)
        dlg._on_cancel()
        mock_reject.assert_called_once()

    def test_on_models_fetched_keeps_current(self, theme_mgr):
        dlg = AIProfileEditDialog(None, [])
        dlg._model_combo.setCurrentText("gpt-4o")
        dlg.on_models_fetched(["gpt-4o", "gpt-4o-mini", "gpt-3.5"])
        assert dlg._model_combo.currentText() == "gpt-4o"


# ═══════════════════════════════════════════════════════════════════════════
# _AIProfilePage — 补充
# ═══════════════════════════════════════════════════════════════════════════


class TestAIProfilePageExtra:
    def test_on_add_accepted_saves(self, theme_mgr, fake_prefs, monkeypatch):
        from unittest.mock import MagicMock

        from PySide6.QtWidgets import QDialog

        page = _AIProfilePage(fake_prefs)
        page.refresh()

        mock_dlg = MagicMock()
        mock_dlg.get_profile.return_value = AIProfile(name="new", provider="openai")
        mock_dlg.exec.return_value = QDialog.DialogCode.Accepted
        monkeypatch.setattr(
            "astrocrawl.gui._ai_profile_page.AIProfileEditDialog",
            lambda *a, **kw: mock_dlg,
        )
        page._on_add()
        assert fake_prefs.get_ai_profile("new") is not None

    def test_on_edit_accepted_saves(self, theme_mgr, fake_prefs, monkeypatch):
        from unittest.mock import MagicMock

        from PySide6.QtWidgets import QDialog

        fake_prefs.save_ai_profile(AIProfile(name="extra", provider="google"))
        page = _AIProfilePage(fake_prefs)
        page.refresh()

        mock_dlg = MagicMock()
        mock_dlg.get_profile.return_value = AIProfile(name="extra", provider="google", model="gemini")
        mock_dlg.exec.return_value = QDialog.DialogCode.Accepted
        monkeypatch.setattr(
            "astrocrawl.gui._ai_profile_page.AIProfileEditDialog",
            lambda *a, **kw: mock_dlg,
        )
        page._on_edit(0)
        updated = fake_prefs.get_ai_profile("extra")
        assert updated is not None
        assert updated.model == "gemini"

    def test_on_checkbox_toggled_sets_pending(self, theme_mgr, seeded_fake_prefs):
        page = _AIProfilePage(seeded_fake_prefs)
        page.refresh()
        page._proxy = None
        page._on_checkbox_toggled(0, False)
        assert page.has_pending is True

    def test_on_set_default_from_button(self, theme_mgr, seeded_fake_prefs):
        seeded_fake_prefs.save_ai_profile(AIProfile(name="secondary", provider="openai"))
        page = _AIProfilePage(seeded_fake_prefs)
        page.refresh()
        page._table.selectRow(1)
        page._on_set_default()
        assert seeded_fake_prefs._data["ai_active_profile"] == "secondary"

    def test_on_set_default_no_selection(self, theme_mgr, seeded_fake_prefs):
        page = _AIProfilePage(seeded_fake_prefs)
        original_active = seeded_fake_prefs._data.get("ai_active_profile", "")
        page._on_set_default()
        assert seeded_fake_prefs._data.get("ai_active_profile", "") == original_active

    def test_on_test_connection_no_selection(self, theme_mgr, fake_prefs):
        page = _AIProfilePage(fake_prefs)
        page._on_test_connection()  # no selection — should return silently

    def test_fetch_models_reentrant_guard(self, theme_mgr, fake_prefs):
        page = _AIProfilePage(fake_prefs)
        page._fetching = True
        page._fetch_models("openai", "", "", lambda m: None)  # should return silently

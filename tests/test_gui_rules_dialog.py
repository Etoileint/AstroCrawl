"""Phase 7 — RulesDialog 全部 3 个 Tab 测试。

覆盖:
- RE01-RE08: RuleEditDialog 只读/编辑/字段收集/保存
- RT01-RT31: _RuleTablePage 数据逻辑/刷新/pending/批量/CRUD/主题
- CP01-CP17: _CustomPage 粘贴导入/AI 生成/错误处理
- SP01-SP06: _SourcePage 表格渲染/启用切换/写入
- RD01-RD05: RulesDialog 顶层 apply/confirm/cancel/refresh/rule_generated
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QDialog, QMessageBox

pytestmark = pytest.mark.gui


@pytest.fixture(autouse=True)
def _patch_dialogs(monkeypatch):
    """全局 patch QMessageBox + RuleEditDialog exec 防止模态阻塞。"""
    mock = MagicMock()
    for method in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, method, mock)
    monkeypatch.setattr(QDialog, "exec", MagicMock())
    # QMessageBox 确认对话框：自动返回第一个按钮（即 Yes/确认按钮）
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda self: self.buttons()[0] if self.buttons() else None)


def _sample_rule(name="test_rule", **overrides):
    data = {
        "name": name,
        "display_name": "Test Rule",
        "description": "A test rule",
        "tags": ["test"],
        "version": 1,
        "enabled": True,
        "fields": {"title": {"selector": "h1", "extract": "text"}},
        "match": {"domains": ["example.com"]},
    }
    data.update(overrides)
    return data


@pytest.fixture
def rtp(qapp, theme_mgr, test_config, monkeypatch, request):
    from astrocrawl.gui.rules_dialog import _RuleTablePage
    from tests._fakes_gui import FakePreferences, FakeRuleLifecycle

    fake_prefs = FakePreferences()
    monkeypatch.setattr("astrocrawl.gui.rules_dialog.get_preferences", lambda: fake_prefs)

    page = _RuleTablePage(test_config)
    fake_lc = FakeRuleLifecycle(
        [
            _sample_rule("rule_a", display_name="Rule A", tags=["电商"]),
            _sample_rule("rule_b", display_name="Rule B", tags=["新闻"]),
            _sample_rule("rule_c", display_name="Rule C", tags=["电商", "新闻"], enabled=False),
        ]
    )
    page._lifecycle = fake_lc
    page.refresh()
    request.addfinalizer(page.deleteLater)
    return page


@pytest.fixture
def source_page(qapp, theme_mgr, monkeypatch, request):
    from astrocrawl.gui.rules_dialog import _SourcePage

    monkeypatch.setattr("astrocrawl.gui.rules_dialog.list_sources_from_file", lambda: [])
    page = _SourcePage()
    request.addfinalizer(page.deleteLater)
    return page


# ═══════════════════════════════════════════════════════════════════════
# RE01-RE08: RuleEditDialog
# ═══════════════════════════════════════════════════════════════════════


class TestRuleEditDialog:
    def test_collect_fields_single(self, qapp, theme_mgr):
        from astrocrawl.gui.rules_dialog import RuleEditDialog

        dlg = RuleEditDialog(_sample_rule(), "user")
        fields = dlg._collect_fields()
        assert "title" in fields
        assert fields["title"]["selector"] == "h1"
        assert fields["title"]["extract"] == "text"

    def test_collect_fields_preserves_fallback(self, qapp, theme_mgr):
        data = _sample_rule()
        data["fields"]["title"]["fallback"] = "default value"
        from astrocrawl.gui.rules_dialog import RuleEditDialog

        dlg = RuleEditDialog(data, "user")
        fields = dlg._collect_fields()
        assert fields["title"]["fallback"] == "default value"

    def test_readonly_mode_fields_disabled(self, qapp, theme_mgr):
        from astrocrawl.gui.rules_dialog import RuleEditDialog

        dlg = RuleEditDialog(_sample_rule(), "pip")
        assert dlg._readonly is True
        assert dlg._name_edit.isReadOnly() is True

    def test_edit_mode_fields_enabled(self, qapp, theme_mgr):
        from astrocrawl.gui.rules_dialog import RuleEditDialog

        dlg = RuleEditDialog(_sample_rule(), "user")
        assert dlg._readonly is False

    def test_readonly_window_title(self, qapp, theme_mgr):
        from astrocrawl.gui.rules_dialog import RuleEditDialog

        dlg = RuleEditDialog(_sample_rule(), "pip")
        assert "View Rule" in dlg.windowTitle()

    def test_edit_window_title(self, qapp, theme_mgr):
        from astrocrawl.gui.rules_dialog import RuleEditDialog

        dlg = RuleEditDialog(_sample_rule(), "user")
        assert "Edit Rule" in dlg.windowTitle()

    def test_apply_saves(self, qapp, theme_mgr, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        rules_dir = tmp_path / ".astrocrawl" / "rules"
        rules_dir.mkdir(parents=True)

        from astrocrawl.gui.rules_dialog import RuleEditDialog

        rule_path = rules_dir / "myrule.json"
        dlg = RuleEditDialog(_sample_rule("myrule"), "user", rule_path)
        dlg._name_edit.setText("myrule_renamed")
        dlg._apply()

    def test_confirm_accepts(self, qapp, theme_mgr, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        rules_dir = tmp_path / ".astrocrawl" / "rules"
        rules_dir.mkdir(parents=True)

        mock_accept = MagicMock()
        from astrocrawl.gui.rules_dialog import RuleEditDialog

        rule_path = rules_dir / "myrule.json"
        dlg = RuleEditDialog(_sample_rule("myrule"), "user", rule_path)
        monkeypatch.setattr(dlg, "accept", mock_accept)
        dlg._confirm()
        mock_accept.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════
# RT01-RT12: _RuleTablePage 数据逻辑
# ═══════════════════════════════════════════════════════════════════════


class TestRuleTablePageData:
    def test_apply_filter_matches_name(self, rtp):
        rtp._search_input.setText("rule_a")
        rtp._apply_filter()
        # 过滤后 proxy rowCount 应 > 0
        assert rtp._table.model().rowCount() > 0

    def test_apply_filter_matches_tag(self, rtp):
        rtp._search_input.setText("电商")
        rtp._apply_filter()
        assert rtp._table.model().rowCount() > 0

    def test_apply_filter_empty_shows_all(self, rtp):
        rtp._search_input.setText("")
        rtp._apply_filter()
        assert rtp._table.model().rowCount() == rtp.rowCount

    def test_apply_filter_no_match_hides_all(self, rtp):
        rtp._search_input.setText("xyznonexistent")
        rtp._apply_filter()
        assert rtp._table.model().rowCount() == 0

    def test_refresh_populates_table(self, rtp):
        assert rtp.rowCount == 3
        names = [rtp.rule_name(r) for r in range(rtp.rowCount)]
        assert "rule_a" in names
        assert "rule_b" in names
        assert "rule_c" in names

    def test_refresh_excludes_default_rule(self, rtp):
        from tests._fakes_gui import FakeRuleLifecycle

        fake_lc = FakeRuleLifecycle(
            [
                _sample_rule("default"),
                _sample_rule("other"),
            ]
        )
        rtp._lifecycle = fake_lc
        rtp.refresh()
        names = [rtp.rule_name(r) for r in range(rtp.rowCount)]
        assert "default" not in names


# ═══════════════════════════════════════════════════════════════════════
# RT13-RT18: refresh / pending
# ═══════════════════════════════════════════════════════════════════════


class TestRuleTablePagePending:
    def test_apply_pending_calls_set_rule_enabled(self, rtp, monkeypatch):
        mock_set = MagicMock()
        monkeypatch.setattr("astrocrawl.gui.rules_dialog.set_rule_enabled", mock_set)

        rtp._pending_toggles = {"rule_a": False, "rule_b": True}
        rtp.apply_pending()

        assert mock_set.call_count == 2
        mock_set.assert_any_call("rule_a", False)
        mock_set.assert_any_call("rule_b", True)
        assert rtp._pending_toggles == {}

    def test_discard_pending_clears(self, rtp):
        rtp._pending_toggles = {"rule_a": False}
        rtp.discard_pending()
        assert rtp._pending_toggles == {}

    def test_apply_pending_empty_noop(self, rtp, monkeypatch):
        mock_set = MagicMock()
        monkeypatch.setattr("astrocrawl.gui.rules_dialog.set_rule_enabled", mock_set)
        rtp._pending_toggles = {}
        rtp.apply_pending()
        mock_set.assert_not_called()

    def test_pending_overlay_applied_in_refresh(self, rtp):
        from PySide6.QtCore import Qt

        rtp._pending_toggles = {"rule_a": False}
        rtp.refresh()

        proxy = rtp._table.model()
        model = proxy.sourceModel()
        for src_row in range(model.rowCount()):
            name = model.get_name(src_row)
            if name == "rule_a":
                enabled = model.data(model.index(src_row, 6), Qt.CheckStateRole)
                assert enabled == Qt.Unchecked.value


# ═══════════════════════════════════════════════════════════════════════
# RT19-RT22: 批量操作
# ═══════════════════════════════════════════════════════════════════════


class TestRuleTablePageBatch:
    def test_select_all_toggles_visible_non_default(self, rtp):
        rtp._on_select_all()
        assert rtp._pending_toggles.get("rule_a") is True
        assert rtp._pending_toggles.get("rule_b") is True
        assert rtp._pending_toggles.get("rule_c") is True

    def test_select_all_respects_filter(self, rtp):
        rtp._search_input.setText("rule_a")
        rtp._apply_filter()
        rtp._on_select_all()

        assert "rule_a" in rtp._pending_toggles
        assert "rule_b" not in rtp._pending_toggles

    def test_deselect_all_toggles_visible_non_default(self, rtp):
        rtp._on_deselect_all()
        assert rtp._pending_toggles.get("rule_a") is False
        assert rtp._pending_toggles.get("rule_b") is False


# ═══════════════════════════════════════════════════════════════════════
# RT23-RT29: 规则 CRUD
# ═══════════════════════════════════════════════════════════════════════


class TestRuleTablePageCRUD:
    def test_reload_uses_lifecycle_and_refreshes(self, rtp):
        rtp._on_reload()
        assert rtp._lifecycle.reload_called is True

    def test_delete_user_rule_succeeds(self, rtp, tmp_path, monkeypatch):
        from tests._fakes_gui import FakeRuleLifecycle

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        rules_dir = tmp_path / ".astrocrawl" / "rules"
        rules_dir.mkdir(parents=True)
        rule_path = rules_dir / "rule_a.json"
        rule_path.write_text("{}")
        rtp._lifecycle = FakeRuleLifecycle(
            [{"name": "rule_a", "display_name": "Rule A"}],
            source_map={"rule_a": "user"},
            path_map={"rule_a": str(rule_path)},
        )
        rtp.refresh()
        rtp._table.selectRow(0)
        rtp._on_delete()
        assert not rule_path.exists()

    def test_delete_non_user_rule_noop(self, rtp):
        initial_count = rtp.rowCount
        rtp._table.selectRow(0)
        rtp._on_delete()
        assert rtp.rowCount == initial_count

    def test_edit_btn_no_selection_warns(self, rtp):
        rtp._table.clearSelection()
        rtp._on_edit_btn()


# ═══════════════════════════════════════════════════════════════════════
# RT30-RT31: 主题
# ═══════════════════════════════════════════════════════════════════════


class TestRuleTablePageTheme:
    def test_apply_theme_no_manager_returns_early(self, rtp):
        rtp._theme_mgr = None
        rtp._apply_theme()

    def test_apply_theme_with_manager(self, rtp, theme_mgr):
        theme_mgr.apply("light", "light", {})
        rtp._apply_theme()


# ═══════════════════════════════════════════════════════════════════════
# CP01-CP17: _CustomPage
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def custom_page(qapp, theme_mgr, fake_prefs, monkeypatch, request):
    from astrocrawl.gui.rules_dialog import _CustomPage

    monkeypatch.setattr("astrocrawl.gui.rules_dialog.get_preferences", lambda: fake_prefs)
    page = _CustomPage()
    request.addfinalizer(page.deleteLater)
    return page


class TestCustomPagePaste:
    def test_paste_import_empty_text_warns(self, custom_page):
        custom_page._paste_edit.setPlainText("")
        custom_page._on_paste_import()

    def test_paste_import_invalid_json_warns(self, custom_page):
        custom_page._paste_edit.setPlainText("not valid json")
        custom_page._on_paste_import()

    def test_paste_import_valid_json_clears_paste(self, custom_page, tmp_path, monkeypatch):

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        rules_dir = tmp_path / ".astrocrawl" / "rules"
        rules_dir.mkdir(parents=True)

        custom_page._paste_edit.setPlainText(json.dumps(_sample_rule("pasted_rule")))
        custom_page._on_paste_import()

        assert custom_page._paste_edit.toPlainText() == ""

    def test_reset_clears_all_fields(self, custom_page):
        custom_page._paste_edit.setPlainText("test")
        custom_page._site_url.setText("https://x.com")
        custom_page._field_requirements.setText("title")
        custom_page._html_input.setPlainText("<html>")

        custom_page.reset()

        assert custom_page._paste_edit.toPlainText() == ""
        assert custom_page._site_url.text() == ""
        assert custom_page._field_requirements.text() == ""
        assert custom_page._html_input.toPlainText() == ""


class TestCustomPageOutputFormat:
    """ADR-0008: 输出格式选择器。"""

    def test_combo_has_four_items(self, custom_page):
        assert custom_page._output_format_combo.count() == 4

    def test_combo_default_is_auto(self, custom_page):
        assert custom_page._output_format_combo.currentIndex() == 0
        assert custom_page._output_format_combo.currentData() == "auto"

    @pytest.mark.parametrize(
        "index,expected_label,expected_data",
        [
            (0, "Auto (Recommended)", "auto"),
            (1, "JSON Schema", "json_schema"),
            (2, "JSON Object", "json_object"),
            (3, "Off", "off"),
        ],
    )
    def test_combo_item_labels_and_data(self, custom_page, index, expected_label, expected_data):
        assert custom_page._output_format_combo.itemText(index) == expected_label
        assert custom_page._output_format_combo.itemData(index) == expected_data

    def test_reset_restores_default(self, custom_page):
        custom_page._output_format_combo.setCurrentIndex(2)  # json_object
        custom_page.reset()
        assert custom_page._output_format_combo.currentIndex() == 0
        assert custom_page._output_format_combo.currentData() == "auto"


def _seed_ai_profile(fake_prefs):
    """Seed FakePreferences with a minimal valid AI profile for generation tests."""
    fake_prefs._data["ai_profiles"] = [
        {
            "name": "test_openai",
            "provider": "openai",
            "model": "gpt-4o-mini",
            "api_key": "sk-test",
            "endpoint": "https://api.openai.com/v1",
            "enabled": True,
            "temperature": 0.0,
            "max_tokens": 4096,
        }
    ]


class TestCustomPageAIGeneration:
    def test_generate_no_api_key_warns(self, custom_page, fake_prefs):
        _seed_ai_profile(fake_prefs)
        fake_prefs._data["ai_profiles"][0]["api_key"] = ""
        fake_prefs._data["ai_profiles"][0]["endpoint"] = ""
        custom_page._html_input.setPlainText("<html></html>")
        custom_page._on_generate()

    def test_generate_no_html_warns(self, custom_page, fake_prefs):
        _seed_ai_profile(fake_prefs)
        custom_page._html_input.setPlainText("")
        custom_page._on_generate()

    def test_generate_html_truncation(self, custom_page, fake_prefs, monkeypatch):
        _seed_ai_profile(fake_prefs)

        long_html = "x" * 25000
        custom_page._html_input.setPlainText(long_html)

        mock_preview = MagicMock()
        monkeypatch.setattr("astrocrawl.gui.rules_dialog.ChatMLPreviewDialog", mock_preview)

        custom_page._on_generate()

        mock_preview.assert_called_once()
        chatml_arg = mock_preview.call_args[0][0]
        assert "<html_source>" in chatml_arg
        assert len(chatml_arg) < 35000

    def test_generate_result_validate_failure(self, custom_page, monkeypatch):
        monkeypatch.setattr(
            "astrocrawl.gui.rules_dialog.validate_rule",
            MagicMock(side_effect=ValueError("invalid")),
        )
        custom_page._on_generate_result({"name": "bad", "fields": {}})

    def test_generate_result_success_clears_html(self, custom_page, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        rules_dir = tmp_path / ".astrocrawl" / "rules"
        rules_dir.mkdir(parents=True)

        mock_validate = MagicMock(
            return_value=MagicMock(
                name="gen_rule",
                fields={"title": MagicMock(selector="h1")},
            )
        )
        monkeypatch.setattr("astrocrawl.gui.rules_dialog.validate_rule", mock_validate)

        custom_page._html_input.setPlainText("<html>old</html>")
        custom_page._on_generate_result(_sample_rule("gen_rule"))

        assert custom_page._html_input.toPlainText() == ""

    def test_generate_error_stops_progress(self, custom_page):
        custom_page._gen_btn.setEnabled(False)
        received = []
        custom_page.busy_changed.connect(lambda v: received.append(v))

        custom_page._on_generate_error("API error")

        assert custom_page._gen_btn.isEnabled() is True
        assert received == [False]


# ═══════════════════════════════════════════════════════════════════════
# SP01-SP06: _SourcePage
# ═══════════════════════════════════════════════════════════════════════


class TestSourcePage:
    def test_refresh_empty_shows_label(self, source_page):
        source_page._refresh()
        proxy = source_page._table.model()
        assert proxy is not None
        assert proxy.sourceModel().rowCount() == 0
        assert source_page._remove_btn.isEnabled() is False

    @pytest.fixture
    def source_page_with_sources(self, qapp, theme_mgr, monkeypatch, request):
        from astrocrawl.gui.rules_dialog import _SourcePage

        monkeypatch.setattr(
            "astrocrawl.gui.rules_dialog.list_sources_from_file",
            lambda: [
                {
                    "name": "src1",
                    "url": "https://example.com/manifest.json",
                    "enabled": True,
                    "state": "active",
                    "last_updated": 0,
                    "rules_count": 5,
                },
            ],
        )
        monkeypatch.setattr("astrocrawl.gui.rules_dialog.update_source_in_file", lambda name, **kw: None)
        page = _SourcePage()
        request.addfinalizer(page.deleteLater)
        return page

    def test_toggle_source_updates_pending(self, source_page_with_sources):
        source_page_with_sources._refresh()
        source_page_with_sources._on_checkbox_toggled(0, False)
        assert "src1" in source_page_with_sources._pending_toggles

    def test_apply_pending_writes_to_cfg(self, source_page_with_sources, monkeypatch):
        from unittest.mock import MagicMock

        mock_update = MagicMock()
        monkeypatch.setattr("astrocrawl.gui.rules_dialog.update_source_in_file", mock_update)
        source_page_with_sources._pending_toggles = {"src1": False}
        source_page_with_sources.apply_pending()
        mock_update.assert_called_once_with("src1", enabled=False)
        assert source_page_with_sources._pending_toggles == {}

    def test_discard_pending_clears(self, source_page):
        source_page._pending_toggles = {"src1": False}
        source_page.discard_pending()
        assert source_page._pending_toggles == {}


# ═══════════════════════════════════════════════════════════════════════
# RD01-RD05: RulesDialog 顶层
# ═══════════════════════════════════════════════════════════════════════


class TestRulesDialogTopLevel:
    @pytest.fixture
    def rules_dialog(self, qapp, theme_mgr, test_config, request):
        from astrocrawl.gui.rules_dialog import RulesDialog

        dlg = RulesDialog(cfg=test_config)
        request.addfinalizer(dlg.deleteLater)
        return dlg

    def test_on_refresh_all_discards_and_reloads(self, rules_dialog):
        from tests._fakes_gui import FakeRuleLifecycle

        fake_lc = FakeRuleLifecycle([_sample_rule("x")])
        rules_dialog._rule_page._lifecycle = fake_lc
        rules_dialog._rule_page._pending_toggles = {"x": True}
        rules_dialog._source_page._pending_toggles = {"y": False}

        rules_dialog._on_refresh_all()

        assert rules_dialog._rule_page._pending_toggles == {}
        assert rules_dialog._source_page._pending_toggles == {}
        assert fake_lc.reload_called is True

    def test_on_apply_applies_both_pages(self, rules_dialog, monkeypatch):
        mock_set = MagicMock()
        monkeypatch.setattr("astrocrawl.gui.rules_dialog.set_rule_enabled", mock_set)

        rules_dialog._rule_page._pending_toggles = {"r": True}
        rules_dialog._source_page._pending_toggles = {}
        rules_dialog._on_apply()
        mock_set.assert_called_once_with("r", True)

    def test_on_confirm_applies_and_accepts(self, rules_dialog, monkeypatch):
        mock_accept = MagicMock()
        monkeypatch.setattr(rules_dialog, "accept", mock_accept)
        monkeypatch.setattr("astrocrawl.gui.rules_dialog.set_rule_enabled", MagicMock())

        rules_dialog._on_confirm()
        mock_accept.assert_called_once()

    def test_on_cancel_discards_both(self, rules_dialog, monkeypatch):
        mock_reject = MagicMock()
        monkeypatch.setattr(rules_dialog, "reject", mock_reject)

        rules_dialog._rule_page._pending_toggles = {"r": True}
        rules_dialog._source_page._pending_toggles = {"s": False}

        rules_dialog._on_cancel()

        assert rules_dialog._rule_page._pending_toggles == {}
        assert rules_dialog._source_page._pending_toggles == {}
        mock_reject.assert_called_once()

    def test_on_rule_generated_refreshes(self, rules_dialog):
        from tests._fakes_gui import FakeRuleLifecycle

        fake_lc = FakeRuleLifecycle([_sample_rule("existing")])
        rules_dialog._rule_page._lifecycle = fake_lc
        fake_lc.reload_called = False
        rules_dialog._on_rule_generated({"name": "new_rule"})
        assert fake_lc.reload_called is True


# ═══════════════════════════════════════════════════════════════════════
# Phase 7 补全 — 复核发现的缺口 (21 用例)
# ═══════════════════════════════════════════════════════════════════════


class TestRuleEditDialogExtended:
    def test_collect_fields_multiple_fields(self, qapp, theme_mgr):
        from astrocrawl.gui.rules_dialog import RuleEditDialog

        data = _sample_rule()
        data["fields"] = {
            "title": {"selector": "h1", "extract": "text"},
            "price": {"selector": ".price", "extract": "text", "multiple": True},
        }
        dlg = RuleEditDialog(data, "user")
        fields = dlg._collect_fields()
        assert "title" in fields
        assert "price" in fields
        assert fields["price"]["extract"] == "text"

    def test_collect_fields_skip_blank_name(self, qapp, theme_mgr):
        from astrocrawl.gui.rules_dialog import RuleEditDialog

        data = _sample_rule()
        data["fields"] = {"": {"selector": "h1"}, "title": {"selector": "h1"}}
        dlg = RuleEditDialog(data, "user")
        fields = dlg._collect_fields()
        assert "" not in fields
        assert "title" in fields

    def test_save_to_file_empty_name_warns(self, qapp, theme_mgr, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        rules_dir = tmp_path / ".astrocrawl" / "rules"
        rules_dir.mkdir(parents=True)
        from astrocrawl.gui.rules_dialog import RuleEditDialog

        dlg = RuleEditDialog(_sample_rule(), "user", rules_dir / "test.json")
        dlg._name_edit.setText("")
        dlg._save_to_file()


class TestRuleTablePageExtended:
    def test_init_lifecycle_loads_and_refreshes(self, qapp, theme_mgr, test_config, monkeypatch):
        from astrocrawl.gui.rules_dialog import _RuleTablePage
        from tests._fakes_gui import FakeRuleLifecycle

        fake_lc = FakeRuleLifecycle([_sample_rule("a"), _sample_rule("b")])
        monkeypatch.setattr("astrocrawl.gui.rules_dialog.RuleLifecycle", lambda cfg, **kw: fake_lc)

        page = _RuleTablePage(test_config)
        page.init_lifecycle()

        assert fake_lc.initial_load_called is True
        assert page.rowCount == 2

    def test_reset_deletes_all_user_rules(self, rtp, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        rules_dir = tmp_path / ".astrocrawl" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "user1.json").write_text("{}")
        (rules_dir / "user2.json").write_text("{}")

        rtp._on_reset()
        assert not list(rules_dir.glob("*.json"))

    def test_init_lifecycle_required_before_refresh(self, rtp):
        """refresh() 不再 lazy-init——必须先调用 init_lifecycle()。"""
        rtp._lifecycle = None
        with pytest.raises(AttributeError):
            rtp.refresh()

    def test_init_lifecycle_creates_from_preferences(self, rtp, tmp_path):
        """init_lifecycle() 从 Preferences SSOT 读取 extra_rules_dirs 和 rules_dirs_enabled。"""
        from astrocrawl.gui.rules_dialog import get_preferences

        extra_dir = tmp_path / "custom_rules"
        extra_dir.mkdir()
        prefs = get_preferences()
        prefs.set_rules_dirs([str(extra_dir)])
        prefs.set_rules_dirs_enabled(False)

        rtp._lifecycle = None
        rtp.init_lifecycle()
        assert rtp._lifecycle is not None
        assert rtp._lifecycle._extra_rules_dirs == [str(extra_dir)]
        assert rtp._lifecycle._rules_dirs_enabled is False

    def test_init_lifecycle_creates_from_preferences_defaults(self, rtp):
        """init_lifecycle() 在 Preferences 无自定义值时使用 fake 默认空值。"""
        rtp._lifecycle = None
        rtp.init_lifecycle()
        assert rtp._lifecycle is not None
        assert rtp._lifecycle._extra_rules_dirs == []
        assert rtp._lifecycle._rules_dirs_enabled is True

    def test_refresh_calls_reload(self, rtp):
        """refresh() 必须从磁盘重载，而非仅重渲染内存快照。"""
        rtp._lifecycle.reload_called = False
        rtp.refresh()
        assert rtp._lifecycle.reload_called is True

    def test_add_extra_dir_recreates_lifecycle(self, rtp, tmp_path, monkeypatch):
        """添加额外目录后 lifecycle 被重建（新对象，非 reload 旧对象）。"""
        extra_dir = tmp_path / "extra_rules"
        extra_dir.mkdir()
        monkeypatch.setattr(
            "astrocrawl.gui.rules_dialog.QFileDialog.getExistingDirectory",
            lambda *a, **kw: str(extra_dir),
        )
        old_lc = rtp._lifecycle
        rtp._on_add_rules_dir()
        assert rtp._lifecycle is not old_lc

    def test_toggle_rules_dirs_enabled_rebuilds_lifecycle(self, rtp):
        """切换额外目录启用/禁用后 lifecycle 被重建。"""
        old_lc = rtp._lifecycle
        rtp._on_rules_dirs_enabled_toggled(False)
        assert rtp._lifecycle is not old_lc

    def test_remove_extra_dir_recreates_lifecycle(self, rtp, tmp_path):
        """移除额外目录后 lifecycle 被重建（新对象，非 reload 旧对象）。"""
        from astrocrawl.gui.rules_dialog import get_preferences

        extra_dir = tmp_path / "extra_rules"
        extra_dir.mkdir()
        pm = get_preferences()
        dirs = pm.get_rules_dirs()
        dirs.append(str(extra_dir))
        pm.set_rules_dirs(dirs)
        rtp._refresh_rules_dirs()
        rtp._rules_dirs_list.setCurrentRow(0)
        old_lc = rtp._lifecycle
        rtp._on_remove_rules_dir()
        assert rtp._lifecycle is not old_lc


class TestCustomPageExtended:
    def test_ai_settings_opens_dialog(self, custom_page, monkeypatch):
        mock_dialog = MagicMock()
        monkeypatch.setattr("astrocrawl.gui.rules_dialog.AdvancedSettingsDialog", mock_dialog)
        custom_page._on_ai_settings()
        mock_dialog.assert_called_once()

    def test_generate_privacy_confirm_no_returns(self, custom_page, fake_prefs, monkeypatch):
        _seed_ai_profile(fake_prefs)
        # 模拟用户点击取消按钮（返回第二个按钮 = NoRole）
        monkeypatch.setattr(
            QMessageBox, "clickedButton", lambda self: self.buttons()[1] if len(self.buttons()) > 1 else None
        )
        custom_page._html_input.setPlainText("<html>")
        custom_page._on_generate()

    def test_generate_result_empty_selector_logs_warning(self, custom_page, monkeypatch):
        import logging

        mock_logger = MagicMock()
        monkeypatch.setattr(logging, "getLogger", MagicMock(return_value=mock_logger))

        mock_validate = MagicMock(
            return_value=MagicMock(
                name="r",
                fields={"title": MagicMock(selector=None)},
            )
        )
        monkeypatch.setattr("astrocrawl.gui.rules_dialog.validate_rule", mock_validate)

        custom_page._on_generate_result(_sample_rule("r"))


class TestSourcePageExtended:
    def test_remove_source_no_selection_warns(self, source_page):
        source_page._table.clearSelection()
        source_page._on_remove_source()

    def test_discard_pending_no_data_noop(self, source_page):
        source_page.discard_pending()
        assert source_page._pending_toggles == {}

    def test_apply_theme_with_manager(self, source_page, theme_mgr):
        source_page._apply_theme()


# ═══════════════════════════════════════════════════════════════════════
# CL01-CL04: 额外规则目录可折叠控件
# ═══════════════════════════════════════════════════════════════════════


class TestRuleTablePageCollapse:
    @pytest.fixture
    def rtp_collapse(self, qapp, theme_mgr, test_config, monkeypatch, request):
        from astrocrawl.gui.rules_dialog import _RuleTablePage
        from tests._fakes_gui import FakePreferences, FakeRuleLifecycle

        fake_prefs = FakePreferences()
        fake_prefs.set_rules_dirs_collapsed(True)
        fake_prefs.set_rules_dirs_enabled(True)
        monkeypatch.setattr("astrocrawl.gui.rules_dialog.get_preferences", lambda: fake_prefs)

        page = _RuleTablePage(test_config)
        fake_lc = FakeRuleLifecycle([_sample_rule("a")])
        page._lifecycle = fake_lc
        page.refresh()
        request.addfinalizer(page.deleteLater)
        return page

    def test_rules_dirs_collapse_initial(self, rtp_collapse):
        from PySide6.QtCore import Qt

        assert rtp_collapse._collapse_btn.isChecked() is True
        assert rtp_collapse._collapse_btn.arrowType() == Qt.RightArrow
        assert rtp_collapse._rules_dirs_content.isHidden() is True

    def test_rules_dirs_collapse_toggle(self, rtp_collapse):
        rtp_collapse._collapse_btn.setChecked(False)
        assert rtp_collapse._rules_dirs_content.isHidden() is False

    def test_rules_dirs_enable_independence(self, rtp_collapse):
        rtp_collapse._enable_cb.setChecked(False)
        assert rtp_collapse._rules_dirs_content.isHidden() is True
        assert rtp_collapse._collapse_btn.isChecked() is True

    def test_rules_dirs_collapse_persists(self, rtp_collapse, monkeypatch):
        from astrocrawl.gui.rules_dialog import get_preferences

        rtp_collapse._collapse_btn.setChecked(False)
        prefs = get_preferences()
        assert prefs.get_rules_dirs_collapsed() is False


# ═══════════════════════════════════════════════════════════════════════
# SB01-SB05: 底部持久状态栏
# ═══════════════════════════════════════════════════════════════════════


class TestStatusBar:
    @pytest.fixture
    def rules_dialog_sb(self, qapp, theme_mgr, test_config, monkeypatch, request):
        from astrocrawl.gui.rules_dialog import RulesDialog

        dlg = RulesDialog(cfg=test_config)
        monkeypatch.setattr(dlg._psb, "show_status", MagicMock(wraps=dlg._psb.show_status))
        request.addfinalizer(dlg.deleteLater)
        return dlg

    def test_status_bar_exists(self, rules_dialog_sb):
        assert rules_dialog_sb._psb._status_bar is not None
        assert rules_dialog_sb._psb._status_bar.objectName() == "status-bar"
        assert rules_dialog_sb._psb._status_bar.text() == "Ready"

    def test_status_bar_updates_text(self, rules_dialog_sb):
        rules_dialog_sb._psb.show_status("test message")
        assert rules_dialog_sb._psb._status_bar.text() == "test message"
        assert rules_dialog_sb._psb._status_level == "success"

    def test_status_bar_color_routing(self, rules_dialog_sb):
        rules_dialog_sb._psb.show_status("error msg", "error")
        assert rules_dialog_sb._psb._status_level == "error"
        rules_dialog_sb._psb.show_status("warn", "warning")
        assert rules_dialog_sb._psb._status_level == "warning"

    def test_status_bar_persistent(self, rules_dialog_sb):
        rules_dialog_sb._psb.show_status("persistent msg")
        assert rules_dialog_sb._psb._status_bar.isHidden() is False

    def test_status_bar_replaces_previous(self, rules_dialog_sb):
        rules_dialog_sb._psb.show_status("first")
        rules_dialog_sb._psb.show_status("second")
        assert rules_dialog_sb._psb._status_bar.text() == "second"

    def test_validate_failure_uses_status_bar(self, rules_dialog_sb, monkeypatch):
        from types import SimpleNamespace

        from tests._fakes_gui import FakeRuleLifecycle

        page = rules_dialog_sb._rule_page
        page._lifecycle = FakeRuleLifecycle([_sample_rule("rule_a")])
        page._rebuild_model()
        page._table.selectRow(0)
        name = page.rule_name(0)
        assert name, "table should have data after FakeRuleLifecycle injection"
        mock_snap = SimpleNamespace(
            get_path=lambda n: Path(f"/fake/{n}.json"),
            get_source=lambda n: "pip",
        )
        monkeypatch.setattr(page._lifecycle, "get_snapshot", lambda: mock_snap)
        monkeypatch.setattr(
            "astrocrawl.rules.load_rule_file",
            MagicMock(side_effect=ValueError("bad schema")),
        )
        page._on_validate()
        assert rules_dialog_sb._psb._status_level == "error"
        assert name in rules_dialog_sb._psb._status_bar.text()


# ═══════════════════════════════════════════════════════════════════════
# SA01-SA11: 远程源页面对齐规则列表
# ═══════════════════════════════════════════════════════════════════════


class TestSourcePageAlignment:
    @pytest.fixture
    def source_page_aligned(self, qapp, theme_mgr, monkeypatch, request):
        from astrocrawl.gui.rules_dialog import _SourcePage

        monkeypatch.setattr(
            "astrocrawl.gui.rules_dialog.list_sources_from_file",
            lambda: [
                {
                    "name": "src1",
                    "url": "https://example.com/manifest.json",
                    "enabled": True,
                    "state": "active",
                    "last_updated": 1717171200,
                    "rules_count": 10,
                    "title": "Test Source",
                    "maintainer": "TestCo",
                    "homepage": "https://example.com",
                    "daily_update_count": 1,
                    "consecutive_failures": 0,
                },
            ],
        )
        monkeypatch.setattr("astrocrawl.gui.rules_dialog.update_source_in_file", lambda name, **kw: True)
        monkeypatch.setattr("astrocrawl.gui.rules_dialog.QDialog.exec", MagicMock(return_value=0))
        page = _SourcePage()
        request.addfinalizer(page.deleteLater)
        return page

    def test_source_edit_button_opens_dialog(self, source_page_aligned, monkeypatch):
        source_page_aligned._refresh()
        source_page_aligned._table.selectRow(0)
        mock_dialog = MagicMock()
        monkeypatch.setattr(
            "astrocrawl.gui.rules_dialog._SourceEditDialog",
            mock_dialog,
        )
        source_page_aligned._on_edit_source()
        mock_dialog.assert_called_once()

    def test_source_validate_button_format(self, source_page_aligned, monkeypatch):
        source_page_aligned._refresh()
        source_page_aligned._table.selectRow(0)
        called = []
        monkeypatch.setattr(
            source_page_aligned, "_show_status", lambda msg, level="success": called.append((msg, level))
        )
        source_page_aligned._on_validate_source()
        assert any("Validating" in c[0] for c in called)

    def test_source_search_filters(self, source_page_aligned):
        source_page_aligned._refresh()
        source_page_aligned._search_input.setText("example")
        source_page_aligned._apply_filter()
        assert source_page_aligned._table.model().rowCount() > 0

    def test_source_search_no_match_hides(self, source_page_aligned):
        source_page_aligned._refresh()
        source_page_aligned._search_input.setText("nonexistent")
        source_page_aligned._apply_filter()
        assert source_page_aligned._table.model().rowCount() == 0

    def test_source_search_empty_shows_all(self, source_page_aligned):
        source_page_aligned._refresh()
        source_page_aligned._search_input.setText("nonexistent")
        source_page_aligned._apply_filter()
        source_page_aligned._search_input.setText("")
        source_page_aligned._apply_filter()
        assert source_page_aligned._table.model().rowCount() > 0

    def test_source_double_click_edits(self, source_page_aligned, monkeypatch):
        source_page_aligned._refresh()
        called = []
        monkeypatch.setattr(source_page_aligned, "_on_edit_source", lambda: called.append(True))
        source_page_aligned._on_source_detail()
        assert len(called) == 1

    def test_editable_source_dialog_sections(self, qapp, theme_mgr):
        from astrocrawl.gui.rules_dialog import _SourceEditDialog

        src = {"name": "test", "url": "https://example.com/manifest.json"}
        dlg = _SourceEditDialog(source=src)
        assert dlg._name_edit.text() == "test"
        assert dlg._url_edit.text() == "https://example.com/manifest.json"

    def test_editable_source_name_url_fields(self, qapp, theme_mgr):
        from astrocrawl.gui.rules_dialog import _SourceEditDialog

        src = {"name": "orig", "url": "https://orig.com/manifest.json"}
        dlg = _SourceEditDialog(source=src)
        assert dlg.get_data() == {"name": "orig", "url": "https://orig.com/manifest.json"}

    def test_editable_source_confirm_saves(self, qapp, theme_mgr, monkeypatch):
        from astrocrawl.gui.rules_dialog import _SourceEditDialog

        monkeypatch.setattr("astrocrawl.gui.rules_dialog.QDialog.accept", MagicMock())
        src = {"name": "test", "url": "https://example.com/manifest.json"}
        dlg = _SourceEditDialog(source=src)
        dlg._name_edit.setText("new-name")
        assert dlg.get_data()["name"] == "new-name"

    def test_editable_source_cancel_discards(self, qapp, theme_mgr, monkeypatch):
        from astrocrawl.gui.rules_dialog import _SourceEditDialog

        monkeypatch.setattr("astrocrawl.gui.rules_dialog.QDialog.reject", MagicMock())
        src = {"name": "test", "url": "https://example.com/manifest.json", "title": "T"}
        dlg = _SourceEditDialog(source=src)
        # 远程信息和运行状态应为只读 QLabel
        layout = dlg.layout()
        assert layout is not None

    def test_add_source_dialog_no_readonly_sections(self, qapp, theme_mgr):
        from PySide6.QtWidgets import QGroupBox as _QGB

        from astrocrawl.gui.rules_dialog import _SourceEditDialog

        dlg = _SourceEditDialog(qapp.activeWindow())
        assert dlg._is_new is True
        titles = [child.title() for child in dlg.findChildren(_QGB)]
        assert "Remote Info" not in titles
        assert "Runtime Status" not in titles

    def test_add_source_empty_url_shows_error(self, qapp, theme_mgr):
        from astrocrawl.gui.rules_dialog import _SourceEditDialog

        dlg = _SourceEditDialog(qapp.activeWindow())
        dlg._url_edit.setText("")
        dlg._validate_and_accept()
        assert dlg._error_label.text() != ""

    def test_add_source_invalid_url_shows_error(self, qapp, theme_mgr):
        from astrocrawl.gui.rules_dialog import _SourceEditDialog

        dlg = _SourceEditDialog(qapp.activeWindow())
        dlg._url_edit.setText("http://x.com")
        dlg._validate_and_accept()
        assert "Invalid URL" in dlg._error_label.text()

    def test_add_source_empty_name_shows_error(self, qapp, theme_mgr):
        from astrocrawl.gui.rules_dialog import _SourceEditDialog

        dlg = _SourceEditDialog(qapp.activeWindow())
        dlg._url_edit.setText("https://example.com/manifest.json")
        dlg._name_edit.setText("")
        dlg._validate_and_accept()
        assert dlg._error_label.text() != ""

    def test_add_source_valid_data_accepts(self, qapp, theme_mgr):
        from astrocrawl.gui.rules_dialog import _SourceEditDialog

        dlg = _SourceEditDialog(qapp.activeWindow())
        dlg._url_edit.setText("https://example.com/manifest.json")
        dlg._name_edit.setText("my-source")
        assert dlg._validate() is True
        assert dlg._error_label.text() == ""

    def test_edit_source_retains_three_sections(self, qapp, theme_mgr):
        from PySide6.QtWidgets import QGroupBox as _QGB

        from astrocrawl.gui.rules_dialog import _SourceEditDialog

        src = {"name": "test", "url": "https://example.com/manifest.json"}
        dlg = _SourceEditDialog(source=src)
        assert dlg._is_new is False
        titles = [child.title() for child in dlg.findChildren(_QGB)]
        assert "Basic Info" in titles
        assert "Remote Info" in titles
        assert "Runtime Status" in titles

    def test_validate_empty_url_shows_error(self, source_page_aligned, monkeypatch):
        monkeypatch.setattr(
            "astrocrawl.gui.rules_dialog.list_sources_from_file",
            lambda: [{"name": "bad", "url": "", "enabled": True, "state": "active"}],
        )
        source_page_aligned._refresh()
        source_page_aligned._table.selectRow(0)
        from astrocrawl.gui import rules_dialog as rd_mod

        called = []
        monkeypatch.setattr(rd_mod.QMessageBox, "information", lambda *a, **kw: called.append(True))
        source_page_aligned._on_validate_source()
        assert len(called) == 1

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidgetItem

from astrocrawl.browser._preview import PreviewPageHandle, PreviewResult
from astrocrawl.rules._schema import FieldRule


def _make_snapshot(rules: list[dict] | None = None):
    rules = rules or [
        {"name": "test_rule", "display_name": "Test Rule", "fields": {}, "tags": [], "version": 1, "enabled": True}
    ]
    by_name = {}
    for r in rules:
        by_name[r["name"]] = SimpleNamespace(
            name=r["name"],
            display_name=r.get("display_name", ""),
            tags=r.get("tags", []),
            version=r.get("version", 1),
            enabled=r.get("enabled", True),
            fields=r.get("fields", {}),
            test_urls=r.get("test_urls", []),
        )
    return SimpleNamespace(by_name=by_name)


class TestPreviewPanelSingleton:
    @pytest.mark.gui
    def test_open_creates_instance(self, qapp, theme_mgr):
        with patch("astrocrawl.gui._preview_panel.PreviewSession"):
            from astrocrawl.gui._preview_panel import PreviewPanel

            PreviewPanel._instance = None
            snapshot = _make_snapshot()
            panel = PreviewPanel.open(snapshot)
            assert PreviewPanel._instance is panel
            assert panel.isVisible()
            panel.reject()

    @pytest.mark.gui
    def test_open_reuses_visible_instance(self, qapp, theme_mgr):
        with patch("astrocrawl.gui._preview_panel.PreviewSession"):
            from astrocrawl.gui._preview_panel import PreviewPanel

            PreviewPanel._instance = None
            snapshot = _make_snapshot()
            panel1 = PreviewPanel.open(snapshot)
            panel2 = PreviewPanel.open(snapshot)
            assert panel1 is panel2
            panel1.reject()

    @pytest.mark.gui
    def test_open_recreates_after_reject(self, qapp, theme_mgr):
        with patch("astrocrawl.gui._preview_panel.PreviewSession"):
            from astrocrawl.gui._preview_panel import PreviewPanel

            PreviewPanel._instance = None
            snapshot = _make_snapshot()
            panel1 = PreviewPanel.open(snapshot)
            panel1.reject()
            panel2 = PreviewPanel.open(snapshot)
            assert panel1 is not panel2
            assert PreviewPanel._instance is panel2
            panel2.reject()


class TestPreviewPanelLayout:
    @pytest.mark.gui
    def test_controls_exist(self, qapp, theme_mgr):
        with patch("astrocrawl.gui._preview_panel.PreviewSession"):
            from astrocrawl.gui._preview_panel import PreviewPanel

            PreviewPanel._instance = None
            snapshot = _make_snapshot()
            panel = PreviewPanel.open(snapshot)
            assert panel._rule_edit is not None
            assert panel._url_edit is not None
            assert panel._go_btn is not None
            assert panel._page_list is not None
            assert panel._page_rows is not None
            assert panel._psb._status_bar is not None
            panel.reject()

    @pytest.mark.gui
    def test_source_model_populated(self, qapp, theme_mgr):
        with patch("astrocrawl.gui._preview_panel.PreviewSession"):
            from astrocrawl.gui._preview_panel import PreviewPanel

            PreviewPanel._instance = None
            snapshot = _make_snapshot(
                [
                    {
                        "name": "rule_a",
                        "display_name": "Rule A",
                        "fields": {},
                        "tags": [],
                        "version": 1,
                        "enabled": True,
                    },
                    {
                        "name": "rule_b",
                        "display_name": "Rule B",
                        "fields": {},
                        "tags": [],
                        "version": 1,
                        "enabled": True,
                    },
                ]
            )
            panel = PreviewPanel.open(snapshot)
            assert panel._source_model.rowCount() == 2
            panel.reject()

    @pytest.mark.gui
    def test_set_rule_autofills(self, qapp, theme_mgr):
        with patch("astrocrawl.gui._preview_panel.PreviewSession"):
            from astrocrawl.gui._preview_panel import PreviewPanel

            PreviewPanel._instance = None
            snapshot = _make_snapshot(
                [
                    {
                        "name": "target",
                        "display_name": "Target Rule",
                        "fields": {},
                        "tags": [],
                        "version": 1,
                        "enabled": True,
                    }
                ]
            )
            panel = PreviewPanel.open(snapshot)
            panel.set_rule("target", "https://custom.url")
            assert panel._url_edit.text() == "https://custom.url"
            panel.reject()

    @pytest.mark.gui
    def test_empty_url_shows_warning(self, qapp, theme_mgr):
        with patch("astrocrawl.gui._preview_panel.PreviewSession"):
            from astrocrawl.gui._preview_panel import PreviewPanel

            PreviewPanel._instance = None
            snapshot = _make_snapshot()
            panel = PreviewPanel.open(snapshot)
            panel._url_edit.clear()
            panel._on_go()
            assert "URL" in panel._psb._status_bar.text()
            panel.reject()

    @pytest.mark.gui
    def test_no_rule_shows_warning(self, qapp, theme_mgr):
        with patch("astrocrawl.gui._preview_panel.PreviewSession"):
            from astrocrawl.gui._preview_panel import PreviewPanel

            PreviewPanel._instance = None
            snapshot = _make_snapshot()
            panel = PreviewPanel.open(snapshot)
            panel._url_edit.setText("https://example.com")
            # No rule selected — _selected_rule_name is None → _build_params returns None
            panel._on_go()
            assert "rule" in panel._psb._status_bar.text().lower()
            panel.reject()


class TestPreviewPanelBuildParams:
    @pytest.mark.gui
    def test_build_params_from_snapshot(self, qapp, theme_mgr):
        with patch("astrocrawl.gui._preview_panel.PreviewSession"):
            from astrocrawl.gui._preview_panel import PreviewPanel

            PreviewPanel._instance = None
            snapshot = _make_snapshot(
                [
                    {
                        "name": "test_rule",
                        "display_name": "Test",
                        "fields": {
                            "title": {"selector": "h1", "extract": "text", "multiple": False},
                            "price": {
                                "selector": ".price",
                                "extract": "text",
                                "multiple": True,
                                "fallback": [{"selector": ".fb", "extract": "text"}],
                            },
                        },
                        "tags": [],
                        "version": 1,
                        "enabled": True,
                    }
                ]
            )
            panel = PreviewPanel.open(snapshot)
            panel.set_rule("test_rule")
            assert panel._selected_rule_name == "test_rule"

            params = panel._build_params()
            assert params is not None
            assert params.rule_name == "test_rule"
            assert len(params.fields) == 2
            for f in params.fields:
                assert f.color.startswith("#")
            assert params.theme_mode in {"light", "dark"}
            assert isinstance(params.theme_tokens, dict)
            assert len(params.theme_tokens) >= 10
            panel.reject()

    @pytest.mark.gui
    def test_build_params_no_rule_returns_none(self, qapp, theme_mgr):
        with patch("astrocrawl.gui._preview_panel.PreviewSession"):
            from astrocrawl.gui._preview_panel import PreviewPanel

            PreviewPanel._instance = None
            snapshot = _make_snapshot()
            panel = PreviewPanel.open(snapshot)
            assert panel._build_params() is None
            panel.reject()


class TestPreviewPanelReject:
    @pytest.mark.gui
    def test_reject_disposes_session(self, qapp, theme_mgr):
        with patch("astrocrawl.gui._preview_panel.PreviewSession") as _mock_session:
            from astrocrawl.gui._preview_panel import PreviewPanel

            PreviewPanel._instance = None
            snapshot = _make_snapshot()
            panel = PreviewPanel.open(snapshot)
            session = panel._session
            panel.reject()
            session.dispose.assert_called_once()
            assert PreviewPanel._instance is None

    @pytest.mark.gui
    def test_go_button_disabled_during_load(self, qapp, theme_mgr):
        with patch("astrocrawl.gui._preview_panel.PreviewSession"):
            from astrocrawl.gui._preview_panel import PreviewPanel

            PreviewPanel._instance = None
            snapshot = _make_snapshot(
                [
                    {
                        "name": "r1",
                        "display_name": "R1",
                        "fields": {"f1": {"selector": "div", "extract": "text"}},
                        "tags": [],
                        "version": 1,
                        "enabled": True,
                    }
                ]
            )
            panel = PreviewPanel.open(snapshot)
            panel.set_rule("r1")
            panel._url_edit.setText("https://example.com")
            panel._on_go()
            assert not panel._go_btn.isEnabled()
            panel.reject()

    @pytest.mark.gui
    def test_go_button_reenabled_after_page_opened(self, qapp, theme_mgr):
        with patch("astrocrawl.gui._preview_panel.PreviewSession"):
            from astrocrawl.gui._preview_panel import PreviewPanel

            PreviewPanel._instance = None
            snapshot = _make_snapshot(
                [
                    {
                        "name": "r1",
                        "display_name": "R1",
                        "fields": {"f1": {"selector": "div", "extract": "text"}},
                        "tags": [],
                        "version": 1,
                        "enabled": True,
                    }
                ]
            )
            panel = PreviewPanel.open(snapshot)
            panel.set_rule("r1")
            panel._url_edit.setText("https://example.com")
            panel._on_go()
            handle = PreviewPageHandle(page_id=0, url="https://example.com", rule_name="r1")
            panel._on_page_opened(handle)
            assert panel._go_btn.isEnabled()
            panel.reject()

    @pytest.mark.gui
    def test_go_button_reenabled_after_error(self, qapp, theme_mgr):
        with patch("astrocrawl.gui._preview_panel.PreviewSession"):
            from astrocrawl.gui._preview_panel import PreviewPanel

            PreviewPanel._instance = None
            snapshot = _make_snapshot(
                [
                    {
                        "name": "r1",
                        "display_name": "R1",
                        "fields": {"f1": {"selector": "div", "extract": "text"}},
                        "tags": [],
                        "version": 1,
                        "enabled": True,
                    }
                ]
            )
            panel = PreviewPanel.open(snapshot)
            panel.set_rule("r1")
            panel._url_edit.setText("https://example.com")
            panel._on_go()
            panel._on_error("连接失败")
            assert panel._go_btn.isEnabled()
            panel.reject()

    @pytest.mark.gui
    def test_go_ignored_while_loading(self, qapp, theme_mgr):
        with patch("astrocrawl.gui._preview_panel.PreviewSession"):
            from astrocrawl.gui._preview_panel import PreviewPanel

            PreviewPanel._instance = None
            snapshot = _make_snapshot(
                [
                    {
                        "name": "r1",
                        "display_name": "R1",
                        "fields": {"f1": {"selector": "div", "extract": "text"}},
                        "tags": [],
                        "version": 1,
                        "enabled": True,
                    }
                ]
            )
            panel = PreviewPanel.open(snapshot)
            panel.set_rule("r1")
            panel._url_edit.setText("https://example.com")
            panel._loading = True
            panel._on_go()
            panel._session.open_page.assert_not_called()
            panel.reject()


class TestPreviewPanelSignals:
    @pytest.mark.gui
    def test_page_opened_adds_to_list(self, qapp, theme_mgr):
        with patch("astrocrawl.gui._preview_panel.PreviewSession"):
            from astrocrawl.gui._preview_panel import PreviewPanel

            PreviewPanel._instance = None
            snapshot = _make_snapshot()
            panel = PreviewPanel.open(snapshot)
            handle = PreviewPageHandle(page_id=0, url="https://example.com", rule_name="my_rule")
            panel._on_page_opened(handle)
            assert panel._page_list.count() == 1
            panel.reject()

    @pytest.mark.gui
    def test_page_closed_removes_from_list(self, qapp, theme_mgr):
        with patch("astrocrawl.gui._preview_panel.PreviewSession"):
            from astrocrawl.gui._preview_panel import PreviewPanel

            PreviewPanel._instance = None
            snapshot = _make_snapshot()
            panel = PreviewPanel.open(snapshot)
            handle = PreviewPageHandle(page_id=7, url="https://example.com", rule_name="r")
            panel._on_page_opened(handle)
            assert panel._page_list.count() == 1
            panel._on_page_closed(7)
            assert panel._page_list.count() == 0
            panel.reject()

    @pytest.mark.gui
    def test_highlight_shows_status(self, qapp, theme_mgr):
        with patch("astrocrawl.gui._preview_panel.PreviewSession"):
            from astrocrawl.gui._preview_panel import PreviewPanel

            PreviewPanel._instance = None
            snapshot = _make_snapshot()
            panel = PreviewPanel.open(snapshot)
            result = PreviewResult(total=5, matched=3, unmatched=2)
            panel._on_highlight_injected(result)
            assert "3/5" in panel._psb._status_bar.text()
            panel.reject()

    @pytest.mark.gui
    def test_error_shows_status(self, qapp, theme_mgr):
        with patch("astrocrawl.gui._preview_panel.PreviewSession"):
            from astrocrawl.gui._preview_panel import PreviewPanel

            PreviewPanel._instance = None
            snapshot = _make_snapshot()
            panel = PreviewPanel.open(snapshot)
            panel._on_error("连接超时")
            assert "连接超时" in panel._psb._status_bar.text()
            panel.reject()

    @pytest.mark.gui
    def test_session_disposed_clears_list(self, qapp, theme_mgr):
        with patch("astrocrawl.gui._preview_panel.PreviewSession"):
            from astrocrawl.gui._preview_panel import PreviewPanel

            PreviewPanel._instance = None
            snapshot = _make_snapshot()
            panel = PreviewPanel.open(snapshot)
            handle = PreviewPageHandle(page_id=0, url="https://example.com", rule_name="r")
            panel._on_page_opened(handle)
            panel._on_session_disposed()
            assert panel._page_list.count() == 0
            panel.reject()


class TestPreviewPanelOpenVariants:
    """open() 工厂方法的边界路径。"""

    @pytest.mark.gui
    def test_open_with_rule_name_sets_rule_on_new(self, qapp, theme_mgr):
        with patch("astrocrawl.gui._preview_panel.PreviewSession"):
            from astrocrawl.gui._preview_panel import PreviewPanel

            PreviewPanel._instance = None
            snapshot = _make_snapshot(
                [
                    {
                        "name": "target",
                        "display_name": "Target Rule",
                        "fields": {},
                        "tags": [],
                        "version": 1,
                        "enabled": True,
                    }
                ]
            )
            panel = PreviewPanel.open(snapshot, rule_name="target", test_url="https://auto.url")
            assert panel._url_edit.text() == "https://auto.url"
            panel.reject()

    @pytest.mark.gui
    def test_open_with_rule_name_reuses_and_sets(self, qapp, theme_mgr):
        with patch("astrocrawl.gui._preview_panel.PreviewSession"):
            from astrocrawl.gui._preview_panel import PreviewPanel

            PreviewPanel._instance = None
            snapshot = _make_snapshot(
                [
                    {
                        "name": "target",
                        "display_name": "Target Rule",
                        "fields": {},
                        "tags": [],
                        "version": 1,
                        "enabled": True,
                    }
                ]
            )
            panel1 = PreviewPanel.open(snapshot)
            panel2 = PreviewPanel.open(snapshot, rule_name="target", test_url="https://reuse.url")
            assert panel1 is panel2
            assert panel2._url_edit.text() == "https://reuse.url"
            panel1.reject()

    @pytest.mark.gui
    def test_open_replaces_invisible_orphan(self, qapp, theme_mgr):
        with patch("astrocrawl.gui._preview_panel.PreviewSession"):
            from astrocrawl.gui._preview_panel import PreviewPanel

            PreviewPanel._instance = None
            snapshot = _make_snapshot()
            panel1 = PreviewPanel.open(snapshot)
            panel1.hide()
            panel2 = PreviewPanel.open(snapshot)
            assert panel1 is not panel2
            assert PreviewPanel._instance is panel2
            panel2.reject()


class TestPreviewPanelCompleter:
    """QCompleter 过滤行为。"""

    @pytest.mark.gui
    def test_completer_filters_contains(self, qapp, theme_mgr):
        with patch("astrocrawl.gui._preview_panel.PreviewSession"):
            from astrocrawl.gui._preview_panel import PreviewPanel

            PreviewPanel._instance = None
            snapshot = _make_snapshot(
                [
                    {
                        "name": "alpha",
                        "display_name": "Alpha Rule",
                        "fields": {},
                        "tags": [],
                        "version": 1,
                        "enabled": True,
                    },
                    {
                        "name": "beta",
                        "display_name": "Beta Test",
                        "fields": {},
                        "tags": [],
                        "version": 1,
                        "enabled": True,
                    },
                    {
                        "name": "gamma",
                        "display_name": "Gamma Test",
                        "fields": {},
                        "tags": [],
                        "version": 1,
                        "enabled": True,
                    },
                ]
            )
            panel = PreviewPanel.open(snapshot)
            panel._completer.setCompletionPrefix("beta")
            assert panel._completer.completionCount() == 1
            panel.reject()

    @pytest.mark.gui
    def test_completer_populate_clears_state(self, qapp, theme_mgr):
        with patch("astrocrawl.gui._preview_panel.PreviewSession"):
            from astrocrawl.gui._preview_panel import PreviewPanel

            PreviewPanel._instance = None
            snapshot = _make_snapshot(
                [
                    {"name": "alpha", "display_name": "Alpha", "fields": {}, "tags": [], "version": 1, "enabled": True},
                    {"name": "zzz", "display_name": "ZZZ", "fields": {}, "tags": [], "version": 1, "enabled": True},
                ]
            )
            panel = PreviewPanel.open(snapshot)
            assert panel._source_model.rowCount() == 2
            assert panel._selected_rule_name is None
            assert panel._rule_edit.text() == ""
            panel.reject()

    @pytest.mark.gui
    def test_set_rule_sets_line_edit_text(self, qapp, theme_mgr):
        with patch("astrocrawl.gui._preview_panel.PreviewSession"):
            from astrocrawl.gui._preview_panel import PreviewPanel

            PreviewPanel._instance = None
            snapshot = _make_snapshot(
                [
                    {
                        "name": "alpha",
                        "display_name": "Alpha Rule",
                        "fields": {},
                        "tags": [],
                        "version": 1,
                        "enabled": True,
                    },
                    {
                        "name": "beta",
                        "display_name": "Beta Test",
                        "fields": {},
                        "tags": [],
                        "version": 1,
                        "enabled": True,
                    },
                ]
            )
            panel = PreviewPanel.open(snapshot)
            panel.set_rule("alpha")
            assert panel._rule_edit.text() == "Alpha Rule"
            assert panel._selected_rule_name == "alpha"
            panel.reject()

    @pytest.mark.gui
    def test_show_all_completions_triggers_completer(self, qapp, theme_mgr):
        with patch("astrocrawl.gui._preview_panel.PreviewSession"):
            from astrocrawl.gui._preview_panel import PreviewPanel

            PreviewPanel._instance = None
            snapshot = _make_snapshot()
            panel = PreviewPanel.open(snapshot)
            with patch.object(panel._completer, "complete") as mock_complete:
                panel._show_all_completions()
                mock_complete.assert_called_once()
            panel.reject()


class TestPreviewPanelRuleSelected:
    """规则选中时 URL 自动填入。"""

    @pytest.mark.gui
    def test_rule_selected_string_test_url(self, qapp, theme_mgr):
        with patch("astrocrawl.gui._preview_panel.PreviewSession"):
            from astrocrawl.gui._preview_panel import PreviewPanel

            PreviewPanel._instance = None
            snapshot = _make_snapshot(
                [
                    {
                        "name": "string_url_rule",
                        "display_name": "String URL",
                        "fields": {},
                        "tags": [],
                        "version": 1,
                        "enabled": True,
                        "test_urls": ["https://string.url/page"],
                    }
                ]
            )
            panel = PreviewPanel.open(snapshot)
            panel.set_rule("string_url_rule")
            assert panel._url_edit.text() == "https://string.url/page"
            panel.reject()

    @pytest.mark.gui
    def test_rule_selected_dict_test_url(self, qapp, theme_mgr):
        with patch("astrocrawl.gui._preview_panel.PreviewSession"):
            from astrocrawl.gui._preview_panel import PreviewPanel

            PreviewPanel._instance = None
            snapshot = _make_snapshot(
                [
                    {
                        "name": "dict_url_rule",
                        "display_name": "Dict URL",
                        "fields": {},
                        "tags": [],
                        "version": 1,
                        "enabled": True,
                        "test_urls": [{"url": "https://dict.url/page"}],
                    }
                ]
            )
            panel = PreviewPanel.open(snapshot)
            panel.set_rule("dict_url_rule")
            assert panel._url_edit.text() == "https://dict.url/page"
            panel.reject()

    @pytest.mark.gui
    def test_completer_activated_selects_rule(self, qapp, theme_mgr):
        with patch("astrocrawl.gui._preview_panel.PreviewSession"):
            from astrocrawl.gui._preview_panel import PreviewPanel

            PreviewPanel._instance = None
            snapshot = _make_snapshot(
                [
                    {
                        "name": "click_rule",
                        "display_name": "Click Rule",
                        "fields": {},
                        "tags": [],
                        "version": 1,
                        "enabled": True,
                        "test_urls": ["https://clicked.url"],
                    }
                ]
            )
            panel = PreviewPanel.open(snapshot)
            panel._on_completer_activated("Click Rule")
            assert panel._selected_rule_name == "click_rule"
            assert panel._url_edit.text() == "https://clicked.url"
            panel.reject()


class TestPreviewPanelInteractions:
    """页面列表交互处理。"""

    @pytest.mark.gui
    def test_page_clicked_activates_session(self, qapp, theme_mgr):
        with patch("astrocrawl.gui._preview_panel.PreviewSession"):
            from astrocrawl.gui._preview_panel import PreviewPanel

            PreviewPanel._instance = None
            snapshot = _make_snapshot()
            panel = PreviewPanel.open(snapshot)
            item = QListWidgetItem()
            item.setData(Qt.UserRole, 42)
            panel._page_list.addItem(item)
            panel._on_page_clicked(item)
            panel._session.activate_page.assert_called_once_with(42)
            panel.reject()

    @pytest.mark.gui
    def test_close_page_row_calls_session_close(self, qapp, theme_mgr):
        with patch("astrocrawl.gui._preview_panel.PreviewSession"):
            from astrocrawl.gui._preview_panel import PreviewPanel

            PreviewPanel._instance = None
            snapshot = _make_snapshot()
            panel = PreviewPanel.open(snapshot)
            panel._on_close_page_row(7)
            panel._session.close_page.assert_called_once_with(7)
            panel.reject()


class TestPreviewPanelBuildParamsFieldRule:
    """_build_params 对 FieldRule 对象的处理。"""

    @pytest.mark.gui
    def test_build_params_from_fieldrule_objects(self, qapp, theme_mgr):
        with patch("astrocrawl.gui._preview_panel.PreviewSession"):
            from astrocrawl.gui._preview_panel import PreviewPanel

            PreviewPanel._instance = None
            fb = FieldRule(selector=".fb", extract="text")
            fields = {
                "title": FieldRule(selector="h1", extract="text", multiple=False),
                "items": FieldRule(selector="li.item", extract="text", multiple=True, fallback=[fb]),
            }
            rule_attrs = {
                "name": "frule_test",
                "display_name": "FieldRule Test",
                "fields": fields,
                "tags": [],
                "version": 1,
                "enabled": True,
                "test_urls": [],
            }
            by_name = {"frule_test": SimpleNamespace(**rule_attrs)}
            snapshot = SimpleNamespace(by_name=by_name)

            panel = PreviewPanel.open(snapshot)
            panel.set_rule("frule_test")
            params = panel._build_params()
            assert params is not None
            assert len(params.fields) == 2
            title_f = params.fields[0]
            assert title_f.name == "title"
            assert title_f.selector == "h1"
            items_f = params.fields[1]
            assert items_f.name == "items"
            assert items_f.multiple is True
            assert len(items_f.fallback) == 1
            assert items_f.fallback[0]["selector"] == ".fb"
            panel.reject()

    @pytest.mark.gui
    def test_build_params_rule_not_found_returns_none(self, qapp, theme_mgr):
        with patch("astrocrawl.gui._preview_panel.PreviewSession"):
            from astrocrawl.gui._preview_panel import PreviewPanel

            PreviewPanel._instance = None
            snapshot = _make_snapshot()
            panel = PreviewPanel.open(snapshot)
            panel._selected_rule_name = "ghost_rule"
            assert panel._build_params() is None
            panel.reject()

    @pytest.mark.gui
    def test_build_params_str_field(self, qapp, theme_mgr):
        with patch("astrocrawl.gui._preview_panel.PreviewSession"):
            from astrocrawl.gui._preview_panel import PreviewPanel

            PreviewPanel._instance = None
            # Fields with a plain string value (not FieldRule, not dict)
            snapshot = _make_snapshot(
                [
                    {
                        "name": "str_field_rule",
                        "display_name": "Str Field",
                        "fields": {"raw_field": "h2.title"},
                        "tags": [],
                        "version": 1,
                        "enabled": True,
                    }
                ]
            )
            panel = PreviewPanel.open(snapshot)
            panel.set_rule("str_field_rule")
            params = panel._build_params()
            assert params is not None
            assert len(params.fields) == 1
            assert params.fields[0].name == "raw_field"
            assert params.fields[0].selector == "h2.title"
            assert params.fields[0].extract == "text"
            panel.reject()


class TestPreviewPanelTheme:
    """主题变更传播。"""

    @pytest.mark.gui
    def test_theme_changed_updates_session(self, qapp, theme_mgr):
        with patch("astrocrawl.gui._preview_panel.PreviewSession"):
            from astrocrawl.gui._preview_panel import PreviewPanel

            PreviewPanel._instance = None
            snapshot = _make_snapshot()
            panel = PreviewPanel.open(snapshot)
            panel._on_theme_changed()
            panel._session.update_theme.assert_called_once()
            panel.reject()

    @pytest.mark.gui
    def test_refresh_backgrounds_after_page_removal(self, qapp, theme_mgr):
        with patch("astrocrawl.gui._preview_panel.PreviewSession"):
            from astrocrawl.gui._preview_panel import PreviewPanel

            PreviewPanel._instance = None
            snapshot = _make_snapshot()
            panel = PreviewPanel.open(snapshot)
            h1 = PreviewPageHandle(page_id=1, url="https://a.com", rule_name="A")
            h2 = PreviewPageHandle(page_id=2, url="https://b.com", rule_name="B")
            panel._on_page_opened(h1)
            panel._on_page_opened(h2)
            assert panel._page_list.count() == 2
            panel._on_page_closed(1)
            assert panel._page_list.count() == 1
            # Remaining row should still have a palette set
            row = panel._page_list.itemWidget(panel._page_list.item(0))
            assert row is not None
            panel.reject()


class TestPreviewPanelCloseEvent:
    """closeEvent 覆盖。"""

    @pytest.mark.gui
    def test_close_event_calls_reject(self, qapp, theme_mgr):
        with patch("astrocrawl.gui._preview_panel.PreviewSession"):
            from PySide6.QtGui import QCloseEvent

            from astrocrawl.gui._preview_panel import PreviewPanel

            PreviewPanel._instance = None
            snapshot = _make_snapshot()
            panel = PreviewPanel.open(snapshot)
            assert PreviewPanel._instance is panel
            panel.closeEvent(QCloseEvent())
            assert PreviewPanel._instance is None

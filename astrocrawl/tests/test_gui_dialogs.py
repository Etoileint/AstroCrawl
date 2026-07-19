"""Phase 4 — 对话框测试。

覆盖:
- AD01-AD23: AdvancedSettingsDialog
- CR01-CR06: CompletionReportDialog._build_rows
- MW35-MW40: MainWindow _on_finished / _on_error
- AW01-AW02: _AiWorker
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QMessageBox

pytestmark = pytest.mark.gui


@pytest.fixture(autouse=True)
def _patch_qmessagebox(monkeypatch):
    """全局 patch QMessageBox 静态方法，防止模态对话框阻塞测试。"""
    mock = MagicMock()
    monkeypatch.setattr(QMessageBox, "warning", mock)
    monkeypatch.setattr(QMessageBox, "critical", mock)
    monkeypatch.setattr(QMessageBox, "information", mock)


# ═══════════════════════════════════════════════════════════════════════
# AD01-AD23: AdvancedSettingsDialog
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def adv_dialog(qapp, theme_mgr, monkeypatch):
    from tests._fakes_gui import FakePreferences

    monkeypatch.setattr("astrocrawl.utils.preferences._preferences", FakePreferences())

    from astrocrawl.config import CrawlerConfig
    from astrocrawl.gui.advanced_dialog import AdvancedSettingsDialog

    cfg = CrawlerConfig(concurrency=1, domain_min_delay=0.0, domain_max_delay=0.0)
    dlg = AdvancedSettingsDialog(cfg=cfg)
    return dlg


class TestAdvancedSettingsDialog:
    def test_get_config_reflects_modified_page_timeout(self, adv_dialog):
        adv_dialog.page_timeout.setValue(30000)
        result = adv_dialog.get_config()
        assert result.page_timeout == 30000

    def test_get_config_reflects_modified_network_idle(self, adv_dialog):
        adv_dialog.network_idle.setValue(15000)
        result = adv_dialog.get_config()
        assert result.network_idle_timeout == 15000

    # proxy_mode migrated to main_window (ADR-0010 Phase 3.5b)

    def test_validate_min_delay_greater_than_max_fails(self, adv_dialog):
        adv_dialog.domain_min_delay.setValue(5.0)
        adv_dialog.domain_max_delay.setValue(1.0)
        assert adv_dialog._validate() is False

    def test_validate_empty_user_agent_fails(self, adv_dialog):
        adv_dialog.user_agent.setText("")
        assert adv_dialog._validate() is False

    def test_validate_default_config_passes(self, adv_dialog):
        assert adv_dialog._validate() is True

    def test_max_runtime_toggle_disabled_preserves_last_value(self, adv_dialog):
        adv_dialog.max_runtime_enable.setChecked(True)
        adv_dialog.max_runtime_spin.setValue(3600)
        adv_dialog.max_runtime_enable.setChecked(False)

        assert adv_dialog.max_runtime_spin.isEnabled() is False
        assert adv_dialog._last_max_runtime == 3600

    def test_max_runtime_toggle_enabled_restores_last(self, adv_dialog):
        adv_dialog.max_runtime_enable.setChecked(True)
        adv_dialog.max_runtime_spin.setValue(7200)
        adv_dialog.max_runtime_enable.setChecked(False)
        adv_dialog.max_runtime_enable.setChecked(True)

        assert adv_dialog.max_runtime_spin.isEnabled() is True
        assert adv_dialog.max_runtime_spin.value() == 7200

    def test_apply_current_rejects_invalid_config(self, adv_dialog):
        callback = MagicMock()
        adv_dialog._on_apply = callback
        adv_dialog.user_agent.setText("")
        adv_dialog._apply_current()
        callback.assert_not_called()

    def test_validate_and_accept_on_valid_config(self, adv_dialog, monkeypatch):
        accept_mock = MagicMock()
        monkeypatch.setattr(adv_dialog, "accept", accept_mock)
        adv_dialog._validate_and_accept()
        accept_mock.assert_called_once()

    def test_get_config_respects_max_total_pages(self, adv_dialog):
        adv_dialog.max_pages.setValue(500)
        result = adv_dialog.get_config()
        assert result.max_total_pages == 500

    def test_get_config_preserves_other_cfg_fields(self, adv_dialog):
        result = adv_dialog.get_config()
        assert result.user_agent == adv_dialog.cfg.user_agent
        assert result.domain_max_concurrency == adv_dialog.cfg.domain_max_concurrency

    def test_get_config_no_longer_includes_global_fields(self, adv_dialog):
        """get_config() 不再包含全局设置字段——这些由 GlobalSettings 管理。"""
        result = adv_dialog.get_config()
        assert not hasattr(result, "output_gzip")
        assert not hasattr(result, "trace_rules")
        assert not hasattr(result, "clear_context_cookies")
        assert not hasattr(result, "log_level")
        assert not hasattr(result, "rules_auto_update")

    def test_get_config_skip_non_essential_checked(self, adv_dialog):
        adv_dialog.skip_non_essential.setChecked(True)
        result = adv_dialog.get_config()
        assert result.skip_non_essential_resources is True

    def test_open_ai_tab_switches_to_tab_2(self, theme_mgr):
        """open_ai_tab=True 时对话框直接切换到 AI 设置标签页。"""
        from astrocrawl.config import CrawlerConfig
        from astrocrawl.gui.advanced_dialog import AdvancedSettingsDialog

        dlg = AdvancedSettingsDialog(cfg=CrawlerConfig(concurrency=1), open_ai_tab=True)
        assert dlg._tabs.currentIndex() == 2

    def test_validate_cookie_file_not_found(self, adv_dialog, tmp_path):
        nonexistent = tmp_path / "missing.json"
        adv_dialog._cookies_file_edit.setText(str(nonexistent))
        assert adv_dialog._validate() is False

    def test_validate_cookie_file_wrong_extension(self, adv_dialog, tmp_path):
        f = tmp_path / "cookies.txt"
        f.write_text("{}")
        adv_dialog._cookies_file_edit.setText(str(f))
        assert adv_dialog._validate() is False

    def test_validate_webhook_invalid_url(self, adv_dialog):
        adv_dialog.webhook_url.setText("ftp://bad.example.com/hook")
        assert adv_dialog._validate() is False

    def test_validate_webhook_https_passes(self, adv_dialog):
        adv_dialog.webhook_url.setText("https://example.com/hook")
        assert adv_dialog._validate() is True

    def test_apply_current_valid_calls_callback(self, adv_dialog, monkeypatch):
        callback = MagicMock()
        monkeypatch.setattr(adv_dialog, "_save_ai_settings", MagicMock())
        adv_dialog._on_apply = callback
        adv_dialog._apply_current()
        callback.assert_called_once()

    def test_on_cancel_rejects_dialog(self, adv_dialog, monkeypatch):
        reject_mock = MagicMock()
        monkeypatch.setattr(adv_dialog, "reject", reject_mock)
        adv_dialog._on_cancel()
        reject_mock.assert_called_once()

    def test_select_log_file_updates_edit(self, adv_dialog, monkeypatch, tmp_path):
        log_path = tmp_path / "test.log"
        monkeypatch.setattr(
            "PySide6.QtWidgets.QFileDialog.getSaveFileName",
            lambda *a, **kw: (str(log_path), "Log Files (*.log)"),
        )
        adv_dialog._select_log_file()
        assert adv_dialog._log_file_edit.text() == str(log_path)

    def test_select_cookies_file_updates_edit(self, adv_dialog, monkeypatch, tmp_path):
        cookie_path = tmp_path / "cookies.json"
        cookie_path.write_text("{}")
        monkeypatch.setattr(
            "PySide6.QtWidgets.QFileDialog.getOpenFileName",
            lambda *a, **kw: (str(cookie_path), "JSON Files (*.json)"),
        )
        adv_dialog._select_cookies_file()
        assert adv_dialog._cookies_file_edit.text() == str(cookie_path)

    def test_reject_disposes_psb_and_calls_super(self, adv_dialog, monkeypatch):
        """reject() 应先 dispose PSB 再调用 super().reject()."""
        from unittest.mock import MagicMock

        psb_mock = MagicMock()
        adv_dialog._psb = psb_mock
        super_mock = MagicMock()
        monkeypatch.setattr("PySide6.QtWidgets.QDialog.reject", super_mock)
        adv_dialog.reject()
        psb_mock.dispose.assert_called_once()
        super_mock.assert_called_once()


class TestGlobalSettingsTab:
    """全局设置标签页 — Preferences 绑定 + get_config 集成。"""

    def test_global_tab_exists(self, adv_dialog):
        tab_text = adv_dialog._tabs.tabText(1)
        assert tab_text == "Global Settings"

    def test_five_tabs_present(self, adv_dialog):
        assert adv_dialog._tabs.count() == 5
        assert adv_dialog._tabs.tabText(0) == "General Settings"
        assert adv_dialog._tabs.tabText(1) == "Global Settings"
        assert adv_dialog._tabs.tabText(2) == "AI Settings"
        assert adv_dialog._tabs.tabText(3) == "Proxy Settings"
        assert adv_dialog._tabs.tabText(4) == "Route Settings"

    def test_global_tab_reads_from_preferences(self, adv_dialog, monkeypatch):
        """全局设置标签页从 Preferences 读取初始暂存值。"""
        from tests._fakes_gui import FakePreferences

        fake = FakePreferences()
        fake.set_rules_auto_update(False)
        fake.set_log_level("DEBUG")
        monkeypatch.setattr("astrocrawl.utils.preferences._preferences", fake)
        from astrocrawl.config import CrawlerConfig
        from astrocrawl.gui.advanced_dialog import AdvancedSettingsDialog

        dlg = AdvancedSettingsDialog(cfg=CrawlerConfig())
        assert dlg._staged_rules_auto_update is False
        assert dlg._staged_log_level == "DEBUG"

    def test_global_tab_staged_not_immediate(self, adv_dialog):
        """全局设置切换仅更新暂存变量，不立即写入 Preferences。"""
        from astrocrawl.utils.preferences import get_preferences

        prefs = get_preferences()
        original = prefs.get_rules_auto_update()
        adv_dialog._pref_rules_auto_update.setChecked(not original)
        assert prefs.get_rules_auto_update() == original  # 未变，仅在暂存
        assert adv_dialog._staged_rules_auto_update is (not original)

    def test_apply_global_settings_persists(self, adv_dialog):
        """_apply_global_settings() 将暂存值写入 Preferences。"""
        from astrocrawl.utils.preferences import get_preferences

        prefs = get_preferences()
        original = prefs.get_rules_auto_update()
        adv_dialog._staged_rules_auto_update = not original
        adv_dialog._apply_global_settings()
        assert prefs.get_rules_auto_update() is (not original)

    def test_get_config_no_longer_includes_global(self, adv_dialog, monkeypatch):
        """get_config() 不再包含全局设置——由 GlobalSettings 管理。"""
        from tests._fakes_gui import FakePreferences

        fake = FakePreferences()
        fake.set_output_gzip(False)
        fake.set_trace_rules(True)
        fake.set_clear_context_cookies(True)
        monkeypatch.setattr("astrocrawl.utils.preferences._preferences", fake)
        from astrocrawl.config import CrawlerConfig
        from astrocrawl.gui.advanced_dialog import AdvancedSettingsDialog

        dlg = AdvancedSettingsDialog(cfg=CrawlerConfig())
        result = dlg.get_config()
        assert not hasattr(result, "output_gzip")
        assert not hasattr(result, "trace_rules")
        assert not hasattr(result, "clear_context_cookies")
        assert not hasattr(result, "log_level")
        assert not hasattr(result, "rules_auto_update")

    def test_apply_global_settings_changes_language(self, adv_dialog, monkeypatch):
        """语言下拉框切换后 _apply_global_settings 应调用 set_language."""
        from astrocrawl.utils.preferences import get_preferences

        prefs = get_preferences()
        original_lang = prefs.get_language()
        new_lang = "zh_CN" if original_lang == "en" else "en"
        idx = adv_dialog._lang_combo.findData(new_lang)
        adv_dialog._lang_combo.setCurrentIndex(idx)
        adv_dialog._apply_global_settings()
        assert prefs.get_language() == new_lang


# ═══════════════════════════════════════════════════════════════════════
# FR01-FR02: _form_row 边界分支
# ═══════════════════════════════════════════════════════════════════════


class TestFormRow:
    def test_label_with_list_no_stretches(self, qapp, theme_mgr):
        """_form_row(label, [w1, w2]) — 多控件无显式拉伸 → widget[0] 3份."""
        from PySide6.QtWidgets import QLabel, QLineEdit

        from astrocrawl.gui.advanced_dialog import _form_row

        w1 = QLineEdit()
        w2 = QLabel("suffix")
        row = _form_row("Host", [w1, w2])
        assert row.count() == 2
        stretch_0 = row.stretch(0)
        stretch_1 = row.stretch(1)
        assert stretch_0 == 1  # label
        assert stretch_1 == 3  # widget[0]

    def test_label_with_single_widget_explicit_stretches(self, qapp, theme_mgr):
        """_form_row(label, w, 1, 3) — 单控件+显式双拉伸."""
        from PySide6.QtWidgets import QLabel

        from astrocrawl.gui.advanced_dialog import _form_row

        w = QLabel("value")
        row = _form_row("Key", w, 1, 3)
        assert row.count() == 2
        assert row.stretch(0) == 1  # label
        assert row.stretch(1) == 3  # widget

    def test_no_label_single_widget(self, qapp, theme_mgr):
        """_form_row(widget=w) — 无标签占整行."""
        from PySide6.QtWidgets import QCheckBox

        from astrocrawl.gui.advanced_dialog import _form_row

        cb = QCheckBox("Option")
        row = _form_row(widget=cb)
        assert row.count() == 1
        assert row.stretch(0) == 1


# ═══════════════════════════════════════════════════════════════════════
# CR01-CR06: CompletionReportDialog._build_rows
# ═══════════════════════════════════════════════════════════════════════


class TestCompletionReportRows:
    @staticmethod
    def _build_rows(report_data):
        from astrocrawl.gui.completion_dialog import CompletionReportDialog

        dlg = CompletionReportDialog.__new__(CompletionReportDialog)
        dlg._report = report_data
        return dlg._build_rows()

    def test_outcomes_ok_and_duplicate(self):
        rows = self._build_rows(
            {
                "outcome_summary": {"ok": 5, "duplicate": 2},
            }
        )
        row_texts = [str(r[1]) for r in rows if r[0] not in ("sep", "section")]
        assert "5" in row_texts
        assert "2" in row_texts

    def test_fetch_errors_merged(self):
        rows = self._build_rows(
            {
                "outcome_summary": {
                    "fetch_error": 3,
                    "internal_error": 1,
                    "stopped": 1,
                },
            }
        )
        labels = [r[0] for r in rows if r[0] not in ("sep", "section")]
        assert "Fetch Failed" in labels

    def test_discovery_section(self):
        rows = self._build_rows(
            {
                "outcome_summary": {},
                "discovery": {
                    "robots": {"ok": 3, "fetch_fail": 1},
                    "sitemap": {"ok": 2, "fetch_fail": 0, "discovered_urls": 100},
                },
            }
        )
        sections = [r[1] for r in rows if r[0] == "section"]
        assert "Discovery Stats" in sections

    def test_fetch_errors_section(self):
        rows = self._build_rows(
            {
                "outcome_summary": {"ok": 1},
                "fetch_errors": {"timeout": 3, "connection_refused": 2},
            }
        )
        sections = [r[1] for r in rows if r[0] == "section"]
        assert any("Failure Categories" in s for s in sections)

    def test_drops_section(self):
        rows = self._build_rows(
            {
                "outcome_summary": {"ok": 1},
                "drops": {"depth": 10, "domain_filter": 5},
            }
        )
        sections = [r[1] for r in rows if r[0] == "section"]
        assert any("Dropped" in s for s in sections)

    def test_empty_report_produces_no_errors(self):
        rows = self._build_rows({})
        assert isinstance(rows, list)


# ═══════════════════════════════════════════════════════════════════════
# MW35-MW40: MainWindow _on_finished / _on_error / _try_init_health_bar
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def main_window_dlg(qapp, theme_mgr):
    from astrocrawl.gui.main_window import MainWindow

    return MainWindow()


class TestMainWindowOnFinished:
    def test_uses_stats_directly_when_non_empty(self, main_window_dlg, monkeypatch):
        from astrocrawl.gui.completion_dialog import CompletionReportDialog

        mock_exec = MagicMock()
        monkeypatch.setattr(CompletionReportDialog, "exec", mock_exec)

        main_window_dlg._on_finished("/tmp/out.jsonl", {"ok": 5})

        log_texts = [main_window_dlg.log_list.item(i).text() for i in range(main_window_dlg.log_list.count())]
        assert any("===== Crawl complete" in t for t in log_texts)

    def test_file_fallback_when_stats_empty(self, main_window_dlg, monkeypatch, tmp_path):
        from astrocrawl.gui.completion_dialog import CompletionReportDialog

        mock_exec = MagicMock()
        monkeypatch.setattr(CompletionReportDialog, "exec", mock_exec)

        output_jsonl = tmp_path / "out.jsonl"
        output_jsonl.write_text("")
        report_path = tmp_path / "out.report.json"
        report_path.write_text(json.dumps({"ok": 3}))

        main_window_dlg._on_finished(str(output_jsonl), {})

        log_texts = [main_window_dlg.log_list.item(i).text() for i in range(main_window_dlg.log_list.count())]
        assert any("===== Crawl complete" in t for t in log_texts)

    def test_file_fallback_file_not_found(self, main_window_dlg, monkeypatch, tmp_path):
        from astrocrawl.gui.completion_dialog import CompletionReportDialog

        mock_exec = MagicMock()
        monkeypatch.setattr(CompletionReportDialog, "exec", mock_exec)

        output_path = tmp_path / "nonexistent.jsonl"
        output_path.write_text("")

        main_window_dlg._on_finished(str(output_path), {})

        log_texts = [main_window_dlg.log_list.item(i).text() for i in range(main_window_dlg.log_list.count())]
        warnings = [t for t in log_texts if "Report file not generated" in t]
        assert len(warnings) == 1

    def test_file_fallback_json_decode_error(self, main_window_dlg, monkeypatch, tmp_path):
        from astrocrawl.gui.completion_dialog import CompletionReportDialog

        mock_exec = MagicMock()
        monkeypatch.setattr(CompletionReportDialog, "exec", mock_exec)

        output_path = tmp_path / "bad.jsonl"
        output_path.write_text("")
        report_path = tmp_path / "bad.report.json"
        report_path.write_text("not valid json")

        main_window_dlg._on_finished(str(output_path), {})

        log_texts = [main_window_dlg.log_list.item(i).text() for i in range(main_window_dlg.log_list.count())]
        warnings = [t for t in log_texts if "Failed to read report" in t]
        assert len(warnings) == 1


class TestMainWindowOnError:
    def test_logs_error_and_shows_critical(self, main_window_dlg):
        main_window_dlg._on_error("test error message")

        log_texts = [main_window_dlg.log_list.item(i).text() for i in range(main_window_dlg.log_list.count())]
        assert any("test error message" in t for t in log_texts)
        assert any("[ERROR]" in t for t in log_texts)

        QMessageBox.critical.assert_called()


# _try_init_health_bar removed (ADR-0010 Phase 3.5b)
# — health bar holds ProxyHealthTracker from ProxySession construction, no polling needed.

# ═══════════════════════════════════════════════════════════════════════
# AW01-AW02: _AiWorker
# ═══════════════════════════════════════════════════════════════════════


class TestAiWorker:
    def test_success_emits_finished(self, qapp, monkeypatch):
        from astrocrawl.gui.rules_dialog import _AiWorker

        mock_generator = MagicMock()
        mock_generator.generate_sync.return_value = {"name": "test_rule", "fields": {}}

        monkeypatch.setattr("astrocrawl.ai.AIClient", MagicMock())
        monkeypatch.setattr("astrocrawl.ai._provider_registry._discover_provider", MagicMock())
        monkeypatch.setattr("astrocrawl.rules.RuleGenerator", MagicMock(return_value=mock_generator))

        config = MagicMock()
        params = MagicMock()

        worker = _AiWorker("https://example.com", "<html></html>", ["title"], 1, config, params, mode="type")

        result_container: list = []

        def _collect(result):
            result_container.append(result)

        worker.finished.connect(_collect)
        worker.run()

        assert len(result_container) == 1
        assert result_container[0] == {"name": "test_rule", "fields": {}}
        mock_generator.generate_sync.assert_called_once()
        assert mock_generator.generate_sync.call_args.kwargs["mode"] == "type"

    def test_error_emits_error_signal(self, qapp, monkeypatch):
        from astrocrawl.ai._config import AIConfig
        from astrocrawl.gui.rules_dialog import _AiWorker

        mock_generator = MagicMock()
        mock_generator.generate_sync = MagicMock(side_effect=RuntimeError("API failed"))
        monkeypatch.setattr("astrocrawl.ai.AIClient", MagicMock())
        monkeypatch.setattr("astrocrawl.ai._provider_registry._discover_provider", MagicMock())
        monkeypatch.setattr("astrocrawl.rules.RuleGenerator", MagicMock(return_value=mock_generator))

        config = AIConfig(api_key="sk-test")
        params = MagicMock()

        worker = _AiWorker("https://example.com", "<html></html>", ["title"], 1, config, params, mode="type")

        error_container: list = []

        def _collect_error(msg):
            error_container.append(msg)

        worker.error_occurred.connect(_collect_error)
        worker.run()

        assert len(error_container) == 1
        assert "API failed" in error_container[0]


class TestPulseBar:
    """RD01: _PulseBar 动画控件停止时使用 repaint() 而非 update()。"""

    def test_stop_uses_repaint(self, qapp, theme_mgr):
        from unittest.mock import patch

        from astrocrawl.gui._animated_bar import _PulseBar

        bar = _PulseBar()
        bar.start()
        assert bar.is_active() is True

        with patch.object(bar, "repaint") as mock_repaint:
            bar.stop()
            mock_repaint.assert_called_once()

        assert bar.is_active() is False

    def test_paint_disabled_blocks_paint(self, qapp, theme_mgr):
        from unittest.mock import patch

        from astrocrawl.gui._animated_bar import _PulseBar

        bar = _PulseBar()
        bar._paint_disabled = True
        with patch.object(bar, "_paint_bar") as mock_paint:
            bar.repaint()
            mock_paint.assert_not_called()

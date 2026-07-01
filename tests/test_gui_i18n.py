"""i18n 翻译系统专项测试 — en ↔ zh_CN 双语验证。"""

from __future__ import annotations

import pytest

from astrocrawl.gui._i18n import current_locale, install_translator, remove_translators


@pytest.mark.gui
class TestI18nTranslatorLifecycle:
    """翻译器安装/卸载生命周期。"""

    def test_install_zh_cn(self, qapp):
        locale = install_translator(qapp, "zh_CN")
        assert locale == "zh_CN"
        assert current_locale() == "zh_CN"
        remove_translators()

    def test_install_en(self, qapp):
        locale = install_translator(qapp, "en")
        assert locale == "en"
        assert current_locale() == "en"
        remove_translators()

    def test_remove_translators(self, qapp):
        install_translator(qapp, "zh_CN")
        remove_translators()
        assert current_locale() == "zh_CN"  # locale stored, translators removed

    def test_fallback_on_missing_locale(self, qapp):
        locale = install_translator(qapp, "fr_FR")
        assert locale == "fr_FR"
        remove_translators()

    def test_auto_detect_locale(self, qapp):
        """locale_name=None 时从 QLocale.system() 自动检测。"""
        locale = install_translator(qapp, locale_name=None)
        assert isinstance(locale, str)
        assert len(locale) >= 2
        remove_translators()

    def test_qt_translator_missing_logged(self, qapp, caplog):
        """对无 qtbase 翻译的区域，记录 debug 日志。"""
        import logging

        caplog.set_level(logging.DEBUG, logger="astrocrawl.gui.i18n")
        install_translator(qapp, "xx_XX")
        assert "event=qt_translator_missing" in caplog.text
        remove_translators()


@pytest.mark.gui
class TestRemoveTranslatorsEdgeCases:
    """remove_translators 边界情况。"""

    def test_remove_without_app(self):
        """无 QApplication 时 remove_translators 安全返回。"""
        remove_translators()

    def test_remove_without_app_clears_installed(self, monkeypatch):
        """无 QApplication 时 remove_translators 清空 _INSTALLED。"""
        from PySide6.QtCore import QCoreApplication, QTranslator

        from astrocrawl.gui import _i18n

        _i18n._INSTALLED.append(QTranslator())
        monkeypatch.setattr(QCoreApplication, "instance", lambda: None)
        remove_translators()
        assert _i18n._INSTALLED == []


@pytest.mark.gui
class TestZhCNTranslations:
    """zh_CN 翻译器加载后关键 widget 显示中文。"""

    def test_main_window_status_bar_ready(self, qapp, theme_mgr):
        install_translator(qapp, "zh_CN")
        from astrocrawl.gui.main_window import MainWindow

        win = MainWindow()
        assert "就绪" in win._status_bar.text()
        remove_translators()

    def test_main_window_run_button(self, qapp, theme_mgr):
        install_translator(qapp, "zh_CN")
        from astrocrawl.gui.main_window import MainWindow

        win = MainWindow()
        assert win._run_btn.text() == "开始爬取"
        remove_translators()

    def test_main_window_pause_button(self, qapp, theme_mgr):
        install_translator(qapp, "zh_CN")
        from astrocrawl.gui.main_window import MainWindow

        win = MainWindow()
        assert win._pause_btn.text() == "暂停"
        remove_translators()

    def test_advanced_dialog_title(self, qapp, theme_mgr):
        install_translator(qapp, "zh_CN")
        from astrocrawl.gui.advanced_dialog import AdvancedSettingsDialog

        dlg = AdvancedSettingsDialog()
        assert dlg.windowTitle() == "高级设置"
        remove_translators()

    def test_proxy_endpoint_dialog_title(self, qapp, theme_mgr):
        install_translator(qapp, "zh_CN")
        from astrocrawl.gui._proxy_endpoint_dialog import ProxyEndpointEditDialog

        dlg = ProxyEndpointEditDialog(parent=None)
        assert dlg.windowTitle() == "添加代理端点"
        remove_translators()


@pytest.mark.gui
class TestEnFallback:
    """无翻译器时显示英文源文本。"""

    def test_main_window_status_bar_english(self, qapp, theme_mgr):
        from astrocrawl.gui.main_window import MainWindow

        win = MainWindow()
        assert "Ready" in win._status_bar.text()

    def test_main_window_run_button_english(self, qapp, theme_mgr):
        from astrocrawl.gui.main_window import MainWindow

        win = MainWindow()
        assert win._run_btn.text() == "Start Crawl"

    def test_advanced_dialog_title_english(self, qapp, theme_mgr):
        from astrocrawl.gui.advanced_dialog import AdvancedSettingsDialog

        dlg = AdvancedSettingsDialog()
        assert dlg.windowTitle() == "Advanced Settings"

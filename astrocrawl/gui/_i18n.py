"""GUI i18n — QTranslator 生命周期。

QObject 子类使用 self.tr(text)；模块级代码使用 QCoreApplication.translate()。
重启生效模式：切换语言后提示用户重启应用。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QCoreApplication, QLibraryInfo, QLocale, QTranslator

from astrocrawl.utils.logging import LogfmtLogger

_LOG = LogfmtLogger("astrocrawl.gui.i18n")

_INSTALLED: list[QTranslator] = []
_locale: str = "en"


def install_translator(app: QCoreApplication, locale_name: str | None = None) -> str:
    """安装 AstroCrawl + Qt 内置翻译器。返回生效的 locale 名。

    locale_name=None 时从 QLocale.system() 自动检测。
    仅在 translations/ 中找到对应 .qm 时才加载。
    回退 en：无 .qm 时 QTranslator 返回英文源文本。
    """
    global _locale, _INSTALLED

    if locale_name is None:
        locale_name = QLocale.system().name()

    _locale = locale_name
    translations_dir = Path(__file__).parent / "translations"

    # 1. Qt 内置翻译（对话框按钮等）
    qt_translator = QTranslator()
    qt_path = QLibraryInfo.path(QLibraryInfo.TranslationsPath)
    if qt_translator.load("qtbase_" + locale_name, qt_path):
        app.installTranslator(qt_translator)
        _INSTALLED.append(qt_translator)
    else:
        _LOG.debug("qt_translator_missing", locale=locale_name, path=str(qt_path))

    # 2. AstroCrawl 应用翻译
    app_translator = QTranslator()
    if app_translator.load("astrocrawl_gui_" + locale_name, str(translations_dir)):
        app.installTranslator(app_translator)
        _INSTALLED.append(app_translator)
        _LOG.info("app_translator_loaded", locale=locale_name)
    else:
        _LOG.debug("app_translator_missing", locale=locale_name, dir=str(translations_dir))

    return _locale


def remove_translators() -> None:
    """卸载所有翻译器（测试 teardown）。"""
    global _INSTALLED
    app = QCoreApplication.instance()
    if app is None:
        _INSTALLED.clear()
        return
    for t in _INSTALLED:
        app.removeTranslator(t)
    _INSTALLED.clear()


def current_locale() -> str:
    """返回当前 locale，如 'zh_CN' 或 'en'。"""
    return _locale

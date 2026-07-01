from __future__ import annotations

import faulthandler
import logging
import os
import sys
from pathlib import Path

# 允许 python astrocrawl/main.py 直接运行
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "astrocrawl"

from astrocrawl._startup import StartupError, check_dependencies, check_gui_dependencies
from astrocrawl.config import ConfigValidationError
from astrocrawl.utils.logging import setup_root_logger


def main() -> None:
    faulthandler.enable()
    from astrocrawl._packaged import setup as _setup_packaged

    _setup_packaged()
    try:
        check_dependencies()
    except StartupError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    setup_root_logger(logging.INFO)
    if len(sys.argv) > 1:
        try:
            from astrocrawl.cli.main import main_cli

            main_cli()
        except StartupError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        except ConfigValidationError as e:
            print(f"Error: {e}", file=sys.stderr)
            print(
                "Tip: use 'astrocrawl rules disable <rule-name>' to disable conflicting rules and retry",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        try:
            check_gui_dependencies()
        except StartupError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        try:
            from PySide6.QtGui import QIcon
            from PySide6.QtWidgets import QApplication, QProxyStyle, QStyle

            # 定义在 main() 内部：避免模块级 import PySide6（保持 CLI 路径延迟加载）。
            # 仅覆盖 styleHint 虚函数，无信号/槽/属性，QObject 子类在函数作用域内安全。
            class _NoDialogIconStyle(QProxyStyle):
                """Suppress standard icons on QDialogButtonBox buttons (covers QMessageBox, QInputDialog).

                Uses Qt's built-in SH_DialogButtonBox_ButtonsHaveIcons hint rather than a
                stylesheet, avoiding the QSS/QPalette mixing that blocks QPalette propagation.
                """

                def styleHint(self, hint, option=None, widget=None, returnData=None):
                    if hint == QStyle.StyleHint.SH_DialogButtonBox_ButtonsHaveIcons:
                        return 0
                    return super().styleHint(hint, option, widget, returnData)

            from astrocrawl.gui.main_window import MainWindow

            app = QApplication(sys.argv)
            app.setOrganizationName("Etl")
            app.setApplicationName("AstroCrawl")
            app.setStyle(_NoDialogIconStyle("Fusion"))

            from astrocrawl.gui._i18n import install_translator
            from astrocrawl.utils.preferences import get_preferences

            # 必须在 QApplication 之后、widget 创建之前调用
            prefs = get_preferences()
            install_translator(app, locale_name=prefs.get_language())

            icon_path = Path(__file__).parent.parent / "assets" / "etl.ico"
            if icon_path.exists():
                app.setWindowIcon(QIcon(str(icon_path)))

            from astrocrawl.gui.theme import init_theme_manager

            init_theme_manager(app, prefs)
            win = MainWindow()
            win.show()
            sys.exit(app.exec())
        except ConfigValidationError as e:
            print(f"Error: {e}", file=sys.stderr)
            print(
                "Tip: rule config ambiguity — disable conflicting rules in GUI Rules Management and retry",
                file=sys.stderr,
            )
            sys.exit(1)
        except Exception as e:
            print(f"Error: GUI launch failed — {e}", file=sys.stderr)
            print("Tip: no desktop environment? Use CLI mode (astrocrawl --help)", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()

"""应用入口 main() 函数测试。

覆盖 CLI/GUI 分发、启动失败、图标处理、执行顺序。
通过 mock 隔离所有外部依赖（QApplication、MainWindow、CLI）。
使用 patch.dict 注入 sys.modules 以确保测试后自动清理。
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from astrocrawl._startup import StartupError


def _make_cli_modules():
    """构造 CLI mock 模块 dict（用于 patch.dict(sys.modules, ...)）。"""
    mock_cli = MagicMock()
    mock_cli.main_cli = MagicMock()
    return {"astrocrawl.cli.main": mock_cli}


def _make_gui_modules():
    """构造 GUI mock 模块 dict。"""
    mock_qtwidgets = MagicMock()
    mock_qtwidgets.QApplication = MagicMock()
    mock_qtgui = MagicMock()
    mock_qtgui.QIcon = MagicMock()
    mock_main_window = MagicMock()
    mock_main_window.MainWindow = MagicMock()
    mock_prefs = MagicMock()
    mock_prefs.get_preferences = MagicMock(return_value=MagicMock())
    mock_theme = MagicMock()
    mock_theme.init_theme_manager = MagicMock(return_value=MagicMock())

    mock_i18n = MagicMock()
    mock_i18n.install_translator = MagicMock(return_value="en")

    return {
        "PySide6.QtWidgets": mock_qtwidgets,
        "PySide6.QtGui": mock_qtgui,
        "astrocrawl.gui.main_window": mock_main_window,
        "astrocrawl.utils.preferences": mock_prefs,
        "astrocrawl.gui.theme": mock_theme,
        "astrocrawl.gui._i18n": mock_i18n,
    }


# ---------------------------------------------------------------------------
# TestMainCLI
# ---------------------------------------------------------------------------


class TestMainCLI:
    """main() — CLI 分发路径（sys.argv 有参数）。"""

    def test_cli_dispatch_with_args(self):
        """sys.argv > 1 时进入 CLI 分支，调用 main_cli。"""
        fake_modules = _make_cli_modules()

        with patch.dict(sys.modules, fake_modules):
            with patch.object(sys, "argv", ["astrocrawl", "crawl"]):
                with patch("astrocrawl.main.check_dependencies"):
                    with patch("astrocrawl.main.check_gui_dependencies"):
                        with patch("astrocrawl.main.setup_root_logger"):
                            with patch("astrocrawl._packaged.setup"):
                                from astrocrawl.main import main

                                main()

        fake_modules["astrocrawl.cli.main"].main_cli.assert_called_once()

    def test_startup_error_exits_with_message(self, capsys):
        """check_dependencies 失败时打印错误并 sys.exit(1)。"""
        fake_modules = _make_cli_modules()

        with patch.dict(sys.modules, fake_modules):
            with patch.object(sys, "argv", ["astrocrawl", "crawl"]):
                with patch("astrocrawl.main.faulthandler.enable"):
                    with patch("astrocrawl.main.setup_root_logger"):
                        with patch("astrocrawl._packaged.setup"):
                            with patch(
                                "astrocrawl.main.check_dependencies",
                                side_effect=StartupError("缺失依赖"),
                            ):
                                with pytest.raises(SystemExit) as exc:
                                    from astrocrawl.main import main

                                    main()

        assert exc.value.code == 1
        _, err = capsys.readouterr()
        assert "缺失依赖" in err


# ---------------------------------------------------------------------------
# TestMainGUI
# ---------------------------------------------------------------------------


class TestMainGUI:
    """main() — GUI 分发路径（无命令行参数）。"""

    def test_gui_dispatch_no_args(self):
        """sys.argv 只有程序名时进入 GUI 分支。"""
        fake_modules = _make_gui_modules()

        with patch.dict(sys.modules, fake_modules):
            with patch.object(sys, "argv", ["astrocrawl"]):
                with patch("astrocrawl.main.check_dependencies"):
                    with patch("astrocrawl.main.check_gui_dependencies"):
                        with patch("astrocrawl.main.setup_root_logger"):
                            with patch("astrocrawl._packaged.setup"):
                                with patch("pathlib.Path.exists", return_value=False):
                                    from astrocrawl.main import main

                                    with pytest.raises(SystemExit):
                                        main()

        qapp = fake_modules["PySide6.QtWidgets"].QApplication
        qapp.assert_called_once()
        qapp.return_value.setOrganizationName.assert_called_once_with("Etl")
        qapp.return_value.setApplicationName.assert_called_once_with("AstroCrawl")
        qapp.return_value.setStyle.assert_called_once()
        style_call = qapp.return_value.setStyle.call_args[0]
        assert len(style_call) == 1

        mainwin = fake_modules["astrocrawl.gui.main_window"].MainWindow
        mainwin.assert_called_once()
        mainwin.return_value.show.assert_called_once()

        qapp.return_value.exec.assert_called_once()

        get_prefs = fake_modules["astrocrawl.utils.preferences"].get_preferences
        get_prefs.assert_called_once()

        init_theme = fake_modules["astrocrawl.gui.theme"].init_theme_manager
        init_theme.assert_called_once()

    def test_gui_icon_set_when_exists(self):
        """图标文件存在时调用 setWindowIcon。"""
        fake_modules = _make_gui_modules()

        with patch.dict(sys.modules, fake_modules):
            with patch.object(sys, "argv", ["astrocrawl"]):
                with patch("astrocrawl.main.check_dependencies"):
                    with patch("astrocrawl.main.check_gui_dependencies"):
                        with patch("astrocrawl.main.setup_root_logger"):
                            with patch("astrocrawl._packaged.setup"):
                                with patch("pathlib.Path.exists", return_value=True):
                                    from astrocrawl.main import main

                                    with pytest.raises(SystemExit):
                                        main()

        fake_modules["PySide6.QtWidgets"].QApplication.return_value.setWindowIcon.assert_called_once()

    def test_gui_icon_not_set_when_missing(self):
        """图标文件不存在时不调用 setWindowIcon。"""
        fake_modules = _make_gui_modules()

        with patch.dict(sys.modules, fake_modules):
            with patch.object(sys, "argv", ["astrocrawl"]):
                with patch("astrocrawl.main.check_dependencies"):
                    with patch("astrocrawl.main.check_gui_dependencies"):
                        with patch("astrocrawl.main.setup_root_logger"):
                            with patch("astrocrawl._packaged.setup"):
                                with patch("pathlib.Path.exists", return_value=False):
                                    from astrocrawl.main import main

                                    with pytest.raises(SystemExit):
                                        main()

        fake_modules["PySide6.QtWidgets"].QApplication.return_value.setWindowIcon.assert_not_called()


# ---------------------------------------------------------------------------
# TestMainOrdering
# ---------------------------------------------------------------------------


class TestMainOrdering:
    """main() — 执行顺序验证。"""

    def test_gui_exits_with_app_return_code(self):
        """GUI 模式以 app.exec() 返回值退出。"""
        fake_modules = _make_gui_modules()
        ret_code = 0
        fake_modules["PySide6.QtWidgets"].QApplication.return_value.exec.return_value = ret_code

        with patch.dict(sys.modules, fake_modules):
            with patch.object(sys, "argv", ["astrocrawl"]):
                with patch("astrocrawl.main.check_dependencies"):
                    with patch("astrocrawl.main.check_gui_dependencies"):
                        with patch("astrocrawl.main.setup_root_logger"):
                            with patch("astrocrawl._packaged.setup"):
                                with patch("pathlib.Path.exists", return_value=False):
                                    with pytest.raises(SystemExit) as exc:
                                        from astrocrawl.main import main

                                        main()

        assert exc.value.code == ret_code


# ---------------------------------------------------------------------------
# TestMainModule — __main__.py entry point
# ---------------------------------------------------------------------------


class TestMainModule:
    """astrocrawl.__main__ — python -m astrocrawl 入口点。"""

    def test_import_has_no_side_effects(self):
        """导入 __main__ 不应触发 main() 执行（__name__ 守卫）。"""
        with patch("astrocrawl.__main__.main") as mock_main:
            mod_name = "astrocrawl.__main__"
            saved = sys.modules.pop(mod_name, None)
            try:
                import astrocrawl.__main__  # noqa: F401 — 验证导入无副作用
            finally:
                sys.modules.pop(mod_name, None)
                if saved is not None:
                    sys.modules[mod_name] = saved

        mock_main.assert_not_called()

    def test_m_flag_cli_help_returns_zero(self):
        """python -m astrocrawl --help 应返回退出码 0。"""
        import subprocess

        result = subprocess.run(
            [sys.executable, "-m", "astrocrawl", "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, f"stderr:\n{result.stderr}"


# ---------------------------------------------------------------------------
# TestMainCLIStartupError — CLI 分支 StartupError 边界
# ---------------------------------------------------------------------------


class TestMainCLIStartupError:
    """main() — CLI 分支捕获 create_crawler → verify_chromium 的 StartupError。"""

    def test_cli_startup_error_clean_exit(self, capsys):
        """CLI 路径中 StartupError → 干净错误消息 + exit(1)。"""
        fake_modules = _make_cli_modules()

        with patch.dict(sys.modules, fake_modules):
            with patch.object(sys, "argv", ["astrocrawl", "crawl", "https://x.com"]):
                with patch("astrocrawl.main.check_dependencies"):
                    with patch("astrocrawl.main.setup_root_logger"):
                        with patch("astrocrawl._packaged.setup"):
                            with patch("astrocrawl.main.faulthandler.enable"):
                                with patch(
                                    "astrocrawl.cli.main.main_cli",
                                    side_effect=StartupError("chromium 缺失"),
                                ):
                                    from astrocrawl.main import main

                                    with pytest.raises(SystemExit) as exc:
                                        main()
        assert exc.value.code == 1
        _, err = capsys.readouterr()
        assert "chromium 缺失" in err


# ---------------------------------------------------------------------------
# TestMainGUIStartupError — GUI 分支 check_gui_dependencies 异常处理
# ---------------------------------------------------------------------------


class TestMainGUIStartupError:
    """main() — GUI 分支捕获 check_gui_dependencies 的 StartupError。"""

    def test_gui_deps_startup_error_clean_exit(self, capsys):
        """check_gui_dependencies 失败 → 干净错误消息 + exit(1)。"""
        fake_modules = _make_gui_modules()

        with patch.dict(sys.modules, fake_modules):
            with patch.object(sys, "argv", ["astrocrawl"]):
                with patch("astrocrawl.main.check_dependencies"):
                    with patch("astrocrawl.main.setup_root_logger"):
                        with patch("astrocrawl._packaged.setup"):
                            with patch("astrocrawl.main.faulthandler.enable"):
                                with patch(
                                    "astrocrawl.main.check_gui_dependencies",
                                    side_effect=StartupError("PySide6 缺失"),
                                ):
                                    from astrocrawl.main import main

                                    with pytest.raises(SystemExit) as exc:
                                        main()
        assert exc.value.code == 1
        _, err = capsys.readouterr()
        assert "PySide6 缺失" in err

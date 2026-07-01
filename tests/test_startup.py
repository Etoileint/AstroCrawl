"""启动依赖检查测试。

覆盖 check_dependencies、_check_re2、check_gui_dependencies、
verify_chromium 及 StartupError。通过 mock builtins.__import__
和 os 函数控制依赖存在性和浏览器可用性。
"""

from __future__ import annotations

import builtins
import sys
from unittest.mock import MagicMock, patch

import pytest

from astrocrawl._startup import StartupError, _check_re2, check_dependencies, check_gui_dependencies, verify_chromium

# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

_real_import = builtins.__import__


def _make_import_mock(failing_libs):
    """构造 __import__ mock：指定库抛 ImportError，其余正常导入。"""

    def _mock_import(name, *args, **kwargs):
        if name in failing_libs:
            raise ImportError(f"No module named '{name}'")
        return _real_import(name, *args, **kwargs)

    return _mock_import


# ---------------------------------------------------------------------------
# TestStartupError
# ---------------------------------------------------------------------------


class TestStartupError:
    """StartupError 异常类。"""

    def test_is_runtime_error_subclass(self):
        assert issubclass(StartupError, RuntimeError)

    def test_can_be_raised_and_caught(self):
        try:
            raise StartupError("测试错误")
        except StartupError as e:
            assert "测试错误" in str(e)
        else:
            raise AssertionError("应抛出 StartupError")


# ---------------------------------------------------------------------------
# TestCheckDependencies
# ---------------------------------------------------------------------------


class TestCheckDependencies:
    """check_dependencies() — 验证必需 Python 库 + re2。"""

    def test_all_deps_present(self, monkeypatch):
        """所有依赖存在时不抛异常。"""
        monkeypatch.setattr("astrocrawl._startup._check_re2", lambda: None)
        check_dependencies()

    def test_missing_one_dep_raises(self, monkeypatch):
        """单个库缺失时抛出含库名的 StartupError。"""
        monkeypatch.setattr("astrocrawl._startup._check_re2", lambda: None)
        mock_import = _make_import_mock({"aiohttp"})
        with patch.object(builtins, "__import__", mock_import):
            with pytest.raises(StartupError, match="aiohttp"):
                check_dependencies()

    def test_missing_multiple_deps_raises(self, monkeypatch):
        """多个库缺失时错误消息包含所有库名。"""
        monkeypatch.setattr("astrocrawl._startup._check_re2", lambda: None)
        mock_import = _make_import_mock({"bs4", "lxml"})
        with patch.object(builtins, "__import__", mock_import):
            with pytest.raises(StartupError, match="bs4.*lxml"):
                check_dependencies()

    def test_calls_check_re2(self, monkeypatch):
        """验证 _check_re2 被调用。"""
        called = []

        def _spy():
            called.append(True)

        monkeypatch.setattr("astrocrawl._startup._check_re2", _spy)
        check_dependencies()
        assert len(called) == 1

    def test_re2_check_propagates_error(self, monkeypatch):
        """_check_re2 抛异常 → check_dependencies 传播。"""

        def _raise_re2():
            raise StartupError("re2 不可用")

        monkeypatch.setattr("astrocrawl._startup._check_re2", _raise_re2)

        with pytest.raises(StartupError, match="re2"):
            check_dependencies()


# ---------------------------------------------------------------------------
# TestCheckRe2
# ---------------------------------------------------------------------------


class TestCheckRe2:
    """_check_re2() — 验证 google-re2 可导入。"""

    def test_re2_importable(self):
        """re2 已安装时不抛异常。（conftest.py 已验证 re2 存在。）"""
        _check_re2()

    def test_re2_missing_raises(self):
        """re2 缺失时抛出 StartupError。"""
        mock_import = _make_import_mock({"re2"})
        with patch.object(builtins, "__import__", mock_import):
            with pytest.raises(StartupError, match="google-re2"):
                _check_re2()


# ---------------------------------------------------------------------------
# TestCheckGuiDependencies
# ---------------------------------------------------------------------------


class TestCheckGuiDependencies:
    """check_gui_dependencies() — 验证 PySide6 可导入。"""

    def test_pyside6_present(self):
        """PySide6 已安装时不抛异常（当前环境应已安装）。"""
        check_gui_dependencies()

    def test_pyside6_missing_raises(self):
        """PySide6 缺失时抛出 StartupError。"""
        mock_import = _make_import_mock({"PySide6"})
        with patch.object(builtins, "__import__", mock_import):
            with pytest.raises(StartupError, match="PySide6"):
                check_gui_dependencies()


# ---------------------------------------------------------------------------
# TestVerifyChromiumBrowser — env 路径（PLAYWRIGHT_BROWSERS_PATH 已设置）
# ---------------------------------------------------------------------------


class TestVerifyChromiumBrowserEnvPath:
    """verify_chromium() — 打包环境（env var 已设置）。"""

    def test_chromium_found(self, monkeypatch, tmp_path):
        """bundled chromium 可执行文件存在时不抛异常。"""
        browsers_root = tmp_path / "browsers"
        browsers_root.mkdir()
        chrome_dir = browsers_root / "chromium-1234"
        chrome_dir.mkdir()
        chrome_bin = chrome_dir / "chrome-linux" / "chrome"
        chrome_bin.parent.mkdir(parents=True)
        chrome_bin.write_text("fake-binary")

        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(browsers_root))
        verify_chromium()

    def test_no_chromium_dir_raises(self, monkeypatch, tmp_path):
        """browsers_root 下无 chromium-* 目录时抛出。"""
        empty = tmp_path / "empty_root"
        empty.mkdir()
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(empty))
        with pytest.raises(StartupError, match="Chromium 浏览器"):
            verify_chromium()

    def test_listdir_oserror_raises(self, monkeypatch, tmp_path):
        """无法读取 browsers_root 时抛出。"""
        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "/nonexistent/path")
        with pytest.raises(StartupError, match="无法读取"):
            verify_chromium()

    def test_chrome_binary_missing_raises(self, monkeypatch, tmp_path):
        """有 chromium-* 目录但无可执行文件时抛出。"""
        browsers_root = tmp_path / "browsers"
        browsers_root.mkdir()
        chrome_dir = browsers_root / "chromium-1234"
        chrome_dir.mkdir()
        # 不创建 chrome-linux/chrome

        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(browsers_root))
        with pytest.raises(StartupError, match="未找到"):
            verify_chromium()

    def test_win32_platform_path(self, monkeypatch, tmp_path):
        """Windows 平台检查 chrome.exe。"""
        browsers_root = tmp_path / "browsers"
        browsers_root.mkdir()
        chrome_dir = browsers_root / "chromium-1234"
        chrome_dir.mkdir()
        chrome_bin = chrome_dir / "chrome-win" / "chrome.exe"
        chrome_bin.parent.mkdir(parents=True)
        chrome_bin.write_text("fake-exe")

        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(browsers_root))
        monkeypatch.setattr(sys, "platform", "win32")
        verify_chromium()

    def test_darwin_platform_path(self, monkeypatch, tmp_path):
        """macOS 平台检查 Chromium.app。"""
        browsers_root = tmp_path / "browsers"
        browsers_root.mkdir()
        chrome_dir = browsers_root / "chromium-1234"
        chrome_dir.mkdir()
        chrome_bin = chrome_dir / "chrome-mac" / "Chromium.app" / "Contents" / "MacOS" / "Chromium"
        chrome_bin.parent.mkdir(parents=True)
        chrome_bin.write_text("fake-app")

        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(browsers_root))
        monkeypatch.setattr(sys, "platform", "darwin")
        verify_chromium()

    def test_empty_env_var_falls_through_to_dev(self, monkeypatch, tmp_path):
        """PLAYWRIGHT_BROWSERS_PATH="" → falsy → 回退到开发路径。"""
        exe = tmp_path / "chrome"
        exe.write_text("fake")

        mock_pw = MagicMock()
        mock_pw.chromium.executable_path = str(exe)
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_pw)
        mock_ctx.__exit__ = MagicMock(return_value=False)

        monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "")
        monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: mock_ctx)
        verify_chromium()


# ---------------------------------------------------------------------------
# TestVerifyChromiumBrowser — dev 路径（无 env var，使用 playwright）
# ---------------------------------------------------------------------------


class TestVerifyChromiumBrowserDevPath:
    """verify_chromium() — 开发环境（无 env var）。"""

    def test_sync_playwright_exe_found(self, monkeypatch, tmp_path):
        """playwright 返回有效的可执行文件路径时不抛异常。"""
        exe = tmp_path / "chrome"
        exe.write_text("fake")

        mock_pw = MagicMock()
        mock_pw.chromium.executable_path = str(exe)
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_pw)
        mock_ctx.__exit__ = MagicMock(return_value=False)

        monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
        monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: mock_ctx)
        verify_chromium()

    def test_sync_playwright_exe_not_found(self, monkeypatch, tmp_path):
        """playwright 返回不存在的路径时抛出。"""
        mock_pw = MagicMock()
        mock_pw.chromium.executable_path = "/nonexistent/chrome"
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_pw)
        mock_ctx.__exit__ = MagicMock(return_value=False)

        monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
        monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: mock_ctx)
        with pytest.raises(StartupError, match="Chromium 浏览器未安装"):
            verify_chromium()

    def test_sync_playwright_native_file_not_found(self, monkeypatch):
        """playwright driver 不存在 → __enter__ 抛 FileNotFoundError → Chromium 未安装。"""
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(side_effect=FileNotFoundError("driver not found"))
        mock_ctx.__exit__ = MagicMock(return_value=False)

        monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
        monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: mock_ctx)
        with pytest.raises(StartupError, match="Chromium 浏览器未安装"):
            verify_chromium()

    def test_sync_playwright_exception(self, monkeypatch):
        """playwright 上下文管理器异常时抛出。"""
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(side_effect=RuntimeError("内部错误"))
        mock_ctx.__exit__ = MagicMock(return_value=False)

        monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
        monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: mock_ctx)
        with pytest.raises(StartupError, match="Playwright 运行时初始化失败"):
            verify_chromium()

    def test_playwright_import_error(self, monkeypatch):
        """playwright 包未安装时抛出 StartupError。"""
        monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
        mock_import = _make_import_mock({"playwright.sync_api"})
        with patch.object(builtins, "__import__", mock_import):
            with pytest.raises(StartupError, match="pip install playwright"):
                verify_chromium()

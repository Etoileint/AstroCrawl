"""打包环境检测测试。

覆盖 Nuitka/PyInstaller 检测、bundle 路径解析、环境变量设置。
使用 unittest.mock.patch.object 注入 sys 属性（打包环境特有属性）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

from astrocrawl._packaged import get_bundle_dir, is_packaged, setup


class TestIsPackaged:
    """is_packaged() — 仅检测 sys.frozen。"""

    def test_frozen_returns_true(self):
        with patch.object(sys, "frozen", True, create=True):
            assert is_packaged() is True

    def test_not_frozen_returns_false(self):
        assert is_packaged() is False


class TestGetBundleDir:
    """get_bundle_dir() — 三条路径：开发/onefile/standalone。"""

    def test_not_packaged_returns_project_root(self):
        """非打包模式返回项目根目录（包含 astrocrawl 子目录）。"""
        result = get_bundle_dir()
        assert (result / "astrocrawl" / "_packaged.py").is_file()

    def test_meipass_exists_returns_meipass(self):
        with (
            patch.object(sys, "frozen", True, create=True),
            patch.object(sys, "_MEIPASS", "/tmp/_MEI12345", create=True),
        ):
            result = get_bundle_dir()
            assert result == Path("/tmp/_MEI12345")

    def test_standalone_no_meipass_returns_executable_parent(self):
        with (
            patch.object(sys, "frozen", True, create=True),
            patch.object(sys, "executable", "/opt/astrocrawl/bin/astrocrawl"),
        ):
            result = get_bundle_dir()
            assert result == Path("/opt/astrocrawl/bin")


class TestSetup:
    """setup() — 打包环境下设置 PLAYWRIGHT_BROWSERS_PATH。"""

    def test_not_packaged_noop(self, monkeypatch):
        """非打包环境不修改环境变量。"""
        monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
        setup()
        assert "PLAYWRIGHT_BROWSERS_PATH" not in os.environ

    def test_packaged_browsers_dir_exists_sets_env(self, monkeypatch, tmp_path):
        """打包环境且 playwright_browsers 目录存在 → 设置环境变量。"""
        bundle = tmp_path / "bundle"
        bundle.mkdir()
        browsers = bundle / "playwright_browsers"
        browsers.mkdir()

        monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)

        with (
            patch.object(sys, "frozen", True, create=True),
            patch("astrocrawl._packaged.get_bundle_dir", return_value=bundle),
        ):
            setup()

        assert os.environ.get("PLAYWRIGHT_BROWSERS_PATH") == str(browsers)

    def test_packaged_browsers_dir_missing_noop(self, monkeypatch, tmp_path):
        """打包环境但 playwright_browsers 不存在 → 不设置环境变量。"""
        bundle = tmp_path / "bundle"
        bundle.mkdir()

        monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)

        with (
            patch.object(sys, "frozen", True, create=True),
            patch("astrocrawl._packaged.get_bundle_dir", return_value=bundle),
        ):
            setup()

        assert "PLAYWRIGHT_BROWSERS_PATH" not in os.environ

    def test_packaged_browsers_is_file_not_dir_noop(self, monkeypatch, tmp_path):
        """playwright_browsers 是文件而非目录 → 不设置环境变量。"""
        bundle = tmp_path / "bundle"
        bundle.mkdir()
        (bundle / "playwright_browsers").write_text("not-a-dir")

        monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)

        with (
            patch.object(sys, "frozen", True, create=True),
            patch("astrocrawl._packaged.get_bundle_dir", return_value=bundle),
        ):
            setup()

        assert "PLAYWRIGHT_BROWSERS_PATH" not in os.environ

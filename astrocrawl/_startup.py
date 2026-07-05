"""启动依赖检查函数库 — 验证运行时环境。

提供细粒度检查函数，不随任何 import 触发副作用。
失败时抛出 StartupError，由调用方决定处理方式。
"""

from __future__ import annotations

import os
import sys as _sys


class StartupError(RuntimeError):
    """启动依赖检查失败。调用方应 catch 并决定是否 sys.exit(1)。"""


def check_dependencies() -> None:
    """验证所有必需的 Python 库和 google-re2 可用。失败时抛出 StartupError。

    Chromium 浏览器二进制由 create_crawler() 通过 verify_chromium() 单独检查。
    """
    missing = []
    for lib in ("aiohttp", "aiosqlite", "bs4", "lxml", "playwright"):
        try:
            __import__(lib)
        except ImportError:
            missing.append(lib)
    if missing:
        raise StartupError(
            f"缺少必要的依赖库: {', '.join(missing)}\n"
            "请执行: pip install aiohttp aiosqlite beautifulsoup4 lxml playwright\n"
            "可选加速: pip install orjson pydantic"
        )

    _check_re2()


def _check_re2() -> None:
    """验证 google-re2 可用。"""
    try:
        import re2  # noqa: F401
    except ImportError:
        raise StartupError(
            "google-re2 未正确安装。请执行: pip install google-re2\n"
            "若编译安装失败，请确保已安装 build-essential 和 libre2-dev (Linux) "
            "或 cmake (macOS/Windows)。"
        ) from None


def check_gui_dependencies() -> None:
    """验证 GUI 模式所需的 PySide6 可用。失败时抛出 StartupError。"""
    try:
        __import__("PySide6")
    except ImportError:
        raise StartupError("GUI 模式需要 PySide6。请执行: pip install astrocrawl[gui]") from None


def verify_chromium() -> None:
    """验证 Chromium 浏览器可执行文件存在。失败时抛出 StartupError。

    打包模式：检查 PLAYWRIGHT_BROWSERS_PATH 下捆绑的 chromium-*/ 目录。
    开发模式：通过 sync_playwright 启动驱动进程查询可执行文件路径。
    """
    browsers_root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if browsers_root:
        try:
            entries = os.listdir(browsers_root)
        except OSError as e:
            raise StartupError(f"无法读取捆绑的浏览器目录: {browsers_root}") from e
        chromium_dirs = [d for d in entries if d.startswith("chromium-")]
        if not chromium_dirs:
            raise StartupError(f"捆绑目录中未找到 Chromium 浏览器: {browsers_root}")
        if _sys.platform == "win32":
            chrome_rel = os.path.join(chromium_dirs[0], "chrome-win", "chrome.exe")
        elif _sys.platform == "darwin":
            chrome_rel = os.path.join(chromium_dirs[0], "chrome-mac", "Chromium.app", "Contents", "MacOS", "Chromium")
        else:
            chrome_rel = os.path.join(chromium_dirs[0], "chrome-linux", "chrome")
        chrome_path = os.path.join(browsers_root, chrome_rel)
        if not os.path.isfile(chrome_path):
            raise StartupError(f"捆绑的 Chromium 可执行文件未找到: {chrome_path}")
        return

    try:
        from playwright.sync_api import sync_playwright

        try:
            with sync_playwright() as p:
                exe = p.chromium.executable_path
                if not os.path.isfile(exe):
                    raise FileNotFoundError(f"浏览器可执行文件未找到: {exe}")
        except FileNotFoundError as e:
            raise StartupError("Playwright Chromium 浏览器未安装，请运行: playwright install chromium") from e
        except Exception as e:
            raise StartupError(f"Playwright 运行时初始化失败: {e}") from e
    except ImportError as e:
        raise StartupError(
            "Playwright Python 包未正确安装，请运行: pip install playwright && playwright install chromium"
        ) from e

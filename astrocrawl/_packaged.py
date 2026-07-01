"""打包环境检测与路径适配。

打包工具（如 Nuitka）将 Python 应用编译为独立可执行文件。
此模块在应用入口最早被调用，负责检测运行环境并设置
PLAYWRIGHT_BROWSERS_PATH，使 Playwright 能找到捆绑的浏览器。

零外部依赖，可安全在 main() 中作为首条语句导入。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_packaged() -> bool:
    """任意打包环境。"""
    return getattr(sys, "frozen", False)


def get_bundle_dir() -> Path:
    """返回打包应用的根目录。

    --standalone 模式：可执行文件所在目录。
    --onefile 模式：临时解压目录（sys._MEIPASS）。
    开发模式：项目根目录（向上两级）。
    """
    if not is_packaged():
        return Path(__file__).resolve().parent.parent

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)

    return Path(sys.executable).resolve().parent


def setup() -> None:
    """设置打包环境：配置路径与环境变量。

    必须在任何 Playwright 相关代码之前调用。
    非打包环境下为空操作。
    """
    if not is_packaged():
        return

    bundle = get_bundle_dir()

    # Playwright 浏览器路径
    # 构建时通过 --include-data-dir 将 ~/.cache/ms-playwright/ 映射到此目录
    browsers_path = bundle / "playwright_browsers"
    if browsers_path.is_dir():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_path)

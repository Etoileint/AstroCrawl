from __future__ import annotations

import re
import subprocess
import sys
from importlib.metadata import version as pkg_version
from pathlib import Path

from astrocrawl._version import __version__, __version_info__


class TestVersion:
    # PEP 440: [N!]N(.N)*[{a|b|rc}N][.postN][.devN]
    _VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)((?:a|b|rc)\d+)?(\.post\d+)?(\.dev\d+)?$")

    def test_version_pep440_format(self):
        """版本号必须符合 PEP 440 规范。"""
        assert self._VERSION_RE.match(__version__), f"版本号不符合 PEP 440 格式: {__version__}"

    def test_version_info_matches(self):
        """__version_info__ 元组与版本号的基础数字段一致。"""
        m = self._VERSION_RE.match(__version__)
        assert m, f"无法从版本号提取基础数字段: {__version__}"
        base = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        assert __version_info__ == base, f"__version_info__={__version_info__} 与版本号 {__version__} 不匹配"

    def test_package_matches_source(self):
        """安装后的包版本必须与 _version.py 的规范源一致。"""
        assert pkg_version("astrocrawl") == __version__

    def test_version_in_user_agent(self):
        from astrocrawl.config import DEFAULT_USER_AGENT

        assert __version__ in DEFAULT_USER_AGENT
        assert "AstroCrawl" in DEFAULT_USER_AGENT

    def test_version_in_init(self):
        from astrocrawl import __all__ as public_all
        from astrocrawl import __version__ as public_version
        from astrocrawl import __version_info__ as public_info

        assert public_version == __version__
        assert public_info == __version_info__
        assert "__version__" in public_all
        assert "__version_info__" in public_all

    def test_standalone_import(self):
        """_version.py 必须可独立导入（零依赖），确保 setuptools build-time import 不会失败。"""
        version_path = Path(__file__).resolve().parent.parent / "astrocrawl" / "_version.py"
        result = subprocess.run(
            [sys.executable, "-c", f"exec(open({str(version_path)!r}).read()); print(__version__, __version_info__)"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, f"独立导入失败: {result.stderr}"
        assert "0.1.2" in result.stdout

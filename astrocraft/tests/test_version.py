from __future__ import annotations

import re
import subprocess
import sys
from importlib.metadata import version as pkg_version
from pathlib import Path

from astrocraft._version import __version__, __version_info__


class TestVersion:
    _VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)((?:a|b|rc)\d+)?(\.post\d+)?(\.dev\d+)?$")

    def test_version_pep440_format(self):
        assert self._VERSION_RE.match(__version__), f"Version not PEP 440: {__version__}"

    def test_version_info_matches(self):
        m = self._VERSION_RE.match(__version__)
        assert m, f"Cannot parse version: {__version__}"
        base = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        assert __version_info__ == base, f"__version_info__={__version_info__} != {__version__}"

    def test_package_matches_source(self):
        assert pkg_version("astrocraft") == __version__

    def test_version_in_init(self):
        from astrocraft import __all__ as public_all
        from astrocraft import __version__ as public_version
        from astrocraft import __version_info__ as public_info

        assert public_version == __version__
        assert public_info == __version_info__
        assert "__version__" in public_all
        assert "__version_info__" in public_all

    def test_standalone_import(self):
        version_path = Path(__file__).resolve().parent.parent / "astrocraft" / "_version.py"
        result = subprocess.run(
            [sys.executable, "-c", f"exec(open({str(version_path)!r}).read()); print(__version__, __version_info__)"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, f"Standalone import failed: {result.stderr}"
        assert __version__ in result.stdout

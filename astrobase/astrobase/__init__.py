"""AstroBase 天枢 — monorepo 纯机制层，零第三方依赖。"""

from __future__ import annotations

from astrobase._atomic import atomic_write_json
from astrobase._json_compat import json_dumps
from astrobase._logging import JsonLogFormatter, LogfmtFormatter, LogfmtLogger, setup_root_logger
from astrobase._types import AsyncCloseable
from astrobase._version import __version__

__all__ = [
    "AsyncCloseable",
    "JsonLogFormatter",
    "LogfmtFormatter",
    "LogfmtLogger",
    "json_dumps",
    "__version__",
    "atomic_write_json",
    "setup_root_logger",
]

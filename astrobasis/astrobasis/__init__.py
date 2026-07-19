"""AstroBasis 天枢 — monorepo 纯机制层，零第三方依赖。"""

from __future__ import annotations

from astrobasis._atomic import atomic_write_json
from astrobasis._json_compat import json_dumps
from astrobasis._logging import JsonLogFormatter, LogfmtFormatter, LogfmtLogger, setup_root_logger
from astrobasis._types import AsyncCloseable
from astrobasis._version import __version__

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

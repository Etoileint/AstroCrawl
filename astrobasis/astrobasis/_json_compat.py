"""JSON serialization compatibility layer — prefer orjson for speed, fall back to stdlib json.

Strategy: json_dumps always returns bytes ending with \n (JSONL format).
         Callers can write directly to io.BytesIO, no str→bytes decode needed.
Mechanism: orjson is an optional accelerated backend ([fast] extra); transparently
           falls back to stdlib json when not installed.
"""

from __future__ import annotations

import json
from typing import Any

from astrobasis._logging import LogfmtLogger

logger = LogfmtLogger("astrobasis.json_compat")

try:
    import orjson as _json_mod

    def json_dumps(obj: Any) -> bytes:
        return _json_mod.dumps(obj, option=_json_mod.OPT_APPEND_NEWLINE)  # type: ignore[no-any-return]

except ImportError:
    logger.debug("orjson_unavailable", dumper="stdlib")

    def json_dumps(obj: Any) -> bytes:
        return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")

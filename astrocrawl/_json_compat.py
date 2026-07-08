"""JSON 序列化兼容层 — 优先 orjson 加速，回退标准库 json。

策略: _json_dumps 始终返回 bytes 并以 \n 结尾（JSONL 格式）。
      调用方可直接写入 io.BytesIO，无需 str→bytes 编解码。
机制: orjson 为可选加速后端（[fast] extra），未安装时透明回退 stdlib json。
"""

from __future__ import annotations

import json
from typing import Any

from astrocrawl.utils.logging import LogfmtLogger

logger = LogfmtLogger("astrocrawl.json_compat")

try:
    import orjson as _json_mod

    def _json_dumps(obj: Any) -> bytes:
        return _json_mod.dumps(obj, option=_json_mod.OPT_APPEND_NEWLINE)  # type: ignore[no-any-return]

except ImportError:
    logger.debug("orjson_unavailable", dumper="stdlib")

    def _json_dumps(obj: Any) -> bytes:
        return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")

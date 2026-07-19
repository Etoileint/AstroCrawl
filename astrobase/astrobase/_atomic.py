"""POSIX atomic file replacement primitive — mkstemp → write → fsync → os.replace → chmod.

Equivalent to SQLite WAL / PostgreSQL WAL / systemd-journald / Git core.fsync.
All rules engine and preferences JSON write paths use this primitive.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import TYPE_CHECKING, Any

from astrobase._logging import LogfmtLogger

if TYPE_CHECKING:
    from pathlib import Path

logger = LogfmtLogger("astrobase.atomic")


def atomic_write_json(
    path: Path,
    data: Any,
    *,
    max_bytes: int | None = None,
    chmod_mask: int | None = 0o600,
) -> None:
    """POSIX atomic file replacement protocol: mkstemp → write → fsync → os.replace → chmod.

    Args:
        path: Target file path.
        data: json.dumps-compatible object.
        max_bytes: Size check before writing, raises ValueError if exceeded. None to skip.
        chmod_mask: File permission after write. None to skip chmod.
    """
    content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    content_bytes = content.encode("utf-8")

    if max_bytes is not None and len(content_bytes) > max_bytes:
        raise ValueError(f"Data size {len(content_bytes)} exceeds limit {max_bytes}")

    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(suffix=".tmp", prefix=path.name + ".", dir=parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content_bytes)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    if chmod_mask is not None:
        try:
            os.chmod(path, chmod_mask)
        except OSError:
            logger.debug("atomic_write_chmod_failed", path=path)

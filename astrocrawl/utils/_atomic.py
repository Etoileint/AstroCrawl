"""POSIX 原子文件替换原语 — mkstemp → write → fsync → os.replace → chmod。

对标 SQLite WAL / PostgreSQL WAL / systemd-journald / Git core.fsync。
所有规则引擎和偏好设置的 JSON 写入路径统一使用此原语。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger("astrocrawl.utils.atomic")


def atomic_write_json(
    path: Path,
    data: Any,
    *,
    max_bytes: int | None = None,
    chmod_mask: int | None = 0o600,
) -> None:
    """POSIX 原子文件替换协议：mkstemp → write → fsync → os.replace → chmod。

    Args:
        path: 目标文件路径。
        data: json.dumps 兼容对象。
        max_bytes: 写入前大小检查，超限抛 ValueError。None 跳过检查。
        chmod_mask: 写入后文件权限。None 跳过 chmod。
    """
    content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    content_bytes = content.encode("utf-8")

    if max_bytes is not None and len(content_bytes) > max_bytes:
        raise ValueError(f"数据大小 {len(content_bytes)} 超过上限 {max_bytes}")

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
            logger.debug("event=atomic_write_chmod_failed path=%s", path)

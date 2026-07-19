"""规则启用状态管理 — rules_state.json 读写。

启用/禁用状态与规则定义文件解耦：
- 规则文件描述「怎么提取」，不应包含 enabled 字段语义
- rules_state.json 是启用/禁用的唯一真相源
- 状态文件不存在时，回退到规则文件内的 enabled 字段（向后兼容）
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

if sys.platform != "win32":
    import fcntl
else:
    fcntl = None  # type: ignore[assignment]

from astrobase import LogfmtLogger, atomic_write_json
from astrocrawl._types import DEFAULT_EXTRACTION_TYPE

logger = LogfmtLogger("astrocrawl.rules.state")

STATE_FILE = Path.home() / ".astrocrawl" / "rules_state.json"
_MAX_FILE_BYTES = 128 * 1024
_VERSION = 1
_LOCK_SUFFIX = ".lock"


def _acquire_lock(state_file: Path, exclusive: bool = True) -> int | None:
    """获取 fcntl.flock 锁。返回 fd，失败返回 None。Windows 上退化为无锁。"""
    if fcntl is None:
        return None
    lock_file = state_file.with_suffix(state_file.suffix + _LOCK_SUFFIX)
    try:
        fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        return fd
    except OSError:
        logger.warning("lock_acquire_failed", path=lock_file)
        return None


def _release_lock(fd: int | None) -> None:
    """释放 fcntl.flock 锁并关闭 fd。进程终止时内核自动释放。Windows 上为 no-op。"""
    if fd is None or fcntl is None:
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    except OSError:
        pass


def get_disabled_rules(path: Path | None = None) -> tuple[set[str], bool]:
    """返回 (禁用的规则名集合, 状态文件是否存在)。LOCK_SH 保证线性一致读。

    状态文件不存在时返回空集合 + False（向后兼容模式）。
    文件损坏或超大时降级为空集合。
    """
    state_file = path if path is not None else STATE_FILE

    if not state_file.exists():
        return set(), False

    lock_fd = _acquire_lock(state_file, exclusive=False)
    try:
        size = state_file.stat().st_size
        if size > _MAX_FILE_BYTES:
            logger.warning("state_file_oversized", size=size, max=_MAX_FILE_BYTES)
            state_file.unlink(missing_ok=True)
            return set(), False

        data = json.loads(state_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("根类型不是 dict")

        entries = data.get("disabled", [])
        if not isinstance(entries, list):
            raise ValueError("disabled 字段不是 list")

        valid = {e for e in entries if isinstance(e, str) and e}
        return valid, True
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, OSError) as exc:
        logger.warning("state_file_corrupt", error=exc)
        state_file.unlink(missing_ok=True)
        return set(), False
    finally:
        _release_lock(lock_fd)


def set_rule_enabled(name: str, enabled: bool, path: Path | None = None) -> None:
    """设置规则的启用/禁用状态。LOCK_EX 包裹整个 RMW 周期。

    default 规则不可禁用，调用无效。
    """
    if name == DEFAULT_EXTRACTION_TYPE:
        logger.warning("state_default_protected")
        return

    state_file = path if path is not None else STATE_FILE
    lock_fd = _acquire_lock(state_file, exclusive=True)
    try:
        disabled, _ = get_disabled_rules_locked(state_file)

        if enabled:
            disabled.discard(name)
        else:
            disabled.add(name)

        _save(disabled, path)
    finally:
        _release_lock(lock_fd)


def set_rules_enabled(name_to_enabled: dict[str, bool], path: Path | None = None) -> None:
    """批量设置规则启用/禁用状态。一次锁、一次原子写入。

    default 规则不可禁用，自动跳过。
    """
    state_file = path if path is not None else STATE_FILE
    lock_fd = _acquire_lock(state_file, exclusive=True)
    try:
        disabled, _ = get_disabled_rules_locked(state_file)
        for name, enabled in name_to_enabled.items():
            if name == DEFAULT_EXTRACTION_TYPE:
                continue
            if enabled:
                disabled.discard(name)
            else:
                disabled.add(name)
        _save(disabled, path)
    finally:
        _release_lock(lock_fd)


def get_disabled_rules_locked(state_file: Path) -> tuple[set[str], bool]:
    """无锁版本的 get_disabled_rules——内层函数，调用方持有锁。"""
    if not state_file.exists():
        return set(), False

    try:
        size = state_file.stat().st_size
        if size > _MAX_FILE_BYTES:
            logger.warning("state_file_oversized", size=size, max=_MAX_FILE_BYTES)
            state_file.unlink(missing_ok=True)
            return set(), False

        data = json.loads(state_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("根类型不是 dict")

        entries = data.get("disabled", [])
        if not isinstance(entries, list):
            raise ValueError("disabled 字段不是 list")

        valid = {e for e in entries if isinstance(e, str) and e}
        return valid, True
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, OSError) as exc:
        logger.warning("state_file_corrupt", error=exc)
        state_file.unlink(missing_ok=True)
        return set(), False


def _save(disabled: set[str], path: Path | None = None) -> None:
    data = {"version": _VERSION, "disabled": sorted(disabled)}
    target = path if path is not None else STATE_FILE
    atomic_write_json(target, data)

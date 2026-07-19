"""插件状态独立持久化 — plugin-state.json（ADR-0011 决策 10）。

独立于 preferences.json：丢失 API key 不可接受，损坏时保留原文件 + 尝试 .bak 恢复。
fcntl.flock 串行化多进程读-改-写，atomic_write_json 保证崩溃安全。
"""

from __future__ import annotations

import copy
import fcntl
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from astrobasis import LogfmtLogger, atomic_write_json

log = LogfmtLogger("astroframe.state")

PLUGIN_STATE_DIR = Path.home() / ".astroframe"
PLUGIN_STATE_FILE = PLUGIN_STATE_DIR / "plugin-state.json"
PLUGIN_STATE_BAK = PLUGIN_STATE_DIR / "plugin-state.json.bak"

MAX_FILE_BYTES = 256 * 1024
MAX_SECRET_PREVIEW = 8

_DEFAULT_STATE: dict[str, Any] = {
    "require_approval": "all",
    "disabled": [],
    "trusted_capabilities": {},
    "configs": {},
}


def _secret_mask(value: str) -> str:
    """敏感值脱敏：前 8 字符 + '...'。"""
    if len(value) <= MAX_SECRET_PREVIEW:
        return value[:2] + "..."
    return value[:MAX_SECRET_PREVIEW] + "..."


def _mask_configs(configs: dict[str, Any]) -> dict[str, Any]:
    """递归脱敏 configs 中的所有值——仅保留前 8 字符供日志。"""
    masked: dict[str, Any] = {}
    for plugin_name, cfg in configs.items():
        if isinstance(cfg, dict):
            masked[plugin_name] = {k: _secret_mask(str(v)) if isinstance(v, str) else v for k, v in cfg.items()}
        else:
            masked[plugin_name] = _secret_mask(str(cfg)) if isinstance(cfg, str) else cfg
    return masked


def _read_state_file(path: Path) -> dict[str, Any] | None:
    """读取并解析状态文件，损坏时返回 None。"""
    try:
        with open(path, "rb") as f:
            raw = f.read()
        if len(raw) > MAX_FILE_BYTES:
            log.warning("plugin_state_oversize", path=str(path), size=len(raw))
            return None
        result: dict[str, Any] = json.loads(raw.decode("utf-8"))
        return result
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        log.warning("plugin_state_corrupt", path=str(path), error=str(exc))
        return None


def _acquire_flock(lock_path: Path) -> int:
    """获取排他文件锁的文件描述符。锁文件与状态文件分离——atomic_write_json 的
    os.replace 会改变状态文件 inode，而锁文件 inode 不变，锁持续有效。"""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def _release_flock(fd: int) -> None:
    """释放 flock 并关闭文件描述符。"""
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        os.close(fd)
    except OSError:
        pass


class PluginState:
    """插件状态管理器 — plugin-state.json 的读写门面。

    并发安全：fcntl.flock(LOCK_EX) 串行化读-改-写。
    崩溃安全：atomic_write_json (mkstemp → fsync → os.replace)。
    恢复策略：主文件损坏 → 尝试 .bak → 回默认值。
    """

    def __init__(self, file_path: Path | None = None) -> None:
        self._file = file_path or PLUGIN_STATE_FILE
        self._bak = Path(str(self._file) + ".bak")
        self._lock = Path(str(self._file) + ".lock")
        self._file.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, Any]:
        """加载当前状态。主文件损坏时尝试 .bak 恢复，均失败时返回默认值。"""
        state = _read_state_file(self._file)
        if state is not None:
            return self._fill_defaults(state)

        log.warning("plugin_state_primary_corrupt", trying_bak=str(self._bak))
        state = _read_state_file(self._bak)
        if state is not None:
            log.info("plugin_state_bak_recovered")
            try:
                atomic_write_json(self._file, state, max_bytes=MAX_FILE_BYTES, chmod_mask=0o600)
            except OSError as exc:
                log.error("plugin_state_bak_writeback_failed", error=str(exc))
            return self._fill_defaults(state)

        log.warning("plugin_state_default_fallback")
        default = copy.deepcopy(_DEFAULT_STATE)
        try:
            atomic_write_json(self._file, default, max_bytes=MAX_FILE_BYTES, chmod_mask=0o600)
        except OSError as exc:
            log.error("plugin_state_default_write_failed", error=str(exc))
        return default

    def save(self, state: dict[str, Any]) -> None:
        """原子写入状态到主文件（flock 串行化 + atomic_write_json）。

        调用者负责 load-modify-save 的原子性——若需要读-改-写串行化，
        请在外部获取锁后调用 _save_locked()，或使用 _modify()。
        """
        state.setdefault("require_approval", _DEFAULT_STATE["require_approval"])
        state.setdefault("disabled", [])
        state.setdefault("trusted_capabilities", {})
        state.setdefault("configs", {})

        log.debug(
            "plugin_state_save",
            configs_count=len(state.get("configs", {})),
            trusted_count=len(state.get("trusted_capabilities", {})),
            disabled_count=len(state.get("disabled", [])),
            masked_configs=str(_mask_configs(state.get("configs", {}))),
        )

        fd = _acquire_flock(self._lock)
        try:
            self._save_locked(state)
        finally:
            _release_flock(fd)

    def _save_locked(self, state: dict[str, Any]) -> None:
        """写入主文件和 .bak 副本——调用者必须已持有 flock。"""
        try:
            atomic_write_json(self._bak, state, max_bytes=MAX_FILE_BYTES, chmod_mask=0o600)
        except OSError as exc:
            log.warning("plugin_state_bak_write_failed", error=str(exc))
        atomic_write_json(self._file, state, max_bytes=MAX_FILE_BYTES, chmod_mask=0o600)

    def _modify(self, update_fn: Any) -> None:
        """读-改-写原子操作：持锁 → load → 修改 → 写入 → 释放。

        消除 load() 与 save() 之间的 TOCTOU 窗口。
        """
        fd = _acquire_flock(self._lock)
        try:
            state = self.load()
            update_fn(state)
            state.setdefault("require_approval", _DEFAULT_STATE["require_approval"])
            state.setdefault("disabled", [])
            state.setdefault("trusted_capabilities", {})
            state.setdefault("configs", {})
            self._save_locked(state)
        finally:
            _release_flock(fd)

    # ── require_approval 策略 ──────────────────────────────────────────────────

    def get_require_approval(self) -> str:
        return str(self.load().get("require_approval", "all"))

    def set_require_approval(self, policy: str) -> None:
        if policy not in ("all", "dangerous", "none"):
            raise ValueError(f"Invalid require_approval policy: {policy!r}")
        self._modify(lambda s: s.update({"require_approval": policy}))

    # ── disabled 管理 ──────────────────────────────────────────────────────────

    def is_disabled(self, plugin_name: str) -> bool:
        return plugin_name in self.load().get("disabled", [])

    def set_disabled(self, plugin_name: str, disabled: bool = True) -> None:
        def _update(state: dict[str, Any]) -> None:
            disabled_list: list[str] = list(state.get("disabled", []))
            if disabled and plugin_name not in disabled_list:
                disabled_list.append(plugin_name)
            elif not disabled and plugin_name in disabled_list:
                disabled_list.remove(plugin_name)
            state["disabled"] = disabled_list

        self._modify(_update)

    # ── 信任记录 ───────────────────────────────────────────────────────────────

    def get_trusted(self, capability_key: str) -> dict[str, Any] | None:
        """获取 capability 的信任记录。key = f"{package_name}/{cap_name}"。"""
        trusted: dict[str, Any] = self.load().get("trusted_capabilities", {})
        result: dict[str, Any] | None = trusted.get(capability_key)
        return result

    def set_trusted(
        self,
        capability_key: str,
        granted_permissions: list[str],
        granted_version: str,
        granted_hash: str,
        *,
        allow_deprecated: bool = False,
    ) -> None:
        def _update(state: dict[str, Any]) -> None:
            record: dict[str, Any] = {
                "granted_permissions": granted_permissions,
                "granted_version": granted_version,
                "granted_hash": granted_hash,
                "granted_at": datetime.now(timezone.utc).isoformat(),
            }
            if allow_deprecated:
                record["allow_deprecated"] = True
            state["trusted_capabilities"][capability_key] = record

        self._modify(_update)

    def remove_trusted(self, capability_key: str) -> None:
        self._modify(lambda s: s["trusted_capabilities"].pop(capability_key, None))

    # ── 配置 CRUD ──────────────────────────────────────────────────────────────

    def get_config(self, plugin_name: str) -> dict[str, Any]:
        configs: dict[str, Any] = self.load().get("configs", {})
        result: dict[str, Any] = configs.get(plugin_name, {})
        return result

    def set_config(self, plugin_name: str, config: dict[str, Any]) -> None:
        def _update(state: dict[str, Any]) -> None:
            state.setdefault("configs", {})[plugin_name] = config

        self._modify(_update)

    # ── 僵尸条目清理（ADR-0011 S19）─────────────────────────────────────────────

    def clean_zombie_disabled(self, known_packages: set[str]) -> list[str]:
        """移除 entry_points 中不存在的包的 disabled 条目。

        S19 显式覆盖 configs + trusted_capabilities；disabled 具有相同语义——
        已卸载插件不应在 disabled 列表中残留，避免 enable --all 展示幽灵条目。
        """
        zombie_names: list[str] = []

        def _update(state: dict[str, Any]) -> None:
            nonlocal zombie_names
            disabled_list: list[str] = state.get("disabled", [])
            zombie_names = [n for n in disabled_list if n not in known_packages]
            if zombie_names:
                state["disabled"] = [n for n in disabled_list if n not in zombie_names]

        self._modify(_update)
        if zombie_names:
            log.info("plugin_zombie_disabled_cleaned", count=len(zombie_names), packages=",".join(zombie_names))
        return zombie_names

    def clean_zombie_configs(self, known_packages: set[str]) -> list[str]:
        """移除 entry_points 中不存在的包的 configs。返回被清理的包名列表。"""
        zombie_keys: list[str] = []

        def _update(state: dict[str, Any]) -> None:
            nonlocal zombie_keys
            configs = state.get("configs", {})
            zombie_keys = [k for k in configs if k not in known_packages]
            if zombie_keys:
                for k in zombie_keys:
                    del configs[k]
                state["configs"] = configs

        self._modify(_update)
        if zombie_keys:
            log.info("plugin_zombie_configs_cleaned", count=len(zombie_keys), packages=",".join(zombie_keys))
        return zombie_keys

    def classify_zombie_trusted(self, known_packages: set[str], grace_days: int = 30) -> tuple[list[str], list[str]]:
        """分类僵尸信任记录（只读，不修改状态）。

        ADR-0011 S19：卸载后 trusted_capabilities 保留 30 天宽限期。
        宽限期从僵尸检测时间 (_zombie_detected_at) 起算，非信任授予时间。

        Returns:
            (expired_keys, in_grace_keys) — expired 已超宽限期可清理，in_grace 尚在宽限期内。
        """
        state = self.load()
        trusted = state.get("trusted_capabilities", {})
        now = time.time()
        grace_seconds = grace_days * 86400
        expired: list[str] = []
        in_grace: list[str] = []

        for key, record in trusted.items():
            pkg = key.split("/", 1)[0]
            if pkg in known_packages:
                continue

            zombie_detected = record.get("_zombie_detected_at")
            if zombie_detected is not None:
                elapsed = max(0.0, now - float(zombie_detected))
                if elapsed > grace_seconds:
                    expired.append(key)
                else:
                    in_grace.append(key)
                continue

            granted_at_str = record.get("granted_at", "")
            try:
                granted_ts = datetime.fromisoformat(granted_at_str).timestamp()
            except (ValueError, OSError):
                in_grace.append(key)
                continue

            if now - granted_ts > grace_seconds:
                expired.append(key)
            else:
                in_grace.append(key)

        return expired, in_grace

    def clean_zombie_trusted(self, known_packages: set[str], grace_days: int = 30) -> list[str]:
        """移除宽限期外仍不存在的包的信任记录。返回被清理的 key 列表。

        宽限期从僵尸检测时间 (_zombie_detected_at) 起算，非信任授予时间。
        时钟回拨防御：max(0, now - detection_ts) 避免负值。
        """
        zombie_keys: list[str] = []
        now = time.time()
        grace_seconds = grace_days * 86400

        def _update(state: dict[str, Any]) -> None:
            nonlocal zombie_keys
            trusted = state.get("trusted_capabilities", {})
            for key, record in list(trusted.items()):
                pkg = key.split("/", 1)[0]
                if pkg not in known_packages:
                    zombie_detected = record.get("_zombie_detected_at")
                    if zombie_detected is not None:
                        elapsed = max(0.0, now - float(zombie_detected))
                        if elapsed > grace_seconds:
                            zombie_keys.append(key)
                        continue

                    granted_at_str = record.get("granted_at", "")
                    try:
                        granted_dt = datetime.fromisoformat(granted_at_str)
                        granted_ts = granted_dt.timestamp()
                    except (ValueError, OSError):
                        record["_zombie_detected_at"] = now
                        trusted[key] = record
                        continue

                    if now - granted_ts > grace_seconds:
                        zombie_keys.append(key)
                    else:
                        record["_zombie_detected_at"] = now
                        trusted[key] = record

            if zombie_keys:
                for k in zombie_keys:
                    del trusted[k]
                state["trusted_capabilities"] = trusted

        self._modify(_update)
        if zombie_keys:
            log.info("plugin_zombie_trusted_cleaned", count=len(zombie_keys), keys=",".join(zombie_keys))
        return zombie_keys

    # ── helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _fill_defaults(state: dict[str, Any]) -> dict[str, Any]:
        """补全缺失的顶层键为默认值。deepcopy 防止修改 _DEFAULT_STATE 的可变默认值。"""
        for key, default in _DEFAULT_STATE.items():
            if key not in state:
                state[key] = copy.deepcopy(default)
        return state

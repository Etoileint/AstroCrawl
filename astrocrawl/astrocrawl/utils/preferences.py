"""用户偏好持久化 — 路径记忆 + 主题配置 + AI Profile 管理（ADR-0007）+ Proxy Profile 管理（ADR-0010）。

原子写入（atomic_write_json），损坏或超大文件自动丢弃。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional, cast

from astrobasis import LogfmtLogger, atomic_write_json
from astrocrawl.ai._profile import AIProfile
from astrocrawl.proxy._config import ParsedProxy, ProxyAuth, ProxyProfile, ProxyType, endpoint_key

_log = LogfmtLogger("astrocrawl.preferences")

# ── storage layout ──────────────────────────────────────────────────────────
PREFERENCES_DIR = Path.home() / ".astrocrawl"
PREFERENCES_FILE = PREFERENCES_DIR / "preferences.json"
OLD_PATH_MEMORY_FILE = PREFERENCES_DIR / "path_memory.json"

# ── safety limits ───────────────────────────────────────────────────────────
MAX_ENTRIES_PER_KEY = 20
MAX_FILE_BYTES = 128 * 1024
MAX_PAYLOAD_BYTES = 64 * 1024

# ── proxy node parsing ──────────────────────────────────────────────────────
_PROXY_TYPE_NAMES = tuple(t.name for t in ProxyType)

# ── theme defaults ──────────────────────────────────────────────────────────
DEFAULT_THEME = {"mode": "light", "base": "light", "overrides": {}}
VALID_MODES = {"light", "dark", "custom"}
VALID_BASES = {"light", "dark"}


def _validate_theme(theme: dict) -> dict:
    """校验并规范化 theme 数据，非法值回退到默认。"""
    if not isinstance(theme, dict):
        return dict(DEFAULT_THEME)
    mode = theme.get("mode", "light")
    base = theme.get("base", "light")
    overrides = theme.get("overrides", {})
    if mode not in VALID_MODES:
        mode = "light"
    if base not in VALID_BASES:
        base = "light"
    if not isinstance(overrides, dict):
        overrides = {}
    overrides = {k: v for k, v in overrides.items() if isinstance(k, str) and isinstance(v, str)}
    return {"mode": mode, "base": base, "overrides": overrides}


class Preferences:
    """持久化有界偏好存储 — 路径记忆 + 主题。

    每类 key（如 ``"output"``, ``"proxy"``）维护独立的 MRU 列表。
    磁盘文件原子写入（write-then-rename），加载时校验。
    """

    __slots__ = ("_data", "_loaded")

    def __init__(self) -> None:
        self._data: Dict[str, Any] = {
            "path_memory": {},
            "theme": dict(DEFAULT_THEME),
            "ai_profiles": [],
            "ai_active_profile": "",
            "ai_last_profile": {},
            "rules_auto_update": True,
            "trace_rules": False,
            "log_level": "INFO",
            "output_gzip": False,
            "clear_context_cookies": False,
            "rules_dirs": [],
            "rules_dirs_collapsed": True,
            "rules_dirs_enabled": True,
            "proxy_profiles": [],
            "proxy_last_used": {},
            "language": "zh_CN",
        }
        self._loaded = False

    # ── path memory API ────────────────────────────────────────────────────

    def get_last_dir(self, key: str, fallback: str = "") -> str:
        """返回 *key* 对应的最近存在目录，或 *fallback*。"""
        self._load()
        for p in self._data.get("path_memory", {}).get(key, []):
            if Path(p).is_dir():
                return cast("str", p)
        return fallback

    def add_path(self, key: str, path: str) -> None:
        """记录 *path* 为 *key* 的最新条目。"""
        if not path:
            return
        self._load()
        pm = self._data.setdefault("path_memory", {})
        paths: list = pm.get(key, [])
        paths = [p for p in paths if p != path]
        paths.insert(0, path)
        pm[key] = paths[:MAX_ENTRIES_PER_KEY]
        self._save()

    # ── theme API ──────────────────────────────────────────────────────────

    def get_theme(self) -> dict:
        """返回主题配置 ``{"mode": str, "base": str, "overrides": dict}``。"""
        self._load()
        return dict(self._data["theme"])

    def set_theme(self, mode: str, base: str, overrides: dict) -> None:
        """写入主题配置并持久化。"""
        self._load()
        self._data["theme"] = _validate_theme({"mode": mode, "base": base, "overrides": overrides})
        self._save()

    # ── language API ───────────────────────────────────────────────────────

    def get_language(self) -> str:
        """返回 GUI 语言，如 'zh_CN' 或 'en'。"""
        self._load()
        return cast("str", self._data.get("language", "zh_CN"))

    def set_language(self, lang: str) -> None:
        """设置 GUI 语言并持久化。"""
        self._load()
        self._data["language"] = lang
        self._save()

    # ── AI Profile CRUD API (ADR-0007) ────────────────────────────────────

    def _get_active_profile_dict(self) -> dict:
        name = self._data.get("ai_active_profile", "")
        for p in self._data["ai_profiles"]:
            if p.get("name") == name:
                return cast("dict[str, Any]", p)
        return cast("dict[str, Any]", self._data["ai_profiles"][0]) if self._data["ai_profiles"] else {}

    def get_active_ai_profile(self) -> AIProfile | None:
        """返回当前活跃 AIProfile。"""
        self._load()
        d = self._get_active_profile_dict()
        return AIProfile.from_dict(d) if d else None

    def get_active_profile_name(self) -> str:
        """返回当前活跃 profile 名称。"""
        self._load()
        return cast("str", self._data.get("ai_active_profile", ""))

    def get_ai_profile(self, name: str) -> AIProfile | None:
        """按名称返回 AIProfile。"""
        self._load()
        for p in self._data.get("ai_profiles", []):
            if p.get("name") == name:
                return AIProfile.from_dict(p)
        return None

    def set_active_ai_profile(self, name: str) -> None:
        """设置活跃 profile。"""
        self._load()
        for p in self._data["ai_profiles"]:
            if p.get("name") == name:
                self._data["ai_active_profile"] = name
                self._save()
                return

    def get_ai_profiles(self) -> list[AIProfile]:
        """返回所有 AI profile（按存储顺序）。"""
        self._load()
        return [AIProfile.from_dict(p) for p in self._data["ai_profiles"]]

    def get_ai_profile_names(self) -> list[str]:
        """返回所有 AI profile 名称列表。"""
        self._load()
        return [p["name"] for p in self._data["ai_profiles"]]

    def save_ai_profile(self, profile: AIProfile) -> None:
        """创建或更新 profile（按 name 匹配），原子写入。"""
        self._load()
        d = profile.to_dict()
        for i, p in enumerate(self._data["ai_profiles"]):
            if p.get("name") == profile.name:
                self._data["ai_profiles"][i] = d
                self._save()
                return
        self._data["ai_profiles"].append(d)
        if len(self._data["ai_profiles"]) == 1 and not self._data.get("ai_active_profile"):
            self._data["ai_active_profile"] = profile.name
        self._save()

    def remove_ai_profile(self, name: str) -> None:
        """删除 profile，清理关联引用。"""
        self._load()
        self._data["ai_profiles"] = [p for p in self._data["ai_profiles"] if p.get("name") != name]
        # 清理 ai_last_profile 引用
        last = dict(self._data.get("ai_last_profile", {}))
        for module_key, profile_name in list(last.items()):
            if profile_name == name:
                last[module_key] = ""
        self._data["ai_last_profile"] = last
        # 若活跃 profile 被删除，回退到第一个剩余 profile，无剩余则清空
        if self._data.get("ai_active_profile") == name:
            remaining = self._data["ai_profiles"]
            self._data["ai_active_profile"] = remaining[0]["name"] if remaining else ""
        self._save()

    # ── C-mode: per-module last-used profile (ADR-0007 决策 4) ───────────

    def get_last_ai_profile(self, module: str) -> str | None:
        """返回模块上次使用的 profile 名（空字符串视为 None）。"""
        self._load()
        name: str = self._data.get("ai_last_profile", {}).get(module, "")
        if name and any(p.get("name") == name for p in self._data.get("ai_profiles", [])):
            return name
        return None

    def set_last_ai_profile(self, module: str, profile_name: str) -> None:
        """记录模块最后使用的 profile。"""
        self._load()
        last = dict(self._data.get("ai_last_profile", {}))
        last[module] = profile_name
        self._data["ai_last_profile"] = last
        self._save()

    # ── Proxy Profile CRUD API (ADR-0010 决策 7) ────────────────────────────

    def get_proxy_profiles(self) -> list[ProxyProfile]:
        """返回所有 ProxyProfile（按存储顺序）。"""
        self._load()
        return [ProxyProfile.from_dict(p) for p in self._data.get("proxy_profiles", [])]

    def get_proxy_profile_names(self) -> list[str]:
        """返回所有 ProxyProfile 名称列表。"""
        self._load()
        return [p.get("name", "") for p in self._data.get("proxy_profiles", [])]

    def get_proxy_profile(self, name: str) -> ProxyProfile | None:
        """按名称返回 ProxyProfile，不存在返回 None。"""
        self._load()
        for p in self._data.get("proxy_profiles", []):
            if p.get("name") == name:
                return ProxyProfile.from_dict(p)
        return None

    def save_proxy_profile(self, profile: ProxyProfile) -> None:
        """创建或更新 ProxyProfile（按 name 匹配），原子写入。

        端点在 Profile 内按 type:host:port 唯一——重复抛 ValueError。
        """
        self._load()
        # 端点去重校验
        seen: dict[str, int] = {}
        for i, ep in enumerate(profile.proxies):
            key = endpoint_key(ep)
            if key in seen:
                first = profile.proxies[seen[key]]
                raise ValueError(
                    f"Profile '{profile.name}' 端点重复: {key} "
                    f"(索引 {seen[key]} 与 {i}，label='{first.label}' 与 '{ep.label}')"
                )
            seen[key] = i
        d = profile.to_dict()
        for i, p in enumerate(self._data.get("proxy_profiles", [])):
            if p.get("name") == profile.name:
                self._data["proxy_profiles"][i] = d
                self._save()
                return
        self._data.setdefault("proxy_profiles", []).append(d)
        self._save()

    def remove_proxy_profile(self, name: str, *, force: bool = False) -> None:
        """删除 ProxyProfile，清理关联引用。"""
        self._load()
        # 找到目标 profile 的 uuid（在删除前）
        target_uuid = ""
        for p in self._data.get("proxy_profiles", []):
            if p.get("name") == name:
                target_uuid = p.get("uuid", "")
                break
        self._data["proxy_profiles"] = [p for p in self._data.get("proxy_profiles", []) if p.get("name") != name]
        # 按 uuid 清理 proxy_last_used 引用
        last = dict(self._data.get("proxy_last_used", {}))
        for consumer_key, entry in list(last.items()):
            if isinstance(entry, dict) and entry.get("profile") == target_uuid:
                last[consumer_key] = {"profile": "", "node": ""}
        self._data["proxy_last_used"] = last
        self._save()

    def get_proxy_last_used(self, consumer: str) -> dict | None:
        """返回 consumer 的代理分配 ``{"profile": uuid, "node": "TYPE:host:port"}`` 或 None。"""
        self._load()
        entry = self._data.get("proxy_last_used", {}).get(consumer)
        if not isinstance(entry, dict):
            return None
        profile_uuid = entry.get("profile", "")
        if not profile_uuid:
            return None
        # 验证 profile 仍存在
        if not any(p.get("uuid") == profile_uuid for p in self._data.get("proxy_profiles", [])):
            last = dict(self._data.get("proxy_last_used", {}))
            last[consumer] = {"profile": "", "node": ""}
            self._data["proxy_last_used"] = last
            self._save()
            return None
        return {"profile": profile_uuid, "node": entry.get("node", "")}

    def set_proxy_last_used(self, consumer: str, profile_uuid: str, node: str = "") -> None:
        """记录 consumer 的代理分配（profile uuid + 节点 TYPE:host:port）。"""
        self._load()
        last = dict(self._data.get("proxy_last_used", {}))
        last[consumer] = {"profile": profile_uuid, "node": node}
        self._data["proxy_last_used"] = last
        self._save()

    def _get_proxy_profile_by_uuid(self, uuid_str: str) -> ProxyProfile | None:
        """按 uuid 查找 ProxyProfile。"""
        self._load()
        for p in self._data.get("proxy_profiles", []):
            if p.get("uuid") == uuid_str:
                return ProxyProfile.from_dict(p)
        return None

    def get_parsed_proxy_for(self, consumer: str) -> ParsedProxy | None:
        """读取 consumer 的 proxy_last_used，定位对应的 ParsedProxy。

        纯同步、纯数据定位——不做网络探测。调用方自行传给模块传输层。
        """
        self._load()
        entry = self.get_proxy_last_used(consumer)
        if not entry:
            return None
        profile_uuid = entry.get("profile", "")
        node = entry.get("node", "")
        if not profile_uuid or not node:
            return None
        profile = self._get_proxy_profile_by_uuid(profile_uuid)
        if profile is None:
            return None
        # 解析 node 字符串，兼容新旧格式
        # 新格式: TYPE:host:port（如 HTTP:127.0.0.1:8080）
        # 旧格式: host:port（如 127.0.0.1:8080）——迁移前遗留数据
        try:
            if ":" in node and node.split(":", 1)[0] in _PROXY_TYPE_NAMES:
                # 新格式 — 按 type:host:port 精确匹配
                type_name, rest = node.split(":", 1)
                host, port_str = rest.rsplit(":", 1)
                port = int(port_str)
                match_type = True
            else:
                # 旧格式 — 按 host:port 匹配（取第一个）
                host, port_str = node.rsplit(":", 1)
                port = int(port_str)
                type_name = ""
                match_type = False
        except (ValueError, AttributeError):
            return None
        for spec in profile.proxies:
            host_match = spec.host == host and spec.port == port
            type_match = not match_type or spec.type.name == type_name
            if host_match and type_match:
                return ParsedProxy(
                    type=spec.type,
                    host=spec.host,
                    port=spec.port,
                    auth=ProxyAuth(username=spec.username, password=spec.password),
                    weight=spec.weight,
                )
        return None

    # ── 全局设置 ────────────────────────────────────────────────────────────

    def get_rules_auto_update(self) -> bool:
        self._load()
        return cast("bool", self._data.get("rules_auto_update", True))

    def set_rules_auto_update(self, val: bool) -> None:
        self._load()
        self._data["rules_auto_update"] = val
        self._save()

    def get_trace_rules(self) -> bool:
        self._load()
        return cast("bool", self._data.get("trace_rules", False))

    def set_trace_rules(self, val: bool) -> None:
        self._load()
        self._data["trace_rules"] = val
        self._save()

    def get_log_level(self) -> str:
        self._load()
        return cast("str", self._data.get("log_level", "INFO"))

    def set_log_level(self, val: str) -> None:
        self._load()
        self._data["log_level"] = val
        self._save()

    def get_output_gzip(self) -> bool:
        self._load()
        return cast("bool", self._data.get("output_gzip", False))

    def set_output_gzip(self, val: bool) -> None:
        self._load()
        self._data["output_gzip"] = val
        self._save()

    def get_clear_context_cookies(self) -> bool:
        self._load()
        return cast("bool", self._data.get("clear_context_cookies", False))

    def set_clear_context_cookies(self, val: bool) -> None:
        self._load()
        self._data["clear_context_cookies"] = val
        self._save()

    def get_rules_dirs_collapsed(self) -> bool:
        self._load()
        return cast("bool", self._data.get("rules_dirs_collapsed", True))

    def set_rules_dirs_collapsed(self, val: bool) -> None:
        self._load()
        self._data["rules_dirs_collapsed"] = val
        self._save()

    def get_rules_dirs(self) -> list:
        self._load()
        return list(self._data.get("rules_dirs", []))

    def set_rules_dirs(self, val: list) -> None:
        self._load()
        self._data["rules_dirs"] = list(val)
        self._save()

    def get_rules_dirs_enabled(self) -> bool:
        self._load()
        return cast("bool", self._data.get("rules_dirs_enabled", True))

    def set_rules_dirs_enabled(self, val: bool) -> None:
        self._load()
        self._data["rules_dirs_enabled"] = val
        self._save()

    # ── internal ───────────────────────────────────────────────────────────

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        self._migrate_old_file()
        try:
            if not PREFERENCES_FILE.is_file():
                return
            size = PREFERENCES_FILE.stat().st_size
            if size > MAX_FILE_BYTES:
                _log.warning("preferences_oversize", size=size)
                PREFERENCES_FILE.unlink(missing_ok=True)
                return
            raw = PREFERENCES_FILE.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("root must be a JSON object")
            pm = data.get("path_memory")
            if isinstance(pm, dict):
                for key, entries in pm.items():
                    if not isinstance(entries, list):
                        continue
                    valid = [e for e in entries if isinstance(e, str) and e]
                    if valid:
                        self._data["path_memory"][key] = valid[:MAX_ENTRIES_PER_KEY]
            theme = data.get("theme")
            self._data["theme"] = _validate_theme(theme)  # type: ignore[arg-type]
            for k in (
                "rules_auto_update",
                "trace_rules",
                "log_level",
                "output_gzip",
                "clear_context_cookies",
                "rules_dirs",
                "rules_dirs_collapsed",
                "rules_dirs_enabled",
                "ai_active_profile",
                "ai_last_profile",
                "proxy_profiles",
                "proxy_last_used",
                "language",
            ):
                if k in data:
                    self._data[k] = data[k]
            # ADR-0007: migrate ai_profiles from old dict format to list format
            if "ai_profiles" in data:
                raw_profiles = data["ai_profiles"]
                if isinstance(raw_profiles, dict):
                    self._data["ai_profiles"] = list(raw_profiles.values())
                elif isinstance(raw_profiles, list):
                    self._data["ai_profiles"] = raw_profiles
            # ADR-0010: validate proxy_profiles is a list (corruption → fallback to [])
            if not isinstance(self._data.get("proxy_profiles"), list):
                self._data["proxy_profiles"] = []
            if not isinstance(self._data.get("proxy_last_used"), dict):
                self._data["proxy_last_used"] = {}
            # 归一化旧格式 proxy_last_used 值（str → dict），缺少 uuid 的 profile 自动生成
            import uuid as _uuid_mod

            changed = False
            last = dict(self._data["proxy_last_used"])
            for k, v in list(last.items()):
                if isinstance(v, str):
                    del last[k]
                    changed = True
            if changed:
                self._data["proxy_last_used"] = last
            for p in self._data["proxy_profiles"]:
                if not p.get("uuid"):
                    p["uuid"] = _uuid_mod.uuid4().hex[:12]
                    changed = True
            if changed:
                self._save()
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError, OSError) as exc:
            _log.warning("preferences_corrupt", error=exc)
            PREFERENCES_FILE.unlink(missing_ok=True)

    def _migrate_old_file(self) -> None:
        """将旧 ``path_memory.json`` 迁移到新 schema。"""
        if not OLD_PATH_MEMORY_FILE.is_file():
            return
        if PREFERENCES_FILE.is_file():
            OLD_PATH_MEMORY_FILE.unlink(missing_ok=True)
            return
        try:
            raw = OLD_PATH_MEMORY_FILE.read_text(encoding="utf-8")
            old_data = json.loads(raw)
            if isinstance(old_data, dict):
                for key, entries in old_data.items():
                    if isinstance(entries, list):
                        valid = [e for e in entries if isinstance(e, str) and e]
                        if valid:
                            self._data["path_memory"][key] = valid[:MAX_ENTRIES_PER_KEY]
            self._save()
            OLD_PATH_MEMORY_FILE.unlink(missing_ok=True)
            _log.info("preferences_migrated")
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError, OSError) as exc:
            _log.warning("old_path_memory_corrupt", error=exc)
            OLD_PATH_MEMORY_FILE.unlink(missing_ok=True)

    def _save(self) -> None:
        atomic_write_json(PREFERENCES_FILE, self._data, max_bytes=MAX_PAYLOAD_BYTES, chmod_mask=0o600)


# ── module-level singleton ──────────────────────────────────────────────────
_preferences: Optional[Preferences] = None


def get_preferences() -> Preferences:
    """返回进程级 :class:`Preferences` 单例。"""
    global _preferences
    if _preferences is None:
        _preferences = Preferences()
    return _preferences


def clear_qt_file_dialog_history() -> None:
    """清空 Qt 文件对话框的 ``[FileDialog] history`` 值。

    同时覆盖 NativeFormat（Linux ``.conf``）和 IniFormat（Windows
    ``.ini``），QSettings API 直接操作，跨平台自适应。
    """
    from PySide6.QtCore import QSettings

    for fmt in (QSettings.NativeFormat, QSettings.IniFormat):
        settings = QSettings(fmt, QSettings.UserScope, "QtProject")
        settings.beginGroup("FileDialog")
        if settings.contains("history"):
            settings.remove("history")
        settings.endGroup()
        settings.sync()

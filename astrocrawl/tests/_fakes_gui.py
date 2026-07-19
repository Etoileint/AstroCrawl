"""GUI 测试替身 — FakePreferences, FakeCrawlSession, FakeRuleLifecycle。

每个 Fake 实现对应的接口，用内存数据结构替代 I/O 和 Qt 外部依赖。
沿用 tests/_fakes.py 的 Fake 命名和设计模式。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, Signal


class FakePreferences:
    """Preferences 的测试替身 — 内存 dict 存储，无 QSettings 依赖。"""

    def __init__(self) -> None:
        self._data: Dict[str, Any] = {
            "theme": {"mode": "light", "base": "light", "overrides": {}},
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
        self._last_dirs: Dict[str, str] = {}
        self._save_called = False

    # ── Theme ──

    def get_theme(self) -> dict:
        return self._data["theme"]

    def set_theme(self, mode: str, base: str, overrides: dict) -> None:
        self._data["theme"] = {"mode": mode, "base": base, "overrides": dict(overrides)}

    # ── Language ──

    def get_language(self) -> str:
        return self._data.get("language", "zh_CN")

    def set_language(self, lang: str) -> None:
        self._data["language"] = lang

    # ── AI / LLM ──

    def _get_active_profile_dict(self) -> dict:
        name = self._data.get("ai_active_profile", "")
        for p in self._data.get("ai_profiles", []):
            if p.get("name") == name:
                return p
        return self._data["ai_profiles"][0] if self._data["ai_profiles"] else {}

    def _update_active_field(self, field: str, value: Any) -> None:
        name = self._data.get("ai_active_profile", "")
        for i, p in enumerate(self._data["ai_profiles"]):
            if p.get("name") == name:
                self._data["ai_profiles"][i][field] = value
                return

    # ── AI Profile CRUD API (ADR-0007) ──

    def get_active_ai_profile(self):
        from astrocrawl.ai._profile import AIProfile

        d = self._get_active_profile_dict()
        return AIProfile.from_dict(d) if d else None

    def get_active_profile_name(self) -> str:
        return self._data.get("ai_active_profile", "")

    def get_ai_profile(self, name: str) -> Optional[Any]:
        from astrocrawl.ai._profile import AIProfile

        for p in self._data.get("ai_profiles", []):
            if p.get("name") == name:
                return AIProfile.from_dict(p)
        return None

    def set_active_ai_profile(self, name: str) -> None:
        for p in self._data.get("ai_profiles", []):
            if p.get("name") == name:
                self._data["ai_active_profile"] = name
                return

    def get_ai_profiles(self) -> list:
        from astrocrawl.ai._profile import AIProfile

        return [AIProfile.from_dict(p) for p in self._data.get("ai_profiles", [])]

    def get_ai_profile_names(self) -> list[str]:
        return [p["name"] for p in self._data.get("ai_profiles", [])]

    def save_ai_profile(self, profile: Any) -> None:
        d = profile.to_dict() if hasattr(profile, "to_dict") else dict(profile)
        for i, p in enumerate(self._data["ai_profiles"]):
            if p.get("name") == d.get("name"):
                self._data["ai_profiles"][i] = d
                return
        self._data["ai_profiles"].append(d)
        if not self._data.get("ai_active_profile"):
            self._data["ai_active_profile"] = d.get("name", "")

    def remove_ai_profile(self, name: str) -> None:
        self._data["ai_profiles"] = [p for p in self._data["ai_profiles"] if p.get("name") != name]
        last = dict(self._data.get("ai_last_profile", {}))
        for mk, pn in list(last.items()):
            if pn == name:
                last[mk] = ""
        self._data["ai_last_profile"] = last
        if self._data.get("ai_active_profile") == name:
            remaining = self._data["ai_profiles"]
            self._data["ai_active_profile"] = remaining[0]["name"] if remaining else ""

    def get_last_ai_profile(self, module: str) -> Optional[str]:
        name: str = self._data.get("ai_last_profile", {}).get(module, "")
        if name and any(p.get("name") == name for p in self._data.get("ai_profiles", [])):
            return name
        return None

    def set_last_ai_profile(self, module: str, profile_name: str) -> None:
        last = dict(self._data.get("ai_last_profile", {}))
        last[module] = profile_name
        self._data["ai_last_profile"] = last

    # ── Proxy Profile CRUD API (ADR-0010) ──

    def get_proxy_profiles(self) -> list:
        from astrocrawl.proxy._config import ProxyProfile

        return [ProxyProfile.from_dict(p) for p in self._data.get("proxy_profiles", [])]

    def get_proxy_profile_names(self) -> list[str]:
        return [p.get("name", "") for p in self._data.get("proxy_profiles", [])]

    def get_proxy_profile(self, name: str) -> Optional[Any]:
        from astrocrawl.proxy._config import ProxyProfile

        for p in self._data.get("proxy_profiles", []):
            if p.get("name") == name:
                return ProxyProfile.from_dict(p)
        return None

    def save_proxy_profile(self, profile: Any) -> None:
        d = profile.to_dict() if hasattr(profile, "to_dict") else dict(profile)
        if not d.get("uuid"):
            d["uuid"] = "test-" + d.get("name", "unknown")
        # 端点去重校验（对标 Preferences.save_proxy_profile）
        from astrocrawl.proxy._config import ProxyType

        seen: dict[str, int] = {}
        for i, ep in enumerate(d.get("proxies", [])):
            key = f"{ProxyType(ep['type']).name}:{ep['host']}:{ep['port']}"
            if key in seen:
                raise ValueError(
                    f"Profile '{d.get('name')}' 端点重复: {key} "
                    f"(索引 {seen[key]} 与 {i}，label='{d['proxies'][seen[key]]['label']}' 与 '{ep['label']}')"
                )
            seen[key] = i
        for i, p in enumerate(self._data["proxy_profiles"]):
            if p.get("name") == d.get("name"):
                self._data["proxy_profiles"][i] = d
                return
        self._data["proxy_profiles"].append(d)

    def remove_proxy_profile(self, name: str, *, force: bool = False) -> None:
        target_uuid = ""
        for p in self._data.get("proxy_profiles", []):
            if p.get("name") == name:
                target_uuid = p.get("uuid", "")
                break
        self._data["proxy_profiles"] = [p for p in self._data["proxy_profiles"] if p.get("name") != name]
        last = dict(self._data.get("proxy_last_used", {}))
        for ck, entry in list(last.items()):
            if isinstance(entry, dict) and entry.get("profile") == target_uuid:
                last[ck] = {"profile": "", "node": ""}
        self._data["proxy_last_used"] = last

    def get_proxy_last_used(self, consumer: str) -> Optional[dict]:
        entry = self._data.get("proxy_last_used", {}).get(consumer)
        if not isinstance(entry, dict):
            return None
        profile_uuid = entry.get("profile", "")
        if not profile_uuid:
            return None
        if not any(p.get("uuid") == profile_uuid for p in self._data.get("proxy_profiles", [])):
            last = dict(self._data.get("proxy_last_used", {}))
            last[consumer] = {"profile": "", "node": ""}
            self._data["proxy_last_used"] = last
            return None
        return {"profile": profile_uuid, "node": entry.get("node", "")}

    def set_proxy_last_used(self, consumer: str, profile_uuid: str, node: str = "") -> None:
        last = dict(self._data.get("proxy_last_used", {}))
        last[consumer] = {"profile": profile_uuid, "node": node}
        self._data["proxy_last_used"] = last

    def _get_proxy_profile_by_uuid(self, uuid_str: str) -> Optional[Any]:
        for p in self._data.get("proxy_profiles", []):
            if p.get("uuid") == uuid_str:
                from astrocrawl.proxy._config import ProxyProfile

                return ProxyProfile.from_dict(p)
        return None

    def get_parsed_proxy_for(self, consumer: str) -> Optional[Any]:
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
        try:
            from astrocrawl.proxy._config import ProxyType

            _type_names = tuple(t.name for t in ProxyType)
            if ":" in node and node.split(":", 1)[0] in _type_names:
                # 新格式 TYPE:host:port
                type_name, rest = node.split(":", 1)
                host, port_str = rest.rsplit(":", 1)
                port = int(port_str)
                match_type = True
            else:
                # 旧格式 host:port（向后兼容）
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
                from astrocrawl.proxy._config import ParsedProxy, ProxyAuth

                return ParsedProxy(
                    type=spec.type,
                    host=spec.host,
                    port=spec.port,
                    auth=ProxyAuth(username=spec.username, password=spec.password),
                    weight=spec.weight,
                )
        return None

    # ── Global settings ──

    def get_rules_auto_update(self) -> bool:
        return self._data.get("rules_auto_update", True)

    def set_rules_auto_update(self, val: bool) -> None:
        self._data["rules_auto_update"] = val

    def get_trace_rules(self) -> bool:
        return self._data.get("trace_rules", False)

    def set_trace_rules(self, val: bool) -> None:
        self._data["trace_rules"] = val

    def get_log_level(self) -> str:
        return self._data.get("log_level", "INFO")

    def set_log_level(self, val: str) -> None:
        self._data["log_level"] = val

    def get_output_gzip(self) -> bool:
        return self._data.get("output_gzip", False)

    def set_output_gzip(self, val: bool) -> None:
        self._data["output_gzip"] = val

    def get_clear_context_cookies(self) -> bool:
        return self._data.get("clear_context_cookies", False)

    def set_clear_context_cookies(self, val: bool) -> None:
        self._data["clear_context_cookies"] = val

    def get_rules_dirs_collapsed(self) -> bool:
        return self._data.get("rules_dirs_collapsed", True)

    def set_rules_dirs_collapsed(self, val: bool) -> None:
        self._data["rules_dirs_collapsed"] = val

    def get_rules_dirs(self) -> list:
        return list(self._data.get("rules_dirs", []))

    def set_rules_dirs(self, val: list) -> None:
        self._data["rules_dirs"] = list(val)

    def get_rules_dirs_enabled(self) -> bool:
        return self._data.get("rules_dirs_enabled", True)

    def set_rules_dirs_enabled(self, val: bool) -> None:
        self._data["rules_dirs_enabled"] = val

    # ── Directory memory ──

    def get_last_dir(self, key: str, fallback: str) -> str:
        return self._last_dirs.get(key, fallback)

    def add_path(self, key: str, path: str) -> None:
        self._last_dirs[key] = path

    # ── Persistence ──

    def _save(self) -> None:
        self._save_called = True

    def clear_qt_file_dialog_history(self) -> None:
        pass


class FakeCrawlSession(QObject):
    """CrawlSession 的测试替身 — 预编程信号发射，无真实爬虫线程。

    通过直接调用信号 emit 模拟爬虫生命周期，通过 _running 标志控制状态。
    """

    message_logged = Signal(str)
    layer_progress = Signal(int, int, int)
    stats_updated = Signal(int, int, int)
    outcome_updated = Signal(dict)
    finished = Signal(str, dict)
    error_occurred = Signal(str)
    pause_changed = Signal(bool)
    worker_state_changed = Signal(int, str)
    rule_matched = Signal(str, object)
    rule_stats_updated = Signal(object)
    session_done = Signal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._running = False
        self._paused = False
        self._output_path: Optional[str] = None
        self._report: Optional[dict] = None
        self._stop_called = False
        self._pause_called = False
        self._resume_called = False
        self._disconnect_called = False

    def start(
        self,
        urls: List[str],
        depth: int,
        concurrency: int,
        output_path: str,
        same_domain_only: bool,
        cfg_overrides: Optional[Dict[str, Any]] = None,
        *,
        proxy_profile: Any = None,
        proxy_mode_override: str | None = None,
        health_tracker: Any = None,
    ) -> None:
        self._running = True
        self._paused = False
        self._output_path = output_path

    def pause(self) -> None:
        if self._running:
            self._paused = True
            self._pause_called = True
            self.pause_changed.emit(True)

    def resume(self) -> None:
        if self._running:
            self._paused = False
            self._resume_called = True
            self.pause_changed.emit(False)

    def stop(self) -> None:
        if self._running:
            self._stop_called = True
            self._running = False

    def is_running(self) -> bool:
        return self._running

    def set_report(self, report: Optional[dict]) -> None:
        self._report = report

    @property
    def output_path(self) -> Optional[str]:
        return self._output_path

    @property
    def last_report(self) -> Optional[dict]:
        return self._report

    @property
    def proxy_manager(self) -> Any:
        return None

    @property
    def stopped(self) -> bool:
        return not self._running

    def dispose(self) -> None:
        import warnings

        self._disconnect_called = True
        for name in (
            "message_logged",
            "layer_progress",
            "stats_updated",
            "outcome_updated",
            "finished",
            "error_occurred",
            "pause_changed",
            "worker_state_changed",
            "session_done",
        ):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                try:
                    getattr(self, name).disconnect()
                except RuntimeError:
                    pass

    def disconnect_signals(self) -> None:
        self.dispose()

    def emit_finished(self, output_path: str, stats: dict) -> None:
        self.finished.emit(output_path, stats)

    def emit_error(self, msg: str) -> None:
        self.error_occurred.emit(msg)

    def emit_log(self, msg: str) -> None:
        self.message_logged.emit(msg)

    def emit_session_done(self) -> None:
        self.session_done.emit()


class FakeRuleLifecycle:
    """RuleLifecycle 的测试替身 — 返回预编程 RuleSnapshot。"""

    def __init__(
        self, rules: Optional[List[dict]] = None, *, source_map: dict | None = None, path_map: dict | None = None
    ) -> None:
        self._rules: List[dict] = rules or []
        self._source_map: dict = source_map or {}
        self._path_map: dict = path_map or {}
        self.reload_called = False
        self.initial_load_called = False

    def initial_load(self) -> None:
        self.initial_load_called = True

    def reload(self) -> None:
        self.reload_called = True

    def get_snapshot(self):
        """返回预编程 RuleSnapshot，by_name 为 {name: SimpleNamespace} 映射。"""
        from types import SimpleNamespace

        by_name: Dict[str, Any] = {}
        for r in self._rules:
            snap = SimpleNamespace(
                name=r["name"],
                display_name=r.get("display_name", ""),
                tags=r.get("tags", []),
                version=r.get("version", 1),
                enabled=r.get("enabled", True),
                fields=r.get("fields", {}),
            )
            by_name[r["name"]] = snap
        _source_map = dict(self._source_map)
        _path_map = dict(self._path_map)
        _get_path = lambda name: Path(_path_map[name]) if name in _path_map else None  # noqa: E731
        return SimpleNamespace(
            by_name=by_name,
            get_path=_get_path,
            get_source=lambda name: _source_map.get(name),
        )


class FakePreviewSession(QObject):
    """PreviewSession 的测试替身 — 预编程信号发射，无真实 Playwright 进程。"""

    page_opened = Signal(object)  # PreviewPageHandle
    page_closed = Signal(int)  # page_id
    highlight_injected = Signal(object)  # PreviewResult
    error_occurred = Signal(str)
    disposed = Signal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._running = False
        self.dispose_called = False
        self.start_called = False
        self._open_page_error: Optional[Exception] = None

    def start(self) -> None:
        self.start_called = True
        self._running = True

    def open_page(self, url: str, params: Any, *, rule_name: str = "") -> None:
        if self._open_page_error:
            raise self._open_page_error

    def close_page(self, page_id: int) -> None:
        pass

    def activate_page(self, page_id: int) -> None:
        pass

    def update_theme(self, theme_mode: str, theme_tokens: dict) -> None:
        pass

    def dispose(self) -> None:
        self.dispose_called = True
        self._running = False

    def is_running(self) -> bool:
        return self._running

    def emit_page_opened(self, handle: Any) -> None:
        self.page_opened.emit(handle)

    def emit_page_closed(self, page_id: int) -> None:
        self.page_closed.emit(page_id)

    def emit_highlight_injected(self, result: Any) -> None:
        self.highlight_injected.emit(result)

    def emit_error(self, msg: str) -> None:
        self.error_occurred.emit(msg)

    def emit_disposed(self) -> None:
        self.disposed.emit()

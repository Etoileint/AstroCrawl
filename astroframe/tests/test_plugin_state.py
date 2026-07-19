"""插件状态持久化测试 — ADR-0011 决策 10 + S19。

覆盖 PluginState 全文：load 恢复策略（主文件→bak→默认值）、
save 原子写入、modify 读-改-写、require_approval 策略管理、
disabled/trusted/configs CRUD、僵尸条目清理、宽限期边界、
secret 脱敏、fill_defaults 补全。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from astrobase import atomic_write_json
from astroframe._state import _DEFAULT_STATE, PluginState, _mask_configs, _read_state_file, _secret_mask

# ── fixture ───────────────────────────────────────────────────────────────────


def _make_state_file(tmp_path: Path, name: str, data: dict | None = None) -> Path:
    """在 tmp_path/<name>/ 下准备 plugin-state.json。data=None 时不创建文件。"""
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    p = d / "plugin-state.json"
    if data is not None:
        atomic_write_json(p, data, chmod_mask=0o600)
    return p


# ── _secret_mask ─────────────────────────────────────────────────────────────


def test_secret_mask_long():
    assert _secret_mask("sk-abcdefghijklmnop") == "sk-abcde..."


def test_secret_mask_short():
    assert _secret_mask("ab") == "ab..."


# ── _mask_configs ────────────────────────────────────────────────────────────


def test_mask_configs():
    masked = _mask_configs({"pkg-a": {"api_key": "sk-secret-value", "timeout": 30}})
    assert masked["pkg-a"]["api_key"] == "sk-secre..."
    assert masked["pkg-a"]["timeout"] == 30


def test_mask_configs_non_dict_value():
    masked = _mask_configs({"pkg-a": "plain-string"})
    assert masked["pkg-a"] == "plain-st..."


# ── _read_state_file ─────────────────────────────────────────────────────────


def test_read_state_file_oversize(tmp_path: Path):
    p = tmp_path / "large.json"
    p.write_text("x" * (256 * 1024 + 1))
    assert _read_state_file(p) is None


def test_read_state_file_missing():
    assert _read_state_file(Path("/nonexistent/path.json")) is None


# ── load ─────────────────────────────────────────────────────────────────────


def test_load_missing_returns_defaults(tmp_path: Path):
    p = _make_state_file(tmp_path, "load_missing", data=None)
    ps = PluginState(p)
    loaded = ps.load()
    assert loaded == _DEFAULT_STATE
    assert p.is_file()


def test_load_normal(tmp_path: Path):
    p = _make_state_file(
        tmp_path,
        "load_normal",
        data={
            "require_approval": "dangerous",
            "disabled": ["x"],
            "trusted_capabilities": {},
            "configs": {},
        },
    )
    ps = PluginState(p)
    loaded = ps.load()
    assert loaded["require_approval"] == "dangerous"
    assert loaded["disabled"] == ["x"]


def test_load_primary_corrupt_bak_recovery(tmp_path: Path):
    d = tmp_path / "load_corrupt"
    d.mkdir()
    state_file = d / "plugin-state.json"
    bak_file = d / "plugin-state.json.bak"
    state_file.write_text("not-json{{{", encoding="utf-8")
    atomic_write_json(
        bak_file,
        {
            "require_approval": "none",
            "disabled": ["pkg-a"],
            "trusted_capabilities": {},
            "configs": {},
        },
        chmod_mask=0o600,
    )
    ps = PluginState(state_file)
    loaded = ps.load()
    assert loaded["require_approval"] == "none"
    assert loaded["disabled"] == ["pkg-a"]


def test_load_both_corrupt_returns_defaults(tmp_path: Path):
    d = tmp_path / "load_both_corrupt"
    d.mkdir()
    state_file = d / "plugin-state.json"
    bak = d / "plugin-state.json.bak"
    state_file.write_text("garbage", encoding="utf-8")
    bak.write_text("also-garbage", encoding="utf-8")
    ps = PluginState(state_file)
    loaded = ps.load()
    assert loaded == _DEFAULT_STATE


def test_load_fills_missing_defaults(tmp_path: Path):
    p = _make_state_file(tmp_path, "load_fills", data={"require_approval": "all"})
    ps = PluginState(p)
    loaded = ps.load()
    assert loaded["disabled"] == []
    assert loaded["trusted_capabilities"] == {}
    assert loaded["configs"] == {}


# ── _fill_defaults ───────────────────────────────────────────────────────────


def test_fill_defaults_adds_missing_keys():
    result = PluginState._fill_defaults({"require_approval": "dangerous"})
    assert result["require_approval"] == "dangerous"
    assert result["disabled"] == []
    assert result["trusted_capabilities"] == {}
    assert result["configs"] == {}


def test_fill_defaults_preserves_existing():
    state = {
        "require_approval": "none",
        "disabled": ["pkg"],
        "trusted_capabilities": {"k": "v"},
        "configs": {"c": {}},
    }
    assert PluginState._fill_defaults(dict(state)) == state


# ── save ─────────────────────────────────────────────────────────────────────


def test_save_and_reload(tmp_path: Path):
    p = _make_state_file(tmp_path, "save_reload", data=None)
    ps = PluginState(p)
    ps.save(
        {
            "require_approval": "none",
            "disabled": ["pkg-x"],
            "trusted_capabilities": {},
            "configs": {},
        }
    )
    reloaded = ps.load()
    assert reloaded["require_approval"] == "none"
    assert reloaded["disabled"] == ["pkg-x"]


def test_save_fills_top_level_defaults(tmp_path: Path):
    p = _make_state_file(tmp_path, "save_fills", data=None)
    ps = PluginState(p)
    ps.save({"disabled": ["test"]})
    reloaded = ps.load()
    assert reloaded["require_approval"] == "all"
    assert reloaded["trusted_capabilities"] == {}


# ── _modify ──────────────────────────────────────────────────────────────────


def test_modify_read_modify_write(tmp_path: Path):
    p = _make_state_file(
        tmp_path,
        "modify_rw",
        data={
            "require_approval": "all",
            "disabled": [],
            "trusted_capabilities": {},
            "configs": {},
        },
    )
    ps = PluginState(p)
    ps._modify(lambda s: s.update({"require_approval": "dangerous"}))
    assert ps.get_require_approval() == "dangerous"


# ── require_approval ─────────────────────────────────────────────────────────


def test_get_require_approval_default(tmp_path: Path):
    p = _make_state_file(tmp_path, "approval_default", data=None)
    ps = PluginState(p)
    assert ps.get_require_approval() == "all"


def test_set_require_approval_valid(tmp_path: Path):
    p = _make_state_file(tmp_path, "approval_valid", data=None)
    ps = PluginState(p)
    ps.set_require_approval("dangerous")
    assert ps.get_require_approval() == "dangerous"


def test_set_require_approval_invalid(tmp_path: Path):
    p = _make_state_file(tmp_path, "approval_invalid", data=None)
    ps = PluginState(p)
    with pytest.raises(ValueError, match="require_approval"):
        ps.set_require_approval("unknown")


# ── disabled ─────────────────────────────────────────────────────────────────


def test_is_disabled_default_false(tmp_path: Path):
    p = _make_state_file(tmp_path, "dis_default", data=None)
    ps = PluginState(p)
    assert not ps.is_disabled("any-pkg")


def test_set_disabled_toggle(tmp_path: Path):
    p = _make_state_file(tmp_path, "dis_toggle", data=None)
    ps = PluginState(p)
    ps.set_disabled("pkg-a", True)
    assert ps.is_disabled("pkg-a")
    ps.set_disabled("pkg-a", False)
    assert not ps.is_disabled("pkg-a")


def test_set_disabled_idempotent(tmp_path: Path):
    p = _make_state_file(tmp_path, "dis_idempotent", data=None)
    ps = PluginState(p)
    ps.set_disabled("pkg-a", True)
    ps.set_disabled("pkg-a", True)


# ── trusted ──────────────────────────────────────────────────────────────────


def test_get_trusted_none(tmp_path: Path):
    p = _make_state_file(tmp_path, "trust_none", data=None)
    ps = PluginState(p)
    assert ps.get_trusted("pkg/cap") is None


def test_set_and_get_trusted(tmp_path: Path):
    p = _make_state_file(tmp_path, "trust_set", data=None)
    ps = PluginState(p)
    ps.set_trusted("pkg/cap", ["crawl.ctx.read"], "1.0.0", "sha256:abc")
    record = ps.get_trusted("pkg/cap")
    assert record is not None
    assert record["granted_version"] == "1.0.0"
    assert record["granted_hash"] == "sha256:abc"
    assert "granted_at" in record


def test_set_trusted_with_allow_deprecated(tmp_path: Path):
    p = _make_state_file(tmp_path, "trust_dep", data=None)
    ps = PluginState(p)
    ps.set_trusted("pkg/cap", ["crawl.ctx.read"], "2.0.0", "sha256:def", allow_deprecated=True)
    record = ps.get_trusted("pkg/cap")
    assert record is not None
    assert record["allow_deprecated"] is True


def test_remove_trusted(tmp_path: Path):
    p = _make_state_file(tmp_path, "trust_remove", data=None)
    ps = PluginState(p)
    ps.set_trusted("pkg/cap", ["crawl.ctx.read"], "1.0.0", "sha256:abc")
    ps.remove_trusted("pkg/cap")
    assert ps.get_trusted("pkg/cap") is None


def test_remove_trusted_nonexistent(tmp_path: Path):
    p = _make_state_file(tmp_path, "trust_nonexist", data=None)
    ps = PluginState(p)
    ps.remove_trusted("nonexistent/key")


# ── config ───────────────────────────────────────────────────────────────────


def test_get_config_default_empty(tmp_path: Path):
    p = _make_state_file(tmp_path, "cfg_default", data=None)
    ps = PluginState(p)
    assert ps.get_config("any-pkg") == {}


def test_set_and_get_config(tmp_path: Path):
    p = _make_state_file(tmp_path, "cfg_set", data=None)
    ps = PluginState(p)
    ps.set_config("pkg-a", {"api_key": "sk-secret", "timeout": 60})
    cfg = ps.get_config("pkg-a")
    assert cfg["api_key"] == "sk-secret"
    assert cfg["timeout"] == 60


# ── zombie: disabled ─────────────────────────────────────────────────────────


def test_clean_zombie_disabled_removes_stale(tmp_path: Path):
    p = _make_state_file(tmp_path, "zd_stale", data=None)
    ps = PluginState(p)
    ps.set_disabled("stale-pkg", True)
    ps.set_disabled("active-pkg", True)
    removed = ps.clean_zombie_disabled({"active-pkg"})
    assert removed == ["stale-pkg"]
    assert not ps.is_disabled("stale-pkg")
    assert ps.is_disabled("active-pkg")


def test_clean_zombie_disabled_no_stale(tmp_path: Path):
    p = _make_state_file(tmp_path, "zd_none", data=None)
    ps = PluginState(p)
    ps.set_disabled("pkg-a", True)
    removed = ps.clean_zombie_disabled({"pkg-a"})
    assert removed == []


# ── zombie: configs ──────────────────────────────────────────────────────────


def test_clean_zombie_configs_removes_stale(tmp_path: Path):
    p = _make_state_file(tmp_path, "zc_stale", data=None)
    ps = PluginState(p)
    ps.set_config("stale-pkg", {"k": "v"})
    ps.set_config("active-pkg", {"k": "v"})
    removed = ps.clean_zombie_configs({"active-pkg"})
    assert removed == ["stale-pkg"]
    assert ps.get_config("stale-pkg") == {}


def test_clean_zombie_configs_no_stale(tmp_path: Path):
    p = _make_state_file(tmp_path, "zc_none", data=None)
    ps = PluginState(p)
    ps.set_config("pkg-a", {"k": "v"})
    removed = ps.clean_zombie_configs({"pkg-a", "pkg-b"})
    assert removed == []


# ── zombie: trusted ──────────────────────────────────────────────────────────


def test_clean_zombie_trusted_expired(tmp_path: Path):
    p = _make_state_file(tmp_path, "zt_expired", data=None)
    ps = PluginState(p)
    ps.set_trusted("stale/cap", ["crawl.ctx.read"], "1.0.0", "sha256:abc")
    removed = ps.clean_zombie_trusted({"active"}, grace_days=0)
    assert "stale/cap" in removed
    assert ps.get_trusted("stale/cap") is None


def test_clean_zombie_trusted_within_grace(tmp_path: Path):
    p = _make_state_file(tmp_path, "zt_grace", data=None)
    ps = PluginState(p)
    ps.set_trusted("fresh/cap", ["crawl.ctx.read"], "1.0.0", "sha256:abc")
    removed = ps.clean_zombie_trusted({"other"}, grace_days=365)
    assert "fresh/cap" not in removed
    record = ps.get_trusted("fresh/cap")
    assert record is not None
    assert "_zombie_detected_at" in record


def test_clean_zombie_trusted_active_untouched(tmp_path: Path):
    p = _make_state_file(tmp_path, "zt_active", data=None)
    ps = PluginState(p)
    ps.set_trusted("pkg-a/cap", ["crawl.ctx.read"], "1.0.0", "sha256:abc")
    removed = ps.clean_zombie_trusted({"pkg-a"}, grace_days=0)
    assert removed == []
    assert ps.get_trusted("pkg-a/cap") is not None


def test_clean_zombie_trusted_two_cycle(tmp_path: Path):
    """首轮标记 _zombie_detected_at → 次轮宽限期过后清理。"""
    p = _make_state_file(tmp_path, "zt_twocycle", data=None)
    ps = PluginState(p)
    ps.set_trusted("stale/cap", ["crawl.ctx.read"], "1.0.0", "sha256:abc")

    removed1 = ps.clean_zombie_trusted({"other"}, grace_days=365)
    assert removed1 == []
    record = ps.get_trusted("stale/cap")
    assert record is not None
    assert "_zombie_detected_at" in record

    removed2 = ps.clean_zombie_trusted({"other"}, grace_days=0)
    assert "stale/cap" in removed2
    assert ps.get_trusted("stale/cap") is None


def test_clean_zombie_trusted_clock_backward_safe(tmp_path: Path):
    """时钟回拨：_zombie_detected_at 在未来 → max(0,...)=0 → 不清理。"""
    p = _make_state_file(tmp_path, "zt_clock", data=None)
    ps = PluginState(p)
    ps.set_trusted("stale/cap", ["crawl.ctx.read"], "1.0.0", "sha256:abc")
    ps._modify(lambda s: s["trusted_capabilities"]["stale/cap"].update({"_zombie_detected_at": 9999999999.0}))
    removed = ps.clean_zombie_trusted({"other"}, grace_days=0)
    assert "stale/cap" not in removed


# ── classify_zombie_trusted ──────────────────────────────────────────────────


def test_classify_zombie_trusted_expired(tmp_path: Path):
    p = _make_state_file(tmp_path, "czt_expired", data=None)
    ps = PluginState(p)
    ps.set_trusted("old/cap", ["crawl.ctx.read"], "1.0.0", "sha256:abc")
    expired, in_grace = ps.classify_zombie_trusted({"active"}, grace_days=0)
    assert "old/cap" in expired
    assert "old/cap" not in in_grace


def test_classify_zombie_trusted_with_zombie_detected(tmp_path: Path):
    p = _make_state_file(tmp_path, "czt_detected", data=None)
    ps = PluginState(p)
    ps.set_trusted("stale/cap", ["crawl.ctx.read"], "1.0.0", "sha256:abc")
    ps._modify(
        lambda s: s["trusted_capabilities"]["stale/cap"].update({"_zombie_detected_at": time.time() - 15 * 86400})
    )
    expired, in_grace = ps.classify_zombie_trusted({"other"}, grace_days=30)
    assert "stale/cap" in in_grace
    assert "stale/cap" not in expired


def test_classify_zombie_trusted_active_skipped(tmp_path: Path):
    p = _make_state_file(tmp_path, "czt_active", data=None)
    ps = PluginState(p)
    ps.set_trusted("active/cap", ["crawl.ctx.read"], "1.0.0", "sha256:abc")
    expired, in_grace = ps.classify_zombie_trusted({"active"}, grace_days=0)
    assert expired == []
    assert in_grace == []


def test_classify_zombie_trusted_unparseable_granted_at(tmp_path: Path):
    p = _make_state_file(tmp_path, "czt_unparse", data=None)
    ps = PluginState(p)
    ps.set_trusted("bad/cap", ["crawl.ctx.read"], "1.0.0", "sha256:abc")
    ps._modify(lambda s: s["trusted_capabilities"]["bad/cap"].update({"granted_at": "not-a-date"}))
    expired, in_grace = ps.classify_zombie_trusted({"other"})
    assert "bad/cap" in in_grace
    assert "bad/cap" not in expired

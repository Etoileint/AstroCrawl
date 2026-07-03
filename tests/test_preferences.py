"""用户偏好持久化测试 — 路径记忆 + 主题 + LLM 配置 + 速率限制。

使用 tmp_path 隔离文件 I/O, monkeypatch 控制时间。
"""

from __future__ import annotations

import json

import pytest

# 导入常量用于 monkeypatch 目标
import astrocrawl.utils.preferences as _prefs_mod
from astrocrawl.ai._profile import AIProfile
from astrocrawl.utils.preferences import Preferences, _validate_theme

# ═══════════════════════════════════════════════════════════════════════
# 辅助
# ═══════════════════════════════════════════════════════════════════════


def _patch_prefs_paths(monkeypatch, tmp_path):
    """将所有 Preferences 文件路径重定向到 tmp_path。"""
    prefs_file = tmp_path / "preferences.json"
    old_file = tmp_path / "path_memory.json"
    monkeypatch.setattr(_prefs_mod, "PREFERENCES_FILE", prefs_file)
    monkeypatch.setattr(_prefs_mod, "OLD_PATH_MEMORY_FILE", old_file)
    return prefs_file, old_file


# ═══════════════════════════════════════════════════════════════════════
# _validate_theme
# ═══════════════════════════════════════════════════════════════════════


class TestValidateTheme:
    """主题校验函数。"""

    def test_valid_theme_passes_through(self):
        """有效 theme 原样返回。"""
        theme = {"mode": "dark", "base": "dark", "overrides": {"bg": "#000"}}
        assert _validate_theme(theme) == theme

    def test_invalid_mode_defaults_to_light(self):
        """非法 mode → "light"。"""
        result = _validate_theme({"mode": "neon", "base": "light", "overrides": {}})
        assert result["mode"] == "light"

    def test_invalid_base_defaults_to_light(self):
        """非法 base → "light"。"""
        result = _validate_theme({"mode": "light", "base": "neon", "overrides": {}})
        assert result["base"] == "light"

    def test_non_dict_overrides_becomes_empty(self):
        """overrides 非 dict → {}。"""
        result = _validate_theme({"mode": "light", "base": "light", "overrides": 42})
        assert result["overrides"] == {}

    def test_non_dict_input_returns_default(self):
        """非 dict 输入 → DEFAULT_THEME 副本。"""
        result = _validate_theme(None)
        assert result == {"mode": "light", "base": "light", "overrides": {}}
        # 返回的是副本, 非原始引用
        result["mode"] = "modified"
        assert _validate_theme(None)["mode"] == "light"

    def test_non_string_overrides_filtered(self):
        """非 str key/value 的 override 被剔除。"""
        result = _validate_theme(
            {
                "mode": "light",
                "base": "light",
                "overrides": {"ok": "val", 42: "int_key", "int_val": 99},
            }
        )
        assert result["overrides"] == {"ok": "val"}


# ═══════════════════════════════════════════════════════════════════════
# 路径记忆
# ═══════════════════════════════════════════════════════════════════════


class TestPathMemory:
    """add_path / get_last_dir。"""

    def test_add_and_retrieve(self, monkeypatch, tmp_path):
        """添加路径后可检索。"""
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        d = tmp_path / "mydir"
        d.mkdir()
        p.add_path("output", str(d))
        assert p.get_last_dir("output") == str(d)

    def test_dedup_moves_to_front(self, monkeypatch, tmp_path):
        """重复路径移至首位。"""
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        d1 = tmp_path / "dir1"
        d1.mkdir()
        d2 = tmp_path / "dir2"
        d2.mkdir()
        p.add_path("k", str(d1))
        p.add_path("k", str(d2))
        p.add_path("k", str(d1))
        assert p.get_last_dir("k") == str(d1)

    def test_truncate_max_entries(self, monkeypatch, tmp_path):
        """超过 MAX_ENTRIES_PER_KEY (20) 截断最旧。"""
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        for i in range(25):
            d = tmp_path / f"dir{i}"
            d.mkdir()
            p.add_path("k", str(d))
        # 验证内部状态：恰好 20 条
        pm = p._data.get("path_memory", {}).get("k", [])
        assert len(pm) == 20
        # 最旧的 5 个 (dir0-dir4) 被截断
        for i in range(5):
            assert str(tmp_path / f"dir{i}") not in pm
        # dir5 (最旧保留) 在列表末尾
        assert pm[-1] == str(tmp_path / "dir5")
        # 最新的 (dir24) 在首位
        assert pm[0] == str(tmp_path / "dir24")
        # 公共 API 返回最新目录
        assert p.get_last_dir("k") == str(tmp_path / "dir24")

    def test_empty_path_ignored(self, monkeypatch, tmp_path):
        """空路径被忽略, 不写入存储。"""
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        p.add_path("k", "")
        assert p.get_last_dir("k", fallback="fb") == "fb"

    def test_multiple_keys_independent(self, monkeypatch, tmp_path):
        """多 key 独立。"""
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        da = tmp_path / "a"
        da.mkdir()
        db = tmp_path / "b"
        db.mkdir()
        p.add_path("ka", str(da))
        p.add_path("kb", str(db))
        assert p.get_last_dir("ka") == str(da)
        assert p.get_last_dir("kb") == str(db)

    def test_get_last_dir_skips_nonexistent(self, monkeypatch, tmp_path):
        """跳过不存在的目录, 返回 fallback。"""
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        p.add_path("k", "/nonexistent/path")
        assert p.get_last_dir("k", fallback="fb") == "fb"


# ═══════════════════════════════════════════════════════════════════════
# 主题
# ═══════════════════════════════════════════════════════════════════════


class TestThemeGetSet:
    """get_theme / set_theme。"""

    def test_default_theme(self, monkeypatch, tmp_path):
        """默认主题值。"""
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        t = p.get_theme()
        assert t == {"mode": "light", "base": "light", "overrides": {}}

    def test_roundtrip(self, monkeypatch, tmp_path):
        """set → get 一致。"""
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        p.set_theme("dark", "dark", {"bg": "#111"})
        t = p.get_theme()
        assert t == {"mode": "dark", "base": "dark", "overrides": {"bg": "#111"}}

    def test_returns_copy(self, monkeypatch, tmp_path):
        """返回副本, 修改不影响内部状态。"""
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        t = p.get_theme()
        t["mode"] = "hacked"
        assert p.get_theme()["mode"] == "light"

    def test_set_validates_theme(self, monkeypatch, tmp_path):
        """set_theme 通过 _validate_theme 过滤非法值。"""
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        p.set_theme("invalid", "invalid", 42)
        t = p.get_theme()
        assert t["mode"] == "light"
        assert t["base"] == "light"
        assert t["overrides"] == {}


# ═══════════════════════════════════════════════════════════════════════
# LLM 配置
# ═══════════════════════════════════════════════════════════════════════


class TestLLMConfig:
    """AI profile CRUD — 替代旧 flat getter/setter（ADR-0007）。"""

    def test_default_api_key_empty(self, monkeypatch, tmp_path):
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        p.save_ai_profile(AIProfile(name="default"))
        profile = p.get_ai_profile("default")
        assert profile is not None
        assert profile.api_key == ""

    def test_key_roundtrip(self, monkeypatch, tmp_path):
        _patch_prefs_paths(monkeypatch, tmp_path)

        p = Preferences()
        p.save_ai_profile(AIProfile())
        updated = AIProfile(name="default", api_key="sk-abc123")
        p.save_ai_profile(updated)
        result = p.get_ai_profile("default")
        assert result is not None
        assert result.api_key == "sk-abc123"

    def test_default_endpoint_empty(self, monkeypatch, tmp_path):
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        p.save_ai_profile(AIProfile(name="default"))
        profile = p.get_ai_profile("default")
        assert profile is not None
        assert profile.endpoint == ""

    def test_endpoint_roundtrip(self, monkeypatch, tmp_path):
        _patch_prefs_paths(monkeypatch, tmp_path)

        p = Preferences()
        p.save_ai_profile(AIProfile(name="default", endpoint="https://custom.api/v1/"))
        result = p.get_ai_profile("default")
        assert result is not None
        assert result.endpoint == "https://custom.api/v1/"


# ═══════════════════════════════════════════════════════════════════════
# 加载/保存
# ═══════════════════════════════════════════════════════════════════════


class TestLoadSave:
    """_load / _save 行为。"""

    def test_no_file_stays_default(self, monkeypatch, tmp_path):
        """无文件时保持默认值, _loaded=True。"""
        prefs_file, _ = _patch_prefs_paths(monkeypatch, tmp_path)
        assert not prefs_file.exists()
        p = Preferences()
        assert p._loaded is False
        p.get_theme()
        assert p._loaded is True

    def test_valid_file_loads(self, monkeypatch, tmp_path):
        """有效 JSON 文件加载 path_memory 和 theme。"""
        prefs_file, _ = _patch_prefs_paths(monkeypatch, tmp_path)
        out_dir = tmp_path / "real_output"
        out_dir.mkdir()
        prefs_file.write_text(
            json.dumps(
                {
                    "path_memory": {"output": [str(out_dir)]},
                    "theme": {"mode": "dark", "base": "dark", "overrides": {}},
                }
            ),
            "utf-8",
        )
        p = Preferences()
        assert p.get_last_dir("output") == str(out_dir)
        assert p.get_theme()["mode"] == "dark"

    def test_corrupt_json_discards_file(self, monkeypatch, tmp_path, caplog):
        """损坏 JSON → 删除文件, 保持默认值。"""
        prefs_file, _ = _patch_prefs_paths(monkeypatch, tmp_path)
        prefs_file.write_text("not json", "utf-8")
        p = Preferences()
        t = p.get_theme()
        assert t["mode"] == "light"
        assert not prefs_file.exists()
        assert "corrupt" in caplog.text

    @pytest.mark.parametrize(
        "content",
        [
            "[1, 2, 3]",
            "42",
            '"just a string"',
        ],
    )
    def test_non_dict_root_discards(self, monkeypatch, tmp_path, caplog, content):
        """JSON 根类型非 dict → 删除文件, 保持默认值。"""
        prefs_file, _ = _patch_prefs_paths(monkeypatch, tmp_path)
        prefs_file.write_text(content, "utf-8")
        p = Preferences()
        assert p.get_theme()["mode"] == "light"
        assert not prefs_file.exists()

    def test_oversized_file_discards(self, monkeypatch, tmp_path, caplog):
        """>128KB 文件 → 删除。"""
        prefs_file, _ = _patch_prefs_paths(monkeypatch, tmp_path)
        prefs_file.write_bytes(b"x" * (128 * 1024 + 1))
        p = Preferences()
        t = p.get_theme()
        assert t["mode"] == "light"
        assert not prefs_file.exists()
        assert "oversize" in caplog.text

    def test_save_writes_valid_json(self, monkeypatch, tmp_path):
        """_save 写入有效 JSON。"""
        prefs_file, _ = _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        p._loaded = True
        p.set_theme("dark", "dark", {})
        assert prefs_file.exists()
        data = json.loads(prefs_file.read_text("utf-8"))
        assert data["theme"]["mode"] == "dark"

    def test_oversized_payload_rejected(self, monkeypatch, tmp_path):
        """>64KB payload → atomic_write_json 抛 ValueError。"""
        prefs_file, _ = _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        p._loaded = True
        # 注入超大 theme overrides 使 payload 超 64KB
        p._data["theme"]["overrides"] = {f"k{i}": "v" * 2000 for i in range(40)}
        initial_data = json.loads(prefs_file.read_text("utf-8")) if prefs_file.exists() else {}
        with pytest.raises(ValueError):
            p._save()
        # 文件未被写入 (保持原样或不存在)
        if prefs_file.exists():
            assert json.loads(prefs_file.read_text("utf-8")) == initial_data


# ═══════════════════════════════════════════════════════════════════════
# 迁移
# ═══════════════════════════════════════════════════════════════════════


class TestMigration:
    """_migrate_old_file 旧 path_memory.json 迁移。"""

    def test_migrate_merges_and_deletes(self, monkeypatch, tmp_path):
        """旧文件 → 合并到新文件 + 删除旧文件。"""
        prefs_file, old_file = _patch_prefs_paths(monkeypatch, tmp_path)
        assert not prefs_file.exists()
        old_dir = tmp_path / "old_path"
        old_dir.mkdir()
        old_file.write_text(json.dumps({"output": [str(old_dir)]}), "utf-8")
        p = Preferences()
        assert p.get_last_dir("output") == str(old_dir)
        assert not old_file.exists()
        assert prefs_file.exists()

    def test_new_exists_just_deletes_old(self, monkeypatch, tmp_path):
        """新文件已存在 → 仅删除旧文件, 不覆盖新数据。"""
        prefs_file, old_file = _patch_prefs_paths(monkeypatch, tmp_path)
        new_dir = tmp_path / "new_path"
        new_dir.mkdir()
        old_dir = tmp_path / "old_path"
        old_dir.mkdir()
        prefs_file.write_text(
            json.dumps(
                {
                    "path_memory": {"output": [str(new_dir)]},
                    "theme": {"mode": "light", "base": "light", "overrides": {}},
                }
            ),
            "utf-8",
        )
        old_file.write_text(json.dumps({"output": [str(old_dir)]}), "utf-8")
        p = Preferences()
        assert p.get_last_dir("output") == str(new_dir)
        assert not old_file.exists()


# ═══════════════════════════════════════════════════════════════════════
# clear_qt_file_dialog_history
# ═══════════════════════════════════════════════════════════════════════


class TestClearQtFileDialogHistory:
    """clear_qt_file_dialog_history。"""

    def test_calls_qsettings(self, monkeypatch):
        """验证 QSettings 在两个 format 上被调用。"""
        calls = []

        class _FakeSettings:
            NativeFormat = 0
            IniFormat = 1
            UserScope = 2

            def __init__(self, fmt, scope, org):
                calls.append(("init", fmt))

            def beginGroup(self, g):
                calls.append(("beginGroup", g))

            def contains(self, key):
                calls.append(("contains", key))
                return True

            def remove(self, key):
                calls.append(("remove", key))

            def endGroup(self):
                calls.append(("endGroup",))

            def sync(self):
                calls.append(("sync",))

        monkeypatch.setattr(
            "PySide6.QtCore.QSettings",
            _FakeSettings,
            raising=False,
        )
        from astrocrawl.utils.preferences import clear_qt_file_dialog_history

        try:
            clear_qt_file_dialog_history()
        except ImportError:
            pytest.skip("PySide6 not available")
        # 验证 native + ini 两个 format 都调用了 QSettings
        assert len(calls) >= 2
        init_fmts = [c[1] for c in calls if c[0] == "init"]
        assert len(init_fmts) == 2


# ═══════════════════════════════════════════════════════════════════════
# AI Profile API (Issue #179)
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def ai_prefs(tmp_path, monkeypatch):
    monkeypatch.setattr("astrocrawl.utils.preferences.PREFERENCES_FILE", tmp_path / "prefs.json")
    p = Preferences()
    return p


class TestAIProfileBasics:
    @staticmethod
    def _seed_default(prefs):
        prefs.save_ai_profile(AIProfile(name="default"))

    def test_get_default_profile_model(self, ai_prefs):
        self._seed_default(ai_prefs)
        prof = ai_prefs.get_ai_profile("default")
        assert prof is not None
        assert prof.model == "gpt-4o-mini"

    def test_save_and_get_ai_model(self, ai_prefs):
        ai_prefs.save_ai_profile(AIProfile(name="default", model="gpt-4o"))
        prof = ai_prefs.get_ai_profile("default")
        assert prof is not None
        assert prof.model == "gpt-4o"

    def test_get_temperature_default(self, ai_prefs):
        self._seed_default(ai_prefs)
        prof = ai_prefs.get_ai_profile("default")
        assert prof is not None
        assert prof.temperature == 0.1

    def test_save_and_get_temperature(self, ai_prefs):
        ai_prefs.save_ai_profile(AIProfile(name="default", temperature=0.7))
        prof = ai_prefs.get_ai_profile("default")
        assert prof is not None
        assert prof.temperature == 0.7

    def test_get_max_tokens_default(self, ai_prefs):
        self._seed_default(ai_prefs)
        prof = ai_prefs.get_ai_profile("default")
        assert prof is not None
        assert prof.max_tokens == 16384

    def test_save_and_get_max_tokens(self, ai_prefs):
        ai_prefs.save_ai_profile(AIProfile(name="default", max_tokens=4096))
        prof = ai_prefs.get_ai_profile("default")
        assert prof is not None
        assert prof.max_tokens == 4096

    def test_get_provider_default(self, ai_prefs):
        self._seed_default(ai_prefs)
        prof = ai_prefs.get_ai_profile("default")
        assert prof is not None
        assert prof.provider == "openai"

    def test_save_and_get_provider(self, ai_prefs):
        ai_prefs.save_ai_profile(AIProfile(name="default", provider="anthropic"))
        prof = ai_prefs.get_ai_profile("default")
        assert prof is not None
        assert prof.provider == "anthropic"

    def test_save_persists_model(self, ai_prefs):
        from astrocrawl.ai._profile import AIProfile

        ai_prefs.save_ai_profile(AIProfile(name="default", model="profile-model"))
        prof = ai_prefs.get_ai_profile("default")
        assert prof is not None
        assert prof.model == "profile-model"

    def test_save_persists_temperature(self, ai_prefs):
        from astrocrawl.ai._profile import AIProfile

        ai_prefs.save_ai_profile(AIProfile(name="default", temperature=0.9))
        prof = ai_prefs.get_ai_profile("default")
        assert prof is not None
        assert prof.temperature == 0.9

    def test_save_persists_max_tokens(self, ai_prefs):
        from astrocrawl.ai._profile import AIProfile

        ai_prefs.save_ai_profile(AIProfile(name="default", max_tokens=8192))
        prof = ai_prefs.get_ai_profile("default")
        assert prof is not None
        assert prof.max_tokens == 8192


class TestGetAIProfile:
    """get_ai_profile — 零副作用按名称读取 profile，返回 AIProfile | None。"""

    @staticmethod
    def _seed_default(prefs):
        prefs.save_ai_profile(AIProfile(name="default"))

    def test_returns_default_profile(self, ai_prefs):
        self._seed_default(ai_prefs)
        prof = ai_prefs.get_ai_profile("default")
        assert prof is not None
        assert prof.model == "gpt-4o-mini"
        assert prof.temperature == 0.1

    def test_returns_none_for_missing(self, ai_prefs):
        prof = ai_prefs.get_ai_profile("nonexistent")
        assert prof is None

    def test_does_not_change_active_profile(self, ai_prefs):
        ai_prefs.save_ai_profile(AIProfile(name="other"))
        ai_prefs.save_ai_profile(AIProfile())
        ai_prefs._data["ai_active_profile"] = "default"
        ai_prefs.get_ai_profile("other")
        assert ai_prefs._data["ai_active_profile"] == "default"

    def test_custom_profile_values(self, ai_prefs):
        prod = AIProfile(
            name="prod",
            model="gpt-4o",
            temperature=0.5,
            max_tokens=4096,
            api_key="sk-test",
            endpoint="https://custom.api.com/v1",
        )
        ai_prefs.save_ai_profile(prod)
        prof = ai_prefs.get_ai_profile("prod")
        assert prof is not None
        assert prof.model == "gpt-4o"
        assert prof.api_key == "sk-test"

    def test_no_disk_write(self, ai_prefs, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "astrocrawl.utils.preferences.PREFERENCES_FILE",
            tmp_path / "prefs.json",
        )
        ai_prefs._data["ai_profiles"] = [AIProfile(name="default").to_dict()]
        ai_prefs._data["ai_active_profile"] = "default"
        prof = ai_prefs.get_ai_profile("default")
        assert prof is not None
        assert prof.model == "gpt-4o-mini"
        assert not (tmp_path / "prefs.json").exists()


class TestSaveRemoveProfile:
    """save_ai_profile / remove_ai_profile / get_ai_profiles — 持久化 CRUD。"""

    def test_save_new_profile_appends(self, ai_prefs):
        ai_prefs.save_ai_profile(AIProfile(name="new_prof", provider="google", model="gemini"))
        profiles = ai_prefs.get_ai_profiles()
        names = [p.name for p in profiles]
        assert "new_prof" in names

    def test_save_updates_existing(self, ai_prefs):
        ai_prefs.save_ai_profile(AIProfile(name="default", model="updated-model"))
        prof = ai_prefs.get_ai_profile("default")
        assert prof is not None
        assert prof.model == "updated-model"
        assert len(ai_prefs.get_ai_profiles()) == 1

    def test_remove_profile_cleans_up(self, ai_prefs):
        ai_prefs.save_ai_profile(AIProfile(name="temp", provider="google"))
        assert ai_prefs.get_ai_profile("temp") is not None
        ai_prefs.remove_ai_profile("temp")
        assert ai_prefs.get_ai_profile("temp") is None

    def test_remove_existing_profile(self, ai_prefs):
        ai_prefs.save_ai_profile(AIProfile(name="default"))
        count_before = len(ai_prefs.get_ai_profiles())
        ai_prefs.remove_ai_profile("default")
        assert len(ai_prefs.get_ai_profiles()) == count_before - 1

    def test_remove_active_resets_to_first_remaining(self, ai_prefs):
        ai_prefs.save_ai_profile(AIProfile(name="first"))
        ai_prefs.save_ai_profile(AIProfile(name="second"))
        ai_prefs.set_active_ai_profile("second")
        assert ai_prefs.get_active_profile_name() == "second"
        ai_prefs.remove_ai_profile("second")
        assert ai_prefs.get_active_profile_name() == "first"

    def test_remove_last_profile_clears_active(self, ai_prefs):
        ai_prefs.save_ai_profile(AIProfile(name="only"))
        ai_prefs.set_active_ai_profile("only")
        ai_prefs.remove_ai_profile("only")
        assert ai_prefs.get_active_profile_name() == ""

    def test_remove_clears_last_profile_refs(self, ai_prefs):
        ai_prefs.save_ai_profile(AIProfile(name="ref_prof"))
        ai_prefs.set_last_ai_profile("rules_generation", "ref_prof")
        ai_prefs.remove_ai_profile("ref_prof")
        assert ai_prefs.get_last_ai_profile("rules_generation") is None

    def test_get_all_profiles_includes_default(self, ai_prefs):
        ai_prefs.save_ai_profile(AIProfile(name="default"))
        profiles = ai_prefs.get_ai_profiles()
        names = [p.name for p in profiles]
        assert "default" in names


class TestActiveProfile:
    """get_active_ai_profile / set_active_ai_profile / get_active_profile_name。"""

    def test_active_profile_returns_none_when_empty(self, ai_prefs):
        prof = ai_prefs.get_active_ai_profile()
        assert prof is None

    def test_active_profile_returns_seeded(self, ai_prefs):
        ai_prefs.save_ai_profile(AIProfile())
        prof = ai_prefs.get_active_ai_profile()
        assert prof is not None
        assert prof.name == ""

    def test_set_active_switches_profile(self, ai_prefs):
        ai_prefs.save_ai_profile(AIProfile(name="second"))
        ai_prefs.set_active_ai_profile("second")
        assert ai_prefs.get_active_profile_name() == "second"

    def test_set_active_nonexistent_is_noop(self, ai_prefs):
        ai_prefs.set_active_ai_profile("nonexistent")
        assert ai_prefs.get_active_profile_name() == ""

    def test_active_profile_name_empty_initially(self, ai_prefs):
        assert ai_prefs.get_active_profile_name() == ""


class TestLastProfileCMode:
    """get_last_ai_profile / set_last_ai_profile — C-mode 持久化。"""

    def test_default_no_last_profile(self, ai_prefs):
        assert ai_prefs.get_last_ai_profile("rules_generation") is None

    def test_set_and_get_last_profile(self, ai_prefs):
        ai_prefs.save_ai_profile(AIProfile(name="default"))
        ai_prefs.set_last_ai_profile("rules_generation", "default")
        assert ai_prefs.get_last_ai_profile("rules_generation") == "default"

    def test_last_profile_ignores_deleted(self, ai_prefs):
        ai_prefs.set_last_ai_profile("rules_generation", "deleted_prof")
        assert ai_prefs.get_last_ai_profile("rules_generation") is None

    def test_multiple_modules_independent(self, ai_prefs):
        ai_prefs.save_ai_profile(AIProfile(name="default"))
        ai_prefs.set_last_ai_profile("rules_generation", "default")
        ai_prefs.set_last_ai_profile("content_analysis", "default")
        assert ai_prefs.get_last_ai_profile("rules_generation") == "default"
        assert ai_prefs.get_last_ai_profile("content_analysis") == "default"


class TestEmptyProfiles:
    """空 profile 列表下所有 AI API 行为。"""

    def test_get_ai_profile_returns_none(self, ai_prefs):
        assert ai_prefs.get_ai_profile("default") is None

    def test_get_active_ai_profile_returns_none(self, ai_prefs):
        assert ai_prefs.get_active_ai_profile() is None

    def test_get_active_profile_name_empty(self, ai_prefs):
        assert ai_prefs.get_active_profile_name() == ""

    def test_get_ai_profiles_empty(self, ai_prefs):
        assert ai_prefs.get_ai_profiles() == []

    def test_get_ai_profile_names_empty(self, ai_prefs):
        assert ai_prefs.get_ai_profile_names() == []

    def test_get_last_ai_profile_returns_none(self, ai_prefs):
        assert ai_prefs.get_last_ai_profile("rules_generation") is None

    def test_save_ai_profile_sets_active(self, ai_prefs):
        ai_prefs.save_ai_profile(AIProfile(name="my_prof"))
        assert ai_prefs.get_active_profile_name() == "my_prof"

    def test_set_active_nonexistent_is_noop(self, ai_prefs):
        ai_prefs.set_active_ai_profile("nonexistent")
        assert ai_prefs.get_active_profile_name() == ""

    def test_remove_ai_profile_empty_noop(self, ai_prefs):
        ai_prefs.remove_ai_profile("anything")
        assert ai_prefs.get_ai_profiles() == []


class TestAIConfigFromProfile:
    """AIConfig.from_profile() — profile 到 AIConfig 映射。"""

    def test_maps_all_fields(self):
        from astrocrawl.ai._config import AIConfig
        from astrocrawl.ai._profile import AIProfile

        prof = AIProfile(
            name="test",
            provider="anthropic",
            model="claude-opus-4-7",
            temperature=0.5,
            max_tokens=8192,
            api_key="sk-test123",
            endpoint="https://custom.api.com",
        )
        config = AIConfig.from_profile(prof)
        assert config.api_key == "sk-test123"
        assert config.provider == "anthropic"
        assert config.base_url == "https://custom.api.com"
        assert config.default_model == "claude-opus-4-7"
        assert config.default_temperature == 0.5
        assert config.default_max_tokens == 8192

    def test_timeout_max_retries_use_defaults(self):
        from astrocrawl.ai._config import AIConfig
        from astrocrawl.ai._profile import AIProfile

        config = AIConfig.from_profile(AIProfile())
        assert config.timeout == 60.0
        assert config.max_retries == 2


# ═══════════════════════════════════════════════════════════════════════
# 全局设置
# ═══════════════════════════════════════════════════════════════════════


class TestGlobalSettingsDefaults:
    """全局设置字段默认值。"""

    def test_rules_auto_update_default(self, monkeypatch, tmp_path):
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        assert p.get_rules_auto_update() is True

    def test_trace_rules_default(self, monkeypatch, tmp_path):
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        assert p.get_trace_rules() is False

    def test_log_level_default(self, monkeypatch, tmp_path):
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        assert p.get_log_level() == "INFO"

    def test_output_gzip_default(self, monkeypatch, tmp_path):
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        assert p.get_output_gzip() is False

    def test_clear_context_cookies_default(self, monkeypatch, tmp_path):
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        assert p.get_clear_context_cookies() is False

    def test_rules_dirs_collapsed_default(self, monkeypatch, tmp_path):
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        assert p.get_rules_dirs_collapsed() is True

    def test_rules_dirs_enabled_default(self, monkeypatch, tmp_path):
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        assert p.get_rules_dirs_enabled() is True


class TestGlobalSettingsRoundtrip:
    """全局设置 get/set 往返 + 持久化恢复。"""

    def test_rules_auto_update_roundtrip(self, monkeypatch, tmp_path):
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        p.set_rules_auto_update(False)
        assert p.get_rules_auto_update() is False
        # 恢复
        p2 = Preferences()
        assert p2.get_rules_auto_update() is False

    def test_trace_rules_roundtrip(self, monkeypatch, tmp_path):
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        p.set_trace_rules(True)
        assert p.get_trace_rules() is True
        p2 = Preferences()
        assert p2.get_trace_rules() is True

    def test_log_level_roundtrip(self, monkeypatch, tmp_path):
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        p.set_log_level("DEBUG")
        assert p.get_log_level() == "DEBUG"
        p2 = Preferences()
        assert p2.get_log_level() == "DEBUG"

    def test_output_gzip_roundtrip(self, monkeypatch, tmp_path):
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        p.set_output_gzip(False)
        assert p.get_output_gzip() is False
        p2 = Preferences()
        assert p2.get_output_gzip() is False

    def test_clear_context_cookies_roundtrip(self, monkeypatch, tmp_path):
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        p.set_clear_context_cookies(True)
        assert p.get_clear_context_cookies() is True
        p2 = Preferences()
        assert p2.get_clear_context_cookies() is True

    def test_rules_dirs_collapsed_roundtrip(self, monkeypatch, tmp_path):
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        p.set_rules_dirs_collapsed(False)
        assert p.get_rules_dirs_collapsed() is False
        p2 = Preferences()
        assert p2.get_rules_dirs_collapsed() is False

    def test_rules_dirs_enabled_roundtrip(self, monkeypatch, tmp_path):
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        p.set_rules_dirs_enabled(False)
        assert p.get_rules_dirs_enabled() is False
        p2 = Preferences()
        assert p2.get_rules_dirs_enabled() is False

    def test_rules_dirs_default(self, monkeypatch, tmp_path):
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        assert p.get_rules_dirs() == []

    def test_rules_dirs_roundtrip(self, monkeypatch, tmp_path):
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        p.set_rules_dirs(["/tmp/rules1", "/tmp/rules2"])
        assert p.get_rules_dirs() == ["/tmp/rules1", "/tmp/rules2"]
        p2 = Preferences()
        assert p2.get_rules_dirs() == ["/tmp/rules1", "/tmp/rules2"]

    def test_all_global_fields_persist(self, monkeypatch, tmp_path):
        """一次设置全部 8 个字段, 新实例全部恢复。"""
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        p.set_rules_auto_update(False)
        p.set_trace_rules(True)
        p.set_log_level("DEBUG")
        p.set_output_gzip(False)
        p.set_clear_context_cookies(True)
        p.set_rules_dirs(["/tmp/a", "/tmp/b"])
        p.set_rules_dirs_collapsed(False)
        p.set_rules_dirs_enabled(False)
        # 新实例恢复
        p2 = Preferences()
        assert p2.get_rules_auto_update() is False
        assert p2.get_trace_rules() is True
        assert p2.get_log_level() == "DEBUG"
        assert p2.get_output_gzip() is False
        assert p2.get_clear_context_cookies() is True
        assert p2.get_rules_dirs() == ["/tmp/a", "/tmp/b"]
        assert p2.get_rules_dirs_collapsed() is False
        assert p2.get_rules_dirs_enabled() is False


# ═══════════════════════════════════════════════════════════════════════
# get_ai_profile_names — 直接调用（G1: 之前从未直接测试）
# ═══════════════════════════════════════════════════════════════════════


class TestGetAIProfileNames:
    """get_ai_profile_names — 返回所有 profile 名称列表。"""

    def test_returns_empty_list_initially(self, ai_prefs):
        names = ai_prefs.get_ai_profile_names()
        assert isinstance(names, list)
        assert names == []

    def test_returns_list_of_names(self, ai_prefs):
        ai_prefs.save_ai_profile(AIProfile(name="default"))
        names = ai_prefs.get_ai_profile_names()
        assert isinstance(names, list)
        assert "default" in names
        assert all(isinstance(n, str) for n in names)

    def test_includes_newly_saved_profile(self, ai_prefs):
        ai_prefs.save_ai_profile(AIProfile(name="new_one"))
        names = ai_prefs.get_ai_profile_names()
        assert "new_one" in names

    def test_excludes_removed_profile(self, ai_prefs):
        ai_prefs.save_ai_profile(AIProfile(name="temp"))
        ai_prefs.remove_ai_profile("temp")
        names = ai_prefs.get_ai_profile_names()
        assert "temp" not in names


# ═══════════════════════════════════════════════════════════════════════
# ai_profiles dict 格式迁移（G3: preferences.py:348）
# ═══════════════════════════════════════════════════════════════════════


class TestAIMigrationDictFormat:
    """ADR-0007: ai_profiles 旧 dict 格式 → 新 list 格式迁移（preferences.py:347-348）。"""

    def test_dict_format_migrated_in_memory_to_list(self, monkeypatch, tmp_path):
        """旧格式 {"default": {...}, "prod": {...}} → 内存中转为 list [{...}, {...}]。"""
        from astrocrawl.ai._profile import AIProfile

        prefs_file, _ = _patch_prefs_paths(monkeypatch, tmp_path)
        default_dict = AIProfile(name="default").to_dict()
        prod_dict = AIProfile(name="prod", provider="google").to_dict()
        prefs_file.write_text(
            json.dumps({"ai_profiles": {"default": default_dict, "prod": prod_dict}}),
            "utf-8",
        )
        p = Preferences()
        profiles = p.get_ai_profiles()
        names = {prof.name for prof in profiles}
        assert "default" in names
        assert "prod" in names
        # 内存中已迁移为 list 格式
        assert isinstance(p._data["ai_profiles"], list)

    def test_dict_format_persisted_on_next_save(self, monkeypatch, tmp_path):
        """迁移后任意写操作触发 _save → 磁盘格式也变为 list。"""
        from astrocrawl.ai._profile import AIProfile

        prefs_file, _ = _patch_prefs_paths(monkeypatch, tmp_path)
        prefs_file.write_text(
            json.dumps({"ai_profiles": {"default": AIProfile().to_dict()}}),
            "utf-8",
        )
        p = Preferences()
        # 触发一次保存（set_theme 会调用 _save）
        p.set_theme("dark", "dark", {})
        saved = json.loads(prefs_file.read_text("utf-8"))
        assert isinstance(saved["ai_profiles"], list)


# ═══════════════════════════════════════════════════════════════════════
# _migrate_old_file 损坏文件处理（G4: preferences.py:395-397）
# ═══════════════════════════════════════════════════════════════════════


class TestMigrationCorrupt:
    """旧 path_memory.json 损坏时的恢复行为。"""

    def test_corrupt_old_file_deleted(self, monkeypatch, tmp_path, caplog):
        """损坏的旧文件 → 删除并记录告警，不阻塞启动。"""
        prefs_file, old_file = _patch_prefs_paths(monkeypatch, tmp_path)
        assert not prefs_file.exists()
        old_file.write_text("{invalid json", "utf-8")
        p = Preferences()
        # 不抛异常，正常加载
        assert p.get_theme()["mode"] == "light"
        assert not old_file.exists()
        assert "old_path_memory_corrupt" in caplog.text

    def test_corrupt_old_file_non_dict(self, monkeypatch, tmp_path):
        """旧文件为合法 JSON 但非 dict → 被当作有效迁移处理（无数据可迁），文件删除。"""
        prefs_file, old_file = _patch_prefs_paths(monkeypatch, tmp_path)
        old_file.write_text("[1, 2, 3]", "utf-8")
        p = Preferences()
        assert p.get_theme()["mode"] == "light"
        # 非 dict 旧文件仍被正常完成（无数据可迁），文件删除且不抛异常
        assert not old_file.exists()


# ═══════════════════════════════════════════════════════════════════════
# get_preferences() 模块级单例（G5: preferences.py:410-411）
# ═══════════════════════════════════════════════════════════════════════


class TestGetPreferencesSingleton:
    """get_preferences() 模块级工厂 — 惰性初始化 + 单例复用。"""

    def test_creates_singleton_on_first_call(self, monkeypatch, tmp_path):
        """首次调用 get_preferences() 创建实例。"""
        _patch_prefs_paths(monkeypatch, tmp_path)
        import astrocrawl.utils.preferences as _prefs_mod

        # 确保从干净状态开始
        monkeypatch.setattr(_prefs_mod, "_preferences", None)
        p1 = _prefs_mod.get_preferences()
        assert p1 is not None
        assert isinstance(p1, Preferences)

    def test_returns_same_instance_on_second_call(self, monkeypatch, tmp_path):
        """第二次调用返回同一实例。"""
        _patch_prefs_paths(monkeypatch, tmp_path)
        import astrocrawl.utils.preferences as _prefs_mod

        monkeypatch.setattr(_prefs_mod, "_preferences", None)
        p1 = _prefs_mod.get_preferences()
        p2 = _prefs_mod.get_preferences()
        assert p1 is p2

    def test_singleton_persists_data(self, monkeypatch, tmp_path):
        """通过单例设置的数据在后续调用中可见。"""
        _patch_prefs_paths(monkeypatch, tmp_path)
        import astrocrawl.utils.preferences as _prefs_mod

        monkeypatch.setattr(_prefs_mod, "_preferences", None)
        p1 = _prefs_mod.get_preferences()
        p1.set_log_level("DEBUG")
        p2 = _prefs_mod.get_preferences()
        assert p2.get_log_level() == "DEBUG"


# ═══════════════════════════════════════════════════════════════════════
# _load path_memory 验证 — 非 list entries 跳过（G6: preferences.py:323-324）
# ═══════════════════════════════════════════════════════════════════════


class TestPathMemoryNonList:
    """_load 中对 path_memory 条目类型的验证。"""

    def test_non_list_entries_skipped(self, monkeypatch, tmp_path):
        """path_memory 中某 key 的值为字符串而非 list → 跳过该条目。"""
        prefs_file, _ = _patch_prefs_paths(monkeypatch, tmp_path)
        valid_dir = tmp_path / "valid_dir"
        valid_dir.mkdir()
        prefs_file.write_text(
            json.dumps(
                {
                    "path_memory": {
                        "good_key": [str(valid_dir)],
                        "bad_key": "not_a_list",
                    },
                    "theme": {"mode": "light", "base": "light", "overrides": {}},
                }
            ),
            "utf-8",
        )
        p = Preferences()
        # 合法条目正常加载
        assert p.get_last_dir("good_key") == str(valid_dir)
        # 非法条目被跳过 — 不抛异常，返回 fallback
        assert p.get_last_dir("bad_key", fallback="fb") == "fb"

    def test_mixed_valid_invalid_entries(self, monkeypatch, tmp_path):
        """path_memory 混合合法与非法条目，合法的不受影响。"""
        prefs_file, _ = _patch_prefs_paths(monkeypatch, tmp_path)
        d1 = tmp_path / "d1"
        d1.mkdir()
        d2 = tmp_path / "d2"
        d2.mkdir()
        prefs_file.write_text(
            json.dumps(
                {
                    "path_memory": {
                        "output": [str(d1)],
                        "broken": 42,  # 整数非法
                        "proxy": [str(d2)],
                    },
                    "theme": {"mode": "light", "base": "light", "overrides": {}},
                }
            ),
            "utf-8",
        )
        p = Preferences()
        assert p.get_last_dir("output") == str(d1)
        assert p.get_last_dir("proxy") == str(d2)


# ═══════════════════════════════════════════════════════════════════════
# save_ai_profile — 首次保存时自动设置 active_profile（G7: preferences.py:186-187）
# ═══════════════════════════════════════════════════════════════════════


class TestSaveFirstProfileAutoActive:
    """save_ai_profile 对 active_profile 的保护行为。"""

    def test_save_first_profile_sets_active(self, ai_prefs):
        """保存首个 profile 自动设为 active。"""
        ai_prefs.save_ai_profile(AIProfile(name="first"))
        assert ai_prefs.get_active_profile_name() == "first"

    def test_save_profile_does_not_change_active(self, ai_prefs):
        """保存新 profile 不会覆盖已有 active。"""
        ai_prefs.save_ai_profile(AIProfile(name="default"))
        assert ai_prefs.get_active_profile_name() == "default"
        ai_prefs.save_ai_profile(AIProfile(name="another"))
        assert ai_prefs.get_active_profile_name() == "default"


# ═══════════════════════════════════════════════════════════════════════
# Proxy Profile CRUD API (ADR-0010)
# ═══════════════════════════════════════════════════════════════════════


class TestProxyProfileCRUD:
    """get_proxy_profiles / get_proxy_profile_names / get_proxy_profile / save_proxy_profile / remove_proxy_profile。"""

    def test_get_profiles_empty_by_default(self, monkeypatch, tmp_path):
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        assert p.get_proxy_profiles() == []

    def test_get_profile_names_empty(self, monkeypatch, tmp_path):
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        assert p.get_proxy_profile_names() == []

    def test_get_profile_none_for_missing(self, monkeypatch, tmp_path):
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        assert p.get_proxy_profile("nonexistent") is None

    def test_save_new_profile_appends(self, monkeypatch, tmp_path):
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        from astrocrawl.proxy._config import ProxyProfile

        p.save_proxy_profile(ProxyProfile(name="my-proxy"))
        assert p.get_proxy_profile("my-proxy") is not None
        assert p.get_proxy_profile("my-proxy").name == "my-proxy"

    def test_save_updates_existing(self, monkeypatch, tmp_path):
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        from astrocrawl.proxy._config import ProxyEndpointSpec, ProxyProfile

        p.save_proxy_profile(ProxyProfile(name="prox", proxies=(ProxyEndpointSpec(label="a", host="h1"),)))
        p.save_proxy_profile(ProxyProfile(name="prox", proxies=(ProxyEndpointSpec(label="b", host="h2"),)))
        result = p.get_proxy_profile("prox")
        assert result is not None
        assert len(result.proxies) == 1
        assert result.proxies[0].label == "b"

    def test_remove_non_default(self, monkeypatch, tmp_path):
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        from astrocrawl.proxy._config import ProxyProfile

        p.save_proxy_profile(ProxyProfile(name="temp"))
        assert p.get_proxy_profile("temp") is not None
        p.remove_proxy_profile("temp")
        assert p.get_proxy_profile("temp") is None

    def test_remove_default_succeeds(self, monkeypatch, tmp_path):
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        from astrocrawl.proxy._config import ProxyProfile

        p.save_proxy_profile(ProxyProfile(name="default"))
        p.remove_proxy_profile("default")
        assert p.get_proxy_profile("default") is None

    def test_remove_default_force(self, monkeypatch, tmp_path):
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        from astrocrawl.proxy._config import ProxyProfile

        p.save_proxy_profile(ProxyProfile(name="default"))
        p.remove_proxy_profile("default", force=True)
        assert p.get_proxy_profile("default") is None

    def test_remove_cleans_last_used_refs(self, monkeypatch, tmp_path):
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        from astrocrawl.proxy._config import ProxyProfile

        profile = ProxyProfile(name="to-remove")
        p.save_proxy_profile(profile)
        saved = p.get_proxy_profile("to-remove")
        p.set_proxy_last_used("preview", saved.uuid, "")
        p.remove_proxy_profile("to-remove")
        assert p.get_proxy_last_used("preview") is None

    def test_save_persists_to_disk(self, monkeypatch, tmp_path):
        prefs_file, _ = _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        from astrocrawl.proxy._config import ProxyProfile

        p.save_proxy_profile(ProxyProfile(name="persisted"))
        # 新实例从磁盘恢复
        p2 = Preferences()
        result = p2.get_proxy_profile("persisted")
        assert result is not None
        assert result.name == "persisted"

    def test_get_all_profiles_includes_saved(self, monkeypatch, tmp_path):
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        from astrocrawl.proxy._config import ProxyProfile

        p.save_proxy_profile(ProxyProfile(name="a"))
        p.save_proxy_profile(ProxyProfile(name="b"))
        profiles = p.get_proxy_profiles()
        names = {prof.name for prof in profiles}
        assert "a" in names
        assert "b" in names

    def test_get_profile_names_matches_profiles(self, monkeypatch, tmp_path):
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        from astrocrawl.proxy._config import ProxyProfile

        p.save_proxy_profile(ProxyProfile(name="a"))
        p.save_proxy_profile(ProxyProfile(name="b"))
        names = p.get_proxy_profile_names()
        assert sorted(names) == ["a", "b"]


class TestProxyLastUsed:
    """get_proxy_last_used / set_proxy_last_used — consumer→profile 映射。"""

    def test_default_none(self, monkeypatch, tmp_path):
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        assert p.get_proxy_last_used("preview") is None

    def test_set_and_get(self, monkeypatch, tmp_path):
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        from astrocrawl.proxy._config import ProxyProfile

        profile = ProxyProfile(name="prox")
        p.save_proxy_profile(profile)
        saved = p.get_proxy_profile("prox")
        p.set_proxy_last_used("preview", saved.uuid, "1.2.3.4:8080")
        entry = p.get_proxy_last_used("preview")
        assert entry is not None
        assert entry["profile"] == saved.uuid
        assert entry["node"] == "1.2.3.4:8080"

    def test_persists_to_disk(self, monkeypatch, tmp_path):
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        from astrocrawl.proxy._config import ProxyProfile

        profile = ProxyProfile(name="prox")
        p.save_proxy_profile(profile)
        saved = p.get_proxy_profile("prox")
        p.set_proxy_last_used("preview", saved.uuid, "1.2.3.4:8080")
        p2 = Preferences()
        entry = p2.get_proxy_last_used("preview")
        assert entry is not None
        assert entry["profile"] == saved.uuid

    def test_auto_cleans_stale_ref(self, monkeypatch, tmp_path):
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        from astrocrawl.proxy._config import ProxyProfile

        profile = ProxyProfile(name="prox")
        p.save_proxy_profile(profile)
        saved = p.get_proxy_profile("prox")
        p.set_proxy_last_used("preview", saved.uuid, "")
        p.remove_proxy_profile("prox")
        assert p.get_proxy_last_used("preview") is None

    def test_stale_ref_cleaned_from_disk(self, monkeypatch, tmp_path):
        prefs_file, _ = _patch_prefs_paths(monkeypatch, tmp_path)
        from astrocrawl.proxy._config import ProxyProfile

        p = Preferences()
        profile = ProxyProfile(name="prox")
        p.save_proxy_profile(profile)
        saved = p.get_proxy_profile("prox")
        p.set_proxy_last_used("preview", saved.uuid, "")
        assert p.get_proxy_last_used("preview") is not None
        raw = json.loads(prefs_file.read_text("utf-8"))
        raw["proxy_profiles"] = []
        prefs_file.write_text(json.dumps(raw), "utf-8")
        p2 = Preferences()
        assert p2.get_proxy_last_used("preview") is None
        p3 = Preferences()
        assert p3.get_proxy_last_used("preview") is None

    def test_multiple_consumers_independent(self, monkeypatch, tmp_path):
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        from astrocrawl.proxy._config import ProxyProfile

        profile = ProxyProfile(name="prox")
        p.save_proxy_profile(profile)
        saved = p.get_proxy_profile("prox")
        p.set_proxy_last_used("preview", saved.uuid, "node1")
        p.set_proxy_last_used("ai", saved.uuid, "node2")
        assert p.get_proxy_last_used("preview")["node"] == "node1"
        assert p.get_proxy_last_used("ai")["node"] == "node2"

    def test_empty_profile_uuid_returns_none(self, monkeypatch, tmp_path):
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        p.set_proxy_last_used("preview", "", "")
        assert p.get_proxy_last_used("preview") is None

    def test_get_parsed_proxy_for_resolves(self, monkeypatch, tmp_path):
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        from astrocrawl.proxy._config import ProxyEndpointSpec, ProxyProfile

        profile = ProxyProfile(name="prox", proxies=(ProxyEndpointSpec(host="1.2.3.4", port=8080),))
        p.save_proxy_profile(profile)
        saved = p.get_proxy_profile("prox")
        p.set_proxy_last_used("preview", saved.uuid, "HTTP:1.2.3.4:8080")
        parsed = p.get_parsed_proxy_for("preview")
        assert parsed is not None
        assert parsed.host == "1.2.3.4"
        assert parsed.port == 8080

    def test_get_parsed_proxy_for_old_format(self, monkeypatch, tmp_path):
        """旧格式 host:port 向后兼容。"""
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        from astrocrawl.proxy._config import ProxyEndpointSpec, ProxyProfile

        profile = ProxyProfile(name="prox", proxies=(ProxyEndpointSpec(host="1.2.3.4", port=8080),))
        p.save_proxy_profile(profile)
        saved = p.get_proxy_profile("prox")
        p.set_proxy_last_used("preview", saved.uuid, "1.2.3.4:8080")
        parsed = p.get_parsed_proxy_for("preview")
        assert parsed is not None
        assert parsed.host == "1.2.3.4"
        assert parsed.port == 8080

    def test_get_parsed_proxy_for_none_when_unset(self, monkeypatch, tmp_path):
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        assert p.get_parsed_proxy_for("preview") is None

    def test_save_proxy_profile_rejects_duplicates(self, monkeypatch, tmp_path):
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        from astrocrawl.proxy._config import ProxyEndpointSpec, ProxyProfile

        ep1 = ProxyEndpointSpec(host="1.2.3.4", port=8080)
        ep2 = ProxyEndpointSpec(host="1.2.3.4", port=8080)  # same host:port, same type
        profile = ProxyProfile(name="dup", proxies=(ep1, ep2))
        with pytest.raises(ValueError, match="端点重复"):
            p.save_proxy_profile(profile)

    def test_save_proxy_profile_allows_different_types(self, monkeypatch, tmp_path):
        """同 host:port 但不同 type 不视为重复。"""
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        from astrocrawl.proxy._config import ProxyEndpointSpec, ProxyProfile, ProxyType

        ep1 = ProxyEndpointSpec(type=ProxyType.HTTP, host="1.2.3.4", port=8080)
        ep2 = ProxyEndpointSpec(type=ProxyType.SOCKS5, host="1.2.3.4", port=8080)
        profile = ProxyProfile(name="multi-type", proxies=(ep1, ep2))
        p.save_proxy_profile(profile)  # 不抛异常
        saved = p.get_proxy_profile("multi-type")
        assert len(saved.proxies) == 2


class TestProxyCorruption:
    """proxy_profiles / proxy_last_used 损坏恢复。"""

    def test_proxy_profiles_non_list_falls_back(self, monkeypatch, tmp_path):
        prefs_file, _ = _patch_prefs_paths(monkeypatch, tmp_path)
        prefs_file.write_text(
            json.dumps({"proxy_profiles": "not_a_list", "theme": {"mode": "light", "base": "light", "overrides": {}}}),
            "utf-8",
        )
        p = Preferences()
        assert p.get_proxy_profiles() == []
        assert p.get_proxy_profile_names() == []

    def test_proxy_last_used_non_dict_falls_back(self, monkeypatch, tmp_path):
        prefs_file, _ = _patch_prefs_paths(monkeypatch, tmp_path)
        prefs_file.write_text(
            json.dumps({"proxy_last_used": 42, "theme": {"mode": "light", "base": "light", "overrides": {}}}),
            "utf-8",
        )
        p = Preferences()
        assert p.get_proxy_last_used("preview") is None
        from astrocrawl.proxy._config import ProxyProfile

        profile = ProxyProfile(name="prox")
        p.save_proxy_profile(profile)
        saved = p.get_proxy_profile("prox")
        p.set_proxy_last_used("preview", saved.uuid, "")
        assert p.get_proxy_last_used("preview") is not None

    @pytest.mark.parametrize("bad_value", [None, 42, "string", True])
    def test_proxy_profiles_non_list_loaded_silently(self, monkeypatch, tmp_path, bad_value):
        prefs_file, _ = _patch_prefs_paths(monkeypatch, tmp_path)
        prefs_file.write_text(
            json.dumps({"proxy_profiles": bad_value, "theme": {"mode": "light", "base": "light", "overrides": {}}}),
            "utf-8",
        )
        p = Preferences()
        assert p.get_proxy_profiles() == []  # 静默回退，不抛异常


# ═══════════════════════════════════════════════════════════════════════
# 语言 getter/setter
# ═══════════════════════════════════════════════════════════════════════


class TestLanguage:
    """get_language / set_language。"""

    def test_default_language_is_zh_cn(self, monkeypatch, tmp_path):
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        assert p.get_language() == "zh_CN"

    def test_set_language_en(self, monkeypatch, tmp_path):
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        p.set_language("en")
        assert p.get_language() == "en"

    def test_set_language_persists(self, monkeypatch, tmp_path):
        _patch_prefs_paths(monkeypatch, tmp_path)
        p = Preferences()
        p.set_language("en")
        p2 = Preferences()
        assert p2.get_language() == "en"

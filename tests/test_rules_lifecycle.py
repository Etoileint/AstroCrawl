"""特征测试：规则生命周期 — 启用/禁用、热重载、加载降级、导入导出、安全门控。

测试文件覆盖 issue #121 的核心验收标准。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

import pytest

from astrocrawl._constants import MAX_RULE_FILE_SIZE
from astrocrawl._types import DEFAULT_EXTRACTION_TYPE
from astrocrawl.rules._io import (
    check_cache_size,
    cleanup_tmp_files,
    export_all_rules,
    export_rule,
    export_rule_to_file,
    import_rule,
    import_rule_preview,
    safe_read_rule_file,
    safe_write_rule_file,
)
from astrocrawl.rules._lifecycle import RuleLifecycle, setup_rule_directories
from astrocrawl.rules._loader import build_rule_snapshot
from astrocrawl.rules._markdown import clean_markdown_wrapper
from astrocrawl.rules._matcher import match_url
from astrocrawl.rules._schema import RuleSchema

# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════


def _make_rule_dict(name="test_rule", domains=None, fields=None, enabled=True):
    return {
        "name": name,
        "version": 1,
        "schema_version": 1,
        "enabled": enabled,
        "match": {
            "domains": domains or [],
            "url_pattern": "",
            "scope": "domain_all" if domains else "any",
        },
        "fields": fields or {"heading": {"selector": "h1"}},
        "options": {"keep_body_text": False, "follow_links": True},
    }


def _write_rule_file(dir_path: Path, rule_dict: dict) -> Path:
    import json as _json

    name = rule_dict.get("name", "unnamed")
    p = dir_path / f"{name}.json"
    p.write_text(_json.dumps(rule_dict), encoding="utf-8")
    return p


# ═══════════════════════════════════════════════════════════════════
# 生命周期
# ═══════════════════════════════════════════════════════════════════


class TestRuleLifecycle:
    """启用/禁用、热重载、加载降级。"""

    def test_disabled_rules_not_in_rules_tuple(self):
        """禁用规则出现在 by_name 但不出现在 rules 元组中。"""
        with tempfile.TemporaryDirectory() as tmp:
            _write_rule_file(Path(tmp), _make_rule_dict("enabled_rule", enabled=True))
            _write_rule_file(Path(tmp), _make_rule_dict("disabled_rule", enabled=False))

            from astrocrawl.config import CrawlerConfig

            cfg = CrawlerConfig()
            # 使用临时状态文件隔离，避免 ~/.astrocrawl/rules_state.json 干扰
            state_file = Path(tmp) / "rules_state.json"
            snap = build_rule_snapshot(cfg, state_file=state_file, extra_rules_dirs=[tmp])

            # 两条规则都在 by_name 中（完整目录）
            assert "enabled_rule" in snap.by_name
            assert "disabled_rule" in snap.by_name
            assert snap.by_name["enabled_rule"].enabled is True
            assert snap.by_name["disabled_rule"].enabled is False
            # 仅已启用规则在 rules 元组中（匹配器/引擎消费）
            rule_names = [r.name for r in snap.rules]
            assert "enabled_rule" in rule_names
            assert "disabled_rule" not in rule_names

    def test_disabled_rule_excluded_from_by_domain_integration(self):
        """D1: 集成验证 build_rule_snapshot 的 by_domain 不含禁用规则。"""
        with tempfile.TemporaryDirectory() as tmp:
            _write_rule_file(Path(tmp), _make_rule_dict("enabled_r", domains=["example.com"], enabled=True))
            _write_rule_file(Path(tmp), _make_rule_dict("disabled_r", domains=["example.com"], enabled=False))

            from astrocrawl.config import CrawlerConfig

            cfg = CrawlerConfig()
            # 使用临时状态文件隔离，避免 ~/.astrocrawl/rules_state.json 干扰
            state_file = Path(tmp) / "rules_state.json"
            snap = build_rule_snapshot(cfg, state_file=state_file, extra_rules_dirs=[tmp])
            # 文件级 enabled=False → 不在 by_domain 中
            assert "enabled_r" in snap.by_domain.get("example.com", ())
            assert "disabled_r" not in snap.by_domain.get("example.com", ())

    def test_state_file_disable_excludes_from_by_domain(self):
        """D1: rules_state.json 禁用规则 → 不在 by_domain 也不在 rules 中。

        规则文件声明 enabled=True，但 state file 覆盖为 disabled——
        这是 GUI disable 按钮触发的真实路径。
        """
        import json as _json

        with tempfile.TemporaryDirectory() as tmp:
            # 规则文件：enabled=True（文件级启用）
            _write_rule_file(Path(tmp), _make_rule_dict("state_disabled_r", domains=["example.com"], enabled=True))

            from astrocrawl.config import CrawlerConfig

            cfg = CrawlerConfig()
            # 预填充 state file 覆盖为 disabled
            state_file = Path(tmp) / "rules_state.json"
            state_data = {"version": 1, "disabled": ["state_disabled_r"]}
            state_file.write_text(_json.dumps(state_data), encoding="utf-8")

            snap = build_rule_snapshot(cfg, state_file=state_file, extra_rules_dirs=[tmp])
            # state file 覆盖后 enabled=False
            assert snap.by_name["state_disabled_r"].enabled is False
            # 不在 by_domain 中
            assert "state_disabled_r" not in snap.by_domain.get("example.com", ())
            # 不在 rules 元组中
            assert "state_disabled_r" not in [r.name for r in snap.rules]

    def test_initial_load_failure_returns_default_only(self):
        """N49: 首次加载时 build_rule_snapshot 异常 → 降级 default-only。"""
        from unittest.mock import patch

        from astrocrawl.config import CrawlerConfig

        cfg = CrawlerConfig()
        lc = RuleLifecycle(cfg)

        with patch("astrocrawl.rules._lifecycle.build_rule_snapshot", side_effect=RuntimeError("simulated")):
            snap = lc.initial_load()
            assert len(snap.rules) == 0
            assert DEFAULT_EXTRACTION_TYPE in snap.by_name

    def test_default_not_in_custom_rules(self):
        """N37/N107: default 不出现在自定义规则的 rules tuple 中，但始终在 by_name 中。"""
        with tempfile.TemporaryDirectory() as tmp:
            _write_rule_file(Path(tmp), _make_rule_dict("my_rule", domains=["example.com"]))
            from astrocrawl.config import CrawlerConfig

            cfg = CrawlerConfig()
            snap = build_rule_snapshot(cfg, extra_rules_dirs=[tmp])
            assert "default" not in [r.name for r in snap.rules]
            assert "default" in snap.by_name

    def test_build_snapshot_records_path_and_source(self):
        """构建快照时记录每条规则的路径和来源 (os.walk provenance)。"""
        with tempfile.TemporaryDirectory() as tmp:
            _write_rule_file(Path(tmp), _make_rule_dict("my_rule", domains=["example.com"]))
            from astrocrawl.config import CrawlerConfig

            cfg = CrawlerConfig()
            snap = build_rule_snapshot(cfg, extra_rules_dirs=[tmp])
            assert snap.get_path("my_rule") is not None
            assert snap.get_path("my_rule").name == "my_rule.json"
            assert snap.get_source("my_rule") == "user"
            # default 规则无文件，路径应为 None
            assert snap.get_path("default") is None

    def test_reload_replaces_snapshot(self):
        """N22: 热重载成功时原子替换快照 + 缓存失效。"""
        from astrocrawl.config import CrawlerConfig

        cfg = CrawlerConfig()
        lc = RuleLifecycle(cfg)
        lc.initial_load()
        assert lc.last_load_ok

        snap_before = lc.get_snapshot()
        lc.reload()
        snap_after = lc.get_snapshot()

        assert lc.last_load_ok
        assert snap_after is not snap_before  # new snapshot object
        # N22: 新快照自带空 cache
        assert len(snap_after._match_cache) == 0

    def test_reload_invalidates_match_cache(self):
        """N22: 热重载后新快照的匹配缓存为空。"""
        from astrocrawl.config import CrawlerConfig

        cfg = CrawlerConfig()
        lc = RuleLifecycle(cfg)
        lc.initial_load()

        # Pre-populate cache on old snapshot
        old_snapshot = lc.get_snapshot()
        match_url("https://test.example.com/page", old_snapshot)

        assert len(old_snapshot._match_cache) > 0
        new_snapshot = lc.reload()
        # 旧快照缓存不动，新快照自带空缓存
        assert len(old_snapshot._match_cache) > 0
        assert len(new_snapshot._match_cache) == 0

    def test_setup_rule_directories_creates_dirs(self):
        """启动时自动创建目录。"""
        with tempfile.TemporaryDirectory() as tmp:
            from astrocrawl.config import CrawlerConfig

            cfg = CrawlerConfig(rules_cache_dir=tmp + "/custom_cache")
            dirs = setup_rule_directories(cfg)
            assert dirs["user"].is_dir()
            assert dirs["cache"].is_dir()

    # ── snapshot property / lazy load ─────────────────────

    def test_snapshot_property_triggers_lazy_load(self):
        """snapshot property 首次访问时触发 initial_load。"""
        from astrocrawl.config import CrawlerConfig

        cfg = CrawlerConfig()
        lc = RuleLifecycle(cfg)
        # 不显式调用 initial_load()，直接访问 property
        snap = lc.snapshot
        assert DEFAULT_EXTRACTION_TYPE in snap.by_name
        assert lc.last_load_ok

    # ── property getters ──────────────────────────────────

    def test_load_error_property_empty_after_success(self):
        """成功加载后 load_error 为空字符串。"""
        from astrocrawl.config import CrawlerConfig

        cfg = CrawlerConfig()
        lc = RuleLifecycle(cfg)
        lc.initial_load()
        assert lc.last_load_ok
        assert lc.load_error == ""

    def test_load_error_property_set_after_failure(self):
        """加载失败后 load_error 包含错误信息。"""
        from unittest.mock import patch

        from astrocrawl.config import CrawlerConfig

        cfg = CrawlerConfig()
        lc = RuleLifecycle(cfg)
        with patch("astrocrawl.rules._lifecycle.build_rule_snapshot", side_effect=RuntimeError("simulated")):
            lc.initial_load()
        assert not lc.last_load_ok
        assert "simulated" in lc.load_error

    def test_loaded_at_set_after_successful_load(self):
        """成功加载后 loaded_at 为 positive float。"""
        from astrocrawl.config import CrawlerConfig

        cfg = CrawlerConfig()
        lc = RuleLifecycle(cfg)
        lc.initial_load()
        assert lc.loaded_at > 0

    # ── reload failure path ───────────────────────────────

    def test_reload_failure_preserves_old_snapshot(self):
        """reload 失败时保留旧快照，last_load_ok=False。"""
        from unittest.mock import patch

        from astrocrawl.config import CrawlerConfig

        cfg = CrawlerConfig()
        lc = RuleLifecycle(cfg)
        lc.initial_load()
        old_snap = lc.get_snapshot()

        with patch("astrocrawl.rules._lifecycle.build_rule_snapshot", side_effect=RuntimeError("reload boom")):
            result = lc.reload()

        assert result is old_snap
        assert not lc.last_load_ok
        assert "reload boom" in lc.load_error

    def test_reload_failure_when_never_loaded_falls_back_to_default(self):
        """reload 失败且从未成功加载 → 降级 default_only。"""
        from unittest.mock import patch

        from astrocrawl.config import CrawlerConfig

        cfg = CrawlerConfig()
        lc = RuleLifecycle(cfg)
        # 不调用 initial_load()，_snapshot 为 None

        with patch("astrocrawl.rules._lifecycle.build_rule_snapshot", side_effect=RuntimeError("boom")):
            snap = lc.reload()

        assert len(snap.rules) == 0
        assert DEFAULT_EXTRACTION_TYPE in snap.by_name
        assert not lc.last_load_ok

    # ── get_snapshot lazy init ────────────────────────────

    def test_get_snapshot_before_init_triggers_load(self):
        """get_snapshot() 在 _snapshot=None 时触发 initial_load。"""
        from astrocrawl.config import CrawlerConfig

        cfg = CrawlerConfig()
        lc = RuleLifecycle(cfg)
        # 不显式调用 initial_load()
        snap = lc.get_snapshot()
        assert DEFAULT_EXTRACTION_TYPE in snap.by_name
        assert lc.last_load_ok

    # ── get_health ────────────────────────────────────────

    def test_get_health_returns_up_after_successful_load(self):
        """成功加载后 get_health() 返回 UP。"""
        from astrocrawl.config import CrawlerConfig

        cfg = CrawlerConfig()
        lc = RuleLifecycle(cfg)
        lc.initial_load()
        health = lc.get_health()
        assert health.status == "UP"

    def test_get_health_returns_degraded_after_failed_load(self):
        """加载失败后 get_health() 返回 DEGRADED。"""
        from unittest.mock import patch

        from astrocrawl.config import CrawlerConfig

        cfg = CrawlerConfig()
        lc = RuleLifecycle(cfg)
        with patch("astrocrawl.rules._lifecycle.build_rule_snapshot", side_effect=RuntimeError("simulated")):
            lc.initial_load()

        health = lc.get_health()
        assert health.status == "DEGRADED"
        assert "simulated" in health.message

    def test_get_health_degraded_when_never_loaded(self):
        """从未加载时 get_health() 返回 DEGRADED + fallback message。"""
        from astrocrawl.config import CrawlerConfig

        cfg = CrawlerConfig()
        lc = RuleLifecycle(cfg)
        # 不调用 initial_load()，_last_load_ok=False, _load_error=""
        health = lc.get_health()
        assert health.status == "DEGRADED"
        assert "rules not loaded" in health.message


# ═══════════════════════════════════════════════════════════════════
# 安全读写
# ═══════════════════════════════════════════════════════════════════


class TestSafeIO:
    """S12/S13/S15/S25/N78 文件安全。"""

    def test_atomic_write_and_read(self):
        """S12: 原子写入 + S13: chmod 0o600。"""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "test.json"
            data = _make_rule_dict("atomic_test")
            safe_write_rule_file(p, data)

            assert p.exists()
            # S13: check permissions
            st = p.stat()
            assert st.st_mode & 0o777 == 0o600

            # Read back
            result = safe_read_rule_file(p)
            assert result["name"] == "atomic_test"

    def test_duplicate_key_rejected(self):
        """S25: 重复 JSON key 检测。"""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "dup.json"
            p.write_text('{"name": "test", "name": "test2", "fields": {}}', encoding="utf-8")
            with pytest.raises(ValueError, match="重复"):
                safe_read_rule_file(p)

    def test_file_size_limit_enforced(self):
        """S14: 写入超限时拒绝。"""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "big.json"
            data = {"name": "big", "fields": {"x": {"selector": "h1", "data": "x" * MAX_RULE_FILE_SIZE}}}
            with pytest.raises(ValueError, match="上限"):
                safe_write_rule_file(p, data)

    def test_cleanup_tmp_files(self):
        """S15: 清理过期 .tmp 文件。"""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            old_tmp = d / "rule.old.tmp"
            old_tmp.write_text("{}")
            # Set mtime to 25h ago
            old_mtime = time.time() - 25 * 3600
            os.utime(str(old_tmp), (old_mtime, old_mtime))

            new_tmp = d / "rule.new.tmp"
            new_tmp.write_text("{}")

            count = cleanup_tmp_files(d)
            assert count == 1
            assert not old_tmp.exists()
            assert new_tmp.exists()  # < 24h, not deleted

    def test_check_cache_size(self):
        """S16: 缓存总大小检查。"""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            p = d / "test.json"
            p.write_text("x" * 1000)
            total = check_cache_size(d, max_bytes=100)
            assert total == 1000
            # 仅警告，不拒绝

    def test_concurrent_write_safety(self):
        """N78: 外部编辑器并发写入——原子替换保证完整性。"""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "concurrent.json"
            safe_write_rule_file(p, _make_rule_dict("v1"))

            # 模拟：读 + 改 + 原子替换
            data = safe_read_rule_file(p)
            data["version"] = 2
            safe_write_rule_file(p, data)

            result = safe_read_rule_file(p)
            assert result["version"] == 2

    def test_cleanup_tmp_files_nonexistent_dir(self):
        """目录不存在时返回 0。"""
        count = cleanup_tmp_files(Path("/nonexistent/path/deadbeef"))
        assert count == 0

    def test_cleanup_tmp_files_oserror_caught(self):
        """OSError (stat/unlink) 被静默捕获。"""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            # Broken symlink → stat() raises OSError
            broken = d / "broken.tmp"
            broken.symlink_to("/nonexistent/target/path")
            # Permission denied on unlink: 旧文件在只读目录中
            subdir = d / "readonly"
            subdir.mkdir()
            old_tmp = subdir / "old.tmp"
            old_tmp.write_text("{}")
            old_mtime = time.time() - 25 * 3600
            os.utime(str(old_tmp), (old_mtime, old_mtime))
            subdir.chmod(0o555)
            try:
                count = cleanup_tmp_files(d)
                assert count == 0  # 两类 OSError 均被捕获
            finally:
                subdir.chmod(0o755)

    def test_check_cache_size_nonexistent_dir(self):
        """目录不存在时返回 0。"""
        total = check_cache_size(Path("/nonexistent/path/deadbeef"))
        assert total == 0

    def test_check_cache_size_oserror_caught(self):
        """OSError on stat() 被静默捕获——broken symlink。"""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            broken = d / "broken.json"
            broken.symlink_to("/nonexistent/target/path")
            total = check_cache_size(d, max_bytes=100)
            assert total == 0  # OSError 捕获，不计入


# ═══════════════════════════════════════════════════════════════════
# 导入 / 导出
# ═══════════════════════════════════════════════════════════════════


class TestImportExport:
    """N29/N30/N42/N102 导入导出语义。"""

    def test_export_strips_metadata(self):
        """N29: 导出剥离元数据（author 清空）。"""
        d = _make_rule_dict("export_test")
        d["author"] = "original_author"
        rule = RuleSchema.model_validate(d)
        result = export_rule(rule)
        assert result["author"] == ""  # N29: author stripped

    def test_export_clears_test_urls(self):
        """N30: 导出清空 test_urls + 隐私提示。"""
        d = _make_rule_dict("export_test")
        d["test_urls"] = [{"url": "https://secret.example.com/page"}]
        rule = RuleSchema.model_validate(d)
        result = export_rule(rule)
        assert result["test_urls"] == []
        assert "_export_note" in result

    def test_export_rule_to_file(self):
        """导出到文件。"""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "exported.json"
            rule = RuleSchema.model_validate(_make_rule_dict("export_to_file"))
            export_rule_to_file(rule, p)
            assert p.exists()
            result = safe_read_rule_file(p)
            assert result["name"] == "export_to_file"
            assert result["author"] == ""  # stripped

    def test_export_all_rules(self):
        """N102: 批量导出。"""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "exports"
            rules = [
                RuleSchema.model_validate(_make_rule_dict("rule1")),
                RuleSchema.model_validate(_make_rule_dict("rule2")),
            ]
            count = export_all_rules(rules, out)
            assert count == 2
            assert (out / "rule1.json").exists()
            assert (out / "rule2.json").exists()

    def test_import_rule_preview(self):
        """N42: 导入预览。"""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "to_import.json"
            safe_write_rule_file(p, _make_rule_dict("preview_test", domains=["example.com"]))
            preview = import_rule_preview(p)
            assert preview["name"] == "preview_test"
            assert preview["fields_count"] == 1
            assert "example.com" in preview["domains"]

    def test_import_rule(self):
        """N42: 导入规则写入。"""
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src.json"
            safe_write_rule_file(src, _make_rule_dict("imported"))
            dest = Path(tmp) / "dest"
            dest.mkdir()
            target = import_rule(src, dest)
            assert target.exists()
            assert target.name == "imported.json"

    def test_import_duplicate_raises(self):
        """重复导入拒绝。"""
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src.json"
            safe_write_rule_file(src, _make_rule_dict("dup"))
            dest = Path(tmp) / "dest"
            dest.mkdir()

            import_rule(src, dest)
            with pytest.raises(FileExistsError):
                import_rule(src, dest)

    def test_import_cleans_markdown(self):
        """N42: 自动清洗 markdown 代码块。"""
        raw = '```json\n{"name": "md_rule", "version": 1, "fields": {"h": {"selector": "h1"}}}\n```'
        cleaned = clean_markdown_wrapper(raw)
        parsed = json.loads(cleaned)
        assert parsed["name"] == "md_rule"

    def test_import_no_fallback_cleaning(self):
        """M11: {...} 回退已删除——无 fence 时返回原文，不拼接。"""
        raw = 'Here is a rule:\n{"name": "plain_rule", "version": 1, "fields": {"h": {"selector": "h1"}}}\nHope this helps!'
        result = clean_markdown_wrapper(raw)
        assert result == raw  # 无 fence，原样返回

    def test_export_all_rules_empty_name_rejected(self):
        """空 name 被 RuleSchema.model_validate 拒绝（Pydantic min_length=1）。"""
        with pytest.raises(ValueError):
            RuleSchema.model_validate({"name": "", "match": {"domains": ["example.com"], "scope": "domain_all"}})

    def test_export_all_rules_missing_name_skipped(self):
        """Pydantic 默认值导致空 name 的 RuleSchema 被 export_all_rules 跳过。"""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "exports"
            # Pydantic v2 对未传入的字段使用默认值而不校验 min_length
            nameless = RuleSchema.model_validate({"match": {"scope": "any"}, "fields": {}})
            assert nameless.name == ""
            rules = [
                RuleSchema.model_validate(_make_rule_dict("valid")),
                nameless,
            ]
            count = export_all_rules(rules, out)
            assert count == 1

    def test_export_all_rules_error_handled(self):
        """规则校验失败时捕获异常，记录 warning，不影响其他规则。"""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "exports"
            bad = RuleSchema.model_validate(
                {"name": "bad_rule", "schema_version": 999, "match": {"scope": "any"}, "fields": {}}
            )
            rules = [
                RuleSchema.model_validate(_make_rule_dict("good")),
                bad,
                RuleSchema.model_validate(_make_rule_dict("also_good")),
            ]
            count = export_all_rules(rules, out)
            assert count == 2  # bad_rule 被 validate_rule 跳过
            assert (out / "good.json").exists()
            assert (out / "also_good.json").exists()

"""特征测试：规则加载器 — 去重、排序、冲突检测、限制门控、错误路径。

覆盖 _loader.py 的边界路径和公开 API。
"""

from __future__ import annotations

import json as _json
import os
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from astrocrawl._constants import MAX_JSON_DEPTH, MAX_RULE_FILE_SIZE
from astrocrawl._types import DEFAULT_EXTRACTION_TYPE
from astrocrawl.rules._loader import (
    RuleConflictError,
    _check_json_depth,
    _deduplicate_rules,
    _detect_ambiguous_rules,
    _load_from_dir,
    _rule_sort_key,
    build_rule_snapshot,
    ensure_no_rule_conflicts,
    load_rule_file,
    validate_rule_files,
)
from astrocrawl.rules._schema import validate_rule

# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════


def _make_rule_dict(name="test_rule", domains=None, fields=None, enabled=True, version=1):
    return {
        "name": name,
        "version": version,
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
    name = rule_dict.get("name", "unnamed")
    p = dir_path / f"{name}.json"
    p.write_text(_json.dumps(rule_dict), encoding="utf-8")
    return p


def _rule_schema(name="test_rule", domains=None, version=1, enabled=True, scope=None, url_pattern=""):
    """构建 RuleSchema 用于纯函数测试。"""
    return validate_rule(
        {
            "name": name,
            "version": version,
            "schema_version": 1,
            "enabled": enabled,
            "match": {
                "domains": domains or [],
                "url_pattern": url_pattern,
                "scope": scope or ("domain_all" if domains else "any"),
            },
            "fields": {"x": {"selector": "h1"}},
        }
    )


# ═══════════════════════════════════════════════════════════════════
# RuleConflictError
# ═══════════════════════════════════════════════════════════════════


class TestRuleConflictError:
    def test_instantiation(self):
        err = RuleConflictError([["rule_a", "rule_b"], ["rule_c", "rule_d"]])
        assert len(err.conflicts) == 2
        assert err.conflicts[0] == ("rule_a", "rule_b")
        assert "2 组冲突规则" in str(err)

    def test_single_conflict_group(self):
        err = RuleConflictError([["r1", "r2"]])
        assert len(err.conflicts) == 1
        assert "1 组冲突规则" in str(err)

    def test_empty_conflicts(self):
        err = RuleConflictError([])
        assert len(err.conflicts) == 0


# ═══════════════════════════════════════════════════════════════════
# build_rule_snapshot — 目录/路径边界
# ═══════════════════════════════════════════════════════════════════


class TestBuildSnapshotDirPaths:
    """extra_rules_dirs 的各类边界路径。"""

    def test_rules_dirs_disabled_skips_extra(self, tmp_path):
        """rules_dirs_enabled=False → 跳过 extra_rules_dirs 中的规则。"""
        from astrocrawl.config import CrawlerConfig

        extra_dir = tmp_path / "extra"
        extra_dir.mkdir()
        _write_rule_file(extra_dir, _make_rule_dict("extra_rule", domains=["example.com"]))

        cfg = CrawlerConfig()
        snap = build_rule_snapshot(cfg, extra_rules_dirs=[str(extra_dir)], rules_dirs_enabled=False)
        assert "extra_rule" not in snap.by_name

    def test_extra_dir_is_file_not_directory(self, tmp_path, caplog):
        """extra_rules_dirs 指向文件而非目录 → WARNING。"""
        from astrocrawl.config import CrawlerConfig

        f = tmp_path / "not_a_dir"
        f.write_text("not a directory")

        cfg = CrawlerConfig()
        build_rule_snapshot(cfg, extra_rules_dirs=[str(f)])
        assert "rules_dir_not_directory" in caplog.text

    def test_extra_dir_not_found(self, tmp_path, caplog):
        """extra_rules_dirs 指向不存在的路径 → DEBUG。"""
        import logging

        from astrocrawl.config import CrawlerConfig

        caplog.set_level(logging.DEBUG)
        logging.getLogger("astrocrawl.rules.loader").setLevel(logging.DEBUG)
        cfg = CrawlerConfig()
        build_rule_snapshot(cfg, extra_rules_dirs=[str(tmp_path / "nope")])
        logging.getLogger("astrocrawl.rules.loader").setLevel(logging.NOTSET)
        assert "rules_dir_not_found" in caplog.text

    def test_load_from_dir_exception_graceful(self, tmp_path, caplog):
        """单个目录加载异常不影响其他目录。"""
        from astrocrawl.config import CrawlerConfig

        good_dir = tmp_path / "good"
        good_dir.mkdir()
        _write_rule_file(good_dir, _make_rule_dict("good_rule", domains=["example.com"]))

        # 为 good_dir 返回正常结果，其他目录不在此测试中
        with patch("astrocrawl.rules._loader._load_from_dir") as mock_load:
            mock_load.side_effect = lambda d, s: (
                [(_rule_schema("good_rule", ["example.com"]), good_dir / "good_rule.json", "user")]
                if str(d).endswith("good")
                else (_ for _ in ()).throw(RuntimeError("boom"))
            )

            cfg = CrawlerConfig()
            build_rule_snapshot(cfg, extra_rules_dirs=[str(good_dir)])
            assert "rule_load_dir_failed" in caplog.text

    def test_empty_deduped_returns_default_only(self):
        """去重后无规则 → RuleSnapshot.default_only()。"""
        from astrocrawl.config import CrawlerConfig

        # patch _load_from_dir 返回空，模拟所有目录均无有效规则
        with patch("astrocrawl.rules._loader._load_from_dir", return_value=[]):
            snap = build_rule_snapshot(CrawlerConfig())
            assert len(snap.rules) == 0
            assert DEFAULT_EXTRACTION_TYPE in snap.by_name


# ═══════════════════════════════════════════════════════════════════
# build_rule_snapshot — 数量限制
# ═══════════════════════════════════════════════════════════════════


class TestBuildSnapshotLimits:
    def test_max_rules_total_truncation(self, tmp_path, caplog, monkeypatch):
        """超过 MAX_RULES_TOTAL 时截断并警告。"""
        from astrocrawl.config import CrawlerConfig

        monkeypatch.setattr("astrocrawl.rules._loader.MAX_RULES_TOTAL", 3)

        for i in range(5):
            _write_rule_file(tmp_path, _make_rule_dict(f"rule_{i}", domains=[f"site{i}.com"]))

        cfg = CrawlerConfig()
        snap = build_rule_snapshot(cfg, extra_rules_dirs=[str(tmp_path)])
        assert len(snap.rules) <= 3
        assert "rule_limit_exceeded" in caplog.text

    def test_generic_rule_limit_enforced(self, tmp_path, caplog):
        """通用规则超过 rules_max_generic 时截断。"""
        from astrocrawl.config import CrawlerConfig

        for i in range(5):
            _write_rule_file(tmp_path, _make_rule_dict(f"generic_{i}"))  # no domains → generic

        cfg = replace(CrawlerConfig(), rules_max_generic=2)
        snap = build_rule_snapshot(cfg, extra_rules_dirs=[str(tmp_path)])
        generic_in_rules = sum(1 for r in snap.rules if r.is_generic)
        assert generic_in_rules <= 2
        assert "generic_rule_limit_exceeded" in caplog.text

    def test_generic_limit_zero_keeps_non_generic(self, tmp_path):
        """generic 限制为 0 时仍保留非泛型规则。"""
        from astrocrawl.config import CrawlerConfig

        _write_rule_file(tmp_path, _make_rule_dict("generic_r", domains=[]))
        _write_rule_file(tmp_path, _make_rule_dict("domain_r", domains=["example.com"]))

        cfg = replace(CrawlerConfig(), rules_max_generic=0)
        snap = build_rule_snapshot(cfg, extra_rules_dirs=[str(tmp_path)])
        # 域名规则不受影响
        assert any(r.name == "domain_r" for r in snap.rules)
        # 泛型规则被过滤
        assert not any(r.name == "generic_r" for r in snap.rules)


# ═══════════════════════════════════════════════════════════════════
# _load_from_dir — 错误路径
# ═══════════════════════════════════════════════════════════════════


class TestLoadFromDirErrors:
    def test_walk_error_callback(self, tmp_path, caplog):
        """os.walk 遍历子目录遇到权限错误 → _on_walk_error 记录 WARNING。"""
        subdir = tmp_path / "sub"
        subdir.mkdir()
        unreadable = subdir / "locked"
        unreadable.mkdir()
        (unreadable / "dummy").write_text("x")
        unreadable.chmod(0o000)
        try:
            _load_from_dir(tmp_path, "user")
        finally:
            unreadable.chmod(0o755)
        assert "rule_dir_unreadable" in caplog.text

    def test_top_level_permission_denied(self, tmp_path, caplog):
        """顶层目录不可读 → _on_walk_error 捕获 (onerror 始终提供) + 返回空列表。"""
        locked = tmp_path / "locked"
        locked.mkdir()
        locked.chmod(0o000)
        try:
            result = _load_from_dir(locked, "user")
            assert result == []
        finally:
            locked.chmod(0o755)
        # os.walk 的 onerror 回调捕获 PermissionError，记录 _on_walk_error
        assert "rule_dir_unreadable" in caplog.text

    def test_permission_error_outer_handler(self, tmp_path, caplog):
        """os.walk 直接 raise PermissionError（无 onerror 等效路径）→ 外层 except 捕获。"""
        locked = tmp_path / "locked"
        locked.mkdir()
        locked.chmod(0o000)
        # onerror=None 时 os.walk 会 raise PermissionError
        with patch("os.walk", side_effect=PermissionError("denied")):
            result = _load_from_dir(locked, "user")
            assert result == []
        assert "rule_dir_permission_denied" in caplog.text


# ═══════════════════════════════════════════════════════════════════
# load_rule_file — 错误路径
# ═══════════════════════════════════════════════════════════════════


class TestLoadRuleFileErrors:
    def test_zero_size_file_skipped(self, tmp_path):
        """空文件返回 None。"""
        p = tmp_path / "empty.json"
        p.write_text("", encoding="utf-8")
        result = load_rule_file(p, "user")
        assert result is None

    def test_json_depth_exceeded(self, tmp_path, caplog):
        """嵌套深度超过 MAX_JSON_DEPTH → 返回 None + WARNING。"""
        p = tmp_path / "deep.json"

        def _deep(d):
            if d <= 1:
                return {"name": "deep", "fields": {"x": {"selector": "h1"}}}
            return {"name": "deep", "fields": {"x": {"selector": "h1"}}, "e": _deep(d - 1)}

        p.write_text(_json.dumps(_deep(MAX_JSON_DEPTH + 1)), encoding="utf-8")
        result = load_rule_file(p, "user")
        assert result is None
        assert "rule_json_depth_exceeded" in caplog.text

    def test_not_a_rule_file_skipped(self, tmp_path):
        """无 'fields' key 的非规则文件被跳过。"""
        p = tmp_path / "not_rule.json"
        p.write_text(_json.dumps({"name": "config", "version": 1}), encoding="utf-8")
        result = load_rule_file(p, "user")
        assert result is None

    def test_invalid_json_value_error(self, tmp_path, caplog):
        """无效 JSON → ValueError 捕获，返回 None。"""
        p = tmp_path / "bad.json"
        p.write_text("{invalid json content!!!}", encoding="utf-8")
        result = load_rule_file(p, "user")
        assert result is None
        assert "rule_load_invalid" in caplog.text

    def test_generic_exception_caught(self, tmp_path, caplog):
        """非 ValueError 异常也被安全捕获。"""
        p = tmp_path / "will_crash.json"
        p.write_text(_json.dumps(_make_rule_dict("crash_rule")), encoding="utf-8")
        with patch("astrocrawl.rules._loader.safe_read_rule_file", side_effect=OSError("disk full")):
            result = load_rule_file(p, "user")
        assert result is None
        assert "rule_load_error" in caplog.text


# ═══════════════════════════════════════════════════════════════════
# _check_json_depth — 单元测试
# ═══════════════════════════════════════════════════════════════════


class TestCheckJsonDepth:
    def test_within_limit(self):
        # depth 2 嵌套，max_depth=5 → True
        assert _check_json_depth({"a": {"b": 1}}, max_depth=5) is True

    def test_at_limit(self):
        # depth 3 嵌套 ({a:{b:{c:1}}})，max_depth=3 → True
        assert _check_json_depth({"a": {"b": {"c": 1}}}, max_depth=3) is True

    def test_exceeds_limit(self):
        # depth 3 嵌套，max_depth=2 → False
        assert _check_json_depth({"a": {"b": {"c": 1}}}, max_depth=2) is False

    def test_list_nesting(self):
        # [1,[2,[3]]] depth 3，max_depth=3 → True
        assert _check_json_depth([1, [2, [3]]], max_depth=3) is True
        # [1,[2,[3]]] depth 3，max_depth=2 → False
        assert _check_json_depth([1, [2, [3]]], max_depth=2) is False

    def test_primitive(self):
        assert _check_json_depth("string", max_depth=0) is True
        assert _check_json_depth(42, max_depth=0) is True


# ═══════════════════════════════════════════════════════════════════
# _deduplicate_rules — 单元测试
# ═══════════════════════════════════════════════════════════════════


class TestDeduplicateRules:
    def test_same_source_newer_version_wins(self):
        """同一来源，高版本保留。"""
        r1 = _rule_schema("dup", version=1)
        r2 = _rule_schema("dup", version=3)
        rules = [(r1, Path("a.json"), "user"), (r2, Path("b.json"), "user")]
        result = _deduplicate_rules(rules)
        assert len(result) == 1
        assert result[0][0].version == 3

    def test_different_source_pip_wins(self):
        """pip 源优先级高于 user。"""
        r_pip = _rule_schema("dup", version=1)
        r_user = _rule_schema("dup", version=10)
        rules = [(r_user, Path("u.json"), "user"), (r_pip, Path("p.json"), "pip")]
        result = _deduplicate_rules(rules)
        assert len(result) == 1
        assert result[0][2] == "pip"

    def test_unknown_source_defaults_to_99(self):
        """未知来源优先级 99 → 最低。"""
        r_known = _rule_schema("dup", version=1)
        r_unknown = _rule_schema("dup", version=10)
        rules = [(r_unknown, Path("u.json"), "alien"), (r_known, Path("k.json"), "user")]
        result = _deduplicate_rules(rules)
        assert len(result) == 1
        assert result[0][2] == "user"  # user pri=2 < alien pri=99

    def test_same_source_same_version_keeps_first(self):
        """同一来源相同版本 → 先到先得。"""
        r1 = _rule_schema("dup", version=1)
        r2 = _rule_schema("dup", version=1)
        rules = [(r1, Path("a.json"), "user"), (r2, Path("b.json"), "user")]
        result = _deduplicate_rules(rules)
        assert len(result) == 1
        assert result[0][1] == Path("a.json")  # 保留第一个

    def test_different_names_both_kept(self):
        r1 = _rule_schema("a", version=1)
        r2 = _rule_schema("b", version=1)
        rules = [(r1, Path("a.json"), "user"), (r2, Path("b.json"), "user")]
        result = _deduplicate_rules(rules)
        assert len(result) == 2


# ═══════════════════════════════════════════════════════════════════
# _rule_sort_key — 单元测试
# ═══════════════════════════════════════════════════════════════════


class TestRuleSortKey:
    def test_domain_pattern_before_domain_all(self):
        r1 = _rule_schema("a", ["example.com"], scope="domain_pattern", url_pattern="/foo")
        r2 = _rule_schema("b", ["example.com"], scope="domain_all")
        # DOMAIN_PATTERN (0) < DOMAIN_ALL (1) → r1 优先
        assert _rule_sort_key(r1) < _rule_sort_key(r2)

    def test_longer_pattern_first(self):
        r1 = _rule_schema("a", ["example.com"], scope="domain_pattern", url_pattern="/foo/bar")
        r2 = _rule_schema("b", ["example.com"], scope="domain_pattern", url_pattern="/foo")
        assert _rule_sort_key(r1) < _rule_sort_key(r2)

    def test_higher_version_first(self):
        r1 = _rule_schema("a", ["example.com"], scope="domain_all", version=5)
        r2 = _rule_schema("b", ["example.com"], scope="domain_all", version=1)
        assert _rule_sort_key(r1) < _rule_sort_key(r2)


# ═══════════════════════════════════════════════════════════════════
# _detect_ambiguous_rules — 单元测试
# ═══════════════════════════════════════════════════════════════════


class TestDetectAmbiguousRules:
    def test_no_conflict_when_different_scope(self):
        rules = [
            _rule_schema("a", ["example.com"], scope="domain_pattern", url_pattern="/a"),
            _rule_schema("b", ["example.com"], scope="domain_all"),
        ]
        conflicts = _detect_ambiguous_rules(rules)
        assert conflicts == []

    def test_conflict_same_domain_pattern_scope(self):
        """DOMAIN_PATTERN 下相同 domains + url_pattern + version → 冲突。"""
        rules = [
            _rule_schema("a", ["example.com"], scope="domain_pattern", url_pattern="/foo", version=1),
            _rule_schema("b", ["example.com"], scope="domain_pattern", url_pattern="/foo", version=1),
        ]
        conflicts = _detect_ambiguous_rules(rules)
        assert len(conflicts) == 1
        assert set(conflicts[0]) == {"a", "b"}

    def test_conflict_same_domain_all_scope(self):
        """DOMAIN_ALL 下相同 domains + version → 冲突（url_pattern 不在 key 中）。"""
        rules = [
            _rule_schema("a", ["example.com"], scope="domain_all", url_pattern="/x", version=1),
            _rule_schema("b", ["example.com"], scope="domain_all", url_pattern="/y", version=1),
        ]
        conflicts = _detect_ambiguous_rules(rules)
        assert len(conflicts) == 1

    def test_conflict_same_global_pattern_scope(self):
        """GLOBAL_PATTERN 下相同 url_pattern + version → 冲突。"""
        rules = [
            _rule_schema("a", [], scope="global_pattern", url_pattern="/foo", version=1),
            _rule_schema("b", [], scope="global_pattern", url_pattern="/foo", version=1),
        ]
        conflicts = _detect_ambiguous_rules(rules)
        assert len(conflicts) == 1

    def test_conflict_same_any_scope(self):
        """ANY 下相同 version → 冲突。"""
        rules = [
            _rule_schema("a", [], scope="any", version=1),
            _rule_schema("b", [], scope="any", version=1),
        ]
        conflicts = _detect_ambiguous_rules(rules)
        assert len(conflicts) == 1

    def test_disabled_rules_excluded_from_conflicts(self):
        """禁用规则不参与冲突检测。"""
        rules = [
            _rule_schema("a", ["example.com"], scope="domain_all", version=1, enabled=True),
            _rule_schema("b", ["example.com"], scope="domain_all", version=1, enabled=False),
        ]
        conflicts = _detect_ambiguous_rules(rules)
        assert conflicts == []

    def test_no_conflict_different_domains(self):
        rules = [
            _rule_schema("a", ["a.com"], scope="domain_all", version=1),
            _rule_schema("b", ["b.com"], scope="domain_all", version=1),
        ]
        conflicts = _detect_ambiguous_rules(rules)
        assert conflicts == []


# ═══════════════════════════════════════════════════════════════════
# ensure_no_rule_conflicts — 集成测试
# ═══════════════════════════════════════════════════════════════════


class TestEnsureNoRuleConflicts:
    def test_raises_when_conflicts_present(self, tmp_path):
        """快照包含冲突 → ensure_no_rule_conflicts 抛出 RuleConflictError。"""
        from astrocrawl.config import CrawlerConfig

        _write_rule_file(
            tmp_path,
            {
                "name": "conflict_a",
                "version": 1,
                "schema_version": 1,
                "enabled": True,
                "match": {"domains": ["example.com"], "scope": "domain_all"},
                "fields": {"x": {"selector": "h1"}},
            },
        )
        _write_rule_file(
            tmp_path,
            {
                "name": "conflict_b",
                "version": 1,
                "schema_version": 1,
                "enabled": True,
                "match": {"domains": ["example.com"], "scope": "domain_all"},
                "fields": {"x": {"selector": "h1"}},
            },
        )

        cfg = CrawlerConfig()
        snap = build_rule_snapshot(cfg, extra_rules_dirs=[str(tmp_path)])
        assert len(snap._conflicts) > 0

        with pytest.raises(RuleConflictError, match="规则配置歧义"):
            ensure_no_rule_conflicts(snap)

    def test_no_raise_when_no_conflicts(self):
        """无冲突时 ensure_no_rule_conflicts 正常返回。"""
        from astrocrawl._types import RuleSnapshot

        snap = RuleSnapshot.default_only()
        ensure_no_rule_conflicts(snap)  # 不应抛异常


# ═══════════════════════════════════════════════════════════════════
# validate_rule_files — 边界路径
# ═══════════════════════════════════════════════════════════════════


class TestValidateRuleFiles:
    def test_invalid_rule_returns_skip(self, tmp_path):
        """无效规则文件 → status='skip' + error 信息。"""
        from astrocrawl.config import CrawlerConfig

        extra_dir = tmp_path / "extra"
        extra_dir.mkdir()
        bad = extra_dir / "bad.json"
        bad.write_text("{not valid json", encoding="utf-8")

        results = validate_rule_files(CrawlerConfig(), extra_rules_dirs=[str(extra_dir)])
        # 过滤出 extra dir 的结果（pip 内置规则也出现在结果中）
        extra_results = [r for r in results if r["source"] == "user" and str(extra_dir) in r["path"]]
        assert len(extra_results) == 1
        assert extra_results[0]["status"] == "skip"
        assert "error" in extra_results[0]

    def test_permission_denied_graceful(self, tmp_path):
        """目录不可读 → PermissionError 静默捕获。"""
        from astrocrawl.config import CrawlerConfig

        locked = tmp_path / "locked"
        locked.mkdir()
        locked.chmod(0o000)
        try:
            results = validate_rule_files(CrawlerConfig(), extra_rules_dirs=[str(locked)])
        finally:
            locked.chmod(0o755)
        # 不应抛异常
        assert isinstance(results, list)

    def test_missing_fields_key_returns_skip(self, tmp_path):
        """无 'fields' key 的文件 → load_rule_file 返回 None → status='skip'。"""
        from astrocrawl.config import CrawlerConfig

        extra_dir = tmp_path / "extra"
        extra_dir.mkdir()
        no_fields = extra_dir / "no_fields.json"
        no_fields.write_text(_json.dumps({"name": "config", "version": 1}), encoding="utf-8")

        results = validate_rule_files(CrawlerConfig(), extra_rules_dirs=[str(extra_dir)])
        extra_results = [r for r in results if r["source"] == "user" and str(extra_dir) in r["path"]]
        assert len(extra_results) == 1
        assert extra_results[0]["status"] == "skip"

    def test_file_too_large_returns_skip(self, tmp_path):
        """超大文件 → status='skip'。"""
        from astrocrawl.config import CrawlerConfig

        extra_dir = tmp_path / "extra"
        extra_dir.mkdir()
        big = extra_dir / "big.json"
        big.write_text("x" * (MAX_RULE_FILE_SIZE + 1), encoding="utf-8")

        results = validate_rule_files(CrawlerConfig(), extra_rules_dirs=[str(extra_dir)])
        extra_results = [r for r in results if r["source"] == "user" and str(extra_dir) in r["path"]]
        assert len(extra_results) == 1
        assert extra_results[0]["status"] == "skip"

    def test_valid_rule_returns_pass(self, tmp_path):
        """有效规则 → status='pass' + name/fields_count。"""
        from astrocrawl.config import CrawlerConfig

        extra_dir = tmp_path / "extra"
        extra_dir.mkdir()
        _write_rule_file(extra_dir, _make_rule_dict("good_rule", domains=["example.com"]))

        results = validate_rule_files(CrawlerConfig(), extra_rules_dirs=[str(extra_dir)])
        extra_results = [r for r in results if r["source"] == "user" and str(extra_dir) in r["path"]]
        assert len(extra_results) == 1
        assert extra_results[0]["status"] == "pass"
        assert extra_results[0]["name"] == "good_rule"
        assert extra_results[0]["fields_count"] == 1

    def test_load_rule_file_exception_becomes_fail(self, tmp_path):
        """load_rule_file 抛出异常 → status='fail'（防御代码路径）。"""
        from astrocrawl.config import CrawlerConfig

        extra_dir = tmp_path / "extra"
        extra_dir.mkdir()
        _write_rule_file(extra_dir, _make_rule_dict("will_fail", domains=["example.com"]))

        with patch("astrocrawl.rules._loader.load_rule_file", side_effect=RuntimeError("simulated")):
            results = validate_rule_files(CrawlerConfig(), extra_rules_dirs=[str(extra_dir)])
        extra_results = [r for r in results if r["source"] == "user" and str(extra_dir) in r["path"]]
        assert len(extra_results) == 1
        assert extra_results[0]["status"] == "fail"
        assert "simulated" in extra_results[0]["error"]

    def test_os_walk_permission_error_caught(self, tmp_path):
        """os.walk throws PermissionError → 目录被静默跳过。"""
        from astrocrawl.config import CrawlerConfig

        ok_dir = tmp_path / "ok"
        ok_dir.mkdir()
        _write_rule_file(ok_dir, _make_rule_dict("ok_rule", domains=["example.com"]))

        # patch os.walk 对特定目录 raise PermissionError
        original_walk = os.walk

        def _selective_walk(path, *args, **kwargs):
            if path == str(ok_dir):
                return original_walk(path, *args, **kwargs)
            raise PermissionError("denied")

        with patch("os.walk", side_effect=_selective_walk):
            results = validate_rule_files(CrawlerConfig(), extra_rules_dirs=[str(ok_dir)])

        # ok_dir 的结果正常（通过 pip 目录也可能有结果）
        ok_results = [r for r in results if r["source"] == "user" and str(ok_dir) in r["path"]]
        assert len(ok_results) == 1
        assert ok_results[0]["status"] == "pass"

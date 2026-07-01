"""规则启用状态管理 — rules_state.json 读写测试。

覆盖 fcntl 锁获取/释放、禁用规则读取、启用/禁用状态设置、损坏恢复。
"""

from __future__ import annotations

import json

import pytest

from astrocrawl._types import DEFAULT_EXTRACTION_TYPE
from astrocrawl.rules._state import (
    _acquire_lock,
    _release_lock,
    _save,
    get_disabled_rules,
    get_disabled_rules_locked,
    set_rule_enabled,
    set_rules_enabled,
)

# ═══════════════════════════════════════════════════════════════════════
# _acquire_lock / _release_lock
# ═══════════════════════════════════════════════════════════════════════


class TestAcquireReleaseLock:
    """fcntl 锁获取与释放。"""

    def test_exclusive_lock_lifecycle(self, tmp_path):
        """LOCK_EX 获取 → _release_lock → fd 关闭, 锁文件被清理。"""
        state_file = tmp_path / "rules_state.json"
        lock_file = tmp_path / "rules_state.json.lock"

        fd = _acquire_lock(state_file, exclusive=True)
        assert fd is not None
        assert fd >= 0
        assert lock_file.exists()

        _release_lock(fd)
        _release_lock(fd)

    def test_none_fd_noop(self):
        """_release_lock(None) 安全无操作, 多次调用不抛异常。"""
        _release_lock(None)
        _release_lock(None)

    def test_lock_failure_returns_none(self, tmp_path, monkeypatch, caplog):
        """os.open 失败 → _acquire_lock 返回 None + WARNING 日志。"""
        state_file = tmp_path / "rules_state.json"

        def _fake_open(path, flags, mode=0o777):
            raise OSError("permission denied")

        monkeypatch.setattr("os.open", _fake_open)
        fd = _acquire_lock(state_file, exclusive=True)
        assert fd is None
        assert "event=lock_acquire_failed" in caplog.text


# ═══════════════════════════════════════════════════════════════════════
# get_disabled_rules
# ═══════════════════════════════════════════════════════════════════════


class TestGetDisabledRules:
    """get_disabled_rules 行为。"""

    def test_no_file_returns_empty(self, tmp_path):
        """文件不存在 → (set(), False)。"""
        path = tmp_path / "nonexistent.json"
        disabled, exists = get_disabled_rules(path)
        assert disabled == set()
        assert exists is False

    def test_empty_state_returns_empty(self, tmp_path):
        """{"disabled": []} → (set(), True)。"""
        path = tmp_path / "state.json"
        path.write_text(json.dumps({"disabled": []}), "utf-8")
        disabled, exists = get_disabled_rules(path)
        assert disabled == set()
        assert exists is True

    def test_normal_entries(self, tmp_path):
        """正确返回已禁用的规则名集合。"""
        path = tmp_path / "state.json"
        path.write_text(json.dumps({"disabled": ["rule_a", "rule_b"]}), "utf-8")
        disabled, exists = get_disabled_rules(path)
        assert disabled == {"rule_a", "rule_b"}
        assert exists is True

    def test_oversized_file_discards(self, tmp_path, caplog):
        """>128KB 文件 → unlink + 返回 (set(), False)。"""
        path = tmp_path / "state.json"
        path.write_bytes(b"x" * (128 * 1024 + 1))
        disabled, exists = get_disabled_rules(path)
        assert disabled == set()
        assert exists is False
        assert not path.exists()
        assert "event=state_file_oversized" in caplog.text

    def test_corrupt_json_discards(self, tmp_path, caplog):
        """JSON 损坏 → unlink + 返回 (set(), False)。"""
        path = tmp_path / "state.json"
        path.write_text("not valid json", "utf-8")
        disabled, exists = get_disabled_rules(path)
        assert disabled == set()
        assert exists is False
        assert not path.exists()
        assert "event=state_file_corrupt" in caplog.text

    @pytest.mark.parametrize(
        "content",
        [
            '{"disabled": "not_a_list"}',
            '{"disabled": 42}',
            '{"disabled": {"nested": "dict"}}',
        ],
    )
    def test_invalid_structure_discards(self, tmp_path, caplog, content):
        """disabled 字段非 list → unlink + 返回 (set(), False)。"""
        path = tmp_path / "state.json"
        path.write_text(content, "utf-8")
        disabled, exists = get_disabled_rules(path)
        assert disabled == set()
        assert exists is False
        assert not path.exists()

    @pytest.mark.parametrize(
        "content",
        [
            "[]",
            "[1, 2, 3]",
            "42",
            "3.14",
            '"just a string"',
            "true",
            "false",
            "null",
        ],
    )
    def test_root_not_dict_discards(self, tmp_path, caplog, content):
        """根值非 dict (list/number/string/bool/null) → unlink + 返回 (set(), False)。"""
        path = tmp_path / "state.json"
        path.write_text(content, "utf-8")
        disabled, exists = get_disabled_rules(path)
        assert disabled == set()
        assert exists is False
        assert not path.exists()
        assert "event=state_file_corrupt" in caplog.text

    def test_exactly_max_bytes_ok(self, tmp_path):
        """文件恰好 _MAX_FILE_BYTES 字节 → 正常读取 (边界值)。"""
        path = tmp_path / "state.json"
        # 手工构造恰好 128KB 的有效 JSON
        head = '{"disabled": ["r"], "x": "'
        tail = '"}'
        pad_len = 128 * 1024 - len(head.encode("utf-8")) - len(tail.encode("utf-8"))
        content = head + ("y" * pad_len) + tail
        assert len(content.encode("utf-8")) == 128 * 1024
        path.write_text(content, "utf-8")
        disabled, exists = get_disabled_rules(path)
        assert exists is True
        assert "r" in disabled

    def test_filters_non_string_entries(self, tmp_path):
        """数字/None/空字符串被剔除, 仅保留非空 str。"""
        path = tmp_path / "state.json"
        path.write_text(json.dumps({"disabled": ["valid", "", None, "keep", 42, "  "]}), "utf-8")
        disabled, exists = get_disabled_rules(path)
        assert "" not in disabled
        assert "valid" in disabled
        assert "keep" in disabled
        # 42 (int) 和 None 被过滤, "  " (非空 str) 保留
        assert len(disabled) == 3
        assert exists is True


# ═══════════════════════════════════════════════════════════════════════
# get_disabled_rules_locked
# ═══════════════════════════════════════════════════════════════════════


class TestGetDisabledRulesLocked:
    """无锁内层函数 get_disabled_rules_locked。"""

    def test_no_file_returns_empty(self, tmp_path):
        """文件不存在 → (set(), False)。"""
        path = tmp_path / "nonexistent.json"
        disabled, exists = get_disabled_rules_locked(path)
        assert disabled == set()
        assert exists is False

    def test_normal_read(self, tmp_path):
        """有效文件正常读取。"""
        path = tmp_path / "state.json"
        path.write_text(json.dumps({"disabled": ["rule_x"]}), "utf-8")
        disabled, exists = get_disabled_rules_locked(path)
        assert disabled == {"rule_x"}
        assert exists is True

    def test_corrupt_json_discards(self, tmp_path, caplog):
        """JSON 损坏 → unlink + 返回 (set(), False)。"""
        path = tmp_path / "state.json"
        path.write_text("not json", "utf-8")
        disabled, exists = get_disabled_rules_locked(path)
        assert disabled == set()
        assert exists is False
        assert not path.exists()

    def test_invalid_structure_discards(self, tmp_path):
        """disabled 非 list → unlink + 返回 (set(), False)。"""
        path = tmp_path / "state.json"
        path.write_text('{"disabled": "not_a_list"}', "utf-8")
        disabled, exists = get_disabled_rules_locked(path)
        assert disabled == set()
        assert exists is False
        assert not path.exists()

    def test_oversized_file_discards(self, tmp_path, caplog):
        """>128KB 文件 → unlink + 返回 (set(), False)。"""
        path = tmp_path / "state.json"
        path.write_bytes(b"x" * (128 * 1024 + 1))
        disabled, exists = get_disabled_rules_locked(path)
        assert disabled == set()
        assert exists is False
        assert not path.exists()
        assert "event=state_file_oversized" in caplog.text

    @pytest.mark.parametrize(
        "content",
        [
            "[]",
            "[1, 2, 3]",
            "42",
            '"just a string"',
            "true",
            "null",
        ],
    )
    def test_root_not_dict_discards(self, tmp_path, caplog, content):
        """根值非 dict → unlink + 返回 (set(), False)。"""
        path = tmp_path / "state.json"
        path.write_text(content, "utf-8")
        disabled, exists = get_disabled_rules_locked(path)
        assert disabled == set()
        assert exists is False
        assert not path.exists()
        assert "event=state_file_corrupt" in caplog.text


# ═══════════════════════════════════════════════════════════════════════
# set_rule_enabled
# ═══════════════════════════════════════════════════════════════════════


class TestSetRuleEnabled:
    """set_rule_enabled 启用/禁用规则。"""

    def test_disable_rule(self, tmp_path):
        """禁用规则 → 名称写入 disabled 列表。"""
        path = tmp_path / "state.json"
        path.write_text(json.dumps({"disabled": []}), "utf-8")
        set_rule_enabled("my_rule", enabled=False, path=path)
        disabled, exists = get_disabled_rules(path)
        assert "my_rule" in disabled
        assert exists is True

    def test_enable_rule(self, tmp_path):
        """启用规则 → 名称从 disabled 移除。"""
        path = tmp_path / "state.json"
        path.write_text(json.dumps({"disabled": ["my_rule", "other"]}), "utf-8")
        set_rule_enabled("my_rule", enabled=True, path=path)
        disabled, exists = get_disabled_rules(path)
        assert "my_rule" not in disabled
        assert "other" in disabled

    def test_default_rule_protected(self, tmp_path, caplog):
        """ "default" 规则不可禁用 (NOP + WARNING 日志)。"""
        path = tmp_path / "state.json"
        path.write_text(json.dumps({"disabled": []}), "utf-8")
        set_rule_enabled(DEFAULT_EXTRACTION_TYPE, enabled=False, path=path)
        assert "event=state_default_protected" in caplog.text
        disabled, _ = get_disabled_rules(path)
        assert DEFAULT_EXTRACTION_TYPE not in disabled

    def test_toggle_cycle(self, tmp_path):
        """禁用→启用→禁用 状态链正确。"""
        path = tmp_path / "state.json"
        path.write_text(json.dumps({"disabled": []}), "utf-8")

        set_rule_enabled("rule", enabled=False, path=path)
        disabled, _ = get_disabled_rules(path)
        assert "rule" in disabled

        set_rule_enabled("rule", enabled=True, path=path)
        disabled, _ = get_disabled_rules(path)
        assert "rule" not in disabled

        set_rule_enabled("rule", enabled=False, path=path)
        disabled, _ = get_disabled_rules(path)
        assert "rule" in disabled

    def test_custom_path(self, tmp_path):
        """path 参数隔离——不同文件独立状态。"""
        path_a = tmp_path / "state_a.json"
        path_b = tmp_path / "state_b.json"
        path_a.write_text(json.dumps({"disabled": []}), "utf-8")
        path_b.write_text(json.dumps({"disabled": []}), "utf-8")

        set_rule_enabled("rule_a", enabled=False, path=path_a)
        set_rule_enabled("rule_b", enabled=False, path=path_b)

        da, _ = get_disabled_rules(path_a)
        db, _ = get_disabled_rules(path_b)
        assert da == {"rule_a"}
        assert db == {"rule_b"}

    def test_file_not_exists_bootstrap(self, tmp_path):
        """状态文件不存在时, set_rule_enabled 创建新文件。"""
        path = tmp_path / "new_state.json"
        assert not path.exists()
        set_rule_enabled("rule", enabled=False, path=path)
        assert path.exists()
        disabled, exists = get_disabled_rules(path)
        assert "rule" in disabled
        assert exists is True


# ═══════════════════════════════════════════════════════════════════════
# 锁降级
# ═══════════════════════════════════════════════════════════════════════


class TestLockDegradation:
    """锁获取失败时的优雅降级。"""

    def test_lock_failure_still_reads_file(self, tmp_path, monkeypatch):
        """_acquire_lock 返回 None → 文件仍被正确读取 (无锁保护)。"""
        path = tmp_path / "state.json"
        path.write_text(json.dumps({"disabled": ["rule"]}), "utf-8")

        monkeypatch.setattr(
            "astrocrawl.rules._state._acquire_lock",
            lambda *a, **kw: None,
        )
        disabled, exists = get_disabled_rules(path)
        assert disabled == {"rule"}
        assert exists is True


# ═══════════════════════════════════════════════════════════════════════
# _save
# ═══════════════════════════════════════════════════════════════════════


class TestSave:
    """_save 写入格式。"""

    def test_creates_correct_structure(self, tmp_path):
        """写入 {"version": 1, "disabled": [...]} 格式。"""
        path = tmp_path / "state.json"
        _save({"rule_b", "rule_a"}, path=path)
        content = json.loads(path.read_text("utf-8"))
        assert content["version"] == 1
        assert content["disabled"] == ["rule_a", "rule_b"]  # sorted

    def test_empty_set_produces_empty_list(self, tmp_path):
        """空集合产生 {"version": 1, "disabled": []}。"""
        path = tmp_path / "state.json"
        _save(set(), path=path)
        content = json.loads(path.read_text("utf-8"))
        assert content == {"version": 1, "disabled": []}


class TestSetRulesEnabled:
    """set_rules_enabled 批量操作 — 一次锁，一次原子写入。"""

    def test_batch_disable_multiple(self, tmp_path):
        path = tmp_path / "state.json"
        set_rules_enabled({"rule_a": False, "rule_b": False}, path=path)
        disabled, exists = get_disabled_rules(path=path)
        assert exists
        assert "rule_a" in disabled
        assert "rule_b" in disabled

    def test_batch_mixed_enable_disable(self, tmp_path):
        path = tmp_path / "state.json"
        set_rules_enabled({"rule_a": False, "rule_b": True, "rule_c": False}, path=path)
        disabled, _ = get_disabled_rules(path=path)
        assert "rule_a" in disabled
        assert "rule_b" not in disabled
        assert "rule_c" in disabled

    def test_batch_default_protected(self, tmp_path):
        path = tmp_path / "state.json"
        set_rules_enabled({DEFAULT_EXTRACTION_TYPE: False, "rule_a": False}, path=path)
        disabled, _ = get_disabled_rules(path=path)
        assert DEFAULT_EXTRACTION_TYPE not in disabled
        assert "rule_a" in disabled

    def test_batch_enable_previously_disabled(self, tmp_path):
        path = tmp_path / "state.json"
        set_rule_enabled("rule_a", False, path=path)
        set_rules_enabled({"rule_a": True}, path=path)
        disabled, _ = get_disabled_rules(path=path)
        assert "rule_a" not in disabled

    def test_batch_empty_dict_noop(self, tmp_path):
        path = tmp_path / "state.json"
        set_rules_enabled({}, path=path)
        disabled, exists = get_disabled_rules(path=path)
        assert exists
        assert disabled == set()

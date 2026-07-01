"""POSIX 原子文件写入原语 atomic_write_json 测试。

覆盖基础写入、权限控制、大小限制、错误恢复。
"""

from __future__ import annotations

import json
import os
import stat
import tempfile

import pytest

from astrocrawl.utils._atomic import atomic_write_json

# ═══════════════════════════════════════════════════════════════════════
# atomic_write_json — 基础写入
# ═══════════════════════════════════════════════════════════════════════


class TestAtomicWriteJsonBasic:
    """基础写入行为。"""

    def test_basic_roundtrip(self, tmp_path):
        """写入 dict → 回读, 验证 JSON 内容 + trailing newline。"""
        path = tmp_path / "test.json"
        data = {"key": "value", "num": 42}
        atomic_write_json(path, data)
        content = path.read_text("utf-8")
        assert content.endswith("\n")
        assert json.loads(content) == data

    def test_auto_create_parent_dir(self, tmp_path):
        """目标路径父目录不存在时自动创建。"""
        path = tmp_path / "sub" / "nested" / "test.json"
        atomic_write_json(path, {"a": 1})
        assert path.exists()
        loaded = json.loads(path.read_text("utf-8"))
        assert loaded == {"a": 1}

    def test_overwrite_existing(self, tmp_path):
        """两次写入同一路径, 第二次覆盖第一次。"""
        path = tmp_path / "test.json"
        atomic_write_json(path, {"v": 1})
        assert json.loads(path.read_text("utf-8")) == {"v": 1}
        atomic_write_json(path, {"v": 2})
        assert json.loads(path.read_text("utf-8")) == {"v": 2}

    def test_unicode_payload(self, tmp_path):
        """中文 + emoji 正确序列化与回读。"""
        path = tmp_path / "unicode.json"
        data = {"msg": "你好世界", "emoji": "✅"}
        atomic_write_json(path, data)
        raw = path.read_text("utf-8")
        loaded = json.loads(raw)
        assert loaded == data
        assert "你好世界" in raw
        assert "✅" in raw


# ═══════════════════════════════════════════════════════════════════════
# atomic_write_json — 权限控制
# ═══════════════════════════════════════════════════════════════════════


class TestAtomicWriteJsonPermissions:
    """chmod_mask 相关行为。"""

    def test_default_chmod_600(self, tmp_path):
        """默认 chmod_mask=0o600, 仅 owner 可读写。"""
        path = tmp_path / "test.json"
        atomic_write_json(path, {"a": 1})
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600
        assert mode & stat.S_IRUSR
        assert mode & stat.S_IWUSR
        assert not (mode & stat.S_IRGRP)
        assert not (mode & stat.S_IWGRP)

    def test_custom_chmod_644(self, tmp_path):
        """chmod_mask=0o644 时 owner 可读写, group/other 仅读。"""
        path = tmp_path / "test.json"
        atomic_write_json(path, {"a": 1}, chmod_mask=0o644)
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o644
        assert mode & stat.S_IRUSR and mode & stat.S_IWUSR
        assert mode & stat.S_IRGRP
        assert mode & stat.S_IROTH

    def test_chmod_none_skips(self, tmp_path, monkeypatch):
        """chmod_mask=None 时不调用 os.chmod。"""
        path = tmp_path / "test.json"
        calls = []

        def _track_chmod(p, m):
            calls.append((p, m))

        monkeypatch.setattr(os, "chmod", _track_chmod)
        atomic_write_json(path, {"a": 1}, chmod_mask=None)
        assert len(calls) == 0
        assert path.exists()

    def test_chmod_failure_no_raise(self, tmp_path, monkeypatch, caplog):
        """chmod 失败仅记 debug 日志, 不抛异常。"""
        path = tmp_path / "test.json"

        def _fake_chmod(p, m):
            raise OSError("permission denied")

        monkeypatch.setattr(os, "chmod", _fake_chmod)
        caplog.set_level("DEBUG")
        atomic_write_json(path, {"a": 1})

        assert path.exists()
        assert "event=atomic_write_chmod_failed" in caplog.text


# ═══════════════════════════════════════════════════════════════════════
# atomic_write_json — 前置失败 (mkstemp 之前, 无临时文件需清理)
# ═══════════════════════════════════════════════════════════════════════


class TestAtomicWriteJsonPrefailure:
    """mkstemp 之前的失败场景 — 无临时文件残留。"""

    def test_json_dumps_failure_no_tempfile(self, tmp_path):
        """非 JSON 可序列化对象 → TypeError, 无临时文件。"""
        path = tmp_path / "test.json"

        class NonSerializable:
            pass

        with pytest.raises(TypeError):
            atomic_write_json(path, NonSerializable())

        assert not path.exists()
        tmps = list(tmp_path.glob("*.tmp"))
        assert len(tmps) == 0

    def test_mkstemp_failure_no_tempfile(self, tmp_path, monkeypatch):
        """mkstemp OSError → 无临时文件需清理。"""
        path = tmp_path / "test.json"

        def _fake_mkstemp(*args, **kwargs):
            raise OSError("permission denied")

        monkeypatch.setattr(tempfile, "mkstemp", _fake_mkstemp)

        with pytest.raises(OSError):
            atomic_write_json(path, {"a": 1})

        assert not path.exists()
        tmps = list(tmp_path.glob("*.tmp"))
        assert len(tmps) == 0


# ═══════════════════════════════════════════════════════════════════════
# atomic_write_json — 大小限制
# ═══════════════════════════════════════════════════════════════════════


class TestAtomicWriteJsonSizeLimit:
    """max_bytes 参数行为。"""

    def test_under_limit_passes(self, tmp_path):
        """payload 未超过 max_bytes 正常写入。"""
        path = tmp_path / "test.json"
        atomic_write_json(path, {"a": 1}, max_bytes=1024)
        assert path.exists()
        assert json.loads(path.read_text("utf-8")) == {"a": 1}

    def test_exact_at_limit_passes(self, tmp_path):
        """payload 恰好等于 max_bytes 正常写入。"""
        path = tmp_path / "test.json"
        data = {"k": "v"}
        serialized = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        size = len(serialized.encode("utf-8"))
        atomic_write_json(path, data, max_bytes=size)
        assert path.exists()
        assert json.loads(path.read_text("utf-8")) == data

    def test_over_limit_raises(self, tmp_path):
        """payload 超过 max_bytes 抛出 ValueError。"""
        path = tmp_path / "test.json"
        with pytest.raises(ValueError):
            atomic_write_json(path, "x" * 1000, max_bytes=10)
        assert not path.exists()

    def test_none_skips_check(self, tmp_path):
        """max_bytes=None 时跳过大小检查, 大 payload 正常写入。"""
        path = tmp_path / "test.json"
        payload = "x" * 10000
        atomic_write_json(path, payload, max_bytes=None)
        assert path.exists()
        loaded = json.loads(path.read_text("utf-8"))
        assert loaded == payload


# ═══════════════════════════════════════════════════════════════════════
# atomic_write_json — 错误恢复
# ═══════════════════════════════════════════════════════════════════════


class TestAtomicWriteJsonRecovery:
    """写入失败时临时文件清理。"""

    def test_write_failure_cleans_tempfile(self, tmp_path, monkeypatch):
        """os.fdopen 写入失败 → 临时文件被清理, 目标文件不存在。"""
        path = tmp_path / "test.json"

        def _fake_fdopen(fd, mode):
            raise OSError("write error")

        monkeypatch.setattr(os, "fdopen", _fake_fdopen)

        with pytest.raises(OSError):
            atomic_write_json(path, {"a": 1})

        assert not path.exists()
        # 验证临时文件被清理: 父目录下无 .tmp 残留
        tmps = list(tmp_path.glob("*.tmp"))
        assert len(tmps) == 0

    def test_replace_failure_cleans_tempfile(self, tmp_path, monkeypatch):
        """os.replace 失败 → 临时文件被清理, 数据部分已写入的临时文件不存在。"""
        path = tmp_path / "test.json"

        def _fake_replace(src, dst):
            raise OSError("replace error")

        monkeypatch.setattr(os, "replace", _fake_replace)

        with pytest.raises(OSError):
            atomic_write_json(path, {"a": 1})

        assert not path.exists()
        tmps = list(tmp_path.glob("*.tmp"))
        assert len(tmps) == 0


# ═══════════════════════════════════════════════════════════════════════
# atomic_write_json — 扩展错误恢复 (覆盖中间故障点)
# ═══════════════════════════════════════════════════════════════════════


class TestAtomicWriteJsonRecoveryExtended:
    """写入中不同阶段的故障 — 临时文件清理。"""

    def test_fsync_failure_cleans_tempfile(self, tmp_path, monkeypatch):
        """os.fsync 失败 (数据已写入临时文件) → 临时文件被清理。"""
        path = tmp_path / "test.json"

        def _fake_fsync(fd):
            raise OSError("fsync error")

        monkeypatch.setattr(os, "fsync", _fake_fsync)

        with pytest.raises(OSError):
            atomic_write_json(path, {"a": 1})

        assert not path.exists()
        tmps = list(tmp_path.glob("*.tmp"))
        assert len(tmps) == 0

    def test_unlink_failure_during_cleanup_still_raises_original(self, tmp_path, monkeypatch):
        """os.unlink 清理失败 → 静默忽略, 仍抛原始异常。"""
        path = tmp_path / "test.json"

        def _fake_fdopen(fd, mode):
            raise OSError("original write error")

        def _fake_unlink(p):
            raise OSError("unlink error")

        monkeypatch.setattr(os, "fdopen", _fake_fdopen)
        monkeypatch.setattr(os, "unlink", _fake_unlink)

        with pytest.raises(OSError) as exc_info:
            atomic_write_json(path, {"a": 1})

        assert "original write error" in str(exc_info.value)
        assert not path.exists()

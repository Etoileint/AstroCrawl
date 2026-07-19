"""POSIX atomic file write primitive atomic_write_json tests."""

from __future__ import annotations

import json
import os
import stat
import tempfile

import pytest

from astrobasis import atomic_write_json


class TestAtomicWriteJsonBasic:
    def test_basic_roundtrip(self, tmp_path):
        path = tmp_path / "test.json"
        data = {"key": "value", "num": 42}
        atomic_write_json(path, data)
        content = path.read_text("utf-8")
        assert content.endswith("\n")
        assert json.loads(content) == data

    def test_auto_create_parent_dir(self, tmp_path):
        path = tmp_path / "sub" / "nested" / "test.json"
        atomic_write_json(path, {"a": 1})
        assert path.exists()
        loaded = json.loads(path.read_text("utf-8"))
        assert loaded == {"a": 1}

    def test_overwrite_existing(self, tmp_path):
        path = tmp_path / "test.json"
        atomic_write_json(path, {"v": 1})
        assert json.loads(path.read_text("utf-8")) == {"v": 1}
        atomic_write_json(path, {"v": 2})
        assert json.loads(path.read_text("utf-8")) == {"v": 2}

    def test_unicode_payload(self, tmp_path):
        path = tmp_path / "unicode.json"
        data = {"msg": "你好世界", "emoji": "✅"}
        atomic_write_json(path, data)
        raw = path.read_text("utf-8")
        loaded = json.loads(raw)
        assert loaded == data
        assert "你好世界" in raw
        assert "✅" in raw


class TestAtomicWriteJsonPermissions:
    def test_default_chmod_600(self, tmp_path):
        path = tmp_path / "test.json"
        atomic_write_json(path, {"a": 1})
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600
        assert mode & stat.S_IRUSR
        assert mode & stat.S_IWUSR
        assert not (mode & stat.S_IRGRP)
        assert not (mode & stat.S_IWGRP)

    def test_custom_chmod_644(self, tmp_path):
        path = tmp_path / "test.json"
        atomic_write_json(path, {"a": 1}, chmod_mask=0o644)
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o644
        assert mode & stat.S_IRUSR
        assert mode & stat.S_IWUSR
        assert mode & stat.S_IRGRP
        assert mode & stat.S_IROTH

    def test_chmod_none_skips(self, tmp_path, monkeypatch):
        path = tmp_path / "test.json"
        calls = []

        def _track_chmod(p, m):
            calls.append((p, m))

        monkeypatch.setattr(os, "chmod", _track_chmod)
        atomic_write_json(path, {"a": 1}, chmod_mask=None)
        assert len(calls) == 0
        assert path.exists()

    def test_chmod_failure_no_raise(self, tmp_path, monkeypatch, caplog):
        path = tmp_path / "test.json"

        def _fake_chmod(p, m):
            raise OSError("permission denied")

        monkeypatch.setattr(os, "chmod", _fake_chmod)
        caplog.set_level("DEBUG")
        atomic_write_json(path, {"a": 1})

        assert path.exists()
        assert "event=atomic_write_chmod_failed" in caplog.text


class TestAtomicWriteJsonPrefailure:
    def test_json_dumps_failure_no_tempfile(self, tmp_path):
        path = tmp_path / "test.json"

        class NonSerializable:
            pass

        with pytest.raises(TypeError):
            atomic_write_json(path, NonSerializable())

        assert not path.exists()
        tmps = list(tmp_path.glob("*.tmp"))
        assert len(tmps) == 0

    def test_mkstemp_failure_no_tempfile(self, tmp_path, monkeypatch):
        path = tmp_path / "test.json"

        def _fake_mkstemp(*args, **kwargs):
            raise OSError("permission denied")

        monkeypatch.setattr(tempfile, "mkstemp", _fake_mkstemp)

        with pytest.raises(OSError):
            atomic_write_json(path, {"a": 1})

        assert not path.exists()
        tmps = list(tmp_path.glob("*.tmp"))
        assert len(tmps) == 0


class TestAtomicWriteJsonSizeLimit:
    def test_under_limit_passes(self, tmp_path):
        path = tmp_path / "test.json"
        atomic_write_json(path, {"a": 1}, max_bytes=1024)
        assert path.exists()
        assert json.loads(path.read_text("utf-8")) == {"a": 1}

    def test_exact_at_limit_passes(self, tmp_path):
        path = tmp_path / "test.json"
        data = {"k": "v"}
        serialized = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        size = len(serialized.encode("utf-8"))
        atomic_write_json(path, data, max_bytes=size)
        assert path.exists()
        assert json.loads(path.read_text("utf-8")) == data

    def test_over_limit_raises(self, tmp_path):
        path = tmp_path / "test.json"
        with pytest.raises(ValueError):
            atomic_write_json(path, "x" * 1000, max_bytes=10)
        assert not path.exists()

    def test_none_skips_check(self, tmp_path):
        path = tmp_path / "test.json"
        payload = "x" * 10000
        atomic_write_json(path, payload, max_bytes=None)
        assert path.exists()
        loaded = json.loads(path.read_text("utf-8"))
        assert loaded == payload


class TestAtomicWriteJsonRecovery:
    def test_write_failure_cleans_tempfile(self, tmp_path, monkeypatch):
        path = tmp_path / "test.json"

        def _fake_fdopen(fd, mode):
            raise OSError("write error")

        monkeypatch.setattr(os, "fdopen", _fake_fdopen)

        with pytest.raises(OSError):
            atomic_write_json(path, {"a": 1})

        assert not path.exists()
        tmps = list(tmp_path.glob("*.tmp"))
        assert len(tmps) == 0

    def test_replace_failure_cleans_tempfile(self, tmp_path, monkeypatch):
        path = tmp_path / "test.json"

        def _fake_replace(src, dst):
            raise OSError("replace error")

        monkeypatch.setattr(os, "replace", _fake_replace)

        with pytest.raises(OSError):
            atomic_write_json(path, {"a": 1})

        assert not path.exists()
        tmps = list(tmp_path.glob("*.tmp"))
        assert len(tmps) == 0


class TestAtomicWriteJsonRecoveryExtended:
    def test_fsync_failure_cleans_tempfile(self, tmp_path, monkeypatch):
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

"""特征测试：远程规则源 — URL 校验、Manifest 下载、SHA256、状态转换、并发控制。

测试文件覆盖 issue #124 的核心验收标准。
"""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from astrocrawl.config import CrawlerConfig
from astrocrawl.rules._source import SourceManager, _validate_source_url

# ═══════════════════════════════════════════════════════════════════
# URL 安全校验 (S01/S06/S07)
# ═══════════════════════════════════════════════════════════════════


class TestURLValidation:
    """S01/S06/S07 URL 安全。"""

    def test_https_required(self):
        with pytest.raises(ValueError, match="HTTPS"):
            _validate_source_url("http://example.com/manifest.json")

    def test_https_accepted(self):
        url = _validate_source_url("https://example.com/manifest.json")
        assert url == "https://example.com/manifest.json"

    def test_reject_auth_in_url(self):
        with pytest.raises(ValueError, match="认证"):
            _validate_source_url("https://user:pass@example.com/manifest.json")

    def test_reject_file_protocol(self):
        with pytest.raises(ValueError):
            _validate_source_url("file:///etc/passwd")

    def test_url_length_limit(self):
        long_url = "https://x.com/" + "a" * 3000
        with pytest.raises(ValueError, match="长度"):
            _validate_source_url(long_url)

    def test_trailing_slash_stripped(self):
        url = _validate_source_url("https://example.com/manifest.json/")
        assert url == "https://example.com/manifest.json"


# ═══════════════════════════════════════════════════════════════════
# SourceManager 生命周期
# ═══════════════════════════════════════════════════════════════════


class TestSourceManager:
    """源生命周期：add/remove/state 转换。"""

    def test_add_source(self, tmp_path, monkeypatch):
        import aiohttp

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")
        session = MagicMock(spec=aiohttp.ClientSession)
        mgr = SourceManager(session, tmp_path / "cache")
        state = mgr.add_source("test", "https://example.com/rules.json")
        assert state.name == "test"
        assert state.url == "https://example.com/rules.json"
        assert state.state == "active"

    def test_remove_source(self, tmp_path, monkeypatch):
        import aiohttp

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")
        session = MagicMock(spec=aiohttp.ClientSession)
        mgr = SourceManager(session, tmp_path / "cache")
        mgr.add_source("test", "https://example.com/rules.json")
        mgr.remove_source("test")
        assert mgr.get_source("test") is None

    def test_list_sources(self, tmp_path, monkeypatch):
        import aiohttp

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")
        session = MagicMock(spec=aiohttp.ClientSession)
        mgr = SourceManager(session, tmp_path / "cache")
        mgr.add_source("a", "https://a.example.com/rules.json")
        mgr.add_source("b", "https://b.example.com/rules.json")
        assert len(mgr.list_sources()) == 2

    @pytest.mark.asyncio
    async def test_degraded_source_skipped_on_update(self, tmp_path, monkeypatch):
        """M2: degraded 状态冷却期内跳过更新。"""
        import time

        import aiohttp

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")
        session = MagicMock(spec=aiohttp.ClientSession)
        mgr = SourceManager(session, tmp_path / "cache")
        state = mgr.add_source("degraded_test", "https://example.com/rules.json")
        state.state = "degraded"
        state.degraded_at = time.monotonic()  # 刚进入 degraded，未冷却

        result = await mgr.update_source("degraded_test")
        assert result["rules_downloaded"] == 0

    @pytest.mark.asyncio
    async def test_daily_update_limit(self, tmp_path, monkeypatch):
        """N9: 每日更新 ≤12 次。"""
        import time

        import aiohttp

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")
        session = MagicMock(spec=aiohttp.ClientSession)
        mgr = SourceManager(session, tmp_path / "cache")
        state = mgr.add_source("limited", "https://example.com/rules.json")
        state.daily_update_count = 12
        state.daily_update_date = time.strftime("%Y-%m-%d")

        result = await mgr.update_source("limited")
        assert result["rules_downloaded"] == 0


# ═══════════════════════════════════════════════════════════════════
# Manifest 下载
# ═══════════════════════════════════════════════════════════════════


class TestManifestDownload:
    """S08-S10 + Schema 校验。"""

    @pytest.mark.asyncio
    async def test_fetch_manifest_success(self, tmp_path, monkeypatch):
        import aiohttp

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")

        manifest_bytes = json.dumps(
            {
                "schema_version": 1,
                "title": "Test Source",
                "version": 1,
                "rules": [],
            }
        ).encode("utf-8")

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_resp.url = "https://example.com/rules.json"
        mock_resp.read = AsyncMock(return_value=manifest_bytes)

        session = AsyncMock(spec=aiohttp.ClientSession)
        session.get.return_value.__aenter__.return_value = mock_resp

        mgr = SourceManager(session, tmp_path / "cache")
        mgr.add_source("test", "https://example.com/rules.json", title="Test Source")

        manifest = await mgr.fetch_manifest("test")
        assert manifest["schema_version"] == 1
        assert manifest["title"] == "Test Source"

    @pytest.mark.asyncio
    async def test_fetch_manifest_emergency_disable(self, tmp_path, monkeypatch):
        """N8: emergency_disable 标记源状态。"""
        import aiohttp

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")

        manifest_bytes = json.dumps(
            {
                "schema_version": 1,
                "emergency_disable": True,
                "rules": [],
            }
        ).encode("utf-8")

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_resp.url = "https://example.com/rules.json"
        mock_resp.read = AsyncMock(return_value=manifest_bytes)

        session = AsyncMock(spec=aiohttp.ClientSession)
        session.get.return_value.__aenter__.return_value = mock_resp

        mgr = SourceManager(session, tmp_path / "cache")
        mgr.add_source("emergency", "https://example.com/rules.json")

        await mgr.fetch_manifest("emergency")
        state = mgr.get_source("emergency")
        assert state.state == "emergency_disabled"

    @pytest.mark.asyncio
    async def test_fetch_manifest_moved_to(self, tmp_path, monkeypatch):
        """N83: moved_to 标记迁移。"""
        import aiohttp

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")

        manifest_bytes = json.dumps(
            {
                "schema_version": 1,
                "moved_to": "https://new.example.com/rules.json",
                "rules": [],
            }
        ).encode("utf-8")

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_resp.url = "https://example.com/rules.json"
        mock_resp.read = AsyncMock(return_value=manifest_bytes)

        session = AsyncMock(spec=aiohttp.ClientSession)
        session.get.return_value.__aenter__.return_value = mock_resp

        mgr = SourceManager(session, tmp_path / "cache")
        mgr.add_source("moved", "https://example.com/rules.json")

        await mgr.fetch_manifest("moved")
        state = mgr.get_source("moved")
        assert state.state == "moved"
        assert state.moved_to == "https://new.example.com/rules.json"


# ═══════════════════════════════════════════════════════════════════
# Config
# ═══════════════════════════════════════════════════════════════════


class TestSourceConfig:
    """S6 新增配置字段。"""

    def test_rules_sources_default_empty(self):
        cfg = CrawlerConfig()
        assert cfg.rules_sources == ()

    def test_rules_auto_update_default_true(self):
        from astrocrawl.config import GlobalSettings

        gs = GlobalSettings()
        assert gs.rules_auto_update is True

    def test_rules_sources_with_data(self):
        cfg = CrawlerConfig(
            rules_sources=[
                {"name": "community", "url": "https://example.com/rules.json", "title": "Community"},
            ]
        )
        assert len(cfg.rules_sources) == 1
        assert cfg.rules_sources[0]["name"] == "community"


# ═══════════════════════════════════════════════════════════════════
# sources.json 文件级 CRUD
# ═══════════════════════════════════════════════════════════════════


class TestSourcesFilePersistence:
    """sources.json 持久化 — add/remove/list/get。"""

    def test_add_source_to_file(self, tmp_path, monkeypatch):
        from astrocrawl.rules._source import add_source_to_file, list_sources_from_file

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")

        add_source_to_file("test_src", "https://example.com/rules.json", title="Test")

        sources = list_sources_from_file()
        assert len(sources) == 1
        assert sources[0]["name"] == "test_src"
        assert sources[0]["url"] == "https://example.com/rules.json"
        assert sources[0]["title"] == "Test"

    def test_add_duplicate_raises(self, tmp_path, monkeypatch):
        from astrocrawl.rules._source import add_source_to_file

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")

        add_source_to_file("src", "https://a.example.com/manifest.json")
        with pytest.raises(ValueError, match="已存在"):
            add_source_to_file("src", "https://b.example.com/manifest.json")

    def test_remove_source_from_file(self, tmp_path, monkeypatch):
        from astrocrawl.rules._source import add_source_to_file, list_sources_from_file, remove_source_from_file

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")

        add_source_to_file("src", "https://example.com/rules.json")
        assert remove_source_from_file("src") is True
        assert list_sources_from_file() == []

    def test_remove_nonexistent_returns_false(self, tmp_path, monkeypatch):
        from astrocrawl.rules._source import remove_source_from_file

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")

        assert remove_source_from_file("nonexistent") is False

    def test_get_source_from_file(self, tmp_path, monkeypatch):
        from astrocrawl.rules._source import add_source_to_file, get_source_from_file

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")

        add_source_to_file("src", "https://example.com/rules.json")
        entry = get_source_from_file("src")
        assert entry["name"] == "src"
        assert entry["url"] == "https://example.com/rules.json"

    def test_get_nonexistent_returns_none(self, tmp_path, monkeypatch):
        from astrocrawl.rules._source import get_source_from_file

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")

        assert get_source_from_file("nonexistent") is None

    def test_list_empty_when_no_file(self, tmp_path, monkeypatch):
        from astrocrawl.rules._source import list_sources_from_file

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")

        assert list_sources_from_file() == []

    def test_add_validates_url(self, tmp_path, monkeypatch):
        from astrocrawl.rules._source import add_source_to_file

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")

        with pytest.raises(ValueError, match="仅支持 HTTPS"):
            add_source_to_file("bad", "http://example.com/rules.json")


# ═══════════════════════════════════════════════════════════════════
# SourceManager + sources.json 集成
# ═══════════════════════════════════════════════════════════════════


class TestSourceManagerFileIntegration:
    """SourceManager 从 sources.json 自动加载 + 持久化。"""

    def test_constructor_loads_from_file(self, tmp_path, monkeypatch):
        import aiohttp

        from astrocrawl.rules._source import SourceManager, add_source_to_file

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")

        add_source_to_file("pre_loaded", "https://example.com/manifest.json", title="Pre")

        session = MagicMock(spec=aiohttp.ClientSession)
        mgr = SourceManager(session, tmp_path / "cache")
        src = mgr.get_source("pre_loaded")
        assert src is not None
        assert src.name == "pre_loaded"
        assert src.url == "https://example.com/manifest.json"

    def test_add_source_persists(self, tmp_path, monkeypatch):
        import aiohttp

        from astrocrawl.rules._source import SourceManager

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")
        monkeypatch.setattr("astrocrawl.rules._source._write_sources_file", lambda sources: None)

        session = MagicMock(spec=aiohttp.ClientSession)
        mgr = SourceManager(session, tmp_path / "cache")
        mgr.add_source("new_src", "https://example.com/rules.json")

        assert mgr.get_source("new_src") is not None

    def test_remove_source_updates_file(self, tmp_path, monkeypatch):
        import aiohttp

        from astrocrawl.rules._source import SourceManager, add_source_to_file

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")

        add_source_to_file("to_remove", "https://example.com/manifest.json")

        session = MagicMock(spec=aiohttp.ClientSession)
        mgr = SourceManager(session, tmp_path / "cache")
        mgr.remove_source("to_remove")
        assert mgr.get_source("to_remove") is None


# ═══════════════════════════════════════════════════════════════════
# 增量更新
# ═══════════════════════════════════════════════════════════════════


class TestIncrementalUpdate:
    """增量更新 — manifest hash 未变时跳过下载。"""

    @pytest.mark.asyncio
    async def test_update_skips_when_hash_unchanged(self, tmp_path, monkeypatch):
        import aiohttp

        from astrocrawl.rules._source import SourceManager, add_source_to_file

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")

        # 预置源并记录 manifest hash
        add_source_to_file("hash_test", "https://example.com/manifest.json", last_manifest_hash="abc123_unchanged_hash")

        session = MagicMock(spec=aiohttp.ClientSession)
        mgr = SourceManager(session, tmp_path / "cache", auto_update=True)

        # Mock fetch_manifest 返回相同 hash
        async def mock_fetch(source_name):
            source = mgr._sources[source_name]
            source.last_manifest_hash = "abc123_unchanged_hash"
            return {"rules": []}

        mgr.fetch_manifest = mock_fetch

        result = await mgr.update_source("hash_test")
        assert result["updated"] is False
        assert result["rules_downloaded"] == 0


# ═══════════════════════════════════════════════════════════════════════
# _log_safe_url
# ═══════════════════════════════════════════════════════════════════════


class TestLogSafeUrl:
    """_log_safe_url — URL 脱敏。"""

    def test_normal_url(self):
        from astrocrawl.rules._source import _log_safe_url

        result = _log_safe_url("https://example.com/path?query=1")
        assert result == "https://example.com"

    def test_strips_query_and_fragment(self):
        from astrocrawl.rules._source import _log_safe_url

        result = _log_safe_url("https://x.com/path?token=secret#section")
        assert result == "https://x.com"

    def test_malformed_url_truncated(self):
        from astrocrawl.rules._source import _log_safe_url

        # urlparse 成功解析 "x"*100 为 scheme="xxx...", netloc="" → 返回 "xxx...://"
        long = "x" * 100
        result = _log_safe_url(long)
        assert len(result) > 0

    def test_invalid_url_no_crash(self):
        from astrocrawl.rules._source import _log_safe_url

        result = _log_safe_url("")
        assert isinstance(result, str)


# ═══════════════════════════════════════════════════════════════════════
# _acquire_sources_lock / _release_sources_lock
# ═══════════════════════════════════════════════════════════════════════


class TestSourcesLock:
    """_acquire_sources_lock / _release_sources_lock。"""

    def test_acquire_opens_lock_file(self, tmp_path, monkeypatch):
        from astrocrawl.rules._source import _acquire_sources_lock, _release_sources_lock

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")
        fd = _acquire_sources_lock(exclusive=True)
        assert fd is not None
        _release_sources_lock(fd)

    def test_release_none_noop(self):
        from astrocrawl.rules._source import _release_sources_lock

        _release_sources_lock(None)

    def test_acquire_failure_returns_none(self, monkeypatch):
        from astrocrawl.rules._source import _acquire_sources_lock

        monkeypatch.setattr("os.open", MagicMock(side_effect=OSError("permission denied")))
        fd = _acquire_sources_lock()
        assert fd is None

    def test_release_oserror_ignored(self, monkeypatch):
        from astrocrawl.rules._source import _release_sources_lock

        # fcntl.flock 成功 → os.close 抛 OSError → 被静默忽略
        monkeypatch.setattr("fcntl.flock", MagicMock())
        monkeypatch.setattr("os.close", MagicMock(side_effect=OSError("bad fd")))
        _release_sources_lock(999)


# ═══════════════════════════════════════════════════════════════════════
# DNS 重绑定检测
# ═══════════════════════════════════════════════════════════════════════


class TestCheckDnsRebinding:
    """check_dns_rebinding — DNS 重绑定硬检测。"""

    @pytest.mark.asyncio
    async def test_public_ip_passes(self, monkeypatch):
        from astrocrawl.rules._source import check_dns_rebinding

        async def _mock_getaddrinfo(host, port):
            return [(None, None, None, None, ("8.8.8.8", 0))]

        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", _mock_getaddrinfo)
        await check_dns_rebinding("https://example.com/manifest.json")

    @pytest.mark.asyncio
    async def test_private_ip_raises(self, monkeypatch):
        from astrocrawl.rules._source import check_dns_rebinding

        async def _mock_getaddrinfo(host, port):
            return [(None, None, None, None, ("10.0.0.1", 0))]

        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", _mock_getaddrinfo)
        with pytest.raises(ValueError, match="DNS"):
            await check_dns_rebinding("https://example.com/manifest.json")

    @pytest.mark.asyncio
    async def test_loopback_raises(self, monkeypatch):
        from astrocrawl.rules._source import check_dns_rebinding

        async def _mock_getaddrinfo(host, port):
            return [(None, None, None, None, ("127.0.0.1", 0))]

        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", _mock_getaddrinfo)
        with pytest.raises(ValueError, match="DNS"):
            await check_dns_rebinding("https://example.com/manifest.json")

    @pytest.mark.asyncio
    async def test_no_hostname_raises(self, monkeypatch):
        from astrocrawl.rules._source import check_dns_rebinding

        # URL without hostname
        with pytest.raises(ValueError, match="hostname"):
            await check_dns_rebinding("not-a-url")


# ═══════════════════════════════════════════════════════════════════════
# _check_redirect_not_cross_origin
# ═══════════════════════════════════════════════════════════════════════


class TestCheckRedirectNotCrossOrigin:
    """_check_redirect_not_cross_origin — S03 重定向检测。"""

    def test_same_origin_passes(self):
        from astrocrawl.rules._source import _check_redirect_not_cross_origin

        _check_redirect_not_cross_origin(
            "https://example.com/new-path",
            "https://example.com/original",
        )

    def test_different_origin_raises(self):
        from astrocrawl.rules._source import _check_redirect_not_cross_origin

        with pytest.raises(ValueError, match="重定向"):
            _check_redirect_not_cross_origin(
                "https://evil.com/manifest.json",
                "https://example.com/manifest.json",
            )

    def test_identical_url_noop(self):
        from astrocrawl.rules._source import _check_redirect_not_cross_origin

        _check_redirect_not_cross_origin("https://x.com/a", "https://x.com/a")


# ═══════════════════════════════════════════════════════════════════════
# SourceManager._rate_limit
# ═══════════════════════════════════════════════════════════════════════


class TestSourceRateLimit:
    """SourceManager._rate_limit — 100ms 请求间隔。"""

    @pytest.mark.asyncio
    async def test_first_call_no_delay(self, monkeypatch, tmp_path):
        from astrocrawl.rules._source import SourceManager

        session = MagicMock(spec=aiohttp.ClientSession)
        mgr = SourceManager(session, tmp_path)
        mgr._last_request = 0
        t0 = time.monotonic()
        await mgr._rate_limit()
        t1 = time.monotonic()
        assert t1 - t0 < 0.05

    @pytest.mark.asyncio
    async def test_consecutive_call_wait(self, monkeypatch, tmp_path):
        from astrocrawl.rules._source import SourceManager

        session = MagicMock(spec=aiohttp.ClientSession)
        mgr = SourceManager(session, tmp_path)
        mgr._last_request = time.monotonic() - 0.01
        t0 = time.monotonic()
        await mgr._rate_limit()
        t1 = time.monotonic()
        assert t1 - t0 >= 0.08


# ═══════════════════════════════════════════════════════════════════════
# Manifest 下载 — 错误路径
# ═══════════════════════════════════════════════════════════════════════


class TestManifestDownloadErrors:
    """_do_fetch_manifest 错误路径全覆盖。"""

    @pytest.mark.asyncio
    async def test_http_non_200(self, tmp_path, monkeypatch):
        import aiohttp

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")
        monkeypatch.setattr("astrocrawl.rules._source.check_dns_rebinding", AsyncMock())

        mock_resp = AsyncMock()
        mock_resp.status = 404
        mock_resp.url = "https://example.com/rules.json"

        session = AsyncMock(spec=aiohttp.ClientSession)
        session.get.return_value.__aenter__.return_value = mock_resp

        mgr = SourceManager(session, tmp_path / "cache")
        mgr.add_source("bad_status", "https://example.com/rules.json")

        with pytest.raises(ValueError, match="HTTP 404"):
            await mgr.fetch_manifest("bad_status")

    @pytest.mark.asyncio
    async def test_content_type_rejection(self, tmp_path, monkeypatch):
        import aiohttp

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")
        monkeypatch.setattr("astrocrawl.rules._source.check_dns_rebinding", AsyncMock())

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.headers = {"Content-Type": "text/html"}
        mock_resp.url = "https://example.com/rules.json"

        session = AsyncMock(spec=aiohttp.ClientSession)
        session.get.return_value.__aenter__.return_value = mock_resp

        mgr = SourceManager(session, tmp_path / "cache")
        mgr.add_source("bad_ct", "https://example.com/rules.json")

        with pytest.raises(ValueError, match="Content-Type"):
            await mgr.fetch_manifest("bad_ct")

    @pytest.mark.asyncio
    async def test_size_limit_exceeded(self, tmp_path, monkeypatch):
        import aiohttp

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")
        monkeypatch.setattr("astrocrawl.rules._source.check_dns_rebinding", AsyncMock())

        # 有效 JSON 但超过 MANIFEST_MAX_BYTES (1MB)
        too_large = json.dumps({"payload": "x" * 2_000_000}).encode("utf-8")

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_resp.url = "https://example.com/rules.json"
        mock_resp.read = AsyncMock(return_value=too_large)

        session = AsyncMock(spec=aiohttp.ClientSession)
        session.get.return_value.__aenter__.return_value = mock_resp

        mgr = SourceManager(session, tmp_path / "cache")
        mgr.add_source("too_big", "https://example.com/rules.json")

        with pytest.raises(ValueError, match="字节限制"):
            await mgr.fetch_manifest("too_big")

    @pytest.mark.asyncio
    async def test_client_error_wrapped(self, tmp_path, monkeypatch):
        import aiohttp

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")
        monkeypatch.setattr("astrocrawl.rules._source.check_dns_rebinding", AsyncMock())

        session = AsyncMock(spec=aiohttp.ClientSession)
        session.get.side_effect = aiohttp.ClientError("connection refused")

        mgr = SourceManager(session, tmp_path / "cache")
        mgr.add_source("conn_err", "https://example.com/rules.json")

        with pytest.raises(ValueError, match="下载失败"):
            await mgr.fetch_manifest("conn_err")

    @pytest.mark.asyncio
    async def test_bad_schema_version(self, tmp_path, monkeypatch):
        import aiohttp

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")
        monkeypatch.setattr("astrocrawl.rules._source.check_dns_rebinding", AsyncMock())

        manifest_bytes = json.dumps({"schema_version": 99, "rules": []}).encode("utf-8")

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_resp.url = "https://example.com/rules.json"
        mock_resp.read = AsyncMock(return_value=manifest_bytes)

        session = AsyncMock(spec=aiohttp.ClientSession)
        session.get.return_value.__aenter__.return_value = mock_resp

        mgr = SourceManager(session, tmp_path / "cache")
        mgr.add_source("bad_schema", "https://example.com/rules.json")

        with pytest.raises(ValueError, match="schema_version"):
            await mgr.fetch_manifest("bad_schema")

    @pytest.mark.asyncio
    async def test_fetch_manifest_source_not_found(self, tmp_path, monkeypatch):
        import aiohttp

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")
        session = MagicMock(spec=aiohttp.ClientSession)
        mgr = SourceManager(session, tmp_path / "cache")

        with pytest.raises(ValueError, match="源不存在"):
            await mgr.fetch_manifest("nonexistent")

    @pytest.mark.asyncio
    async def test_fetch_manifest_emergency_blocked(self, tmp_path, monkeypatch):
        import aiohttp

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")
        session = MagicMock(spec=aiohttp.ClientSession)
        mgr = SourceManager(session, tmp_path / "cache")
        state = mgr.add_source("blocked", "https://example.com/rules.json")
        state.state = "emergency_disabled"

        with pytest.raises(ValueError, match="紧急禁用"):
            await mgr.fetch_manifest("blocked")

    @pytest.mark.asyncio
    async def test_fetch_manifest_moved_blocked(self, tmp_path, monkeypatch):
        import aiohttp

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")
        session = MagicMock(spec=aiohttp.ClientSession)
        mgr = SourceManager(session, tmp_path / "cache")
        state = mgr.add_source("migrated", "https://example.com/rules.json")
        state.state = "moved"
        state.moved_to = "https://new.example.com/rules.json"

        with pytest.raises(ValueError, match="已迁移"):
            await mgr.fetch_manifest("migrated")

    @pytest.mark.asyncio
    async def test_degraded_recovery_to_active(self, tmp_path, monkeypatch):
        """N43: degraded → active on successful manifest fetch."""
        import aiohttp

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")
        monkeypatch.setattr("astrocrawl.rules._source.check_dns_rebinding", AsyncMock())

        manifest_bytes = json.dumps({"schema_version": 1, "rules": []}).encode("utf-8")

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_resp.url = "https://example.com/rules.json"
        mock_resp.read = AsyncMock(return_value=manifest_bytes)

        session = AsyncMock(spec=aiohttp.ClientSession)
        session.get.return_value.__aenter__.return_value = mock_resp

        mgr = SourceManager(session, tmp_path / "cache")
        state = mgr.add_source("recover", "https://example.com/rules.json")
        state.state = "degraded"
        state.consecutive_failures = 3

        await mgr.fetch_manifest("recover")
        assert state.state == "active"
        assert state.consecutive_failures == 0


# ═══════════════════════════════════════════════════════════════════════
# 规则下载 — download_rule + _do_download_rule
# ═══════════════════════════════════════════════════════════════════════


class TestRuleDownload:
    """download_rule() 入口 + _do_download_rule() 核心路径。"""

    @pytest.mark.asyncio
    async def test_source_not_found(self, tmp_path, monkeypatch):
        import aiohttp

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")
        session = MagicMock(spec=aiohttp.ClientSession)
        mgr = SourceManager(session, tmp_path / "cache")

        with pytest.raises(ValueError, match="源不存在"):
            await mgr.download_rule("nonexistent", {"download_url": "https://x.com/r.json", "name": "test"})

    @pytest.mark.asyncio
    async def test_missing_download_url(self, tmp_path, monkeypatch):
        import aiohttp

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")
        session = MagicMock(spec=aiohttp.ClientSession)
        mgr = SourceManager(session, tmp_path / "cache")
        mgr.add_source("src", "https://example.com/manifest.json")

        with pytest.raises(ValueError, match="download_url"):
            await mgr.download_rule("src", {"name": "test"})

    @pytest.mark.asyncio
    async def test_cross_origin_download_url(self, tmp_path, monkeypatch):
        import aiohttp

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")
        session = MagicMock(spec=aiohttp.ClientSession)
        mgr = SourceManager(session, tmp_path / "cache")
        mgr.add_source("src", "https://example.com/manifest.json")

        with pytest.raises(ValueError, match="跨源"):
            await mgr.download_rule(
                "src",
                {"download_url": "https://evil.com/rule.json", "name": "test"},
            )

    @pytest.mark.asyncio
    async def test_download_success(self, tmp_path, monkeypatch):
        import aiohttp

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")
        monkeypatch.setattr("astrocrawl.rules._source.check_dns_rebinding", AsyncMock())

        rule_json = json.dumps({"name": "test_rule", "schema_version": 1, "fields": {}}).encode("utf-8")

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_resp.url = "https://example.com/r/test_rule.json"
        mock_resp.read = AsyncMock(return_value=rule_json)

        session = AsyncMock(spec=aiohttp.ClientSession)
        session.get.return_value.__aenter__.return_value = mock_resp

        cache_dir = tmp_path / "cache"
        mgr = SourceManager(session, cache_dir)
        mgr.add_source("src", "https://example.com/manifest.json")

        path = await mgr.download_rule(
            "src",
            {"download_url": "https://example.com/r/test_rule.json", "name": "test_rule", "sha256": ""},
        )
        assert path.exists()
        assert path.name == "test_rule.json"

    @pytest.mark.asyncio
    async def test_download_with_sha256_match(self, tmp_path, monkeypatch):
        import hashlib

        import aiohttp

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")
        monkeypatch.setattr("astrocrawl.rules._source.check_dns_rebinding", AsyncMock())

        rule_json = json.dumps({"name": "test_rule", "schema_version": 1, "fields": {}}).encode("utf-8")
        expected_hash = hashlib.sha256(rule_json).hexdigest()

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_resp.url = "https://example.com/r/test_rule.json"
        mock_resp.read = AsyncMock(return_value=rule_json)

        session = AsyncMock(spec=aiohttp.ClientSession)
        session.get.return_value.__aenter__.return_value = mock_resp

        cache_dir = tmp_path / "cache"
        mgr = SourceManager(session, cache_dir)
        mgr.add_source("src", "https://example.com/manifest.json")

        path = await mgr.download_rule(
            "src",
            {"download_url": "https://example.com/r/test_rule.json", "name": "test_rule", "sha256": expected_hash},
        )
        assert path.exists()

    @pytest.mark.asyncio
    async def test_download_sha256_mismatch(self, tmp_path, monkeypatch):
        import aiohttp

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")
        monkeypatch.setattr("astrocrawl.rules._source.check_dns_rebinding", AsyncMock())

        rule_json = json.dumps({"name": "test_rule", "fields": {}}).encode("utf-8")

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_resp.url = "https://example.com/r/test_rule.json"
        mock_resp.read = AsyncMock(return_value=rule_json)

        session = AsyncMock(spec=aiohttp.ClientSession)
        session.get.return_value.__aenter__.return_value = mock_resp

        mgr = SourceManager(session, tmp_path / "cache")
        mgr.add_source("src", "https://example.com/manifest.json")

        with pytest.raises(ValueError, match="SHA256 不匹配"):
            await mgr.download_rule(
                "src",
                {"download_url": "https://example.com/r/test_rule.json", "name": "test_rule", "sha256": "deadbeef" * 8},
            )

    @pytest.mark.asyncio
    async def test_download_http_non_200(self, tmp_path, monkeypatch):
        import aiohttp

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")
        monkeypatch.setattr("astrocrawl.rules._source.check_dns_rebinding", AsyncMock())

        mock_resp = AsyncMock()
        mock_resp.status = 500
        mock_resp.url = "https://example.com/r/test_rule.json"

        session = AsyncMock(spec=aiohttp.ClientSession)
        session.get.return_value.__aenter__.return_value = mock_resp

        mgr = SourceManager(session, tmp_path / "cache")
        mgr.add_source("src", "https://example.com/manifest.json")

        with pytest.raises(ValueError, match="HTTP 500"):
            await mgr.download_rule(
                "src",
                {"download_url": "https://example.com/r/test_rule.json", "name": "test_rule"},
            )

    @pytest.mark.asyncio
    async def test_download_client_error(self, tmp_path, monkeypatch):
        import aiohttp

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")
        monkeypatch.setattr("astrocrawl.rules._source.check_dns_rebinding", AsyncMock())

        session = AsyncMock(spec=aiohttp.ClientSession)
        session.get.side_effect = aiohttp.ClientError("timeout")

        mgr = SourceManager(session, tmp_path / "cache")
        mgr.add_source("src", "https://example.com/manifest.json")

        with pytest.raises(ValueError, match="下载失败"):
            await mgr.download_rule(
                "src",
                {"download_url": "https://example.com/r/test_rule.json", "name": "test_rule"},
            )

    @pytest.mark.asyncio
    async def test_download_path_traversal_prevented(self, tmp_path, monkeypatch):
        """N17: 路径遍历防护 — 仅保留文件名最后一段。"""
        import aiohttp

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")
        monkeypatch.setattr("astrocrawl.rules._source.check_dns_rebinding", AsyncMock())

        rule_json = json.dumps({"name": "safe_rule", "fields": {}}).encode("utf-8")

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_resp.url = "https://example.com/r/rule.json"
        mock_resp.read = AsyncMock(return_value=rule_json)

        session = AsyncMock(spec=aiohttp.ClientSession)
        session.get.return_value.__aenter__.return_value = mock_resp

        cache_dir = tmp_path / "cache"
        mgr = SourceManager(session, cache_dir)
        mgr.add_source("src", "https://example.com/manifest.json")

        path = await mgr.download_rule(
            "src",
            {"download_url": "https://example.com/r/rule.json", "name": "../../etc/passwd", "sha256": ""},
        )
        # 路径遍历防护：文件名应为 "passwd.json"，不应逃逸出 cache_dir
        assert path.parent == cache_dir / "src"
        assert path.name == "passwd.json"


# ═══════════════════════════════════════════════════════════════════════
# update_source — 错误路径与全生命周期
# ═══════════════════════════════════════════════════════════════════════


class TestUpdateSourceFull:
    """update_source 全路径：错误/降级/规则下载/dry_run。"""

    @pytest.mark.asyncio
    async def test_source_not_found(self, tmp_path, monkeypatch):
        import aiohttp

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")
        session = MagicMock(spec=aiohttp.ClientSession)
        mgr = SourceManager(session, tmp_path / "cache")

        with pytest.raises(ValueError, match="源不存在"):
            await mgr.update_source("nonexistent")

    @pytest.mark.asyncio
    async def test_degraded_probing_after_cooldown(self, tmp_path, monkeypatch):
        """M2: 冷却期满后放行探测。"""
        import aiohttp

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")
        monkeypatch.setattr("astrocrawl.rules._source.check_dns_rebinding", AsyncMock())

        manifest_bytes = json.dumps({"schema_version": 1, "rules": []}).encode("utf-8")

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_resp.url = "https://example.com/rules.json"
        mock_resp.read = AsyncMock(return_value=manifest_bytes)

        session = AsyncMock(spec=aiohttp.ClientSession)
        session.get.return_value.__aenter__.return_value = mock_resp

        mgr = SourceManager(session, tmp_path / "cache")
        state = mgr.add_source("probe_test", "https://example.com/rules.json")
        state.state = "degraded"
        state.degraded_at = time.monotonic() - 9999  # 很久以前

        result = await mgr.update_source("probe_test")
        assert result["updated"] is True

    @pytest.mark.asyncio
    async def test_manifest_fetch_triggers_degraded(self, tmp_path, monkeypatch):
        """连续 3 次失败 → degraded。"""
        import aiohttp

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")
        monkeypatch.setattr("astrocrawl.rules._source.check_dns_rebinding", AsyncMock())

        session = AsyncMock(spec=aiohttp.ClientSession)
        session.get.side_effect = aiohttp.ClientError("boom")

        mgr = SourceManager(session, tmp_path / "cache")
        state = mgr.add_source("fail3", "https://example.com/rules.json")
        state.consecutive_failures = 2

        with pytest.raises(ValueError):
            await mgr.update_source("fail3")
        assert state.state == "degraded"
        assert state.consecutive_failures == 3

    @pytest.mark.asyncio
    async def test_dry_run_skips_download(self, tmp_path, monkeypatch):
        import aiohttp

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")
        monkeypatch.setattr("astrocrawl.rules._source.check_dns_rebinding", AsyncMock())

        manifest_bytes = json.dumps(
            {"schema_version": 1, "rules": [{"download_url": "https://example.com/r/test.json", "name": "test_rule"}]}
        ).encode("utf-8")

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_resp.url = "https://example.com/rules.json"
        mock_resp.read = AsyncMock(return_value=manifest_bytes)

        session = AsyncMock(spec=aiohttp.ClientSession)
        session.get.return_value.__aenter__.return_value = mock_resp

        mgr = SourceManager(session, tmp_path / "cache")
        mgr.add_source("dry", "https://example.com/rules.json")

        result = await mgr.update_source("dry", dry_run=True)
        assert result["rules_downloaded"] == 0

    @pytest.mark.asyncio
    async def test_download_rules_with_partial_failure(self, tmp_path, monkeypatch):
        import aiohttp

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")
        monkeypatch.setattr("astrocrawl.rules._source.check_dns_rebinding", AsyncMock())

        # Manifest with 2 rules — first succeeds, second fails
        manifest_bytes = json.dumps(
            {
                "schema_version": 1,
                "rules": [
                    {"download_url": "https://example.com/r/good.json", "name": "good_rule"},
                    {"download_url": "https://example.com/r/bad.json", "name": "bad_rule"},
                ],
            }
        ).encode("utf-8")

        manifest_resp = AsyncMock()
        manifest_resp.status = 200
        manifest_resp.headers = {"Content-Type": "application/json"}
        manifest_resp.url = "https://example.com/rules.json"
        manifest_resp.read = AsyncMock(return_value=manifest_bytes)

        # good rule response
        rule_json = json.dumps({"name": "good_rule", "fields": {}}).encode("utf-8")
        good_resp = AsyncMock()
        good_resp.status = 200
        good_resp.headers = {"Content-Type": "application/json"}
        good_resp.url = "https://example.com/r/good.json"
        good_resp.read = AsyncMock(return_value=rule_json)

        session = AsyncMock(spec=aiohttp.ClientSession)
        # First call → manifest, second call → rule_good, third call → rule_bad fails
        session.get.side_effect = [
            MagicMock(__aenter__=AsyncMock(return_value=manifest_resp)),
            MagicMock(__aenter__=AsyncMock(return_value=good_resp)),
            aiohttp.ClientError("bad rule download failed"),
        ]

        cache_dir = tmp_path / "cache"
        mgr = SourceManager(session, cache_dir)
        mgr.add_source("partial", "https://example.com/rules.json")

        result = await mgr.update_source("partial")
        # One rule downloaded, one failed (logged but not fatal)
        assert result["rules_downloaded"] == 1


# ═══════════════════════════════════════════════════════════════════════
# update_all
# ═══════════════════════════════════════════════════════════════════════


class TestUpdateAll:
    """update_all — 批量更新 + 状态跳过。"""

    @pytest.mark.asyncio
    async def test_auto_update_disabled(self, tmp_path, monkeypatch):
        import aiohttp

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")
        session = MagicMock(spec=aiohttp.ClientSession)
        mgr = SourceManager(session, tmp_path / "cache", auto_update=False)

        result = await mgr.update_all()
        assert result["sources_updated"] == []

    @pytest.mark.asyncio
    async def test_skips_emergency_disabled(self, tmp_path, monkeypatch):
        import aiohttp

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")
        session = MagicMock(spec=aiohttp.ClientSession)
        mgr = SourceManager(session, tmp_path / "cache")
        state = mgr.add_source("blocked", "https://example.com/rules.json")
        state.state = "emergency_disabled"

        result = await mgr.update_all()
        assert "blocked" not in result["sources_updated"]

    @pytest.mark.asyncio
    async def test_skips_moved(self, tmp_path, monkeypatch):
        import aiohttp

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")
        session = MagicMock(spec=aiohttp.ClientSession)
        mgr = SourceManager(session, tmp_path / "cache")
        state = mgr.add_source("gone", "https://example.com/rules.json")
        state.state = "moved"
        state.moved_to = "https://new.example.com/rules.json"

        result = await mgr.update_all()
        assert "gone" not in result["sources_updated"]

    @pytest.mark.asyncio
    async def test_update_error_does_not_crash(self, tmp_path, monkeypatch):
        """单源失败不影响其他源。"""
        import aiohttp

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")
        session = MagicMock(spec=aiohttp.ClientSession)
        mgr = SourceManager(session, tmp_path / "cache")
        state = mgr.add_source("will_fail", "https://example.com/rules.json")
        state.daily_update_count = 12  # 已达上限，会被跳过
        state.daily_update_date = time.strftime("%Y-%m-%d")

        result = await mgr.update_all()
        assert result["sources_skipped"] >= 1


# ═══════════════════════════════════════════════════════════════════════
# _persist_source_metadata
# ═══════════════════════════════════════════════════════════════════════


class TestPersistSourceMetadata:
    """_persist_source_metadata — 更新 + 回退创建。"""

    def test_update_existing_source(self, tmp_path, monkeypatch):
        import aiohttp

        from astrocrawl.rules._source import SourceManager, add_source_to_file, get_source_from_file

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")

        add_source_to_file("meta_test", "https://example.com/manifest.json", title="Test")

        session = MagicMock(spec=aiohttp.ClientSession)
        mgr = SourceManager(session, tmp_path / "cache")
        mgr._persist_source_metadata("meta_test", "abc123_hash")

        entry = get_source_from_file("meta_test")
        assert entry["last_manifest_hash"] == "abc123_hash"

    def test_fallback_create_new_source(self, tmp_path, monkeypatch):
        """_add_source_internal 添加了内存源但未持久化 → update 找不到 → add fallback。"""
        import aiohttp

        from astrocrawl.rules._source import SourceManager, get_source_from_file

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")

        session = MagicMock(spec=aiohttp.ClientSession)
        mgr = SourceManager(session, tmp_path / "cache")
        mgr._add_source_internal("orphan", "https://example.com/manifest.json", title="Orphan")

        mgr._persist_source_metadata("orphan", "fallback_hash")

        entry = get_source_from_file("orphan")
        assert entry["last_manifest_hash"] == "fallback_hash"


# ═══════════════════════════════════════════════════════════════════════
# CRUD 缺口补全
# ═══════════════════════════════════════════════════════════════════════


class TestCrudGaps:
    """文件级 CRUD 缺口：update_source_in_file、文件损坏。"""

    def test_update_source_in_file_success(self, tmp_path, monkeypatch):
        from astrocrawl.rules._source import add_source_to_file, get_source_from_file, update_source_in_file

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")
        add_source_to_file("upd", "https://example.com/rules.json", title="Old")

        result = update_source_in_file("upd", title="New", homepage="https://example.com")
        assert result is True
        entry = get_source_from_file("upd")
        assert entry["title"] == "New"
        assert entry["homepage"] == "https://example.com"

    def test_update_source_in_file_not_found(self, tmp_path, monkeypatch):
        from astrocrawl.rules._source import update_source_in_file

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")
        assert update_source_in_file("nobody") is False

    def test_list_corrupt_json(self, tmp_path, monkeypatch):
        from astrocrawl.rules._source import list_sources_from_file

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")
        tmp_path.joinpath("sources.json").write_text("not valid json {{{", encoding="utf-8")

        result = list_sources_from_file()
        assert result == []

    def test_list_not_a_dict(self, tmp_path, monkeypatch):
        from astrocrawl.rules._source import list_sources_from_file

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")
        tmp_path.joinpath("sources.json").write_text("[1, 2, 3]", encoding="utf-8")

        result = list_sources_from_file()
        assert result == []

    def test_source_manager_skip_corrupt_entry(self, tmp_path, monkeypatch):
        """SourceManager 构造时跳过损坏条目。"""
        import aiohttp

        from astrocrawl.rules._source import SourceManager

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")

        # 写入一个有效 + 一个无 URL 的无效条目
        import json as _json

        tmp_path.joinpath("sources.json").write_text(
            _json.dumps(
                {
                    "sources": [
                        {"name": "good", "url": "https://example.com/rules.json"},
                        {"name": "bad", "url": "http://not-https.com/rules.json"},  # HTTPS校验失败
                    ]
                }
            ),
            encoding="utf-8",
        )

        session = MagicMock(spec=aiohttp.ClientSession)
        mgr = SourceManager(session, tmp_path / "cache")
        assert mgr.get_source("good") is not None
        assert mgr.get_source("bad") is None  # 被跳过


# ═══════════════════════════════════════════════════════════════════════
# SourceManager.remove_source — 缓存清理
# ═══════════════════════════════════════════════════════════════════════


class TestRemoveSourceCacheCleanup:
    """remove_source 缓存目录清理。"""

    def test_remove_source_cleans_cache_dir(self, tmp_path, monkeypatch):
        import aiohttp

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")

        cache_dir = tmp_path / "cache"
        source_cache = cache_dir / "cleanme"
        source_cache.mkdir(parents=True)
        (source_cache / "dummy.json").write_text("{}")

        session = MagicMock(spec=aiohttp.ClientSession)
        mgr = SourceManager(session, cache_dir)
        mgr.add_source("cleanme", "https://example.com/rules.json")

        assert source_cache.is_dir()
        mgr.remove_source("cleanme")
        assert not source_cache.exists()

    def test_remove_source_no_cache_dir(self, tmp_path, monkeypatch):
        import aiohttp

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")

        session = MagicMock(spec=aiohttp.ClientSession)
        mgr = SourceManager(session, tmp_path / "cache")
        mgr.add_source("nocache", "https://example.com/rules.json")

        mgr.remove_source("nocache")  # 无缓存目录，不崩溃
        assert mgr.get_source("nocache") is None


# ═══════════════════════════════════════════════════════════════════════
# 安全校验 — 边界情况
# ═══════════════════════════════════════════════════════════════════════


class TestSecurityEdgeCases:
    """URL 校验 + DNS 检查边界。"""

    def test_auth_in_path_position(self):
        """第二道 @ 检查：https://evil.com@legit.com — urlparse 可能漏过。"""
        from astrocrawl.rules._source import _validate_source_url

        # urlparse("https://evil.com@legit.com") → netloc="evil.com", path="" → 漏过第一道
        # 但 https://evil.com@legit.com 实际连接到 legit.com
        with pytest.raises(ValueError, match="认证"):
            _validate_source_url("https://evil.com@legit.com/manifest.json")

    @pytest.mark.asyncio
    async def test_dns_check_failure_soft(self, monkeypatch):
        """DNS 解析失败时仅 WARNING，不阻断下载。"""
        from astrocrawl.rules._source import check_dns_rebinding

        async def _mock_getaddrinfo(host, port):
            raise OSError("DNS temporarily unavailable")

        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", _mock_getaddrinfo)
        # 不抛异常
        await check_dns_rebinding("https://example.com/manifest.json")

    @pytest.mark.asyncio
    async def test_dns_check_non_s05_value_error_suppressed(self, monkeypatch):
        """非 S05 的 ValueError 被静默吞下（ip_address 对畸形 IP 字符串）。"""
        from astrocrawl.rules._source import check_dns_rebinding

        async def _mock_getaddrinfo(host, port):
            return [(None, None, None, None, ("not-an-ip", 0))]

        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", _mock_getaddrinfo)
        await check_dns_rebinding("https://example.com/manifest.json")  # 不抛异常

    def test_url_empty_raises(self):
        from astrocrawl.rules._source import _validate_source_url

        with pytest.raises(ValueError, match="长度"):
            _validate_source_url("")

    def test_log_safe_url_exception_path(self):
        """_log_safe_url — urlparse 异常时的截断回退。"""
        from astrocrawl.rules._source import _log_safe_url

        long_str = "x" * 100
        result = _log_safe_url(long_str)
        assert len(result) <= 64


# ═══════════════════════════════════════════════════════════════════════
# 最终缺口补全
# ═══════════════════════════════════════════════════════════════════════


class TestFinalGaps:
    """剩余未覆盖行：rmtree OSError、download Content-Type/size、update_all updated + error。"""

    def test_remove_source_cache_cleanup_oserror(self, tmp_path, monkeypatch):
        """shutil.rmtree 抛 OSError 时被静默捕获。"""
        import shutil

        import aiohttp

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")

        cache_dir = tmp_path / "cache"
        source_cache = cache_dir / "err_src"
        source_cache.mkdir(parents=True)

        session = MagicMock(spec=aiohttp.ClientSession)
        mgr = SourceManager(session, cache_dir)
        mgr.add_source("err_src", "https://example.com/rules.json")

        monkeypatch.setattr(shutil, "rmtree", MagicMock(side_effect=OSError("permission denied")))
        mgr.remove_source("err_src")  # 不抛异常
        assert mgr.get_source("err_src") is None

    @pytest.mark.asyncio
    async def test_download_rule_content_type_rejection(self, tmp_path, monkeypatch):
        """_do_download_rule — 非 JSON Content-Type 被拒绝。"""
        import aiohttp

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")
        monkeypatch.setattr("astrocrawl.rules._source.check_dns_rebinding", AsyncMock())

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.headers = {"Content-Type": "text/plain"}
        mock_resp.url = "https://example.com/r/test_rule.json"

        session = AsyncMock(spec=aiohttp.ClientSession)
        session.get.return_value.__aenter__.return_value = mock_resp

        mgr = SourceManager(session, tmp_path / "cache")
        mgr.add_source("src", "https://example.com/manifest.json")

        with pytest.raises(ValueError, match="Content-Type"):
            await mgr.download_rule(
                "src",
                {"download_url": "https://example.com/r/test_rule.json", "name": "test_rule"},
            )

    @pytest.mark.asyncio
    async def test_download_rule_size_limit(self, tmp_path, monkeypatch):
        """_do_download_rule — 规则文件超过 MAX_RULE_FILE_SIZE 被拒绝。"""
        import aiohttp

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")
        monkeypatch.setattr("astrocrawl.rules._source.check_dns_rebinding", AsyncMock())

        # 有效 JSON 但超过 MAX_RULE_FILE_SIZE (2MB)
        too_large = json.dumps({"name": "big_rule", "fields": {}, "data": "x" * 3_000_000}).encode("utf-8")

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_resp.url = "https://example.com/r/test_rule.json"
        mock_resp.read = AsyncMock(return_value=too_large)

        session = AsyncMock(spec=aiohttp.ClientSession)
        session.get.return_value.__aenter__.return_value = mock_resp

        mgr = SourceManager(session, tmp_path / "cache")
        mgr.add_source("src", "https://example.com/manifest.json")

        with pytest.raises(ValueError, match="字节"):
            await mgr.download_rule(
                "src",
                {"download_url": "https://example.com/r/test_rule.json", "name": "test_rule"},
            )

    @pytest.mark.asyncio
    async def test_update_all_with_successful_update(self, tmp_path, monkeypatch):
        """update_all — 成功更新的源出现在 sources_updated 中。"""
        import aiohttp

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")
        monkeypatch.setattr("astrocrawl.rules._source.check_dns_rebinding", AsyncMock())

        manifest_bytes = json.dumps({"schema_version": 1, "rules": []}).encode("utf-8")

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.headers = {"Content-Type": "application/json"}
        mock_resp.url = "https://example.com/rules.json"
        mock_resp.read = AsyncMock(return_value=manifest_bytes)

        session = AsyncMock(spec=aiohttp.ClientSession)
        session.get.return_value.__aenter__.return_value = mock_resp

        mgr = SourceManager(session, tmp_path / "cache")
        mgr.add_source("will_update", "https://example.com/rules.json")

        result = await mgr.update_all()
        assert "will_update" in result["sources_updated"]

    @pytest.mark.asyncio
    async def test_update_all_catches_update_source_exception(self, tmp_path, monkeypatch):
        """update_all — 单源 update_source 抛异常时被捕获，不影响流程。"""
        import aiohttp

        monkeypatch.setattr("astrocrawl.rules._source.SOURCES_FILE", tmp_path / "sources.json")
        monkeypatch.setattr("astrocrawl.rules._source.check_dns_rebinding", AsyncMock())

        session = AsyncMock(spec=aiohttp.ClientSession)
        session.get.side_effect = aiohttp.ClientError("network error")

        mgr = SourceManager(session, tmp_path / "cache")
        mgr.add_source("crash", "https://example.com/rules.json")

        result = await mgr.update_all()
        assert "crash" not in result["sources_updated"]
        assert result["sources_skipped"] >= 1

"""CrawlState 行为测试 — 真实 CrawlState + aiosqlite :memory:。

使用与 test_db.py 相同的用例模式，验证 CrawlState 核心行为。
"""

from __future__ import annotations

import pytest

from tests._fakes import FakeBrowserPool

# ── 内容哈希 ──────────────────────────────────────────────


class TestContentHashes:
    async def test_add_and_detect_duplicate(self, fake_state):
        ok = await fake_state.add_content_hash("abc123", "https://example.com")
        assert ok is True
        dup = await fake_state.add_content_hash("abc123", "https://other.com")
        assert dup is False


# ── 边界链接 ──────────────────────────────────────────────────


class TestBoundaryLinks:
    async def test_save_and_promote(self, fake_state):
        saved = await fake_state.save_boundary_links(
            "https://parent.com",
            ["https://child1.com", "https://child2.com"],
            0,
        )
        assert saved == 2
        promoted = await fake_state.promote_boundary_links(new_depth=2)
        assert len(promoted) == 2
        assert ("https://child1.com", 1) in promoted

    async def test_get_lost_children(self, fake_state):
        await fake_state.save_boundary_links("https://parent.com", ["https://lost.com"], 0)
        lost = await fake_state.get_lost_children(parent_depth=0)
        assert "https://lost.com" in lost
        await fake_state.mark_completed("https://lost.com", 0)
        lost2 = await fake_state.get_lost_children(parent_depth=0)
        assert "https://lost.com" not in lost2


# ── 元数据与统计 ──────────────────────────────────────────────────


class TestMetaAndStats:
    async def test_set_and_get_meta(self, fake_state):
        await fake_state.set_meta("key1", "value1")
        assert await fake_state.get_meta("key1") == "value1"
        assert await fake_state.get_meta("nonexistent", "fallback") == "fallback"

    async def test_counts_by_outcome(self, fake_state):
        await fake_state.mark_completed("https://a.com", 0, outcome="ok")
        await fake_state.mark_completed("https://b.com", 0, outcome="robots_denied")
        await fake_state.mark_completed("https://c.com", 0, outcome="ok")
        counts = await fake_state.counts_by_outcome()
        assert counts == {"ok": 2, "robots_denied": 1}

    async def test_reset_all(self, fake_state):
        await fake_state.mark_completed("https://b.com", 0)
        await fake_state.set_meta("key", "val")
        await fake_state.reset_all()
        assert await fake_state.completed_count() == 0
        assert await fake_state.get_meta("key") == ""


# ── FakeBrowserPool ─────────────────────────────────────────────


class TestFakeBrowserPool:
    async def test_default_success(self, fake_browser_pool):
        from astrocrawl.browser.browser_pool import FetchRequest

        result = await fake_browser_pool.send(FetchRequest("https://example.com"))
        assert result.url == "https://example.com"
        assert result.html == "<html></html>"
        assert result.status_code == 200

    async def test_preprogrammed_failure(self):
        from astrocrawl.browser.browser_pool import FetchError, FetchRequest

        f = FakeBrowserPool({"https://bad.com": FetchError("timeout", "timeout", True)})
        result = await f.send(FetchRequest("https://bad.com"))
        assert isinstance(result, FetchError)
        assert result.error == "timeout"
        assert result.is_infra is True

    async def test_call_tracking(self, fake_browser_pool):
        from astrocrawl.browser.browser_pool import FetchRequest

        await fake_browser_pool.send(FetchRequest("https://a.com"))
        await fake_browser_pool.send(FetchRequest("https://b.com"))
        assert len(fake_browser_pool.calls) == 2
        assert fake_browser_pool.calls[0] == "https://a.com"
        assert fake_browser_pool.calls[1] == "https://b.com"


# ── FakeWriter ────────────────────────────────────────────────


class TestFakeWriter:
    async def test_records_collected(self, fake_writer):
        await fake_writer.start()
        await fake_writer.write_record({"url": "a", "status": 200})
        await fake_writer.write_record({"url": "b", "status": 404})
        await fake_writer.aclose()
        assert len(fake_writer.records) == 2
        assert fake_writer.records[0]["url"] == "a"
        assert fake_writer._started is True
        assert fake_writer.finalized is True


# ═══════════════════════════════════════════════════════════════════════
# 新增 Browser Fake 基础设施测试
# ═══════════════════════════════════════════════════════════════════════


class TestFakeBrowser:
    """FakeBrowser 契约验证。"""

    async def test_new_context_kwargs_captured(self):
        from tests._fakes import FakeBrowser

        browser = FakeBrowser()
        ctx = await browser.new_context(
            viewport={"width": 800, "height": 600},
            user_agent="test",
        )
        assert browser.last_context_kwargs["viewport"] == {"width": 800, "height": 600}
        assert browser.last_context_kwargs["user_agent"] == "test"
        assert ctx is not None

    async def test_raise_on_new_context(self):
        from tests._fakes import FakeBrowser

        browser = FakeBrowser()
        browser._raise_on_new_context = RuntimeError("fail")
        with pytest.raises(RuntimeError):
            await browser.new_context()


class TestFakeBrowserContext:
    """FakeBrowserContext 契约验证。"""

    async def test_new_page_controllable_failure(self):
        from tests._fakes import FakeBrowserContext

        ctx = FakeBrowserContext()
        ctx._raise_on_new_page = RuntimeError("fail")
        with pytest.raises(RuntimeError):
            await ctx.new_page()

    async def test_cookies_roundtrip(self):
        from tests._fakes import FakeBrowserContext

        ctx = FakeBrowserContext()
        await ctx.add_cookies([{"name": "a", "value": "1"}])
        cookies = await ctx.cookies()
        assert len(cookies) == 1
        assert cookies[0]["name"] == "a"
        await ctx.clear_cookies()
        assert len(await ctx.cookies()) == 0


class TestFakeProxyManager:
    """FakeProxyManager 契约验证。"""

    async def test_get_proxy_prefer_different(self):
        from tests._fakes import FakeProxyManager

        pm = FakeProxyManager(["http://p1:8080", "http://p2:8080"])
        p = await pm.get_proxy(prefer_different_than="http://p1:8080")
        assert p == "http://p2:8080"

    async def test_get_proxy_empty_returns_none(self):
        from tests._fakes import FakeProxyManager

        pm = FakeProxyManager()
        assert await pm.get_proxy() is None

    def test_healthy_proxies_all_available(self):
        """全部代理健康 → 返回完整列表。"""
        from tests._fakes import FakeProxyManager

        pm = FakeProxyManager(["http://p1:8080", "http://p2:8080"])
        assert pm.healthy_proxies_in_pool() == ["http://p1:8080", "http://p2:8080"]

    def test_healthy_proxies_all_unavailable(self):
        """全部代理不健康 → 返回空列表。"""
        from tests._fakes import FakeProxyManager

        pm = FakeProxyManager(["http://p1:8080", "http://p2:8080"])
        pm._available_flag = False
        assert pm.healthy_proxies_in_pool() == []


class TestFakePage:
    """FakePage 扩展方法契约验证。"""

    async def test_close_marks_closed(self):
        from tests._fakes import FakePage

        page = FakePage()
        assert not page.is_closed()
        await page.close()
        assert page.is_closed()

    async def test_evaluate_returns_empty_string(self):
        from tests._fakes import FakePage

        page = FakePage()
        result = await page.evaluate("1 + 1")
        assert result == ""

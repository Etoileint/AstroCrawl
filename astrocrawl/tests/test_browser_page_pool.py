"""PagePool 页面生命周期 + safe_close_page 测试。

使用 FakeBrowserContext + FakePage 验证页面创建/销毁/关闭生命周期。
"""

from __future__ import annotations

import pytest

from astrobase import LogfmtLogger
from astrocrawl.browser.page_pool import PagePool, safe_close_page
from tests._fakes import FakeBrowserContext, FakePage

# ═══════════════════════════════════════════════════════════════════════
# safe_close_page
# ═══════════════════════════════════════════════════════════════════════


class TestSafeClosePage:
    """safe_close_page 安全关闭。"""

    _log = LogfmtLogger("astrocrawl.test")

    async def test_none_page_noop(self):
        """None → 无操作, 不抛异常。"""
        await safe_close_page(None, self._log)

    async def test_already_closed_noop(self):
        """已关闭页面 → 无操作, 不重复关闭。"""
        page = FakePage()
        await page.close()
        assert page.is_closed()
        await safe_close_page(page, self._log)
        assert page.is_closed()

    async def test_normal_close(self):
        """正常页面关闭成功。"""
        page = FakePage()
        assert not page.is_closed()
        await safe_close_page(page, self._log)
        assert page.is_closed()

    async def test_close_error_suppressed(self, monkeypatch):
        """page.close() 非超时异常被捕获，不向上传播。"""
        page = FakePage()

        async def _bad_close():
            raise RuntimeError("browser dead")

        monkeypatch.setattr(page, "close", _bad_close)
        await safe_close_page(page, self._log)
        # 不抛异常，页面保持未关闭状态


# ═══════════════════════════════════════════════════════════════════════
# PagePool.acquire
# ═══════════════════════════════════════════════════════════════════════


class TestPagePoolAcquire:
    """PagePool.acquire 页面获取。"""

    async def test_create_new_page(self):
        """调用 context.new_page() 创建新页面。"""
        ctx = FakeBrowserContext()
        pool = PagePool(ctx)
        page = await pool.acquire()
        assert page is not None
        assert page in ctx._pages
        assert not page.is_closed()

    async def test_closed_pool_raises(self):
        """池关闭后 acquire 抛出 RuntimeError。"""
        ctx = FakeBrowserContext()
        pool = PagePool(ctx)
        await pool.close_all()
        with pytest.raises(RuntimeError, match="已关闭"):
            await pool.acquire()

    async def test_retry_exhaustion(self, monkeypatch):
        """PAGE_CREATE_RETRIES 耗尽后传播异常。"""
        ctx = FakeBrowserContext()
        ctx._raise_on_new_page = RuntimeError("creation failed")
        monkeypatch.setattr("astrocrawl.browser.page_pool.PAGE_CREATE_RETRIES", 2)
        monkeypatch.setattr("astrocrawl.browser.page_pool.PAGE_CREATE_BACKOFF", 0.0)
        pool = PagePool(ctx)
        with pytest.raises(RuntimeError, match="creation failed"):
            await pool.acquire()

    async def test_retry_succeeds_after_transient_failure(self, monkeypatch):
        """首次 new_page 失败后重试成功，返回有效页面。"""
        ctx = FakeBrowserContext()
        call_count = 0
        _original = ctx.new_page

        async def _flaky():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("transient")
            return await _original()

        monkeypatch.setattr(ctx, "new_page", _flaky)
        monkeypatch.setattr("astrocrawl.browser.page_pool.PAGE_CREATE_BACKOFF", 0.0)
        pool = PagePool(ctx)
        page = await pool.acquire()
        assert page is not None
        assert not page.is_closed()
        assert call_count == 2


# ═══════════════════════════════════════════════════════════════════════
# PagePool.remove_broken
# ═══════════════════════════════════════════════════════════════════════


class TestPagePoolRemoveBroken:
    """PagePool.remove_broken 坏页面销毁。"""

    async def test_remove_broken_closes_page(self):
        """正常页面被关闭。"""
        ctx = FakeBrowserContext()
        pool = PagePool(ctx)
        page = await pool.acquire()
        await pool.remove_broken(page)
        assert page.is_closed()

    async def test_remove_broken_none_noop(self):
        """None 无操作，不抛异常。"""
        ctx = FakeBrowserContext()
        pool = PagePool(ctx)
        await pool.remove_broken(None)

    async def test_remove_broken_already_closed_noop(self):
        """已关闭页面不重复操作。"""
        ctx = FakeBrowserContext()
        pool = PagePool(ctx)
        page = await pool.acquire()
        await page.close()
        await pool.remove_broken(page)

    async def test_remove_broken_survives_goto_failure(self):
        """about:blank goto 失败后仍执行 safe_close_page，页面被关闭。"""
        ctx = FakeBrowserContext()
        pool = PagePool(ctx)
        page = FakePage(goto_error=RuntimeError("page dead"))
        await pool.remove_broken(page)
        assert page.is_closed()


# ═══════════════════════════════════════════════════════════════════════
# PagePool.close_all
# ═══════════════════════════════════════════════════════════════════════


class TestPagePoolCloseAll:
    """PagePool.close_all 关闭池。"""

    async def test_close_all_sets_closed(self):
        """close_all 标记 _closed=True，阻止后续 acquire。"""
        ctx = FakeBrowserContext()
        pool = PagePool(ctx)
        await pool.close_all()
        assert pool._closed
        with pytest.raises(RuntimeError, match="已关闭"):
            await pool.acquire()

    async def test_close_all_idempotent(self):
        """连续两次 close_all 不抛异常。"""
        ctx = FakeBrowserContext()
        pool = PagePool(ctx)
        await pool.close_all()
        await pool.close_all()
        assert pool._closed

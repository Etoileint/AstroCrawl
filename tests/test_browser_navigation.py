"""safe_goto 双层超时包装测试 — navigation.py。

使用 FakePage (goto_error 注入 + goto_delay 控制) 验证超时行为。
"""

from __future__ import annotations

import asyncio

import pytest

from astrocrawl.browser.navigation import safe_goto
from tests._fakes import FakePage

# ═══════════════════════════════════════════════════════════════════════
# safe_goto
# ═══════════════════════════════════════════════════════════════════════


class TestSafeGoto:
    """safe_goto 双层超时包装。"""

    async def test_returns_response(self):
        """成功路径返回 Response。"""
        page = FakePage(url="http://example.com", html="<html></html>")
        response = await safe_goto(page, "http://example.com", 30000, 35.0)
        assert response is not None
        assert response.url == "http://example.com"
        assert response.status == 200
        assert response.ok is True

    async def test_playwright_timeout_propagates(self):
        """page.goto 抛异常 → 异常传播。"""
        page = FakePage(goto_error=TimeoutError("CDP timeout"))
        with pytest.raises(TimeoutError, match="CDP timeout"):
            await safe_goto(page, "http://example.com", 30000, 35.0)

    async def test_asyncio_timeout_fires(self):
        """page.goto 挂起 → 外层 asyncio.wait_for TimeoutError。"""
        page = FakePage(goto_delay=1.0)
        with pytest.raises(asyncio.TimeoutError):
            await safe_goto(page, "http://example.com", 30000, 0.01)

    async def test_wait_until_passthrough(self):
        """wait_until 参数透传到 page.goto, 默认值 'domcontentloaded'。"""
        page = FakePage()
        await safe_goto(page, "http://x.com", 30000, 35.0, wait_until="load")
        assert page._last_wait_until == "load"
        # 默认值
        page2 = FakePage()
        await safe_goto(page2, "http://y.com", 30000, 35.0)
        assert page2._last_wait_until == "domcontentloaded"

    async def test_timeout_arithmetic(self):
        """asyncio_timeout_s 应 > playwright_timeout_ms/1000 + 5s 缓冲。"""
        playwright_ms = 30000
        asyncio_s = playwright_ms / 1000 + 5.0
        assert asyncio_s == 35.0
        assert asyncio_s > playwright_ms / 1000
        page = FakePage()
        response = await safe_goto(page, "http://x.com", playwright_ms, asyncio_s)
        assert response is not None

    async def test_goto_returns_none_returns_none(self):
        """page.goto() 返回 None （如同页导航 about:blank）→ safe_goto 也返回 None。"""

        class _NoneGotoPage:
            async def goto(self, url, timeout=30000, wait_until="domcontentloaded"):
                return None

        page = _NoneGotoPage()
        response = await safe_goto(page, "about:blank", 30000, 35.0)
        assert response is None

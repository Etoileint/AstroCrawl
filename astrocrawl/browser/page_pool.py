from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING, Optional

from astrocrawl._constants import (
    ABOUT_BLANK_ASYNCIO_TIMEOUT,
    PAGE_CLOSE_TIMEOUT,
    PAGE_CREATE_BACKOFF,
    PAGE_CREATE_RETRIES,
    PAGE_CREATE_TIMEOUT,
)
from astrocrawl.utils.logging import LogfmtLogger

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext, Page


async def safe_close_page(page: Optional[Page], log: LogfmtLogger) -> None:
    """安全关闭页面，带超时保护。"""
    if page is None or page.is_closed():
        return
    try:
        await asyncio.wait_for(page.close(), timeout=PAGE_CLOSE_TIMEOUT)
    except asyncio.TimeoutError:
        log.warning("page_close_timeout", timeout=PAGE_CLOSE_TIMEOUT)
    except Exception as e:
        log.debug("page_close_error", error=e)


class PagePool:
    """管理一个浏览器上下文中的页面生命周期。

    页面用完即销毁，不复用。BrowserContext 级槽位复用已捕获全部有意义的资源复用，
    页面级回收的清理成本（unroute + clear_cookies + localStorage + sessionStorage
    + about:blank）超过新建成本（单次 CDP Target.createTarget）。
    """

    def __init__(self, context: BrowserContext) -> None:
        self.context = context
        self._closed = False
        self._log = LogfmtLogger("astrocrawl.pagepool")

    async def acquire(self) -> Page:
        """创建新页面（带重试）。"""
        last_error = None
        for attempt in range(PAGE_CREATE_RETRIES):
            if self._closed:
                raise RuntimeError("PagePool 已关闭")
            try:
                page = await asyncio.wait_for(self.context.new_page(), timeout=PAGE_CREATE_TIMEOUT)
                return page
            except Exception as e:
                last_error = e
                self._log.warning("page_create_failed", attempt=attempt + 1, max_attempts=PAGE_CREATE_RETRIES, error=e)
                if attempt < PAGE_CREATE_RETRIES - 1:
                    await asyncio.sleep(
                        random.uniform(0, PAGE_CREATE_BACKOFF * (attempt + 1)),
                    )
        self._log.error("page_create_exhausted", attempts=PAGE_CREATE_RETRIES, error=last_error)
        raise last_error or RuntimeError("创建页面失败")

    async def remove_broken(self, page: Page) -> None:
        """立即关闭损坏页面，不回收。先导航到 about:blank 清理待处理操作。"""
        if page is not None and not page.is_closed():
            try:
                await asyncio.wait_for(
                    page.goto("about:blank", timeout=3000, wait_until="commit"),
                    timeout=ABOUT_BLANK_ASYNCIO_TIMEOUT,
                )
            except Exception:
                pass
            await safe_close_page(page, self._log)

    async def close_all(self) -> None:
        """标记池为已关闭，阻止新页面创建。"""
        self._closed = True

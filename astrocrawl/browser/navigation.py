"""Playwright I/O 超时安全包装器。

page.goto() 是代码库中唯一缺少 asyncio.wait_for 的网络 I/O 操作。
所有其他 Playwright 操作 (new_context, new_page, close) 均有超时保护。
此模块提供一致的双层超时包装。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from playwright.async_api import Page, Response


async def safe_goto(
    page: "Page",
    url: str,
    playwright_timeout_ms: int,
    asyncio_timeout_s: float,
    wait_until: str = "domcontentloaded",
) -> Optional["Response"]:
    """双层超时保护的 page.goto 包装。

    内层：Playwright CDP 级超时 (playwright_timeout_ms)
    外层：Python asyncio 级超时 (asyncio_timeout_s)

    外层作为兜底——当 Playwright 超时因 TCP SYN 挂起而不触发时，
    asyncio.wait_for 强制在 deadline 抛出 TimeoutError。

    推荐 asyncio_timeout_s = playwright_timeout_ms / 1000 + 5.0
    5s 缓冲覆盖 CDP 导航启动延迟。
    """
    return await asyncio.wait_for(
        page.goto(url, timeout=playwright_timeout_ms, wait_until=wait_until),  # type: ignore[arg-type]
        timeout=asyncio_timeout_s,
    )

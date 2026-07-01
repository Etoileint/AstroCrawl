from __future__ import annotations

import asyncio
from typing import Optional

from PySide6.QtCore import QThread

from astrocrawl._constants import SHUTDOWN_ASYNCGEN_TIMEOUT, SHUTDOWN_EXECUTOR_TIMEOUT, SHUTDOWN_PENDING_TIMEOUT
from astrocrawl.crawler.engine import AsyncCrawler


class CrawlerThread(QThread):
    def __init__(self, crawler: AsyncCrawler):
        super().__init__()
        self._crawler = crawler
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    @property
    def crawler(self) -> AsyncCrawler:
        """公开访问爬虫实例（供 GUI 健康条等组件读取）。"""
        return self._crawler

    def run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._crawler.run())
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            if self._crawler.signals:
                self._crawler.signals.error.emit(self.tr("Event loop exception: {0}").format(exc))
        finally:
            # 安全关闭事件循环：取消所有残留 task（排除自身，否则 cancel 自己
            # 然后 wait 等自己 → 循环等待 → 100% 耗尽 SHUTDOWN_PENDING_TIMEOUT 秒）。
            # 第一层 _cleanup_all() 已正确关闭浏览器/HTTP/DB，此处仅处理第三方库
            # （Playwright/aiohttp）内部创建的 task 和 async generator。
            try:
                current = asyncio.current_task(loop)
                pending = {t for t in asyncio.all_tasks(loop) if t is not current}
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.wait(pending, timeout=SHUTDOWN_PENDING_TIMEOUT))
            except RuntimeError:
                pass
            except Exception:
                pass
            try:
                loop.run_until_complete(
                    asyncio.wait_for(loop.shutdown_default_executor(), timeout=SHUTDOWN_EXECUTOR_TIMEOUT)
                )
            except (RuntimeError, asyncio.TimeoutError):
                pass
            except Exception:
                pass
            try:
                loop.run_until_complete(asyncio.wait_for(loop.shutdown_asyncgens(), timeout=SHUTDOWN_ASYNCGEN_TIMEOUT))
            except (RuntimeError, asyncio.TimeoutError):
                pass
            except Exception:
                pass
            try:
                loop.close()
            except Exception:
                pass
            self._loop = None

    def stop(self) -> None:
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._crawler.request_stop)

    def pause(self) -> None:
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._crawler.request_pause)

    def resume(self) -> None:
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._crawler.request_resume)

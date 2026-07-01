from __future__ import annotations

import asyncio
import logging
import warnings
from typing import Any, Optional

from PySide6.QtCore import QObject, QThread, Signal

from astrocrawl._constants import SHUTDOWN_ASYNCGEN_TIMEOUT, SHUTDOWN_EXECUTOR_TIMEOUT, SHUTDOWN_PENDING_TIMEOUT
from astrocrawl.browser._preview import PreviewBrowser, PreviewPageHandle, PreviewParams

logger = logging.getLogger("astrocrawl.gui.preview")

_SESSION_SIGNALS = (
    "page_opened",
    "page_closed",
    "highlight_injected",
    "error_occurred",
    "disposed",
)


class PreviewThread(QThread):
    _page_closed = Signal(int)  # worker→session 转发，对标 CrawlerSignals

    def __init__(self, *, theme_mode: str = "light", proxy: Any = None):
        super().__init__()
        self._theme_mode = theme_mode
        self._proxy = proxy
        self._browser: Optional[PreviewBrowser] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._browser = PreviewBrowser(theme_mode=self._theme_mode, proxy=self._proxy)
        self._browser.set_page_closed_callback(lambda page_id: self._page_closed.emit(page_id))
        try:
            loop.run_until_complete(self._browser.run())
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("event=preview_browser_crashed error=%s", exc)
        finally:
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
        if self._browser is None:
            return
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._browser.request_stop)

    def call_async(self, coro: Any) -> Any:
        if not self._loop or not self._loop.is_running():
            raise RuntimeError("event loop is not running")
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def open_page(self, url: str, params: PreviewParams, *, rule_name: str = "") -> Any:
        if self._browser is None:
            raise RuntimeError("browser is not ready")
        return self.call_async(self._browser.open_page(url, params, rule_name=rule_name))

    def close_page_by_id(self, page_id: int) -> Any:
        if self._browser is None:
            raise RuntimeError("browser is not ready")
        return self.call_async(self._browser.close_page_by_id(page_id))

    def activate_page(self, handle: PreviewPageHandle) -> Any:
        if self._browser is None:
            raise RuntimeError("browser is not ready")
        return self.call_async(self._browser.activate_page(handle))

    def update_theme(self, theme_mode: str, theme_tokens: dict) -> Any:
        if self._browser is None:
            raise RuntimeError("browser is not ready")
        return self.call_async(self._browser.update_theme(theme_mode, theme_tokens))


class PreviewSession(QObject):
    """MVP Presenter：GUI 通过此外观与 PreviewBrowser 交互。"""

    page_opened = Signal(object)  # PreviewPageHandle
    page_closed = Signal(int)  # page_id
    highlight_injected = Signal(object)  # PreviewResult
    error_occurred = Signal(str)
    disposed = Signal()

    def __init__(self, parent: Optional[QObject] = None, *, theme_mode: str = "light", proxy: Any = None):
        super().__init__(parent)
        self._theme_mode = theme_mode
        self._proxy = proxy
        self._thread: Optional[PreviewThread] = None
        self._disposed = False

    def start(self) -> None:
        self._thread = PreviewThread(theme_mode=self._theme_mode, proxy=self._proxy)
        self._thread._page_closed.connect(self.page_closed)  # worker→session 转发
        self._thread.finished.connect(self._on_thread_finished)
        self._thread.start()

    def open_page(self, url: str, params: PreviewParams, *, rule_name: str = "") -> None:
        if self._thread is None:
            self.error_occurred.emit(self.tr("PreviewSession not started"))
            return
        try:
            future = self._thread.open_page(url, params, rule_name=rule_name)
        except RuntimeError:
            self.error_occurred.emit(self.tr("Browser is not ready"))
            return
        try:
            handle, result = future.result(timeout=30)
        except Exception as exc:
            logger.warning("event=preview_open_failed url=%s rule=%s error=%s", url, rule_name, exc)
            self.error_occurred.emit(self.tr("Failed to open page: {0}").format(exc))
            return
        self.page_opened.emit(handle)
        self.highlight_injected.emit(result)

    def close_page(self, page_id: int) -> None:
        if self._thread is None:
            return
        try:
            future = self._thread.close_page_by_id(page_id)
            future.result(timeout=5)
        except Exception:
            pass
        self.page_closed.emit(page_id)

    def activate_page(self, page_id: int) -> None:
        if self._thread is None:
            return
        handle = PreviewPageHandle(page_id=page_id, url="", rule_name="")
        try:
            future = self._thread.activate_page(handle)
            future.result(timeout=5)
        except Exception:
            pass

    def update_theme(self, theme_mode: str, theme_tokens: dict) -> None:
        if self._thread is None:
            return
        try:
            future = self._thread.update_theme(theme_mode, theme_tokens)
            future.result(timeout=5)
        except Exception:
            pass

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        # Layer 1: 断开 worker→session 转发
        if self._thread is not None:
            try:
                self._thread._page_closed.disconnect()
            except RuntimeError:
                pass
            try:
                self._thread.finished.disconnect()
            except RuntimeError:
                pass
        # Layer 2: 断开所有外部连接
        for name in _SESSION_SIGNALS:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                try:
                    getattr(self, name).disconnect()
                except RuntimeError:
                    pass
        if self._thread is not None:
            self._thread.stop()
            self._thread.wait(5000)
            self._thread = None
        self.deleteLater()

    def _on_thread_finished(self) -> None:
        self.disposed.emit()
        self.dispose()

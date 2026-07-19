from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PySide6.QtCore import QEventLoop, QTimer

from astrocrawl.browser._preview import PreviewParams
from astrocrawl.gui._preview_session import _SESSION_SIGNALS, PreviewSession, PreviewThread


class TestSessionSignals:
    def test_all_signals_in_tuple(self):
        assert "page_opened" in _SESSION_SIGNALS
        assert "page_closed" in _SESSION_SIGNALS
        assert "highlight_injected" in _SESSION_SIGNALS
        assert "error_occurred" in _SESSION_SIGNALS
        assert "disposed" in _SESSION_SIGNALS

    def test_signal_count(self):
        assert len(_SESSION_SIGNALS) == 5


class TestPreviewSessionInit:
    def test_creates_browser(self):
        session = PreviewSession()
        assert session._theme_mode == "light"
        assert session._proxy is None
        assert session._thread is None
        assert session._disposed is False

    def test_signals_exist(self):
        session = PreviewSession()
        assert hasattr(session, "page_opened")
        assert hasattr(session, "page_closed")
        assert hasattr(session, "highlight_injected")
        assert hasattr(session, "error_occurred")
        assert hasattr(session, "disposed")


class TestPreviewSessionOpenPage:
    @pytest.mark.gui
    def test_open_page_not_started_emits_error(self, qapp):
        session = PreviewSession()
        errors = []
        session.error_occurred.connect(errors.append)
        session.open_page("https://example.com", PreviewParams(fields=[]))
        assert len(errors) == 1
        assert "PreviewSession not started" in errors[0]

    @pytest.mark.gui
    def test_open_page_emits_signals(self, qapp):
        session = PreviewSession()

        mock_page = MagicMock()
        mock_page.goto = AsyncMock()
        mock_page.emulate_media = AsyncMock()
        mock_page.close = AsyncMock()
        mock_page.evaluate = AsyncMock(
            return_value={
                "total": 3,
                "matched": 2,
                "unmatched": 1,
                "fallback_activated": False,
                "main_active": 2,
                "fallback_count": 0,
            }
        )

        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_playwright = MagicMock()
        mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_context.new_page = AsyncMock(return_value=mock_page)

        with patch("playwright.async_api.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)
            mock_ap.return_value.stop = AsyncMock()
            # _script loaded from real file; Playwright is mocked so evaluate is no-op

            opened_handles = []
            results = []
            session.page_opened.connect(opened_handles.append)
            session.highlight_injected.connect(results.append)
            session.start()

            def do_open():
                session.open_page("https://example.com", PreviewParams(fields=[]), rule_name="test")
                session._thread._browser.request_stop()

            QTimer.singleShot(50, do_open)
            # pump event loop
            loop = QEventLoop()
            QTimer.singleShot(5000, loop.quit)
            session._thread.finished.connect(loop.quit)
            loop.exec()

        assert len(opened_handles) == 1
        assert opened_handles[0].page_id == 0
        assert len(results) == 1
        assert results[0].total == 3
        assert results[0].matched == 2

    @pytest.mark.gui
    def test_open_page_error_emits_error_occurred(self, qapp):
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError

        session = PreviewSession()

        mock_page = MagicMock()
        mock_page.goto = AsyncMock(side_effect=PlaywrightTimeoutError("timeout"))
        mock_page.close = AsyncMock()

        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_playwright = MagicMock()
        mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_context.new_page = AsyncMock(return_value=mock_page)

        with patch("playwright.async_api.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)
            mock_ap.return_value.stop = AsyncMock()
            # _script loaded from real file; Playwright is mocked so evaluate is no-op

            errors = []
            session.error_occurred.connect(errors.append)
            session.start()

            def do_open():
                session.open_page("https://fail.com", PreviewParams(fields=[]))
                session._thread._browser.request_stop()

            QTimer.singleShot(50, do_open)
            loop = QEventLoop()
            QTimer.singleShot(5000, loop.quit)
            session._thread.finished.connect(loop.quit)
            loop.exec()

        assert len(errors) == 1
        assert "Failed to open page" in errors[0]


class TestPreviewSessionClosePage:
    def test_close_page_not_started(self):
        session = PreviewSession()
        session.close_page(0)

    @pytest.mark.gui
    def test_close_page_emits_signal(self, qapp):
        session = PreviewSession()

        mock_page = MagicMock()
        mock_page.goto = AsyncMock()
        mock_page.close = AsyncMock()
        mock_page.evaluate = AsyncMock(
            return_value={
                "total": 0,
                "matched": 0,
                "unmatched": 0,
                "fallback_activated": False,
                "main_active": 0,
                "fallback_count": 0,
            }
        )

        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_playwright = MagicMock()
        mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_context.new_page = AsyncMock(return_value=mock_page)

        with patch("playwright.async_api.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)
            mock_ap.return_value.stop = AsyncMock()
            # _script loaded from real file; Playwright is mocked so evaluate is no-op

            closed_ids = []
            session.page_closed.connect(closed_ids.append)
            session.start()

            def do_test():
                session.open_page("https://example.com", PreviewParams(fields=[]))
                session.close_page(0)
                session._thread._browser.request_stop()

            QTimer.singleShot(50, do_test)
            loop = QEventLoop()
            QTimer.singleShot(5000, loop.quit)
            session._thread.finished.connect(loop.quit)
            loop.exec()

        assert len(closed_ids) == 1
        assert closed_ids[0] == 0


class TestPreviewSessionDispose:
    @pytest.mark.gui
    def test_dispose_idempotent(self, qapp):
        session = PreviewSession()
        session.dispose()
        session.dispose()
        assert session._thread is None

    @pytest.mark.gui
    def test_dispose_does_not_emit_disposed_directly(self, qapp):
        """disposed 信号仅由 _on_thread_finished 发射，dispose() 直接调用不发射。"""
        session = PreviewSession()
        disposed = []
        session.disposed.connect(lambda d=disposed: d.append(True))
        session.dispose()
        assert len(disposed) == 0

    @pytest.mark.gui
    def test_dispose_stops_thread(self, qapp):
        session = PreviewSession()

        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_playwright = MagicMock()
        mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)

        with patch("playwright.async_api.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)
            mock_ap.return_value.stop = AsyncMock()
            # _script loaded from real file; Playwright is mocked so evaluate is no-op
            session.start()
            assert session._thread is not None
            # Wait for _browser to be created in worker thread
            for _ in range(50):
                if session._thread._browser is not None:
                    break
                import time

                time.sleep(0.02)
            assert session._thread._browser is not None, "浏览器未在 1 秒内初始化"
            session._thread._browser.request_stop()

            loop = QEventLoop()
            QTimer.singleShot(5000, loop.quit)
            session._thread.finished.connect(loop.quit)
            loop.exec()

            # _on_thread_finished already called dispose() which calls deleteLater()
        assert session._thread is None

    @pytest.mark.gui
    def test_on_thread_finished_emits_disposed(self, qapp):
        session = PreviewSession()
        disposed = []
        session.disposed.connect(lambda d=disposed: d.append(True))
        session._on_thread_finished()
        assert len(disposed) == 1

    @pytest.mark.gui
    def test_dispose_clears_thread(self, qapp):
        session = PreviewSession()

        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_playwright = MagicMock()
        mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)

        with patch("playwright.async_api.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)
            mock_ap.return_value.stop = AsyncMock()
            # _script loaded from real file; Playwright is mocked so evaluate is no-op
            session.start()

            def request_stop():
                for _ in range(50):
                    if session._thread._browser is not None:
                        break
                    import time

                    time.sleep(0.02)
                session._thread._browser.request_stop()

            QTimer.singleShot(50, request_stop)
            loop = QEventLoop()
            QTimer.singleShot(5000, loop.quit)
            session._thread.finished.connect(loop.quit)
            loop.exec()

        session.dispose()
        assert session._thread is None


class TestPreviewSessionUpdateTheme:
    def test_update_theme_not_started_noop(self):
        session = PreviewSession()
        session.update_theme("dark", {})

    @pytest.mark.gui
    def test_update_theme_calls_browser(self, qapp):
        session = PreviewSession()

        mock_page = MagicMock()
        mock_page.goto = AsyncMock()
        mock_page.close = AsyncMock()
        mock_page.emulate_media = AsyncMock()
        mock_page.evaluate = AsyncMock(
            return_value={
                "total": 0,
                "matched": 0,
                "unmatched": 0,
                "fallback_activated": False,
                "main_active": 0,
                "fallback_count": 0,
            }
        )

        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_playwright = MagicMock()
        mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_context.new_page = AsyncMock(return_value=mock_page)

        with patch("playwright.async_api.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)
            mock_ap.return_value.stop = AsyncMock()
            # _script loaded from real file; Playwright is mocked so evaluate is no-op
            session.start()

            def do_test():
                session.open_page("https://example.com", PreviewParams(fields=[]))
                session.update_theme("dark", {"window_bg": "#1E1E2E"})
                session._thread._browser.request_stop()

            QTimer.singleShot(50, do_test)
            loop = QEventLoop()
            QTimer.singleShot(5000, loop.quit)
            session._thread.finished.connect(loop.quit)
            loop.exec()

        mock_page.emulate_media.assert_any_call(color_scheme="dark")


class TestPreviewThreadCallAsync:
    def test_call_async_not_running(self):
        thread = PreviewThread()
        with pytest.raises(RuntimeError, match="event loop is not running"):
            thread.call_async(asyncio.sleep(0))


class TestPreviewSessionActivatePage:
    def test_activate_page_not_started(self):
        session = PreviewSession()
        session.activate_page(0)

    @pytest.mark.gui
    def test_activate_page_calls_browser(self, qapp):
        session = PreviewSession()

        mock_page = MagicMock()
        mock_page.goto = AsyncMock()
        mock_page.emulate_media = AsyncMock()
        mock_page.bring_to_front = AsyncMock()
        mock_page.close = AsyncMock()
        mock_page.evaluate = AsyncMock(
            return_value={
                "total": 1,
                "matched": 1,
                "unmatched": 0,
                "fallback_activated": False,
                "main_active": 1,
                "fallback_count": 0,
            }
        )

        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_playwright = MagicMock()
        mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_context.new_page = AsyncMock(return_value=mock_page)

        with patch("playwright.async_api.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)
            mock_ap.return_value.stop = AsyncMock()
            # _script loaded from real file; Playwright is mocked so evaluate is no-op
            session.start()

            def do_test():
                session.open_page("https://example.com", PreviewParams(fields=[]))
                session.activate_page(0)
                session._thread._browser.request_stop()

            QTimer.singleShot(50, do_test)
            loop = QEventLoop()
            QTimer.singleShot(5000, loop.quit)
            session._thread.finished.connect(loop.quit)
            loop.exec()

        mock_page.bring_to_front.assert_called_once()


class TestPreviewSessionProxy:
    def test_accepts_proxy(self):
        from astrocrawl.proxy._config import ParsedProxy, ProxyAuth, ProxyType

        proxy = ParsedProxy(type=ProxyType.HTTP, host="127.0.0.1", port=8080, auth=ProxyAuth())
        session = PreviewSession(proxy=proxy)
        assert session._proxy is not None
        assert session._proxy.host == "127.0.0.1"

    def test_no_proxy_default(self):
        session = PreviewSession()
        assert session._proxy is None

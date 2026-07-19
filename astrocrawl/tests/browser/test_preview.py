from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from astrocrawl.browser._preview import (
    PreviewBrowser,
    PreviewFieldParams,
    PreviewPageHandle,
    PreviewParams,
    PreviewResult,
    _load_inject_script,
    assign_field_colors,
)


class TestAssignFieldColors:
    def test_assigns_colors_cyclically(self):
        fields = [{"name": "a"}, {"name": "b"}, {"name": "c"}]
        result = assign_field_colors(fields)
        assert result[0]["color"] == "#FF6B6B"
        assert result[1]["color"] == "#4ECDC4"
        assert result[2]["color"] == "#45B7D1"

    def test_wraps_around_10_colors(self):
        fields = [{"name": f"f{i}"} for i in range(12)]
        result = assign_field_colors(fields)
        assert result[0]["color"] == "#FF6B6B"
        assert result[10]["color"] == "#FF6B6B"
        assert result[11]["color"] == "#4ECDC4"

    def test_empty_list(self):
        assert assign_field_colors([]) == []

    def test_preserves_original_keys(self):
        fields = [{"name": "x", "selector": "div", "extra": "keep"}]
        result = assign_field_colors(fields)
        assert result[0]["name"] == "x"
        assert result[0]["selector"] == "div"
        assert result[0]["extra"] == "keep"
        assert "color" in result[0]


class TestParamsToDict:
    def test_converts_params_to_dict(self):
        params = PreviewParams(
            fields=[
                PreviewFieldParams(name="title", selector="h1", color="#FF6B6B"),
                PreviewFieldParams(
                    name="price",
                    selector=".price",
                    extract="text",
                    multiple=True,
                    color="#4ECDC4",
                    fallback=[{"selector": ".fallback-price", "extract": "text"}],
                ),
            ],
            rule_name="test_rule",
        )
        result = PreviewBrowser._params_to_dict(params)
        assert result["rule_name"] == "test_rule"
        assert len(result["fields"]) == 2
        assert result["fields"][0]["name"] == "title"
        assert result["fields"][0]["selector"] == "h1"
        assert result["fields"][0]["color"] == "#FF6B6B"
        assert result["fields"][0]["multiple"] is False
        assert result["fields"][1]["fallback"] == [{"selector": ".fallback-price", "extract": "text"}]
        assert "theme" in result
        assert result["theme"]["mode"] == "light"
        assert isinstance(result["theme"]["tokens"], dict)

    def test_empty_fields(self):
        params = PreviewParams(fields=[], rule_name="empty")
        result = PreviewBrowser._params_to_dict(params)
        assert result["fields"] == []
        assert result["rule_name"] == "empty"
        assert "theme" in result

    def test_params_to_dict_includes_theme_data(self):
        params = PreviewParams(
            fields=[],
            rule_name="test",
            theme_mode="dark",
            theme_tokens={"window_bg": "#1E1E2E", "accent": "#89B4FA"},
        )
        result = PreviewBrowser._params_to_dict(params)
        assert result["theme"]["mode"] == "dark"
        assert result["theme"]["tokens"]["window_bg"] == "#1E1E2E"


class TestLoadInjectScript:
    def test_loads_script_from_file(self):
        import astrocrawl.browser._preview as mod

        mod._INJECT_SCRIPT = None
        script = _load_inject_script()
        assert "window.__astrocrawl_preview" in script
        assert "window.__astrocrawl_destroy" in script
        assert "window.__astrocrawl_update_theme" in script
        assert "HighlightEngine" in script
        assert "LabelManager" in script
        assert "SceneObserver" in script
        assert len(script) > 0

    def test_caches_script(self):
        import astrocrawl.browser._preview as mod

        mod._INJECT_SCRIPT = None
        s1 = _load_inject_script()
        s2 = _load_inject_script()
        assert s1 is s2


class TestPreviewResult:
    def test_default_values(self):
        r = PreviewResult()
        assert r.total == 0
        assert r.matched == 0
        assert r.unmatched == 0
        assert r.fallback_activated is False

    def test_full_result(self):
        r = PreviewResult(total=5, matched=3, unmatched=2, fallback_activated=True, main_active=2, fallback_count=1)
        assert r.total == 5
        assert r.matched == 3
        assert r.unmatched == 2
        assert r.fallback_activated is True
        assert r.main_active == 2
        assert r.fallback_count == 1


class TestPreviewPageHandle:
    def test_handle_fields(self):
        h = PreviewPageHandle(page_id=7, url="https://example.com", rule_name="my_rule")
        assert h.page_id == 7
        assert h.url == "https://example.com"
        assert h.rule_name == "my_rule"

    def test_handle_equality(self):
        h1 = PreviewPageHandle(page_id=1, url="https://a.com", rule_name="r")
        h2 = PreviewPageHandle(page_id=1, url="https://a.com", rule_name="r")
        assert h1 == h2


class TestPreviewBrowserLifecycle:
    @pytest.mark.asyncio
    async def test_run_starts_and_stops(self):
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_playwright = MagicMock()
        mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)

        browser = PreviewBrowser()
        browser._script = "/* test */"

        with patch("playwright.async_api.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)
            mock_ap.return_value.stop = AsyncMock()

            async def set_stop():
                await asyncio.sleep(0.01)
                browser.request_stop()

            task = asyncio.create_task(browser.run())
            await asyncio.sleep(0.005)
            await set_stop()
            await asyncio.wait_for(task, timeout=5)

        assert browser._ready is False

    @pytest.mark.asyncio
    async def test_run_ready_flag(self):
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_playwright = MagicMock()
        mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)

        browser = PreviewBrowser()
        browser._script = "/* test */"

        with patch("playwright.async_api.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)
            mock_ap.return_value.stop = AsyncMock()

            async def wait_ready_and_stop():
                while not browser._ready:
                    await asyncio.sleep(0.001)
                browser.request_stop()

            task = asyncio.create_task(browser.run())
            await wait_ready_and_stop()
            await asyncio.wait_for(task, timeout=5)

        assert browser._ready is False

    @pytest.mark.asyncio
    async def test_open_page_not_ready_raises(self):
        browser = PreviewBrowser()
        params = PreviewParams(fields=[])
        with pytest.raises(RuntimeError, match="未就绪"):
            await browser.open_page("https://example.com", params)

    @pytest.mark.asyncio
    async def test_open_page_creates_handle_and_result(self):
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_page = MagicMock()
        mock_page.emulate_media = AsyncMock()
        mock_playwright = MagicMock()
        mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_page.goto = AsyncMock()
        mock_page.close = AsyncMock()
        mock_page.evaluate = AsyncMock(
            return_value={
                "total": 3,
                "matched": 2,
                "unmatched": 1,
                "fallback_activated": True,
                "main_active": 1,
                "fallback_count": 1,
            }
        )

        browser = PreviewBrowser()
        browser._script = "/* test */"

        with patch("playwright.async_api.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)
            mock_ap.return_value.stop = AsyncMock()

            async def wait_ready_and_test():
                while not browser._ready:
                    await asyncio.sleep(0.001)
                params = PreviewParams(
                    fields=[PreviewFieldParams(name="title", selector="h1", color="#FF6B6B")],
                    rule_name="test",
                )
                handle, result = await browser.open_page("https://example.com", params, rule_name="test")
                browser.request_stop()
                return handle, result

            task = asyncio.create_task(browser.run())
            handle, result = await wait_ready_and_test()
            await asyncio.wait_for(task, timeout=5)

        assert handle.page_id == 0
        assert handle.url == "https://example.com"
        assert result.total == 3
        assert result.matched == 2
        assert result.fallback_activated is True

    @pytest.mark.asyncio
    async def test_open_page_goto_error_cleans_up(self):
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError

        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_page = MagicMock()
        mock_page.emulate_media = AsyncMock()
        mock_playwright = MagicMock()
        mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_page.goto = AsyncMock(side_effect=PlaywrightTimeoutError("timeout"))
        mock_page.close = AsyncMock()

        browser = PreviewBrowser()
        browser._script = "/* test */"

        with patch("playwright.async_api.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)
            mock_ap.return_value.stop = AsyncMock()

            async def wait_ready_and_test():
                while not browser._ready:
                    await asyncio.sleep(0.001)
                params = PreviewParams(fields=[])
                try:
                    await browser.open_page("https://fail.com", params)
                except PlaywrightTimeoutError:
                    pass
                browser.request_stop()

            task = asyncio.create_task(browser.run())
            await wait_ready_and_test()
            await asyncio.wait_for(task, timeout=5)

        mock_page.close.assert_called()
        assert len(browser._pages) == 0

    @pytest.mark.asyncio
    async def test_close_page_removes_from_pages(self):
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_page = MagicMock()
        mock_page.emulate_media = AsyncMock()
        mock_playwright = MagicMock()
        mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_context.new_page = AsyncMock(return_value=mock_page)
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

        browser = PreviewBrowser()
        browser._script = "/* test */"

        with patch("playwright.async_api.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)
            mock_ap.return_value.stop = AsyncMock()

            async def wait_ready_and_test():
                while not browser._ready:
                    await asyncio.sleep(0.001)
                params = PreviewParams(fields=[])
                handle, _ = await browser.open_page("https://example.com", params)
                assert handle.page_id in browser._pages
                await browser.close_page(handle)
                assert handle.page_id not in browser._pages
                browser.request_stop()

            task = asyncio.create_task(browser.run())
            await wait_ready_and_test()
            await asyncio.wait_for(task, timeout=5)

    @pytest.mark.asyncio
    async def test_close_page_nonexistent_id_noop(self):
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_playwright = MagicMock()
        mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)

        browser = PreviewBrowser()
        browser._script = "/* test */"

        with patch("playwright.async_api.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)
            mock_ap.return_value.stop = AsyncMock()

            async def wait_ready_and_test():
                while not browser._ready:
                    await asyncio.sleep(0.001)
                handle = PreviewPageHandle(page_id=999, url="", rule_name="")
                await browser.close_page(handle)
                browser.request_stop()

            task = asyncio.create_task(browser.run())
            await wait_ready_and_test()
            await asyncio.wait_for(task, timeout=5)

    @pytest.mark.asyncio
    async def test_close_page_idempotent(self):
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_page = MagicMock()
        mock_page.emulate_media = AsyncMock()
        mock_playwright = MagicMock()
        mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_context.new_page = AsyncMock(return_value=mock_page)
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

        browser = PreviewBrowser()
        browser._script = "/* test */"

        with patch("playwright.async_api.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)
            mock_ap.return_value.stop = AsyncMock()

            async def wait_ready_and_test():
                while not browser._ready:
                    await asyncio.sleep(0.001)
                params = PreviewParams(fields=[])
                handle, _ = await browser.open_page("https://example.com", params)
                await browser.close_page(handle)
                await browser.close_page(handle)
                assert handle.page_id not in browser._pages
                browser.request_stop()

            task = asyncio.create_task(browser.run())
            await wait_ready_and_test()
            await asyncio.wait_for(task, timeout=5)

        assert mock_page.close.call_count == 1

    @pytest.mark.asyncio
    async def test_activate_page_raises_for_unknown(self):
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_playwright = MagicMock()
        mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)

        browser = PreviewBrowser()
        browser._script = "/* test */"

        with patch("playwright.async_api.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)
            mock_ap.return_value.stop = AsyncMock()

            async def wait_ready_and_test():
                while not browser._ready:
                    await asyncio.sleep(0.001)
                handle = PreviewPageHandle(page_id=999, url="", rule_name="")
                with pytest.raises(ValueError, match="不存在"):
                    await browser.activate_page(handle)
                browser.request_stop()

            task = asyncio.create_task(browser.run())
            await wait_ready_and_test()
            await asyncio.wait_for(task, timeout=5)

    @pytest.mark.asyncio
    async def test_active_pages_reflects_current_state(self):
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_page = MagicMock()
        mock_page.emulate_media = AsyncMock()
        mock_playwright = MagicMock()
        mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_context.new_page = AsyncMock(return_value=mock_page)
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

        browser = PreviewBrowser()
        browser._script = "/* test */"

        with patch("playwright.async_api.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)
            mock_ap.return_value.stop = AsyncMock()

            async def wait_ready_and_test():
                while not browser._ready:
                    await asyncio.sleep(0.001)
                params = PreviewParams(fields=[])
                h1, _ = await browser.open_page("https://a.com", params)
                h2, _ = await browser.open_page("https://b.com", params)
                pages = browser.active_pages
                page_ids = [p.page_id for p in pages]
                assert h1.page_id in page_ids
                assert h2.page_id in page_ids
                await browser.close_page(h1)
                pages = browser.active_pages
                page_ids = [p.page_id for p in pages]
                assert h1.page_id not in page_ids
                assert h2.page_id in page_ids
                browser.request_stop()

            task = asyncio.create_task(browser.run())
            await wait_ready_and_test()
            await asyncio.wait_for(task, timeout=5)

    @pytest.mark.asyncio
    async def test_browser_disconnected_stops_run(self):
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_playwright = MagicMock()
        mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        disconnected_callbacks = []

        mock_browser.on = MagicMock(side_effect=lambda event, cb: disconnected_callbacks.append(cb))

        browser = PreviewBrowser()
        browser._script = "/* test */"

        with patch("playwright.async_api.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)
            mock_ap.return_value.stop = AsyncMock()

            async def fire_disconnected():
                while not browser._ready:
                    await asyncio.sleep(0.001)
                for cb in disconnected_callbacks:
                    cb()

            task = asyncio.create_task(browser.run())
            await fire_disconnected()
            await asyncio.wait_for(task, timeout=5)

        assert browser._ready is False

    @pytest.mark.asyncio
    async def test_cleanup_on_error_during_run(self):
        mock_playwright = MagicMock()
        mock_playwright.chromium.launch = AsyncMock(side_effect=RuntimeError("chromium crash"))

        browser = PreviewBrowser()
        browser._script = "/* test */"

        with patch("playwright.async_api.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)
            mock_ap.return_value.stop = AsyncMock()

            with pytest.raises(RuntimeError, match="chromium crash"):
                await browser.run()

        assert browser._ready is False

    @pytest.mark.asyncio
    async def test_launch_with_preview_chromium_args(self):
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_playwright = MagicMock()
        mock_launch = AsyncMock(return_value=mock_browser)
        mock_playwright.chromium.launch = mock_launch
        mock_browser.new_context = AsyncMock(return_value=mock_context)

        browser = PreviewBrowser()
        browser._script = "/* test */"

        with patch("playwright.async_api.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)
            mock_ap.return_value.stop = AsyncMock()

            async def launch_and_stop():
                while not browser._ready:
                    await asyncio.sleep(0.001)
                browser.request_stop()

            task = asyncio.create_task(browser.run())
            await launch_and_stop()
            await asyncio.wait_for(task, timeout=5)

        call_args = mock_launch.call_args
        assert call_args.kwargs["headless"] is False
        launch_args = call_args.kwargs["args"]
        assert "--disable-blink-features=AutomationControlled" in launch_args
        assert "--disable-dev-shm-usage" in launch_args
        assert "--no-sandbox" in launch_args
        assert "--log-level=3" in launch_args
        assert "--force-dark-mode" not in launch_args
        assert "--disable-gpu" not in launch_args

    @pytest.mark.asyncio
    async def test_launch_with_force_dark_mode(self):
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_playwright = MagicMock()
        mock_launch = AsyncMock(return_value=mock_browser)
        mock_playwright.chromium.launch = mock_launch
        mock_browser.new_context = AsyncMock(return_value=mock_context)

        browser = PreviewBrowser(theme_mode="dark")
        browser._script = "/* test */"

        with patch("playwright.async_api.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)
            mock_ap.return_value.stop = AsyncMock()

            async def launch_and_stop():
                while not browser._ready:
                    await asyncio.sleep(0.001)
                browser.request_stop()

            task = asyncio.create_task(browser.run())
            await launch_and_stop()
            await asyncio.wait_for(task, timeout=5)

        launch_args = mock_launch.call_args.kwargs["args"]
        assert "--force-dark-mode" in launch_args

    @pytest.mark.asyncio
    async def test_context_created_with_no_viewport(self):
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_playwright = MagicMock()
        mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_new_context = AsyncMock(return_value=mock_context)
        mock_browser.new_context = mock_new_context

        browser = PreviewBrowser()
        browser._script = "/* test */"

        with patch("playwright.async_api.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)
            mock_ap.return_value.stop = AsyncMock()

            async def launch_and_stop():
                while not browser._ready:
                    await asyncio.sleep(0.001)
                browser.request_stop()

            task = asyncio.create_task(browser.run())
            await launch_and_stop()
            await asyncio.wait_for(task, timeout=5)

        mock_new_context.assert_called_once_with(no_viewport=True)

    @pytest.mark.asyncio
    async def test_emulate_media_called_on_open_page(self):
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_page = MagicMock()
        mock_page.emulate_media = AsyncMock()
        mock_playwright = MagicMock()
        mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_page.goto = AsyncMock()
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

        browser = PreviewBrowser()
        browser._script = "/* test */"

        with patch("playwright.async_api.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)
            mock_ap.return_value.stop = AsyncMock()

            async def wait_ready_and_test():
                while not browser._ready:
                    await asyncio.sleep(0.001)
                params = PreviewParams(fields=[PreviewFieldParams(name="t", selector="h1")])
                await browser.open_page("https://example.com", params)
                browser.request_stop()

            task = asyncio.create_task(browser.run())
            await wait_ready_and_test()
            await asyncio.wait_for(task, timeout=5)

        mock_page.emulate_media.assert_called_once_with(color_scheme="light")

    @pytest.mark.asyncio
    async def test_emulate_media_dark_mode(self):
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_page = MagicMock()
        mock_page.emulate_media = AsyncMock()
        mock_playwright = MagicMock()
        mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_context.new_page = AsyncMock(return_value=mock_page)
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

        browser = PreviewBrowser(theme_mode="dark")
        browser._script = "/* test */"

        with patch("playwright.async_api.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)
            mock_ap.return_value.stop = AsyncMock()

            async def wait_ready_and_test():
                while not browser._ready:
                    await asyncio.sleep(0.001)
                params = PreviewParams(fields=[])
                await browser.open_page("https://example.com", params)
                browser.request_stop()

            task = asyncio.create_task(browser.run())
            await wait_ready_and_test()
            await asyncio.wait_for(task, timeout=5)

        mock_page.emulate_media.assert_called_once_with(color_scheme="dark")

    @pytest.mark.asyncio
    async def test_update_theme_updates_all_pages(self):
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_page1 = MagicMock()
        mock_page2 = MagicMock()
        mock_playwright = MagicMock()
        mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_context.new_page = AsyncMock(side_effect=[mock_page1, mock_page2])
        mock_page1.goto = AsyncMock()
        mock_page1.emulate_media = AsyncMock()
        mock_page1.close = AsyncMock()
        mock_page1.evaluate = AsyncMock(
            return_value={
                "total": 0,
                "matched": 0,
                "unmatched": 0,
                "fallback_activated": False,
                "main_active": 0,
                "fallback_count": 0,
            }
        )
        mock_page2.goto = AsyncMock()
        mock_page2.emulate_media = AsyncMock()
        mock_page2.close = AsyncMock()
        mock_page2.evaluate = AsyncMock(
            return_value={
                "total": 0,
                "matched": 0,
                "unmatched": 0,
                "fallback_activated": False,
                "main_active": 0,
                "fallback_count": 0,
            }
        )

        browser = PreviewBrowser()
        browser._script = "/* test */"

        with patch("playwright.async_api.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)
            mock_ap.return_value.stop = AsyncMock()

            async def wait_ready_and_test():
                while not browser._ready:
                    await asyncio.sleep(0.001)
                params = PreviewParams(fields=[])
                await browser.open_page("https://a.com", params)
                await browser.open_page("https://b.com", params)
                await browser.update_theme("dark", {"window_bg": "#1E1E2E"})
                browser.request_stop()

            task = asyncio.create_task(browser.run())
            await wait_ready_and_test()
            await asyncio.wait_for(task, timeout=5)

        assert mock_page1.emulate_media.call_count >= 2
        assert mock_page2.emulate_media.call_count >= 1
        mock_page1.evaluate.assert_any_call(
            "window.__astrocrawl_update_theme",
            {"mode": "dark", "tokens": {"window_bg": "#1E1E2E"}},
        )
        mock_page2.evaluate.assert_any_call(
            "window.__astrocrawl_update_theme",
            {"mode": "dark", "tokens": {"window_bg": "#1E1E2E"}},
        )

    @pytest.mark.asyncio
    async def test_update_theme_single_page_closed_gracefully(self):
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_page = MagicMock()
        mock_page.emulate_media = AsyncMock()
        mock_playwright = MagicMock()
        mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_page.goto = AsyncMock()
        mock_page.emulate_media = AsyncMock()
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

        browser = PreviewBrowser()
        browser._script = "/* test */"

        with patch("playwright.async_api.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)
            mock_ap.return_value.stop = AsyncMock()

            async def wait_ready_and_test():
                while not browser._ready:
                    await asyncio.sleep(0.001)
                params = PreviewParams(fields=[])
                handle, _ = await browser.open_page("https://a.com", params)
                await browser.close_page(handle)
                await browser.update_theme("dark", {})

            task = asyncio.create_task(browser.run())
            await wait_ready_and_test()
            browser.request_stop()
            await asyncio.wait_for(task, timeout=5)

    @pytest.mark.asyncio
    async def test_run_handles_script_load_failure(self):
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_playwright = MagicMock()
        mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)

        browser = PreviewBrowser()

        with patch("playwright.async_api.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)
            mock_ap.return_value.stop = AsyncMock()

            with patch("astrocrawl.browser._preview._load_inject_script", side_effect=OSError("file not found")):

                async def launch_and_stop():
                    while not browser._ready:
                        await asyncio.sleep(0.001)
                    browser.request_stop()

                task = asyncio.create_task(browser.run())
                await launch_and_stop()
                await asyncio.wait_for(task, timeout=5)

        assert browser._script == ""
        assert browser._ready is False

    @pytest.mark.asyncio
    async def test_activate_page_calls_bring_to_front(self):
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_page = MagicMock()
        mock_page.emulate_media = AsyncMock()
        mock_page.bring_to_front = AsyncMock()
        mock_playwright = MagicMock()
        mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_page.goto = AsyncMock()
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

        browser = PreviewBrowser()
        browser._script = "/* test */"

        with patch("playwright.async_api.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)
            mock_ap.return_value.stop = AsyncMock()

            async def wait_ready_and_test():
                while not browser._ready:
                    await asyncio.sleep(0.001)
                params = PreviewParams(fields=[PreviewFieldParams(name="t", selector="h1")])
                handle, _ = await browser.open_page("https://example.com", params)
                await browser.activate_page(handle)
                browser.request_stop()

            task = asyncio.create_task(browser.run())
            await wait_ready_and_test()
            await asyncio.wait_for(task, timeout=5)

        mock_page.bring_to_front.assert_called_once()

    @pytest.mark.asyncio
    async def test_browser_side_page_close_triggers_callback(self):
        close_callbacks: list = []
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_page = MagicMock()
        mock_page.emulate_media = AsyncMock()
        mock_page.on = MagicMock(side_effect=lambda event, cb: close_callbacks.append(cb) if event == "close" else None)
        mock_playwright = MagicMock()
        mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_browser.new_context = AsyncMock(return_value=mock_context)
        mock_context.new_page = AsyncMock(return_value=mock_page)
        mock_page.goto = AsyncMock()
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

        browser = PreviewBrowser()
        browser._script = "/* test */"
        callback_calls: list[int] = []
        browser.set_page_closed_callback(lambda pid: callback_calls.append(pid))

        with patch("playwright.async_api.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)
            mock_ap.return_value.stop = AsyncMock()

            async def wait_ready_and_test():
                while not browser._ready:
                    await asyncio.sleep(0.001)
                params = PreviewParams(fields=[PreviewFieldParams(name="t", selector="h1")])
                handle, _ = await browser.open_page("https://example.com", params)
                assert handle.page_id in browser._pages
                for cb in close_callbacks:
                    cb()
                browser.request_stop()

            task = asyncio.create_task(browser.run())
            await wait_ready_and_test()
            await asyncio.wait_for(task, timeout=5)

        assert callback_calls == [0]
        assert 0 not in browser._pages
        # Second invocation hits idempotency guard (page_id already removed)
        callback_calls.clear()
        for cb in close_callbacks:
            cb()
        assert callback_calls == []


class TestPreviewBrowserProxy:
    @pytest.mark.asyncio
    async def test_no_proxy_no_context_option(self):
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_playwright = MagicMock()
        mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_new_context = AsyncMock(return_value=mock_context)
        mock_browser.new_context = mock_new_context

        browser = PreviewBrowser()
        browser._script = "/* test */"

        with patch("playwright.async_api.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)
            mock_ap.return_value.stop = AsyncMock()

            async def launch_and_stop():
                while not browser._ready:
                    await asyncio.sleep(0.001)
                browser.request_stop()

            task = asyncio.create_task(browser.run())
            await launch_and_stop()
            await asyncio.wait_for(task, timeout=5)

        call_kwargs = mock_new_context.call_args.kwargs
        assert "proxy" not in call_kwargs

    @pytest.mark.asyncio
    async def test_proxy_passed_to_context(self):
        from astrocrawl.proxy._config import ParsedProxy, ProxyAuth, ProxyType

        proxy = ParsedProxy(type=ProxyType.HTTP, host="127.0.0.1", port=9999, auth=ProxyAuth())
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_playwright = MagicMock()
        mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)
        mock_new_context = AsyncMock(return_value=mock_context)
        mock_browser.new_context = mock_new_context

        browser = PreviewBrowser(proxy=proxy)
        browser._script = "/* test */"

        with patch("playwright.async_api.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_playwright)
            mock_ap.return_value.stop = AsyncMock()

            async def launch_and_stop():
                while not browser._ready:
                    await asyncio.sleep(0.001)
                browser.request_stop()

            task = asyncio.create_task(browser.run())
            await launch_and_stop()
            await asyncio.wait_for(task, timeout=5)

        call_kwargs = mock_new_context.call_args.kwargs
        assert "proxy" in call_kwargs
        assert call_kwargs["proxy"]["server"].startswith("http://")

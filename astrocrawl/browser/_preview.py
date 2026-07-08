from __future__ import annotations

import asyncio
import gc
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

from astrocrawl.utils.logging import LogfmtLogger

logger = LogfmtLogger("astrocrawl.browser.preview")

_PREVIEW_CHROMIUM_ARGS = (
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
    "--no-sandbox",
    "--log-level=3",
)

# 10-tone palette for rule preview overlay injection (cyclically assigned)
_PREVIEW_FIELD_COLORS = (
    "#FF6B6B",
    "#4ECDC4",
    "#45B7D1",
    "#96CEB4",
    "#FFEAA7",
    "#DDA0DD",
    "#F0B27A",
    "#A29BFE",
    "#FF8A80",
    "#A8D8EA",
)


@dataclass(frozen=True)
class PreviewFieldParams:
    name: str
    selector: str
    extract: str = "text"
    attr: str = ""
    multiple: bool = False
    color: str = "#FF6B6B"
    fallback: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class PreviewParams:
    fields: list[PreviewFieldParams]
    rule_name: str = ""
    theme_mode: str = "light"
    theme_tokens: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PreviewResult:
    total: int = 0
    matched: int = 0
    unmatched: int = 0
    fallback_activated: bool = False
    main_active: int = 0
    fallback_count: int = 0


@dataclass(frozen=True)
class PreviewPageHandle:
    page_id: int
    url: str
    rule_name: str


def assign_field_colors(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for i, f in enumerate(fields):
        entry = dict(f)
        entry["color"] = _PREVIEW_FIELD_COLORS[i % len(_PREVIEW_FIELD_COLORS)]
        result.append(entry)
    return result


_INJECT_SCRIPT: str | None = None


def _load_inject_script() -> str:
    global _INJECT_SCRIPT
    if _INJECT_SCRIPT is None:
        path = Path(__file__).parent / "_preview_inject.js"
        _INJECT_SCRIPT = path.read_text(encoding="utf-8")
    return _INJECT_SCRIPT


class PreviewBrowser:
    def __init__(self, theme_mode: str = "light", proxy: Any = None) -> None:
        self._stop_event = asyncio.Event()
        self._ready = False
        self._pages: dict[int, Any] = {}
        self._page_urls: dict[int, str] = {}
        self._page_rules: dict[int, str] = {}
        self._next_id = 0
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._script: str | None = None
        self._on_page_closed_callback: Callable[[int], None] | None = None
        self._theme_mode: str = theme_mode
        self._proxy: Any = proxy

    def set_page_closed_callback(self, callback: Callable[[int], None]) -> None:
        self._on_page_closed_callback = callback

    async def run(self) -> None:
        try:
            self._script = _load_inject_script()
        except Exception:
            logger.warning("inject_script_load_failed", exc_info=True)
            self._script = ""

        from playwright.async_api import async_playwright

        gc.disable()
        try:
            self._playwright = await async_playwright().start()
            args = list(_PREVIEW_CHROMIUM_ARGS)
            if self._theme_mode == "dark":
                args.append("--force-dark-mode")
            from astrocrawl.browser._device_caps import get_chromium_flags

            args.extend(get_chromium_flags())
            self._browser = await self._playwright.chromium.launch(headless=False, args=args)
            self._browser.on("disconnected", lambda: self._stop_event.set())
            context_kwargs: dict[str, Any] = {"no_viewport": True}
            if self._proxy is not None:
                context_kwargs["proxy"] = {"server": self._proxy.to_url_with_auth()}
            self._context = await self._browser.new_context(**context_kwargs)
            self._ready = True
            gc.enable()
            logger.info("preview_browser_ready")
            await self._stop_event.wait()
        finally:
            gc.enable()
            self._ready = False
            await self._cleanup()

    async def _cleanup(self) -> None:
        for page in list(self._pages.values()):
            try:
                await page.close()
            except Exception:
                pass
        self._pages.clear()
        self._page_urls.clear()
        self._page_rules.clear()
        try:
            if self._context:
                await self._context.close()
        except Exception:
            pass
        try:
            if self._browser:
                await self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass
        logger.info("preview_browser_stopped")

    def request_stop(self) -> None:
        self._stop_event.set()

    async def open_page(
        self, url: str, params: PreviewParams, *, rule_name: str = ""
    ) -> tuple[PreviewPageHandle, PreviewResult]:
        if not self._ready:
            raise RuntimeError("PreviewBrowser 未就绪")
        page = await self._context.new_page()
        page_id = self._next_id
        self._next_id += 1
        self._pages[page_id] = page
        self._page_urls[page_id] = url
        self._page_rules[page_id] = rule_name

        def _on_browser_close() -> None:
            if page_id not in self._pages:
                return
            self._pages.pop(page_id, None)
            self._page_urls.pop(page_id, None)
            self._page_rules.pop(page_id, None)
            logger.info("preview_page_closed_browser", page_id=page_id)
            if self._on_page_closed_callback:
                self._on_page_closed_callback(page_id)

        page.on("close", _on_browser_close)

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            scheme = "dark" if self._theme_mode == "dark" else "light"
            await page.emulate_media(color_scheme=scheme)
            if self._script:
                await page.evaluate(self._script)
            result_data = await page.evaluate(
                "(params) => window.__astrocrawl_preview(params)",
                self._params_to_dict(params),
            )
            result = PreviewResult(
                total=result_data.get("total", 0),
                matched=result_data.get("matched", 0),
                unmatched=result_data.get("unmatched", 0),
                fallback_activated=result_data.get("fallback_activated", False),
                main_active=result_data.get("main_active", 0),
                fallback_count=result_data.get("fallback_count", 0),
            )
            handle = PreviewPageHandle(page_id=page_id, url=url, rule_name=rule_name)
            logger.info(
                "preview_page_opened",
                page_id=page_id,
                url=url,
                rule=rule_name,
                matched=result.matched,
                total=result.total,
            )
            return handle, result
        except Exception:
            logger.warning("preview_page_open_failed", page_id=page_id, url=url)
            self._pages.pop(page_id, None)
            self._page_urls.pop(page_id, None)
            self._page_rules.pop(page_id, None)
            try:
                await page.close()
            except Exception:
                pass
            raise

    async def close_page(self, handle: PreviewPageHandle) -> None:
        await self.close_page_by_id(handle.page_id)

    async def close_page_by_id(self, page_id: int) -> None:
        page = self._pages.pop(page_id, None)
        self._page_urls.pop(page_id, None)
        self._page_rules.pop(page_id, None)
        if page is None:
            return
        try:
            await page.evaluate("window.__astrocrawl_destroy()")
        except Exception:
            pass
        try:
            await page.close()
        except Exception:
            pass
        logger.info("preview_page_closed", page_id=page_id)

    async def activate_page(self, handle: PreviewPageHandle) -> None:
        page = self._pages.get(handle.page_id)
        if page is None:
            raise ValueError(f"page_id={handle.page_id} 不存在")
        await page.bring_to_front()

    async def update_theme(self, theme_mode: str, theme_tokens: dict[str, str]) -> None:
        self._theme_mode = theme_mode
        scheme = "dark" if theme_mode == "dark" else "light"
        for _page_id, page in list(self._pages.items()):
            try:
                await page.emulate_media(color_scheme=scheme)
                await page.evaluate(
                    "window.__astrocrawl_update_theme",
                    {"mode": theme_mode, "tokens": theme_tokens},
                )
            except Exception:
                pass

    @property
    def active_pages(self) -> list[PreviewPageHandle]:
        return [
            PreviewPageHandle(page_id=k, url=self._page_urls.get(k, ""), rule_name=self._page_rules.get(k, ""))
            for k in self._pages
        ]

    @staticmethod
    def _params_to_dict(params: PreviewParams) -> dict[str, Any]:
        fields = []
        for f in params.fields:
            entry = {
                "name": f.name,
                "selector": f.selector,
                "extract": f.extract,
                "attr": f.attr,
                "multiple": f.multiple,
                "color": f.color,
            }
            if f.fallback:
                entry["fallback"] = f.fallback
            fields.append(entry)
        return {
            "fields": fields,
            "rule_name": params.rule_name,
            "theme": {"mode": params.theme_mode, "tokens": params.theme_tokens},
        }

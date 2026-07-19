"""浏览器上下文槽位池 — 机制层。

封装所有 Playwright BrowserContext / PagePool 的创建、替换、销毁操作。
不包含策略决策——何时创建、何时轮换由 ContextPool（策略层）决定。
"""

from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Protocol

from astrocrawl._constants import CONTEXT_CREATE_TIMEOUT, PAGE_CLOSE_TIMEOUT, SLOT_CREATE_BACKOFF
from astrocrawl.browser.page_pool import PagePool

if TYPE_CHECKING:
    from playwright.async_api import Browser, BrowserContext

    from astrobasis import LogfmtLogger


class BrowserSlotContextConfig(Protocol):
    """浏览器视口/UA 配置——SlotPool._new_context() 使用。"""

    viewport_width: int
    viewport_height: int
    user_agent: str


class BrowserSlotAuthConfig(Protocol):
    """认证配置——SlotPool._new_context() 使用。"""

    auth_basic_user: str
    auth_basic_pass: str
    auth_bearer_token: str
    cookies_file: str
    custom_headers: List[str]


class BrowserSlotPoolConfig(Protocol):
    """池化参数——SlotPool.create()/replace() 使用。"""

    page_pool_size_per_context: int


class BrowserSlotConfig(BrowserSlotContextConfig, BrowserSlotAuthConfig, BrowserSlotPoolConfig, Protocol):
    """完整配置（向后兼容组合协议）。"""

    pass


async def _safe_close_context(ctx: Optional[BrowserContext], log: LogfmtLogger) -> None:
    """安全关闭浏览器上下文，带超时保护。"""
    if ctx is None:
        return
    try:
        await asyncio.wait_for(ctx.close(), timeout=PAGE_CLOSE_TIMEOUT)
    except asyncio.TimeoutError:
        log.warning("context_close_timeout", timeout=PAGE_CLOSE_TIMEOUT)
    except Exception as e:
        log.debug("context_close_error", error=e)


class SlotCreateError(RuntimeError):
    """槽位创建重试耗尽。"""


class SlotPool:
    """浏览器上下文的物理生命周期管理（机制层）。

    负责 BrowserContext 和 PagePool 的创建、替换、销毁，
    以及代理 URL → 槽位的映射。不决定何时操作——只执行操作。
    """

    def __init__(
        self,
        browser: Browser,
        max_slots: int,
        cfg: BrowserSlotConfig,
        log: LogfmtLogger,
    ) -> None:
        self._browser = browser
        self._max_slots = max_slots
        self._cfg = cfg
        self._log = log
        self._contexts: List[Optional[BrowserContext]] = [None] * max_slots
        self._page_pools: List[Optional[PagePool]] = [None] * max_slots
        self._proxy_map: Dict[int, Optional[str]] = {}
        self._closed = False

    # ── 生命周期 ──────────────────────────────────────────────

    async def create(self, idx: int, proxy_url: Optional[str] = None, max_attempts: int = 3) -> bool:
        """创建槽位（带重试）：new_context + new PagePool。返回成功标志。"""
        for attempt in range(1, max_attempts + 1):
            if self._closed:
                return False
            try:
                ctx = await self._new_context(proxy_url)
                pool = PagePool(ctx)
                self._contexts[idx] = ctx
                self._page_pools[idx] = pool
                self._proxy_map[idx] = proxy_url
                return True
            except Exception as e:
                self._log.warning("slot_create_failed", idx=idx, attempt=attempt, max_attempts=max_attempts, error=e)
                if attempt < max_attempts:
                    await asyncio.sleep(
                        SLOT_CREATE_BACKOFF / 2 + random.uniform(0, SLOT_CREATE_BACKOFF / 2),
                    )
        self._log.error("slot_create_exhausted", idx=idx, attempts=max_attempts)
        return False

    async def replace(self, idx: int, proxy_url: Optional[str], max_attempts: int = 3) -> None:
        """替换槽位的浏览器上下文（原子交换）。

        先建新后毁旧——新上下文创建成功后才关闭旧上下文。
        max_attempts 次全部失败后抛出 SlotCreateError。
        """
        last_exc = None
        for attempt in range(1, max_attempts + 1):
            if self._closed:
                return
            try:
                new_ctx = await self._new_context(proxy_url)
                new_pool = PagePool(new_ctx)
                # 原子交换
                old_ctx = self._contexts[idx]
                old_pool = self._page_pools[idx]
                self._contexts[idx] = new_ctx
                self._page_pools[idx] = new_pool
                self._proxy_map[idx] = proxy_url
                # 安全销毁旧资源
                if old_pool:
                    try:
                        await old_pool.close_all()
                    except Exception as e:
                        self._log.debug("old_page_pool_close_error", error=e)
                if old_ctx:
                    try:
                        await _safe_close_context(old_ctx, self._log)
                    except Exception as e:
                        self._log.debug("old_context_close_error", error=e)
                return
            except Exception as e:
                last_exc = e
                self._log.warning("context_create_failed", attempt=attempt, max_attempts=max_attempts, error=e)
                if attempt < max_attempts:
                    await asyncio.sleep(
                        SLOT_CREATE_BACKOFF / 2 + random.uniform(0, SLOT_CREATE_BACKOFF / 2),
                    )
        raise SlotCreateError(f"槽位 {idx} 替换失败，{max_attempts} 次重试耗尽: {last_exc}")

    async def destroy(self, idx: int) -> None:
        """销毁槽位：关闭 PagePool 和 BrowserContext，清理槽位数组。"""
        old_pool = self._page_pools[idx]
        old_ctx = self._contexts[idx]
        self._contexts[idx] = None
        self._page_pools[idx] = None
        self._proxy_map.pop(idx, None)
        if old_pool:
            try:
                await old_pool.close_all()
            except Exception as e:
                self._log.debug("page_pool_close_error", error=e)
        if old_ctx:
            try:
                await _safe_close_context(old_ctx, self._log)
            except Exception as e:
                self._log.debug("context_close_error", error=e)

    def stop_accepting(self) -> None:
        """标记槽位池为关闭中——阻止新 context 创建，已有操作可继续。

        对标 HikariCP close 先标记关闭再驱逐连接的二阶段模式。
        create() / replace() 已检查 self._closed 并提前返回。
        """
        self._closed = True

    async def close_all(self) -> None:
        """关闭所有槽位。"""
        self._closed = True
        for pool in self._page_pools:
            if pool:
                await pool.close_all()
        for ctx in self._contexts:
            if ctx:
                await _safe_close_context(ctx, self._log)

    # ── 查询 ──────────────────────────────────────────────────

    def get_context(self, idx: int) -> Optional[BrowserContext]:
        if 0 <= idx < self._max_slots:
            return self._contexts[idx]
        return None

    def get_page_pool(self, idx: int) -> Optional[PagePool]:
        if 0 <= idx < self._max_slots:
            return self._page_pools[idx]
        return None

    def get_proxy_url(self, idx: int) -> Optional[str]:
        return self._proxy_map.get(idx)

    def slot_is_valid(self, idx: int) -> bool:
        return 0 <= idx < self._max_slots and self._page_pools[idx] is not None

    @property
    def max_slots(self) -> int:
        return self._max_slots

    @property
    def browser(self) -> Browser:
        return self._browser

    @property
    def is_closed(self) -> bool:
        return self._closed

    # ── 内部：上下文创建 ──────────────────────────────────────

    async def _new_context(self, proxy_url: Optional[str]) -> BrowserContext:
        """创建浏览器上下文，整合代理、认证头、自定义头、Cookie。"""
        ctx_kwargs: dict = {
            "viewport": {"width": self._cfg.viewport_width, "height": self._cfg.viewport_height},
            "user_agent": self._cfg.user_agent,
        }
        if proxy_url:
            ctx_kwargs["proxy"] = {"server": proxy_url}

        if self._cfg.auth_basic_user and self._cfg.auth_basic_pass:
            ctx_kwargs["http_credentials"] = {
                "username": self._cfg.auth_basic_user,
                "password": self._cfg.auth_basic_pass,
            }
        extra_headers: Dict[str, str] = {}
        if self._cfg.auth_bearer_token:
            extra_headers["Authorization"] = f"Bearer {self._cfg.auth_bearer_token}"
        for hdr in self._cfg.custom_headers:
            if ":" in hdr:
                key, val = hdr.split(":", 1)
                extra_headers[key.strip()] = val.strip()
        if extra_headers:
            ctx_kwargs["extra_http_headers"] = extra_headers

        ctx = await asyncio.wait_for(
            self._browser.new_context(**ctx_kwargs),
            timeout=CONTEXT_CREATE_TIMEOUT,
        )
        if self._cfg.cookies_file:
            await self._load_cookies(ctx)
        return ctx

    async def _load_cookies(self, ctx: BrowserContext) -> None:
        cookies_path = Path(self._cfg.cookies_file).resolve()
        if not cookies_path.is_file() or cookies_path.suffix.lower() != ".json":
            self._log.warning("cookie_file_invalid", path=cookies_path)
            return
        try:
            raw = json.loads(cookies_path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                raise ValueError("Cookie 文件应为 JSON 数组")
            valid, invalid = [], []
            for item in raw:
                if isinstance(item, dict) and "name" in item and "value" in item:
                    valid.append(item)
                else:
                    invalid.append(item)
            if invalid:
                self._log.warning("cookie_entries_dropped", count=len(invalid))
            if valid:
                await asyncio.wait_for(
                    ctx.add_cookies(valid),  # type: ignore[arg-type]
                    timeout=CONTEXT_CREATE_TIMEOUT,
                )
        except Exception as e:
            self._log.warning("cookie_load_failed", error=e)

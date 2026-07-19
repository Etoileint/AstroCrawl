"""上下文池 — 代理绑定 + 上下文生命周期管理。

ContextPool 仅负责槽位的代理绑定和浏览器上下文的物理创建/替换。
调度由 BrowserPool._global_slots + _slot_sem 管理，ContextPool 不参与。

架构对标: HikariCP PoolBase/HikariPool 分离 — ContextPool 是纯生命周期管理器，
调度器在 BrowserPool。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncIterator, Optional

from astrobasis import LogfmtLogger
from astrocrawl._constants import SLOT_CREATE_RETRIES
from astrocrawl.browser._slot_pool import SlotPool
from astrocrawl.config import ConfigError, CrawlerConfig, GlobalSettings

if TYPE_CHECKING:
    from astrocrawl._path_strategy import PathSwitch
    from astrocrawl.browser.page_pool import PagePool
    from astrocrawl.proxy import ProxySession


class ContextPool:
    """槽位代理绑定与上下文生命周期管理（纯机制层，无调度逻辑）。"""

    def __init__(
        self,
        browser,
        max_slots: int,
        proxy_session: Optional[ProxySession],
        cfg: CrawlerConfig,
        global_settings: GlobalSettings = GlobalSettings(),
    ):
        self._cfg = cfg
        self._global_settings = global_settings
        self._proxy_session = proxy_session
        self._path_switch = cfg.get_path_switch()
        self._max_slots = max_slots
        self._log = LogfmtLogger("astrocrawl.contextpool")
        self._slot_pool = SlotPool(
            browser,
            max_slots,
            cfg,  # type: ignore[arg-type]
            self._log,
        )
        self._closed = False

        if cfg.proxy_mode in ("proxy_only", "prefer_proxy", "prefer_direct") and not proxy_session:
            raise ConfigError(
                f"proxy_mode='{cfg.proxy_mode}' requires at least one proxy. "
                f"Provide proxies or set proxy_mode to 'direct_only'."
            )

    # ── 初始化 ────────────────────────────────────────────────

    async def init(self) -> None:
        """批量创建所有槽位，代理分配策略在此决定。"""
        if self._proxy_session and self._path_switch.main_is_proxy:
            proxy_cycle = self._proxy_session.proxies
        else:
            proxy_cycle = [None]  # type: ignore[list-item, assignment]

        async def _create_one(idx: int, proxy_url: Optional[str]) -> bool:
            return await self._slot_pool.create(idx, proxy_url, max_attempts=SLOT_CREATE_RETRIES)

        tasks = []
        for idx in range(self._max_slots):
            proxy_url = proxy_cycle[idx % len(proxy_cycle)] if proxy_cycle else None
            tasks.append(_create_one(idx, proxy_url))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for idx, result in enumerate(results):
            if isinstance(result, Exception):
                self._log.warning("slot_parallel_create_failed", idx=idx, error=result)
        if not any(self._slot_pool.slot_is_valid(i) for i in range(self._max_slots)):
            raise RuntimeError("无法创建任何浏览器上下文，爬虫终止")

    # ── 代理轮换（公共入口）────────────────────────────────────

    async def rotate_proxy(self, idx: int) -> bool:
        """为槽位轮换代理。engine.py / _dispatch_retry_strategy 调用。"""
        if not self._proxy_session:
            return False
        old_proxy = self._slot_pool.get_proxy_url(idx)
        if old_proxy and not self._proxy_session.is_available(old_proxy):
            if not self._proxy_session.healthy_proxies_in_pool():
                return False
        new_proxy = await self._proxy_session.get_proxy(prefer_different_than=old_proxy)
        if new_proxy is None:
            return False
        await self._slot_pool.replace(idx, new_proxy)
        return True

    async def replace_context(self, idx: int, max_attempts: int = 3) -> None:
        """替换槽位的浏览器上下文（_dispatch_retry_strategy 调用）。

        若槽位原本使用代理则获取一个新代理 URL，否则直连。
        """
        proxy_url = None
        if self._proxy_session and self._slot_pool.get_proxy_url(idx) is not None:
            proxy_url = await self._proxy_session.get_proxy()
        await self._slot_pool.replace(idx, proxy_url, max_attempts=max_attempts)

    @asynccontextmanager
    async def scoped_path(self, idx: int, target: str) -> AsyncIterator[None]:
        """临时将槽位切换到 target（"proxy"|"direct"），退出时还原路径。"""
        was_proxy = self._slot_pool.get_proxy_url(idx) is not None
        target_is_proxy = target == "proxy"
        if was_proxy == target_is_proxy:
            yield
            return

        if target_is_proxy:
            if not self._proxy_session:
                raise RuntimeError("scoped_path: ProxySession 未配置，无法切换到代理路径")
            new_proxy = await self._proxy_session.get_proxy()
            if new_proxy is None:
                raise RuntimeError("scoped_path: 无可用代理")
            await self._slot_pool.replace(idx, new_proxy)
        else:
            await self._slot_pool.replace(idx, None)

        try:
            yield
        finally:
            try:
                if was_proxy:
                    restore_proxy = await self._proxy_session.get_proxy()  # type: ignore[union-attr]
                    await self._slot_pool.replace(idx, restore_proxy)
                else:
                    await self._slot_pool.replace(idx, None)
            except Exception:
                self._log.warning("scoped_path_restore_failed", idx=idx, exc_info=True)

    # ── 代理健康反馈 ──────────────────────────────────────────

    async def mark_proxy_success(self, idx: int) -> None:
        proxy_url = self._slot_pool.get_proxy_url(idx)
        if proxy_url and self._proxy_session:
            await self._proxy_session.mark_success(proxy_url)

    async def mark_proxy_failure(self, idx: int, weight: int = 1) -> None:
        proxy_url = self._slot_pool.get_proxy_url(idx)
        if proxy_url and self._proxy_session:
            await self._proxy_session.mark_failure(proxy_url, weight=weight)

    # ── 查询 ──────────────────────────────────────────────────

    @property
    def path_switch(self) -> "PathSwitch":
        return self._path_switch

    def get_proxy_for_slot(self, idx: int) -> Optional[str]:
        return self._slot_pool.get_proxy_url(idx)

    def get_page_pool(self, idx: int) -> Optional[PagePool]:
        return self._slot_pool.get_page_pool(idx)

    def slot_is_valid(self, idx: int) -> bool:
        return self._slot_pool.slot_is_valid(idx)

    # ── 生命周期 ──────────────────────────────────────────────

    def stop_accepting(self) -> None:
        """标记上下文池为关闭中——阻止 slot 创建/替换，已有操作可继续。

        级联到 SlotPool：scoped_path.__aexit__ 还原 slot 时 replace() 检测 _closed 静默跳过。
        """
        self._closed = True
        self._slot_pool.stop_accepting()

    async def close_all(self) -> None:
        """关闭所有槽位和资源。"""
        self._closed = True
        await self._slot_pool.close_all()

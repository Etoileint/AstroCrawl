"""BrowserPool — 多 Chromium 实例池 + Actor 消息模式。

对标：
- Puppeteer Cluster: 每 Browser 管理 N 个 page
- Browserless.io: 浏览器池 + 健康检查 + 故障转移
- Erlang/OTP gen_server: send(request) → Future 响应模式
- Scrapy RetryMiddleware: 抓取重试由 BrowserPool 内部管理

Worker 通过 send(FetchRequest) 交互，不接触 Playwright 对象。
"""

from __future__ import annotations

import asyncio
import logging
import math
import random as _random
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from astrocrawl._constants import (
    BLOCKED_RESOURCE_TYPES,
    BROWSER_GOTO_BUFFER_S,
    BROWSER_LAUNCH_TIMEOUT,
    BROWSER_RESTART_TIMEOUT_MULT,
    CHROMIUM_LAUNCH_ARGS,
    CONTENT_READ_TIMEOUT,
    CONTEXT_HEALTH_CHECK_INTERVAL,
    HARD_CLEANUP_TIMEOUT,
    MAX_ERROR_MESSAGE_LENGTH,
    RELEASE_UNROUTE_TIMEOUT,
)
from astrocrawl._retry_strategy import FATAL_HTTP_STATUS, RetryStrategy
from astrocrawl._types import FetchErrorCategory, classify_fetch_error
from astrocrawl.browser._domain_memory import DomainPathMemory
from astrocrawl.browser._retry import ProxyFailureClassifier
from astrocrawl.browser.context_pool import ContextPool
from astrocrawl.browser.navigation import safe_goto
from astrocrawl.browser.page_pool import PagePool, safe_close_page
from astrocrawl.config import CrawlerConfig, GlobalSettings
from astrocrawl.health import Health
from astrocrawl.utils.url import parse_domain

_log = logging.getLogger("astrocrawl.browserpool")


def _safe_set_result(future: asyncio.Future, result) -> bool:
    """安全设置 Future 结果，防止 InvalidStateError（对标结构化并发）。"""
    if not future.done():
        future.set_result(result)
        return True
    return False


def _safe_future_has_result(future: asyncio.Future) -> bool:
    return future.done()


# ── 辅助函数（从 _page_fetcher.py 迁移）────────────────────────────


async def _safe_unroute(page: Optional[Page], timeout: float) -> None:
    """安全解除页面路由，带超时保护。"""
    if page is None or page.is_closed():
        return
    try:
        await asyncio.wait_for(page.unroute_all(), timeout=timeout)
    except Exception:
        pass


def _make_resource_filter(blocked_types: frozenset):
    """返回 Playwright 兼容的 route handler 闭包。"""

    async def _filter(route):
        try:
            if route.request.resource_type in blocked_types:
                await route.abort()
            else:
                await route.continue_()
        except Exception:
            pass

    return _filter


def _backoff_with_jitter(backoff: float, strategy: str = "full") -> float:
    """随机抖动退避。"""
    if strategy == "full":
        return _random.uniform(0, backoff)
    return backoff / 2 + _random.uniform(0, backoff / 2)


SLOTS_PER_BROWSER = 8
MAX_BROWSERS = 8
MIN_BROWSERS = 1


@dataclass(frozen=True)
class FetchRequest:
    url: str
    timeout_ms: int = 20000
    wait_until: str = "domcontentloaded"


@dataclass(frozen=True)
class FetchResponse:
    url: str
    html: str
    status_code: int


@dataclass(frozen=True)
class FetchError:
    error: str
    category: str  # FetchErrorCategory.value
    is_infra: bool = False


# ── BrowserInstance ──────────────────────────────────────────────


class BrowserInstance:
    """单个 Chromium 进程 + N 个 ContextPool slot。"""

    def __init__(
        self,
        idx: int,
        slots: int,
        cfg: CrawlerConfig,
        proxy_session: Any = None,
        global_settings: GlobalSettings = GlobalSettings(),
    ) -> None:
        self.idx = idx
        self._slots = slots
        self._cfg = cfg
        self._proxy_session = proxy_session
        self._global_settings = global_settings
        self._browser = None
        self._ctx_pool: Optional[ContextPool] = None
        self._closed = False
        self._healthy = True
        self._last_health_check = 0.0
        self._restart_backoff_until = 0.0
        self._restart_failures = 0

    async def start(self, playwright) -> None:
        launch_args = list(CHROMIUM_LAUNCH_ARGS)
        self._browser = await asyncio.wait_for(  # type: ignore[func-returns-value]
            playwright.chromium.launch(headless=True, args=launch_args),
            timeout=BROWSER_LAUNCH_TIMEOUT,
        )
        self._ctx_pool = ContextPool(
            self._browser,
            self._slots,
            self._proxy_session,
            self._cfg,
            global_settings=self._global_settings,
        )
        await self._ctx_pool.init()
        self._healthy = True

    @property
    def ctx_pool(self) -> Optional[ContextPool]:
        return self._ctx_pool

    @property
    def is_healthy(self) -> bool:
        return self._healthy and self._ctx_pool is not None

    async def health_ping(self) -> bool:
        """CDP 探活——仅检测浏览器进程是否存活，不占用 slot。对标 K8s liveness probe。"""
        if not self._ctx_pool or self._closed:
            return False
        try:
            if self._browser is not None and self._browser.is_connected():
                return True
            return False
        except Exception:
            return False

    async def restart(self, playwright) -> None:
        """杀进程 → 重启 → 重建 ContextPool。"""
        _log.warning("event=browser_restart id=%d", self.idx)
        self._healthy = False
        try:
            if self._ctx_pool:
                await asyncio.wait_for(
                    self._ctx_pool.close_all(),
                    timeout=HARD_CLEANUP_TIMEOUT,
                )
        except Exception:
            pass
        try:
            if self._browser:
                await asyncio.wait_for(
                    self._browser.close(),
                    timeout=HARD_CLEANUP_TIMEOUT,
                )
        except Exception:
            pass
        await self.start(playwright)
        _log.info("event=browser_restart_done id=%d", self.idx)

    def stop_accepting(self) -> None:
        """标记浏览器实例为关闭中——阻止新 context 创建，级联到下层。"""
        if self._ctx_pool:
            self._ctx_pool.stop_accepting()

    async def close(self) -> None:
        self._closed = True
        if self._ctx_pool:
            try:
                await asyncio.wait_for(
                    self._ctx_pool.close_all(),
                    timeout=HARD_CLEANUP_TIMEOUT,
                )
            except Exception:
                pass
        if self._browser:
            try:
                await asyncio.wait_for(
                    self._browser.close(),
                    timeout=HARD_CLEANUP_TIMEOUT,
                )
            except Exception:
                pass


# ── BrowserPool ──────────────────────────────────────────────────


class BrowserPool:
    """多 Chromium 实例池。Actor 消息模式。

    K 个 Browser × (concurrency/K) 个 slot。
    Worker 通过 send(FetchRequest) 交互 → 不接触 Playwright。
    """

    def __init__(
        self,
        concurrency: int,
        cfg: CrawlerConfig,
        proxy_session: Any = None,
        global_settings: GlobalSettings = GlobalSettings(),
    ) -> None:
        self._K = max(MIN_BROWSERS, min(MAX_BROWSERS, math.ceil(concurrency / SLOTS_PER_BROWSER)))
        self._slots_per_browser = math.ceil(concurrency / self._K)
        self._cfg = cfg
        self._proxy_session = proxy_session
        self._global_settings = global_settings
        self._path_switch = cfg.get_path_switch()
        self._domain_memory = DomainPathMemory()
        self._browsers: Dict[int, BrowserInstance] = {}
        self._slot_sem = asyncio.Semaphore(concurrency)
        self._global_slots: asyncio.Queue = asyncio.Queue(maxsize=self._K * self._slots_per_browser)
        self._mailbox: asyncio.Queue = asyncio.Queue()
        self._closed = False
        self._actor_task: Optional[asyncio.Task] = None
        self._health_task: Optional[asyncio.Task] = None
        self._handle_tasks: set = set()  # 追踪进行中的 _handle 任务
        self._pw = None  # playwright instance

    # ── Worker API ────────────────────────────────────────────

    async def send(self, request: FetchRequest) -> FetchResponse | FetchError:
        """Erlang gen_server.call() 等价。发送请求，等待响应。"""
        future = asyncio.get_running_loop().create_future()
        await self._mailbox.put((request, future))
        try:
            return await future  # type: ignore[no-any-return]
        except asyncio.CancelledError:
            if not future.done():
                future.cancel()
            raise

    # ── Actor 主循环 ──────────────────────────────────────────

    async def run(self) -> None:
        """Actor 主循环：消费 mailbox → 分配 Browser → 导航 → 返回结果。"""
        while not self._closed:
            try:
                request, future = await self._mailbox.get()
            except (asyncio.CancelledError, RuntimeError):
                break
            if self._closed:
                _safe_set_result(future, FetchError("BrowserPool 已关闭", "generic", True))
                continue

            await self._slot_sem.acquire()
            t = asyncio.create_task(self._handle(request, future))
            self._handle_tasks.add(t)
            t.add_done_callback(
                lambda t, s=self._handle_tasks: s.discard(t) or (None if t.cancelled() else t.exception())  # type: ignore[misc]
            )

    async def _handle(self, request: FetchRequest, future: asyncio.Future) -> None:
        """双层资源守卫：slot 守卫 → page 守卫 → 业务逻辑。

        对标 RAII / Go defer: 资源获取后立即进入 try/finally，确保退出时释放。
        asyncio.shield() 保护 slot 归还不被 CancelledError 中断。
        _slot_sem 由 sem_released 标志 + 外层 finally 兜底，消除取消路径泄漏。
        """
        slot = None
        sem_released = False
        try:
            slot = await self._global_slots.get()
            try:  # ── slot 守卫 ──
                browser, slot_idx = slot
                ctx_pool = browser.ctx_pool
                if ctx_pool is None:
                    _safe_set_result(future, FetchError("浏览器未就绪", "generic", True))
                    self._slot_sem.release()
                    sem_released = True
                    return
                if not ctx_pool.slot_is_valid(slot_idx):
                    _safe_set_result(future, FetchError("槽位已失效", "generic", True))
                    self._slot_sem.release()
                    sem_released = True
                    return

                pool = ctx_pool.get_page_pool(slot_idx)
                try:
                    page = await pool.acquire()  # type: ignore[union-attr]
                except Exception as e:
                    _safe_set_result(future, FetchError(f"获取页面失败: {e}", "generic", True))
                    self._slot_sem.release()
                    sem_released = True
                    return

                self._slot_sem.release()
                sem_released = True

                try:  # ── page 守卫 ──
                    await self._do_fetch(
                        request,
                        future,
                        page,
                        pool,
                        ctx_pool,
                        slot_idx,
                    )
                except Exception as e:
                    # _do_fetch 未预期异常 → 兜底设置 future 结果，防止调用方永久挂起
                    if not _safe_future_has_result(future):
                        cat = classify_fetch_error(str(e))
                        try:
                            _safe_set_result(future, FetchError(str(e), cat.value, False))
                        except asyncio.InvalidStateError:
                            pass
                finally:
                    try:
                        await _safe_unroute(page, RELEASE_UNROUTE_TIMEOUT)
                    except Exception:
                        pass
                    try:
                        await safe_close_page(page, _log)
                    except Exception:
                        pass
            finally:  # ── slot 释放 (cancellation 安全) ──
                if slot is not None:
                    await asyncio.shield(self._global_slots.put(slot))
        finally:
            if not sem_released:
                self._slot_sem.release()

    async def _do_fetch(
        self,
        request: FetchRequest,
        future: asyncio.Future,
        page: Page,
        pool: PagePool,
        ctx_pool,
        slot_idx: int,
    ) -> None:
        """纯业务逻辑：域名记忆 → 主路径 → 路径回退 → 结果派发。

        不包含任何资源获取/释放操作。slot 和 page 由调用方 _handle() 管理。
        最终 is_infra 仅由此函数裁决：CONTEXT_FAILURE→True，其余→False。
        """
        domain = parse_domain(request.url)
        asyncio_timeout = request.timeout_ms / 1000.0 + BROWSER_GOTO_BUFFER_S

        # ═══ Phase 0: 域名记忆快捷路径 ═══
        if (
            self._domain_memory.needs_proxy(domain)
            and self._path_switch
            and not self._path_switch.main_is_proxy
            and self._path_switch.has_fallback
        ):
            async with ctx_pool.scoped_path(slot_idx, "proxy"):
                page, pool, response, err_str, fatal = await self._retry_loop(  # type: ignore[assignment]
                    page,
                    pool,
                    ctx_pool,
                    slot_idx,
                    request,
                    asyncio_timeout,
                )
                if response is not None:
                    _safe_set_result(future, response)
                    return

        elif (
            self._domain_memory.needs_direct(domain)
            and self._path_switch
            and self._path_switch.main_is_proxy
            and self._path_switch.has_fallback
        ):
            async with ctx_pool.scoped_path(slot_idx, "direct"):
                page, pool, response, err_str, fatal = await self._retry_loop(  # type: ignore[assignment]
                    page,
                    pool,
                    ctx_pool,
                    slot_idx,
                    request,
                    asyncio_timeout,
                )
                if response is not None:
                    _safe_set_result(future, response)
                    return

        # ═══ Phase 1: 主路径重试 ═══
        page, pool, response, err_str, fatal = await self._retry_loop(  # type: ignore[assignment]
            page,
            pool,
            ctx_pool,
            slot_idx,
            request,
            asyncio_timeout,
        )
        if response is not None:
            _safe_set_result(future, response)
            return

        # ═══ Phase 2: 路径回退 ═══
        err_final = fatal or err_str or "未知错误"
        fallback_cat = classify_fetch_error(err_final)

        if self._path_switch and self._path_switch.has_fallback and not _safe_future_has_result(future):
            fallback_target = None

            if self._path_switch.should_fallback_for_error(fallback_cat):
                fallback_target = "proxy"
            elif self._path_switch.should_fallback_for_proxy_exhaustion(fallback_cat):
                fallback_target = "direct"
            elif self._domain_memory.needs_direct(domain):
                fallback_target = "direct"
            elif self._domain_memory.needs_proxy(domain):
                fallback_target = "proxy"

            if fallback_target:
                if page is None and pool is not None:
                    try:
                        page = await pool.acquire()  # type: ignore[union-attr]
                    except Exception:
                        pass

                async with ctx_pool.scoped_path(slot_idx, fallback_target):
                    page, pool, response, err_str, fatal = await self._retry_loop(  # type: ignore[assignment]
                        page,
                        pool,
                        ctx_pool,
                        slot_idx,
                        request,
                        asyncio_timeout,
                    )
                    if response is not None:
                        if fallback_target == "proxy":
                            self._domain_memory.remember(domain)
                        elif fallback_target == "direct":
                            self._domain_memory.remember_direct(domain)
                        _safe_set_result(future, response)
                        return
                    err_final = fatal or err_str or err_final

        # ═══ 最终错误裁决 ═══
        if not _safe_future_has_result(future):
            err_final = err_final[:MAX_ERROR_MESSAGE_LENGTH]
            cat = classify_fetch_error(err_final)
            is_infra = cat == FetchErrorCategory.CONTEXT_FAILURE
            _safe_set_result(future, FetchError(err_final, cat.value, is_infra))

    async def _release_broken_page(
        self,
        page: Optional[Page],
        pool: Optional[PagePool],
    ) -> None:
        """释放损坏的页面：取消路由并移除。"""
        if page is not None and pool is not None:
            try:
                await _safe_unroute(page, RELEASE_UNROUTE_TIMEOUT)
            except Exception:
                pass
            try:
                await pool.remove_broken(page)
            except Exception:
                pass

    async def _attempt_single_fetch(
        self,
        page: Page,
        request: FetchRequest,
        asyncio_timeout: float,
        slot_has_proxy: bool,
    ) -> Tuple[Optional[FetchResponse], Optional[str], Optional[RetryStrategy]]:
        """一次完整的 goto+wait+content 尝试。返回 (response, err_str, strategy)。

        导航成功时 response 非 None。失败时 err_str 和 strategy 用于重试分发。
        """
        try:
            if self._cfg.skip_non_essential_resources:
                await page.route("**/*", _make_resource_filter(BLOCKED_RESOURCE_TYPES))

            resp = await safe_goto(
                page,
                request.url,
                playwright_timeout_ms=request.timeout_ms,
                asyncio_timeout_s=asyncio_timeout,
            )

            if resp and not resp.ok:
                status = resp.status
                if status in FATAL_HTTP_STATUS:
                    return None, f"net::HTTP_{status}: 不可重试错误", RetryStrategy.FATAL
                strategy = ProxyFailureClassifier.classify(
                    f"net::HTTP_{status}",
                    has_proxy=slot_has_proxy,
                    http_status=status,
                )
                return None, f"net::HTTP_{status}", strategy

            # 导航成功
            try:
                await page.wait_for_load_state(
                    "networkidle",
                    timeout=self._cfg.network_idle_timeout,
                )
            except PlaywrightTimeoutError:
                pass

            final_url = page.url
            html = await asyncio.wait_for(page.content(), timeout=CONTENT_READ_TIMEOUT)
            status_code = resp.status if resp else 0
            return FetchResponse(final_url, html, status_code), None, None

        except asyncio.TimeoutError:
            err_str = "Timeout exceeded (asyncio safety net)"
            strategy = ProxyFailureClassifier.classify(err_str, has_proxy=slot_has_proxy)
            return None, err_str, strategy
        except PlaywrightError as exc:
            err_str = str(exc)
            strategy = ProxyFailureClassifier.classify(err_str, has_proxy=slot_has_proxy)
            return None, err_str, strategy
        except Exception as exc:
            err_str = str(exc)
            strategy = ProxyFailureClassifier.classify(err_str, has_proxy=slot_has_proxy)
            return None, err_str, strategy

    async def _dispatch_retry_strategy(
        self,
        strategy: RetryStrategy,
        err_str: str,
        ctx_pool,
        slot_idx: int,
        page: Optional[Page],
        pool: Optional[PagePool],
        attempt: int,
        slot_has_proxy: bool,
    ) -> Tuple[Optional[Page], Optional[PagePool], bool, bool, Optional[str]]:
        """执行策略分发动作。返回 (page, pool, slot_has_proxy, should_retry, fatal_error_str)。

        ROTATE_PROXY → 轮换代理后 retry（不计入 max_retries，自然耗尽）
        REPLACE_CONTEXT → 重建上下文后 retry
        TRANSIENT → backoff 后 retry
        FATAL → 不 retry

        fatal_error_str 非 None 时表示不可恢复错误，_retry_loop 应终止。
        不再直接写 future——最终裁决由 _do_fetch 完成。
        """
        if strategy == RetryStrategy.ROTATE_PROXY:
            is_timeout = classify_fetch_error(err_str) == FetchErrorCategory.TIMEOUT
            await ctx_pool.mark_proxy_failure(slot_idx, weight=3 if is_timeout else 1)
            await self._release_broken_page(page, pool)
            page = None
            old_proxy = ctx_pool.get_proxy_for_slot(slot_idx)
            rotated = await ctx_pool.rotate_proxy(slot_idx)
            if rotated:
                new_proxy = ctx_pool.get_proxy_for_slot(slot_idx)
                if old_proxy and new_proxy == old_proxy:
                    return page, pool, slot_has_proxy, False, "代理轮换失败——无可用替代代理"
                pool = ctx_pool.get_page_pool(slot_idx)
                slot_has_proxy = ctx_pool.get_proxy_for_slot(slot_idx) is not None
                return page, pool, slot_has_proxy, True, None
            else:
                return page, pool, slot_has_proxy, False, "代理轮换失败——无可用替代代理"

        elif strategy == RetryStrategy.REPLACE_CONTEXT:
            await self._release_broken_page(page, pool)
            page = None
            try:
                await ctx_pool.replace_context(slot_idx)
                pool = ctx_pool.get_page_pool(slot_idx)
                if pool is None:
                    return page, pool, slot_has_proxy, False, "上下文恢复失败，槽位已失效"
                slot_has_proxy = ctx_pool.get_proxy_for_slot(slot_idx) is not None
            except RuntimeError:
                return page, pool, slot_has_proxy, False, "上下文槽位修复失败，爬虫终止"
            return page, pool, slot_has_proxy, True, None

        elif strategy == RetryStrategy.TRANSIENT:
            await self._release_broken_page(page, pool)
            page = None
            await asyncio.sleep(
                _backoff_with_jitter(
                    self._cfg.retry_backoff_base**attempt,
                )
            )
            return page, pool, slot_has_proxy, True, None

        return page, pool, slot_has_proxy, False, None  # FATAL

    async def _retry_loop(
        self,
        page: Optional[Page],
        pool: Optional[PagePool],
        ctx_pool,
        slot_idx: int,
        request: FetchRequest,
        asyncio_timeout: float,
    ) -> Tuple[Optional[Page], Optional[PagePool], Optional[FetchResponse], str, Optional[str]]:
        """主重试循环。返回 (page, pool, response, err_str, fatal_error_str)。

        ROTATE_PROXY 不消耗 retries_remaining，以 rotate_proxy()→False 自然终止。
        TRANSIENT / REPLACE_CONTEXT 消耗 retries_remaining。
        is_infra 由此处不再追踪——最终由 _do_fetch 裁决。
        """
        slot_has_proxy = ctx_pool.get_proxy_for_slot(slot_idx) is not None
        retries_remaining = self._cfg.max_retries
        err_str = ""
        fatal_error_str = None
        attempt = 0

        while retries_remaining > 0 and fatal_error_str is None:
            if self._closed:
                return page, pool, None, err_str, None

            if page is None:
                try:
                    page = await pool.acquire()  # type: ignore[union-attr]
                except Exception:
                    retries_remaining -= 1
                    await asyncio.sleep(
                        _backoff_with_jitter(
                            self._cfg.retry_backoff_base**attempt,
                        )
                    )
                    attempt += 1
                    continue

            response, err, strategy = await self._attempt_single_fetch(
                page,
                request,
                asyncio_timeout,
                slot_has_proxy,
            )
            if response is not None:
                if slot_has_proxy:
                    try:
                        await ctx_pool.mark_proxy_success(slot_idx)
                    except Exception:
                        pass
                return page, pool, response, "", None

            err_str = err or "未知错误"
            strategy = strategy or RetryStrategy.TRANSIENT
            if strategy == RetryStrategy.FATAL:
                return page, pool, None, err_str, None

            page, pool, slot_has_proxy, should_retry, fatal = await self._dispatch_retry_strategy(
                strategy,
                err_str,
                ctx_pool,
                slot_idx,
                page,
                pool,
                attempt,
                slot_has_proxy,
            )
            if fatal:
                fatal_error_str = fatal
                break

            if strategy in (RetryStrategy.TRANSIENT, RetryStrategy.REPLACE_CONTEXT):
                retries_remaining -= 1

            if not should_retry:
                break

            attempt += 1

        return page, pool, None, err_str, fatal_error_str

    # ── 生命周期 ──────────────────────────────────────────────

    async def start(self, playwright) -> None:
        self._pw = playwright
        for i in range(self._K):
            bi = BrowserInstance(i, self._slots_per_browser, self._cfg, self._proxy_session, self._global_settings)
            await bi.start(playwright)
            self._browsers[i] = bi
            for slot_idx in range(self._slots_per_browser):
                await self._global_slots.put((bi, slot_idx))
        self._actor_task = asyncio.create_task(self.run(), name="BrowserPool.Actor")
        self._health_task = asyncio.create_task(self._health_check_loop(), name="BrowserPool.Health")
        _log.info("event=browserpool_start browsers=%d slots_per=%d", self._K, self._slots_per_browser)

    def stop_accepting(self) -> None:
        """标记 BrowserPool 为关闭中——阻止新 context 创建，级联到所有 BrowserInstance。

        对标 Java ExecutorService.shutdown()：停新任务，已有任务继续。
        SlotPool.replace() 检测 _closed 后静默跳过 slot 还原。
        """
        for bi in self._browsers.values():
            bi.stop_accepting()

    async def drain(self) -> None:
        """等待所有进行中的 _handle 任务完成。

        对标 Java ExecutorService.awaitTermination()。
        从 shutdown() 中提取为独立阶段，供引擎在 crawl_done 之前调用。
        """
        if self._handle_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._handle_tasks, return_exceptions=True),
                    timeout=HARD_CLEANUP_TIMEOUT,
                )
            except asyncio.TimeoutError:
                for t in self._handle_tasks:
                    t.cancel()
        self._handle_tasks.clear()

    async def shutdown(self) -> None:
        self._closed = True
        # 停 Actor
        if self._actor_task and not self._actor_task.done():
            self._actor_task.cancel()
            try:
                await self._actor_task
            except asyncio.CancelledError:
                pass
        # 停健康检查
        if self._health_task and not self._health_task.done():
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass
        # 等待进行中的 _handle 任务
        await self.drain()
        # 关闭所有 Browser
        for bi in self._browsers.values():
            await bi.close()
        self._browsers.clear()
        # 排空 mailbox
        drained = 0
        while not self._mailbox.empty():
            try:
                req, fut = self._mailbox.get_nowait()
                _safe_set_result(fut, FetchError("BrowserPool 关闭", "generic", True))
                drained += 1
            except asyncio.QueueEmpty:
                break
        if drained:
            _log.debug("event=browserpool_drain count=%d", drained)

    def get_health(self) -> Health:
        alive = sum(1 for bi in self._browsers.values() if not bi._closed)
        total = len(self._browsers)
        if self._closed:
            return Health("DOWN", "BrowserPool 已关闭", {"browsers": f"{alive}/{total}"})
        if alive == 0 and total > 0:
            return Health("DOWN", "所有 Browser 已死亡", {"browsers": f"0/{total}"})
        if alive < total:
            return Health("DEGRADED", f"{alive}/{total} browsers alive", {"browsers": f"{alive}/{total}"})
        return Health(
            "UP", f"{total} browsers alive", {"browsers": str(total), "slots_per_browser": self._slots_per_browser}
        )

    def should_pause_dequeuing(self) -> bool:
        """proxy_only 且全部代理 OPEN 时暂停出队。"""
        if not self._proxy_session or not self._path_switch:
            return False
        if self._path_switch.on_exhausted != "pause":
            return False
        return self._proxy_session.all_dead()  # type: ignore[no-any-return]

    @property
    def proxy_recovery_event(self):
        if self._proxy_session:
            return self._proxy_session.recovery_event
        return None

    async def _restart_browser_with_backoff(self, bi: BrowserInstance, idx: int) -> None:
        try:
            await asyncio.wait_for(
                bi.restart(self._pw),
                timeout=HARD_CLEANUP_TIMEOUT * BROWSER_RESTART_TIMEOUT_MULT,
            )
            bi._restart_failures = 0
        except Exception:
            bi._restart_failures += 1
            backoff = min(1.0 * (2**bi._restart_failures), 60.0)
            bi._restart_backoff_until = time.monotonic() + backoff
            _log.error(
                "event=browser_restart_failed id=%d failures=%d next_retry=%.0fs",
                idx,
                bi._restart_failures,
                backoff,
            )

    async def _health_check_loop(self) -> None:
        while not self._closed:
            try:
                await asyncio.sleep(CONTEXT_HEALTH_CHECK_INTERVAL)
            except asyncio.CancelledError:
                break
            if self._closed:
                break
            for i, bi in list(self._browsers.items()):
                if self._closed:
                    break
                if not bi.is_healthy:
                    if time.monotonic() < bi._restart_backoff_until:
                        continue
                    _log.info("event=browser_retry_restart id=%d", i)
                    await self._restart_browser_with_backoff(bi, i)
                    continue
                healthy = await bi.health_ping()
                if not healthy:
                    _log.warning("event=browser_unhealthy_restart id=%d", i)
                    await self._restart_browser_with_backoff(bi, i)

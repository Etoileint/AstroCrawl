"""测试替身 — 轻量级假实现，供 engine 集成测试使用。

每个 Fake 实现对应的 Protocol/接口，用内存数据结构替代 I/O。
无外部依赖（仅标准库 + asyncio），可直接用于 CI 无 DB/无浏览器环境。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Set

from astrocrawl.proxy._config import ParsedProxy, ProxyAuth, ProxyType


def _pp(url: str) -> ParsedProxy:
    """测试辅助：从代理 URL 字符串创建 ParsedProxy（ADR-0010 Phase 6.3 后使用）。

    注意：始终设 type=ProxyType.HTTP、默认 port=8080——不做 scheme 推断。
    所有当前调用方传入 http:// URL，行为正确。若未来需要 SOCKS5/HTTPS
    测试，改用 ProxyEndpointSpec.from_url() 或扩展此函数。
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return ParsedProxy(
        type=ProxyType.HTTP,
        host=parsed.hostname or "unknown",
        port=parsed.port or 8080,
        auth=ProxyAuth(username=parsed.username or "", password=parsed.password or ""),
        weight=1,
    )


# ═══════════════════════════════════════════════════════════════════════
# FakeBrowserPool — BrowserPool 的预编程替代
# ═══════════════════════════════════════════════════════════════════════


class FakeBrowserPool:
    """返回预编程 FetchResponse/FetchError 的假 BrowserPool。

    实现 BrowserPool 契约。默认行为: 所有 URL 返回成功。
    可通过 responses dict 注入特定 URL 的成功/失败。
    """

    def __init__(self, responses: Optional[Dict[str, Any]] = None):
        from astrocrawl.browser.browser_pool import FetchError, FetchResponse

        self._responses = responses or {}
        self._default_response = FetchResponse
        self._default_error = FetchError
        self.calls: List[str] = []  # URL list
        self._actor_task = None
        self._health_task = None
        self._closed = False

    async def send(self, request: Any) -> Any:
        from astrocrawl.browser.browser_pool import FetchResponse

        self.calls.append(request.url)
        if request.url in self._responses:
            return self._responses[request.url]
        return FetchResponse(url=request.url, html="<html></html>", status_code=200)

    async def start(self, playwright: Any = None) -> None:
        self._actor_task = asyncio.create_task(asyncio.sleep(0))
        self._health_task = asyncio.create_task(asyncio.sleep(0))

    async def shutdown(self) -> None:
        for t in (self._actor_task, self._health_task):
            if t and not t.done():
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass

    def get_health(self) -> Any:
        from astrocrawl.health import Health

        return Health("UP")

    def stop_accepting(self) -> None:
        self._closed = True

    def should_pause_dequeuing(self) -> bool:
        return False  # 测试中默认不暂停出队

    @property
    def proxy_recovery_event(self):
        return None

    async def drain(self) -> None:
        pass


# ═══════════════════════════════════════════════════════════════════════
# FakeWriter — AsyncJsonlWriter 的 no-op 替代
# ═══════════════════════════════════════════════════════════════════════


class FakeWriter:
    """记录写入调用的假 JSONL 写入器。不写磁盘，仅收集记录到内存列表。"""

    def __init__(self, **kwargs: Any):
        self.records: List[dict] = []
        self._started: bool = False
        self.finalized: bool = False

    async def start(self, resume: bool = False) -> None:
        self._started = True

    async def write_record(self, record: dict) -> None:
        self.records.append(record)

    async def aclose(self) -> None:
        self.finalized = True


# ═══════════════════════════════════════════════════════════════════════
# FakeContextPool + FakePage/FakePagePool — ContextPool 契约的实现
# ═══════════════════════════════════════════════════════════════════════


class FakeResponse:
    """模拟 Playwright Response。"""

    def __init__(self, status: int = 200, ok: bool = True, url: str = ""):
        self.status = status
        self.ok = ok
        self.url = url


class FakePage:
    """模拟 Playwright Page——支持配置化成功/失败行为。

    默认：url=""、html="<html></html>"、status=200、ok=True。
    可通过 goto_error 注入异常（如 PlaywrightTimeoutError）。
    """

    def __init__(
        self,
        url: str = "",
        html: str = "<html></html>",
        status: int = 200,
        ok: bool = True,
        goto_error: Optional[Exception] = None,
        goto_delay: float = 0.0,
    ):
        self._url = url
        self._html = html
        self._status = status
        self._ok = ok
        self._goto_error = goto_error
        self._goto_delay = goto_delay
        self._routes: list = []
        self._closed = False
        self._last_wait_until: str = ""

    @property
    def url(self) -> str:
        return self._url

    async def goto(
        self,
        url: str,
        timeout: int = 30000,
        wait_until: str = "domcontentloaded",
    ) -> FakeResponse:
        self._last_wait_until = wait_until
        if self._goto_delay > 0:
            await asyncio.sleep(self._goto_delay)
        if self._goto_error:
            raise self._goto_error
        self._url = url
        return FakeResponse(self._status, self._ok, url)

    async def route(self, pattern: str, handler) -> None:
        self._routes.append((pattern, handler))

    async def unroute_all(self) -> None:
        self._routes.clear()

    async def wait_for_load_state(
        self,
        state: str,
        timeout: int = 30000,
    ) -> None:
        pass

    async def content(self) -> str:
        return self._html

    def is_closed(self) -> bool:
        return self._closed

    async def close(self) -> None:
        self._closed = True

    async def evaluate(self, expression: str, arg: Any = None) -> Any:
        if hasattr(self, "_eval_result"):
            return self._eval_result
        return ""


class FakePagePool:
    """模拟 PagePool——可注入页面工厂以控制测试行为。"""

    def __init__(self, page_factory=None):
        self._pages: List[FakePage] = []
        self._factory = page_factory or (lambda: FakePage())
        self._broken: Set[int] = set()
        self._closed = False

    async def acquire(self) -> FakePage:
        for i, page in enumerate(self._pages):
            if i not in self._broken:
                return page
        page = self._factory()
        self._pages.append(page)
        return page

    async def remove_broken(self, page: Any) -> None:
        for i, p in enumerate(self._pages):
            if p is page:
                self._broken.add(i)
                return

    async def close_all(self) -> None:
        self._closed = True
        for page in self._pages:
            await page.close()
        self._pages.clear()

    def replace_all(self, factory=None) -> None:
        if factory:
            self._factory = factory
        self._pages.clear()
        self._broken.clear()


class _FakePathSwitch:
    """模拟 PathSwitch——默认直连模式。"""

    main_is_proxy = False
    fallback_is_proxy = False
    on_exhausted = "fail"


class FakeContextPool:
    """ContextPool 契约的槽位模拟实现。

    支持配置槽位数、代理 URL 列表、按槽位注入失败页面。
    """

    path_switch: _FakePathSwitch

    def __init__(
        self,
        slot_count: int = 2,
        proxy_urls: Optional[List[str]] = None,
    ):
        self._slot_count = slot_count
        self._proxy_urls = proxy_urls or []
        self._proxy_index: Dict[int, int] = {}  # slot → proxy url index
        self._page_pools: Dict[int, FakePagePool] = {}
        self._closed = False
        self.path_switch = _FakePathSwitch()

        for i in range(slot_count):
            self._page_pools[i] = FakePagePool()

    # ── 配置方法 ──────────────────────────────────────────────

    def set_proxy_for_slot(self, idx: int, proxy_index: int = 0) -> None:
        """为指定槽位绑定代理 URL（索引指向 _proxy_urls 列表）。"""
        self._proxy_index[idx] = proxy_index

    def set_page_factory_for_slot(
        self,
        idx: int,
        factory,
    ) -> None:
        """为指定槽位设置页面工厂（用于注入失败页面）。"""
        pool = self._page_pools.get(idx)
        if pool:
            pool.replace_all(factory)

    # ── ContextPool 契约 ───────────────────────────────────

    def get_proxy_for_slot(self, idx: int) -> Optional[str]:
        if idx in self._proxy_index:
            pi = self._proxy_index[idx]
            if 0 <= pi < len(self._proxy_urls):
                return self._proxy_urls[pi]
        return None

    def get_page_pool(self, idx: int) -> Optional[FakePagePool]:
        return self._page_pools.get(idx)

    def slot_is_valid(self, idx: int) -> bool:
        return idx in self._page_pools and not self._closed

    async def scoped_path(self, idx: int, target: str):
        class _Scope:
            async def __aenter__(self2):
                pass

            async def __aexit__(self2, *args):
                pass

        return _Scope()

    async def mark_proxy_success(self, idx: int) -> None:
        pass

    async def mark_proxy_failure(self, idx: int, weight: int = 1) -> None:
        pass

    async def rotate_proxy(self, idx: int) -> bool:
        if idx not in self._proxy_index or not self._proxy_urls:
            return False
        current = self._proxy_index[idx]
        if current + 1 < len(self._proxy_urls):
            self._proxy_index[idx] = current + 1
            return True
        return False

    async def replace_context(self, idx: int) -> None:
        pool = self._page_pools.get(idx)
        if pool:
            pool.replace_all()

    # ── 生命周期 ──────────────────────────────────────────────

    async def init(self) -> None:
        pass

    async def close_all(self) -> None:
        self._closed = True


# ═══════════════════════════════════════════════════════════════════════
# FakeBrowser / FakeBrowserContext — Playwright 替身 (ISP 最小接口)
# ═══════════════════════════════════════════════════════════════════════


class FakeBrowser:
    """模拟 playwright.Browser — 仅实现 new_context()。

    _raise_on_new_context: 可控注入异常以测试重试逻辑。
    last_context_kwargs: 记录最近一次 new_context 调用参数供断言。
    """

    def __init__(self):
        self._raise_on_new_context: Optional[Exception] = None
        self.last_context_kwargs: dict = {}
        self._contexts: List[FakeBrowserContext] = []

    async def new_context(self, **kwargs) -> "FakeBrowserContext":
        self.last_context_kwargs = kwargs
        if self._raise_on_new_context:
            raise self._raise_on_new_context
        ctx = FakeBrowserContext()
        self._contexts.append(ctx)
        return ctx


class FakeBrowserContext:
    """模拟 playwright.BrowserContext — 仅实现被测代码调用的方法。

    _raise_on_new_page: 可控注入异常以测试重试逻辑。
    """

    def __init__(self):
        self._raise_on_new_page: Optional[Exception] = None
        self._closed = False
        self._cookies: list = []
        self._pages: List[FakePage] = []

    async def new_page(self) -> FakePage:
        if self._raise_on_new_page:
            raise self._raise_on_new_page
        page = FakePage()
        self._pages.append(page)
        return page

    async def close(self) -> None:
        self._closed = True

    async def add_cookies(self, cookies: list) -> None:
        self._cookies.extend(cookies)

    async def clear_cookies(self) -> None:
        self._cookies.clear()

    async def cookies(self) -> list:
        return list(self._cookies)


# ═══════════════════════════════════════════════════════════════════════
# FakeProxyManager / FakeHealth — 代理管理替身
# ═══════════════════════════════════════════════════════════════════════


class _FakeHealth:
    """代理健康状态 — 可配置 is_available。"""

    def __init__(self, available: bool = True):
        self._available = available

    def is_available(self, url: str) -> bool:
        return self._available


class FakeProxyManager:
    """模拟 ProxySession — 可配置代理列表和健康状态。

    支持 prefer_different_than 过滤和 mark_success/failure 调用记录。
    ADR-0010 Phase 4: 适配 ProxySession 风格接口（is_available 直接方法，非 .health.）
    """

    def __init__(self, proxies: Optional[List[str]] = None):
        self._proxies = proxies or []
        self._available_flag = True
        self.health = _FakeHealth()
        self.success_calls: List[str] = []
        self.failure_calls: List[tuple] = []

    @property
    def proxies(self) -> List[str]:
        return list(self._proxies)

    def is_available(self, proxy_url: str) -> bool:
        """直接方法——ContextPool 调用 self._proxy_session.is_available()。"""
        return self._available_flag

    def healthy_proxies_in_pool(self) -> List[str]:
        """返回健康代理列表。"""
        return [p for p in self._proxies if self.is_available(p)]

    async def get_proxy(self, prefer_different_than: Optional[str] = None) -> Optional[str]:
        if not self._proxies:
            return None
        if prefer_different_than is not None:
            for p in self._proxies:
                if p != prefer_different_than:
                    return p
        return self._proxies[0]

    async def mark_success(self, url: str) -> None:
        self.success_calls.append(url)

    async def mark_failure(self, url: str, weight: int = 1) -> None:
        self.failure_calls.append((url, weight))


# ═══════════════════════════════════════════════════════════════════════
# _SignalSpy / _SpySignals — 共享信号间谍，供多测试文件复用
# ═══════════════════════════════════════════════════════════════════════


class _SignalSpy:
    """轻量信号间谍 — 记录所有 emit 调用。"""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def emit(self, *args: Any) -> None:
        self.calls.append(args)

    def connect(self, fn: Any) -> None:
        pass  # no-op for spy


class _SpySignals:
    """模拟 CrawlerSignals — 所有信号均为 _SignalSpy。

    每个属性显式定义以保证 mypy 类型推断。
    SIGNAL_NAMES 断言检测新增信号时的遗漏。
    """

    def __init__(self) -> None:
        self.layer_progress = _SignalSpy()
        self.stats_update = _SignalSpy()
        self.outcome_update = _SignalSpy()
        self.finished = _SignalSpy()
        self.error = _SignalSpy()
        self.pause_state = _SignalSpy()
        self.worker_state = _SignalSpy()
        self.rule_matched = _SignalSpy()
        self.rule_stats_updated = _SignalSpy()

        from astrocrawl.crawler.signals import SIGNAL_NAMES

        actual = {k for k, v in vars(self).items() if isinstance(v, _SignalSpy)}
        assert actual == SIGNAL_NAMES, (
            f"_SpySignals out of sync with SIGNAL_NAMES:\n"
            f"  Missing: {SIGNAL_NAMES - actual}\n"
            f"  Extra:   {actual - SIGNAL_NAMES}"
        )

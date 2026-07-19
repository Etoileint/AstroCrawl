from __future__ import annotations

import asyncio
import base64
import re
import time
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

import aiohttp
from aiohttp import ClientTimeout

from astrobasis import LogfmtLogger
from astrocrawl._constants import ROBOTS_FETCH_MAX_CONCURRENT, ROBOTS_FETCH_TIMEOUT, ROBOTS_MAX_SIZE
from astrocrawl.network._fetch import aiohttp_retry_fetch
from astrocrawl.utils.url import strip_www

_logger = LogfmtLogger("astrocrawl.robots")

if TYPE_CHECKING:
    from astrocrawl._path_strategy import PathSwitch
    from astrocrawl.network.throttling import DomainRateLimiter
    from astrocrawl.proxy import ProxySession


def _compile_robots_rule(rule: str) -> Tuple[Callable[[str], bool], int]:
    """将 robots.txt 规则编译为匹配函数和 specificity（原始路径长度）。

    返回 (matcher, specificity)：
    - matcher: 接受 URL path，返回是否匹配
    - specificity: 原始规则路径长度，用于"最具体规则优先"判定
    """
    rule = rule.strip()
    spec = len(rule)  # 原始路径长度 = specificity
    if not rule:
        return (lambda path: False), 0
    escaped = re.escape(rule)
    escaped = escaped.replace(r"\*", ".*")
    if escaped.endswith(r"\$"):
        escaped = escaped[:-2] + "$"
    else:
        escaped = escaped + ".*"
    try:
        compiled = re.compile(escaped)
    except re.error:
        return (lambda path: False), 0
    return (lambda path: compiled.fullmatch(path) is not None), spec


class AsyncRobotsParser:
    def __init__(self, user_agent: str):
        self.user_agent_lower = user_agent.lower()
        # 每条规则为 (matcher, specificity) 二元组
        self.disallow_rules: List[Tuple[Callable[[str], bool], int]] = []
        self.allow_rules: List[Tuple[Callable[[str], bool], int]] = []
        self.allow_all = True
        self.crawl_delay: Optional[float] = None
        self.sitemaps: List[str] = []
        self.fetch_status: str = "not_checked"  # ok / not_found / fetch_failed / http_NNN

    @classmethod
    async def from_url(
        cls,
        url: str,
        user_agent: str,
        session: aiohttp.ClientSession,
        timeout: int = ROBOTS_FETCH_TIMEOUT,
        allow_all_on_error: bool = True,
        proxy: Optional[str] = None,
    ) -> "AsyncRobotsParser":
        parser = cls(user_agent)
        try:
            async with session.get(url, timeout=ClientTimeout(total=timeout), proxy=proxy) as resp:
                if resp.status == 200:
                    raw = await resp.content.read(ROBOTS_MAX_SIZE + 1)
                    if len(raw) > ROBOTS_MAX_SIZE:
                        _logger.warning(
                            "robots_txt_oversize",
                            url=url,
                            max_bytes=ROBOTS_MAX_SIZE,
                        )
                        raw = raw[:ROBOTS_MAX_SIZE]
                    text = raw.decode("utf-8", errors="replace")
                    parser.parse(text)
                    parser.fetch_status = "ok"
                else:
                    parser.allow_all = True
                    parser.fetch_status = f"http_{resp.status}"
        except Exception:
            if allow_all_on_error:
                parser.allow_all = True
                parser.fetch_status = "fetch_failed"
            else:
                raise
        return parser

    def parse(self, content: str) -> None:
        groups: Dict[str, dict] = {}
        current_ua = None
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.lower().startswith("user-agent:"):
                ua_value = line.split(":", 1)[1].strip().lower()
                current_ua = ua_value
                if ua_value not in groups:
                    groups[ua_value] = {"disallow": [], "allow": [], "delay": None}
                continue
            if current_ua is None:
                continue
            lower = line.lower()
            group = groups[current_ua]
            if lower.startswith("disallow:"):
                path = line.split(":", 1)[1].strip()
                if path:
                    group["disallow"].append(_compile_robots_rule(path))
            elif lower.startswith("allow:"):
                path = line.split(":", 1)[1].strip()
                if path:
                    group["allow"].append(_compile_robots_rule(path))
            elif lower.startswith("crawl-delay:"):
                try:
                    delay = float(line.split(":", 1)[1].strip())
                    if 0.0 < delay <= 3600.0:
                        group["delay"] = delay
                except (ValueError, OverflowError):
                    pass
            elif lower.startswith("request-rate:"):
                try:
                    rate_str = line.split(":", 1)[1].strip()
                    req, sec = rate_str.split("/", 1)
                    req_num = float(req.strip())
                    sec_num = float(sec.strip())
                    if req_num > 0 and sec_num > 0:
                        delay = sec_num / req_num
                        if 0.0 < delay <= 3600.0:
                            group["delay"] = delay
                except (ValueError, ZeroDivisionError, OverflowError):
                    pass
            elif lower.startswith("sitemap:"):
                sitemap_url = line.split(":", 1)[1].strip()
                if sitemap_url:
                    self.sitemaps.append(sitemap_url)
        selected = groups.get(self.user_agent_lower)
        if selected is None:
            selected = groups.get("*")
        if selected is None:
            self.disallow_rules = []
            self.allow_rules = []
            self.allow_all = True
            return
        self.disallow_rules = selected["disallow"]
        self.allow_rules = selected["allow"]
        self.crawl_delay = selected["delay"]
        self.allow_all = False

    def can_fetch(self, url: str) -> bool:
        if self.allow_all:
            return True
        parsed = urlparse(url)
        path = unquote(parsed.path) or "/"
        # robots.txt 规范 (RFC 9309 §2.2.2)："最具体的匹配规则优先"。
        # 不能简单地 Allow 优先——Disallow: /foo/bar (specificity 8) 比
        # Allow: /foo (specificity 4) 更具体，即使 Allow 在前也要以 Disallow 为准。
        # 必须收集全部匹配规则，选出 specificity（原始路径长度）最大者。
        best_spec = -1
        best_kind = None
        for matcher, spec in self.allow_rules:
            if matcher(path) and spec > best_spec:
                best_spec = spec
                best_kind = "allow"
        for matcher, spec in self.disallow_rules:
            if matcher(path) and spec > best_spec:
                best_spec = spec
                best_kind = "disallow"
        if best_kind == "allow":
            return True
        if best_kind == "disallow":
            return False
        return True


class RobotsCache:
    def __init__(
        self,
        user_agent: str,
        session: aiohttp.ClientSession,
        ttl: int = 3600,
        max_size: int = 1000,
        domain_rate_limiter: Optional["DomainRateLimiter"] = None,
        respect_crawl_delay: bool = False,
        proxy_session: Optional["ProxySession"] = None,
        path_switch: Optional["PathSwitch"] = None,
        *,
        max_retries: int = 3,
        retry_backoff_base: float = 2.0,
        custom_headers: Optional[List[str]] = None,
        auth_basic_user: str = "",
        auth_basic_pass: str = "",
        auth_bearer_token: str = "",
    ):
        self._ua = user_agent
        self._session = session
        self._ttl = ttl
        self._max_size = max_size
        self._lock = asyncio.Lock()
        self._cache: Dict[str, Tuple[AsyncRobotsParser, float]] = {}
        self._tasks: Dict[str, asyncio.Task] = {}
        self._fetch_semaphore = asyncio.Semaphore(ROBOTS_FETCH_MAX_CONCURRENT)
        self._log = LogfmtLogger("astrocrawl.robots")
        self.domain_rate_limiter = domain_rate_limiter
        self.respect_crawl_delay = respect_crawl_delay
        self._fetch_status: Dict[str, str] = {}
        self._status_lock = asyncio.Lock()
        self._proxy_session = proxy_session
        self._path_switch = path_switch
        self._max_retries = max_retries
        self._retry_backoff_base = retry_backoff_base
        self._custom_headers = custom_headers
        self._auth_basic_user = auth_basic_user
        self._auth_basic_pass = auth_basic_pass
        self._auth_bearer_token = auth_bearer_token

    def _build_headers(self) -> dict[str, str]:
        headers = {"User-Agent": self._ua}
        if self._auth_bearer_token:
            headers["Authorization"] = f"Bearer {self._auth_bearer_token}"
        elif self._auth_basic_user and self._auth_basic_pass:
            creds = base64.b64encode(f"{self._auth_basic_user}:{self._auth_basic_pass}".encode()).decode()
            headers["Authorization"] = f"Basic {creds}"
        for hdr in self._custom_headers or []:
            if ":" in hdr:
                k, v = hdr.split(":", 1)
                headers[k.strip()] = v.strip()
        return headers

    async def is_allowed(self, url: str) -> bool:
        p = urlparse(url)
        origin = f"{p.scheme}://{p.netloc}"
        try:
            parser = await self._get_or_fetch_robots(origin)
            return parser.can_fetch(url)
        except Exception:
            return True

    async def _get_or_fetch_robots(self, origin: str) -> AsyncRobotsParser:
        """获取 origin 的 robots.txt 解析结果。

        统一 is_allowed 和 get_sitemaps 的获取逻辑：缓存命中直接返回，
        缓存过期则通过 _tasks 去重机制避免同一 origin 的并发重复请求。
        """
        async with self._lock:
            cached = self._cache.get(origin)
            if cached:
                parser, expire = cached
                if time.monotonic() < expire:
                    return parser
                del self._cache[origin]
            task = self._tasks.get(origin)
            if task is None:
                task = asyncio.create_task(self._fetch_robots(origin))
                self._tasks[origin] = task
        try:
            return await task  # type: ignore[no-any-return]
        except Exception:
            async with self._lock:
                self._tasks.pop(origin, None)
            raise

    async def _fetch_robots(self, origin: str) -> AsyncRobotsParser:
        async with self._fetch_semaphore:
            robots_url = f"{origin}/robots.txt"
            result = await aiohttp_retry_fetch(
                url=robots_url,
                http_session=self._session,
                proxy_session=self._proxy_session,
                path_switch=self._path_switch,
                timeout=ROBOTS_FETCH_TIMEOUT,
                max_retries=self._max_retries,
                retry_backoff_base=self._retry_backoff_base,
                headers=self._build_headers(),
                max_bytes=ROBOTS_MAX_SIZE,
            )
            parser = AsyncRobotsParser(self._ua)
            if result.content is not None:
                parser.parse(result.content.decode("utf-8", errors="replace"))
                parser.fetch_status = "ok"
            elif result.http_status > 0:
                parser.allow_all = True
                parser.fetch_status = f"http_{result.http_status}"
            else:
                parser.allow_all = True
                parser.fetch_status = "fetch_failed"
                self._log.debug("robots_fetch_failed", origin=origin, fallback="allow_all")
        async with self._lock:
            expire = time.monotonic() + self._ttl
            self._cache[origin] = (parser, expire)
            if len(self._cache) > self._max_size:
                oldest_origin = min(self._cache.keys(), key=lambda k: self._cache[k][1])
                del self._cache[oldest_origin]
            self._tasks.pop(origin, None)
        async with self._status_lock:
            self._fetch_status[origin] = parser.fetch_status
        if self.respect_crawl_delay and self.domain_rate_limiter and parser.crawl_delay is not None:
            domain = strip_www(urlparse(origin).netloc)
            await self.domain_rate_limiter.set_crawl_delay(domain, parser.crawl_delay)
        return parser

    async def get_sitemaps(self, origin: str) -> List[str]:
        try:
            parser = await self._get_or_fetch_robots(origin)
            return parser.sitemaps
        except Exception:
            return []

    async def get_fetch_status(self, origin: str) -> str:
        """返回该源站 robots.txt 的获取状态。"""
        async with self._status_lock:
            return self._fetch_status.get(origin, "not_checked")

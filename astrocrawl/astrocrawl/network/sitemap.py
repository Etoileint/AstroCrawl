"""Sitemap 解析与全生命周期发现模块。

SitemapParser  — 纯同步 XML 解析，处理 gzip、索引、URL 集，提取完整元数据。
SitemapDiscovery — 异步网络层，管理种子 + 动态源站的 sitemap 发现，
                   将 URL 按层级深度入队。"""

from __future__ import annotations

import asyncio
import base64
import math
import re
import zlib
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Awaitable, Callable, ClassVar, List, Optional, Set, Tuple, Union
from typing import Protocol as _Protocol
from urllib.parse import urljoin

from astrocrawl._constants import SITEMAP_FETCH_TIMEOUT, SITEMAP_MAX_CONTENT_SIZE, SITEMAP_MAX_DECOMPRESSED
from astrocrawl._types import DropReason
from astrocrawl.network._fetch import aiohttp_retry_fetch
from astrocrawl.utils.url import is_valid_http_url, normalize_url

if TYPE_CHECKING:
    import aiohttp
    from bs4 import BeautifulSoup

    from astrobase import LogfmtLogger
    from astrocrawl._path_strategy import PathSwitch
    from astrocrawl.crawler.outcomes import CrawlStats
    from astrocrawl.network.robots import RobotsCache
    from astrocrawl.proxy import ProxySession


class SitemapConfig(_Protocol):
    """SitemapDiscovery 需要的配置字段（ISP 窄接口）。"""

    sitemap_fetch_concurrency: int
    sitemap_additional_paths: Tuple[str, ...]
    sitemap_max_recursion: int
    sitemap_max_urls: int
    tracking_params: frozenset
    max_retries: int
    retry_backoff_base: float
    user_agent: str
    custom_headers: Tuple[str, ...]
    auth_basic_user: str
    auth_basic_pass: str
    auth_bearer_token: str


_GZIP_MAGIC = b"\x1f\x8b"

# 日期格式解析器（按常见程度排序）
#  支持：ISO 8601 日期/时间/时区 | 小数秒 | 无冒号时区偏移
_ISO_FORMATS: Tuple[Tuple[str, re.Pattern], ...] = (
    (
        "iso_tz",
        re.compile(
            r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(\.\d+)?"
            r"([+-]\d{2}:?\d{2}|Z)?$"
        ),
    ),
    ("iso_date", re.compile(r"^\d{4}-\d{2}-\d{2}$")),
)


# ── Data ──────────────────────────────────────────────────────────────────────


@dataclass
class SitemapEntry:
    loc: str
    lastmod: Optional[datetime] = None
    changefreq: Optional[str] = None
    priority: Optional[float] = None


# ── Parser ────────────────────────────────────────────────────────────────────


class SitemapParser:
    _FREQ_VALUES: ClassVar[frozenset] = frozenset(
        {
            "always",
            "hourly",
            "daily",
            "weekly",
            "monthly",
            "yearly",
            "never",
        }
    )

    @staticmethod
    def _decompress_gzip(data: bytes) -> bytes:
        d = zlib.decompressobj(wbits=zlib.MAX_WBITS | 16)
        return d.decompress(data, max_length=SITEMAP_MAX_DECOMPRESSED)

    @staticmethod
    def _parse_date(text: Optional[str]) -> Optional[datetime]:
        if not text:
            return None
        text = text.strip()

        # 1) ISO 8601（99% 实际情况，最快路径）
        for _fmt_name, pattern in _ISO_FORMATS:
            m = pattern.match(text)
            if m:
                try:
                    if _fmt_name == "iso_tz":
                        date_str = m.group(1)
                        time_str = m.group(2)
                        frac_str = m.group(3)  # 可选小数秒，含前导 '.'
                        if frac_str:
                            time_str += frac_str
                            dt = datetime.strptime(f"{date_str}T{time_str}", "%Y-%m-%dT%H:%M:%S.%f")
                        else:
                            dt = datetime.strptime(f"{date_str}T{time_str}", "%Y-%m-%dT%H:%M:%S")
                        tz_str = m.group(4)
                        if tz_str and tz_str != "Z":
                            from datetime import timedelta
                            from datetime import timezone as _tz

                            sign = 1 if tz_str[0] == "+" else -1
                            tz_body = tz_str[1:]
                            if ":" in tz_body:
                                h_str, m_str = tz_body.split(":", 1)
                            else:
                                h_str, m_str = tz_body[:2], tz_body[2:]
                            offset = sign * (int(h_str) * 60 + int(m_str))
                            dt = dt.replace(tzinfo=_tz(timedelta(minutes=offset)))
                        elif tz_str == "Z":
                            dt = dt.replace(tzinfo=timezone.utc)
                        return dt
                    else:  # iso_date
                        return datetime.strptime(text, "%Y-%m-%d")
                except (ValueError, OverflowError):
                    pass

        # 2) RFC 2822/1123 catch-all（无 gatekeeper — 不通过正则预筛）
        from email.utils import parsedate_to_datetime

        try:
            return parsedate_to_datetime(text)
        except (ValueError, OverflowError):
            pass

        return None

    @staticmethod
    def _parse_priority(text: Optional[str]) -> Optional[float]:
        if not text:
            return None
        text = text.strip()
        try:
            p = float(text)
        except ValueError:
            return None
        if math.isnan(p):
            return None
        if p < 0.0:
            return 0.0
        if p > 1.0:
            return 1.0
        return p

    @staticmethod
    def _parse_freq(text: Optional[str]) -> Optional[str]:
        if not text:
            return None
        v = text.strip().lower()
        return v if v in SitemapParser._FREQ_VALUES else None

    @staticmethod
    def is_index(content: Union[str, bytes]) -> bool:
        doc_type, _ = SitemapParser._parse_sitemap(content)
        return doc_type == "index"

    @staticmethod
    def extract_index_urls(content: Union[str, bytes]) -> List[str]:
        doc_type, entries = SitemapParser._parse_sitemap(content)
        if doc_type == "index":
            return [e.loc for e in entries if e.loc]
        return []

    @staticmethod
    def parse(content: Union[str, bytes]) -> List[SitemapEntry]:
        _, entries = SitemapParser._parse_sitemap(content)
        return entries

    @staticmethod
    def _make_soup(content: Union[str, bytes]) -> "BeautifulSoup":
        from bs4 import BeautifulSoup

        if isinstance(content, bytes) and content[:2] == _GZIP_MAGIC:
            try:
                content = SitemapParser._decompress_gzip(content)
            except (zlib.error, OSError):
                pass  # 回退：当作原始 bytes 解析，由上层 catch 处理

        try:
            return BeautifulSoup(content, "lxml-xml")
        except Exception:
            return BeautifulSoup(content, "html.parser")

    @staticmethod
    def _parse_sitemap(content: Union[str, bytes]) -> "Tuple[str, List[SitemapEntry]]":
        """单次 XML 解析，通过根元素名判定文档类型并提取数据。

        _make_soup 内部处理 gzip 解压——分类与解析在同一层，消除守门员反模式。
        返回 ("index"|"urlset", entries)，三个公开方法均委托到此。
        """
        try:
            soup = SitemapParser._make_soup(content)
        except Exception:
            return ("urlset", [])

        # 按 Sitemap 协议通过根元素判定文档类型（非子元素推断）
        if soup.find(re.compile(r"^sitemapindex$")) is not None:
            entries: List[SitemapEntry] = []
            for sm in soup.find_all(re.compile(r"^sitemap$")):
                loc = sm.find("loc")
                if not loc or not loc.text.strip():
                    continue
                lastmod_el = sm.find("lastmod")
                entries.append(
                    SitemapEntry(
                        loc=loc.text.strip(),
                        lastmod=SitemapParser._parse_date(lastmod_el.text if lastmod_el else None),
                    )
                )
            return ("index", entries)

        entries = []
        for url_tag in soup.find_all(re.compile(r"^url$")):
            loc = url_tag.find("loc")
            if not loc or not loc.text.strip():
                continue
            lastmod_el = url_tag.find("lastmod")
            freq_el = url_tag.find("changefreq")
            prio_el = url_tag.find("priority")
            entries.append(
                SitemapEntry(
                    loc=loc.text.strip(),
                    lastmod=SitemapParser._parse_date(lastmod_el.text if lastmod_el else None),
                    changefreq=SitemapParser._parse_freq(freq_el.text if freq_el else None),
                    priority=SitemapParser._parse_priority(prio_el.text if prio_el else None),
                )
            )
        return ("urlset", entries)


# ── Discovery Manager ─────────────────────────────────────────────────────────


class SitemapDiscovery:
    def __init__(
        self,
        http_session: aiohttp.ClientSession,
        robots_cache: Optional["RobotsCache"],
        stats: "CrawlStats",
        enqueue_callback: Callable[[str, int], Awaitable[bool]],
        stop_event: asyncio.Event,
        config: "SitemapConfig",
        log: LogfmtLogger,
        proxy_session: Optional["ProxySession"] = None,
        path_switch: Optional["PathSwitch"] = None,
    ):
        self._http_session = http_session
        self._robots_cache = robots_cache
        self._stats = stats
        self._enqueue_callback = enqueue_callback
        self._stop_event = stop_event
        self._cfg = config
        self._log = log
        self._proxy_session = proxy_session
        self._path_switch = path_switch

        self._max_retries = config.max_retries
        self._retry_backoff_base = config.retry_backoff_base
        self._user_agent = config.user_agent
        self._custom_headers = list(config.custom_headers)
        self._auth_basic_user = config.auth_basic_user
        self._auth_basic_pass = config.auth_basic_pass
        self._auth_bearer_token = config.auth_bearer_token

        self._discovery_done = asyncio.Event()
        self._discovery_done.set()

        self._seen_origins: Set[str] = set()
        self._seen_sitemap_urls: Set[str] = set()
        self._discovery_lock = asyncio.Lock()
        self._fetch_sem = asyncio.Semaphore(config.sitemap_fetch_concurrency)

        self._pending_origins = 0
        self._completion_lock = asyncio.Lock()
        self._pending_tasks: Set[asyncio.Task] = set()

    @property
    def discovery_done(self) -> asyncio.Event:
        return self._discovery_done

    # ── Headers ───────────────────────────────────────────────────────────

    def _build_headers(self) -> dict[str, str]:
        headers = {"User-Agent": self._user_agent}
        if self._auth_bearer_token:
            headers["Authorization"] = f"Bearer {self._auth_bearer_token}"
        elif self._auth_basic_user and self._auth_basic_pass:
            creds = base64.b64encode(f"{self._auth_basic_user}:{self._auth_basic_pass}".encode()).decode()
            headers["Authorization"] = f"Basic {creds}"
        for hdr in self._custom_headers:
            if ":" in hdr:
                k, v = hdr.split(":", 1)
                headers[k.strip()] = v.strip()
        return headers

    # ── 公共接口 ──────────────────────────────────────────────────────────

    async def start_discovery(
        self,
        origins: Set[str],
        enqueue_depth: int,
    ) -> None:
        if not origins:
            return
        async with self._discovery_lock:
            for o in origins:
                self._seen_origins.add(o)
        async with self._completion_lock:
            self._pending_origins += len(origins)
            self._discovery_done.clear()

        async def _run_guarded(og: str):
            async with self._pending_guard():
                await self._discover_origin(og, enqueue_depth)

        tasks = [asyncio.create_task(_run_guarded(o)) for o in origins]
        await self._gather_logged(tasks, "Sitemap 种子发现")

    @asynccontextmanager
    async def _pending_guard(self):
        """保证退出时 _pending_origins 递减，消除手动标志位。"""
        try:
            yield
        finally:
            await self._decrement_pending()

    def discover_origin_if_new(self, origin: str, enqueue_depth: int) -> None:
        """动态触发：首次遇到 origin 时由引擎调用。"""
        if self._stop_event.is_set():
            return

        async def _guard_and_launch():
            async with self._discovery_lock:
                if origin in self._seen_origins:
                    return
                self._seen_origins.add(origin)

            async with self._completion_lock:
                self._pending_origins += 1
                self._discovery_done.clear()

            async with self._pending_guard():
                await self._stats.increment_discovery_total_origins()
                await self._discover_origin(origin, enqueue_depth)

        task = asyncio.create_task(_guard_and_launch())
        self._pending_tasks.add(task)
        task.add_done_callback(lambda t: self._pending_tasks.discard(t))

    # ── 内部 ──────────────────────────────────────────────────────────────

    async def _discover_origin(
        self,
        origin: str,
        enqueue_depth: int,
    ) -> None:
        candidate_urls: List[str] = []

        # 1) robots.txt 中的 Sitemap 指令
        robots_sitemaps = await self._get_robots_sitemaps(origin)
        for sm_url in robots_sitemaps:
            if is_valid_http_url(sm_url):
                candidate_urls.append(sm_url)
            else:
                candidate_urls.append(urljoin(origin, sm_url))

        # 2) 默认路径
        for path in self._cfg.sitemap_additional_paths:
            candidate_urls.append(f"{origin}{path}")

        # 3) 并行处理所有候选（本地计数器隔离 per-origin 统计）
        counter: List[int] = [0]
        tasks = [
            asyncio.create_task(
                self._fetch_and_process_sitemap(url, sitemap_depth=0, enqueue_depth=enqueue_depth, counter=counter)
            )
            for url in candidate_urls
        ]
        await self._gather_logged(tasks, "Sitemap 候选获取")

        # 4) 记录 per-origin 统计
        try:
            await self._stats.inc_discovery_robots_done()
            await self._stats.inc_discovery_sitemap_done()

            robots_status = "—"
            if self._robots_cache is not None:
                robots_status = await self._robots_cache.get_fetch_status(origin)
            await self._stats.record_origin_discovery(
                origin,
                robots_status,
                counter[0],
            )
            self._log.debug(
                "sitemap_origin_done",
                origin=origin,
                robots=robots_status,
                urls=counter[0],
            )
        except Exception:
            self._log.warning("sitemap_per_origin_stats_failed", origin=origin, exc_info=True)

    async def _fetch_and_process_sitemap(
        self,
        url: str,
        sitemap_depth: int,
        enqueue_depth: int,
        counter: Optional[List[int]] = None,
    ) -> None:
        # 去重 + 递归深度限制
        async with self._discovery_lock:
            if url in self._seen_sitemap_urls:
                return
            self._seen_sitemap_urls.add(url)

        if sitemap_depth > self._cfg.sitemap_max_recursion or self._stop_event.is_set():
            return

        content = await self._fetch_sitemap_content(url)
        if content is None:
            return

        try:
            doc_type, entries = SitemapParser._parse_sitemap(content)
        except Exception:
            self._log.warning("sitemap_parse_failed", url=url)
            return

        if doc_type == "index":
            child_urls = [e.loc for e in entries if e.loc]
            if child_urls:
                tasks = [
                    asyncio.create_task(
                        self._fetch_and_process_sitemap(
                            child_url,
                            sitemap_depth + 1,
                            enqueue_depth,
                            counter,
                        )
                    )
                    for child_url in child_urls
                ]
                await self._gather_logged(tasks, "Sitemap 索引递归")
        else:
            for entry in entries:
                if self._stop_event.is_set():
                    return
                try:
                    norm = normalize_url(entry.loc, self._cfg)
                except Exception:
                    continue
                if not norm:
                    continue
                if not is_valid_http_url(norm):
                    continue

                async with self._discovery_lock:
                    discovered = await self._stats.get_sitemap_discovered()
                    if discovered >= self._cfg.sitemap_max_urls:
                        await self._stats.record_drop(DropReason.QUEUE_FULL)
                        return

                ok = await self._enqueue_callback(norm, enqueue_depth)
                if ok and counter is not None:
                    counter[0] += 1

    async def _fetch_sitemap_content(self, url: str) -> Optional[bytes]:
        async with self._fetch_sem:
            result = await aiohttp_retry_fetch(
                url=url,
                http_session=self._http_session,
                proxy_session=self._proxy_session,
                path_switch=self._path_switch,
                timeout=SITEMAP_FETCH_TIMEOUT,
                max_retries=self._max_retries,
                retry_backoff_base=self._retry_backoff_base,
                headers=self._build_headers(),
                max_bytes=SITEMAP_MAX_CONTENT_SIZE,
            )
            if result.content is not None:
                await self._stats.record_sitemap_fetch(True)
                return result.content
            else:
                await self._stats.record_sitemap_fetch(False)
                return None

    async def _get_robots_sitemaps(self, origin: str) -> List[str]:
        if self._robots_cache is None:
            self._log.debug("sitemap_robots_cache_missing", origin=origin)
            return []
        try:
            return await self._robots_cache.get_sitemaps(origin)
        except Exception:
            self._log.debug("sitemap_robots_error", origin=origin, exc_info=True)
            return []

    async def _gather_logged(self, tasks: List[asyncio.Task], label: str) -> None:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, BaseException):
                self._log.warning("sitemap_task_error", label=label, error=r)

    async def _decrement_pending(self) -> None:
        # asyncio.shield 防止已在传播中的 CancelledError (从
        # _discover_origin 的 finally 调用时) 中断锁获取，确保
        # _pending_origins 计数正确归零。
        await asyncio.shield(self._decrement_pending_impl())

    async def _decrement_pending_impl(self) -> None:
        async with self._completion_lock:
            self._pending_origins -= 1
            if self._pending_origins <= 0:
                self._discovery_done.set()

    # ── 生命周期 ──────────────────────────────────────────────

    async def aclose(self) -> None:
        """取消所有未完成的发现任务，等待完成（幂等）。

        实现 AsyncCloseable 协议。必须在 http_session.close() 之前调用。
        """
        if not self._pending_tasks:
            return
        for t in list(self._pending_tasks):
            if not t.done():
                t.cancel()
        await asyncio.gather(*self._pending_tasks, return_exceptions=True)

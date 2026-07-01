"""零依赖共享内核 — 纯数据/纯策略对象，位于依赖图最底层。

对标: aiohttp typedefs.py (包根专用类型模块)、DDD Shared Kernel (跨层共享纯类型在最底层)。

模块级零内部导入 (from astrocrawl. 不在模块顶层出现)。
RuleSnapshot.default_only() 体内的延迟导入用于打破与 rules._schema 的循环依赖。
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Protocol, Tuple, runtime_checkable

if TYPE_CHECKING:
    from astrocrawl.rules._schema import RuleSchema


# ── 抓取错误子分类 ──────────────────────────────────────────────


class FetchErrorCategory(Enum):
    DNS = "dns"
    SSL = "ssl"
    TIMEOUT = "timeout"
    HTTP_4XX = "http_4xx"
    HTTP_5XX = "http_5xx"
    PROXY = "proxy"
    PROXY_EXHAUSTED = "proxy_exhausted"
    CONTEXT_FAILURE = "context_failure"
    CONNECTION_REFUSED = "connection_refused"
    CONNECTION_RESET = "connection_reset"
    TARGET_CLOSED = "target_closed"
    ABORTED = "aborted"
    DOWNLOAD = "download"
    TOO_MANY_REDIRECTS = "too_many_redirects"
    GENERIC = "generic"


ERROR_PATTERNS: Dict[FetchErrorCategory, List[str]] = {
    # 此映射是错误分类的唯一真源（SSOT）。
    # classify_fetch_error() 服务于统计报告，_retry.py
    # 通过 classify_fetch_error() 获取类别后映射到 RetryStrategy。
    # 新增错误模式只需在此处添加。
    FetchErrorCategory.DNS: [
        "net::ERR_NAME_NOT_RESOLVED",
    ],
    FetchErrorCategory.SSL: [
        "net::ERR_SSL_PROTOCOL_ERROR",
        "net::ERR_CERT_AUTHORITY_INVALID",
    ],
    FetchErrorCategory.TIMEOUT: [
        "net::ERR_TIMED_OUT",
        "Timeout ",  # Playwright: "Timeout 20000ms exceeded."
        "Navigation timeout",  # Playwright: "Navigation timeout of 30000 ms exceeded"
    ],
    FetchErrorCategory.CONNECTION_REFUSED: [
        "net::ERR_CONNECTION_REFUSED",
    ],
    FetchErrorCategory.CONNECTION_RESET: [
        "net::ERR_CONNECTION_RESET",
        "net::ERR_CONNECTION_CLOSED",
    ],
    FetchErrorCategory.TARGET_CLOSED: [
        "Target closed",
        "Session closed",
        "Page crashed",
        "Browser closed",
        "has been closed",  # 覆盖 "Target has been closed" 等变体
        "Execution context was destroyed",
        "Protocol error",  # 宽泛——可能匹配非 TARGET_CLOSED 的 CDP 错误
        "Unable to find",  # 宽泛——可能匹配元素选择器错误
    ],
    FetchErrorCategory.CONTEXT_FAILURE: [
        "上下文恢复失败",
        "上下文槽位修复失败",
    ],
    FetchErrorCategory.ABORTED: [
        "net::ERR_ABORTED",
    ],
    FetchErrorCategory.PROXY: [
        "net::ERR_TUNNEL_CONNECTION_FAILED",
        "net::ERR_PROXY_CONNECTION_FAILED",
        "net::ERR_PROXY_CERTIFICATE_INVALID",
    ],
    FetchErrorCategory.PROXY_EXHAUSTED: [
        "代理轮换失败",
    ],
    FetchErrorCategory.HTTP_4XX: [
        "net::HTTP_403",
        "net::HTTP_404",
        "net::HTTP_410",
        "net::HTTP_451",
        "net::HTTP_429",  # 限流——策略层按 TRANSIENT 处理
    ],
    FetchErrorCategory.HTTP_5XX: [
        "net::HTTP_502",
        "net::HTTP_503",
        "net::HTTP_500",
    ],
    FetchErrorCategory.DOWNLOAD: [
        "Download is starting",
    ],
    FetchErrorCategory.TOO_MANY_REDIRECTS: [
        "net::ERR_TOO_MANY_REDIRECTS",
    ],
    FetchErrorCategory.GENERIC: [],
}


def classify_fetch_error(error_str: str) -> FetchErrorCategory:
    """根据错误字符串将其分类到 FetchErrorCategory (纯函数, SSOT)。"""
    if not error_str:
        return FetchErrorCategory.GENERIC
    for cat, patterns in ERROR_PATTERNS.items():
        for pat in patterns:
            if pat in error_str:
                return cat
    return FetchErrorCategory.GENERIC


# ── 链接/URL 过滤丢弃原因 ───────────────────────────────────────


class DropReason(Enum):
    EXCLUDE_PATTERN = "exclude_pattern"
    NOFOLLOW_LINK = "nofollow_link"
    CROSS_DOMAIN = "cross_domain"
    INVALID_URL = "invalid_url"
    QUEUE_FULL = "queue_full"
    ALREADY_VISITED = "already_visited"
    SKIP_DUPLICATE_LINKS = "skip_duplicate_links"
    SAME_PAGE_DUP = "same_page_dup"
    DOWNLOAD_CANDIDATE = "download_candidate"


# ── 队列入队结果 ───────────────────────────────────────────────────


class EnqueueResult(Enum):
    """`push_to_queue_single` 的三态返回 — 消除 bool 信息丢失。"""

    ENQUEUED = "enqueued"
    QUEUE_FULL = "queue_full"
    DUPLICATE = "duplicate"


# ── 解析规则引擎共享类型 ─────────────────────────────────────

DEFAULT_EXTRACTION_TYPE = "default"
DOWNLOAD_EXTRACTION_TYPE = "download"  # ADR-0002 对接预留


class RuleMatchCache:
    """域名级 LRU 匹配缓存 + 惰性 TTL。对标 functools.lru_cache。

    缓存仅用于 domain_all / any scope 的匹配结果——有 url_pattern 的 scope
    同域不同路径可能匹配不同规则。
    maxsize 和 ttl 不可配置（应用内缓存参数由开发者设定）。
    """

    def __init__(self, maxsize: int = 10000, ttl: float = 3600) -> None:
        self._cache: OrderedDict[str, tuple[str, float]] = OrderedDict()  # domain -> (rule_name, timestamp)
        self._maxsize = maxsize
        self._ttl = ttl

    def get(self, domain: str) -> str | None:
        entry = self._cache.get(domain)
        if entry is None:
            return None
        rule_name, ts = entry
        # 惰性 TTL
        if time.time() - ts > self._ttl:
            del self._cache[domain]
            return None
        # LRU: move to end (most recently used)
        self._cache.move_to_end(domain)
        return rule_name

    def set(self, domain: str, rule_name: str) -> None:
        if domain in self._cache:
            self._cache.move_to_end(domain)
        self._cache[domain] = (rule_name, time.time())
        # LRU 淘汰最旧
        while len(self._cache) > self._maxsize:
            self._cache.popitem(last=False)

    def __len__(self) -> int:
        return len(self._cache)


@dataclass(frozen=True)
class RuleSnapshot:
    """所有已启用规则的不可变全量快照（启动时加载，热重载时原子替换）。

    rules: 非 default 规则按优先级排序 (最优先在前)
    by_name: name → RuleSchema 索引，O(1) 按名查找
    by_domain: domain → rule_names 索引，仅含已启用规则，用于 O(1) 域名查找
    _generic_rules: 无 domain 的泛型规则名元组，已排序
    _match_cache: 域名匹配缓存，绑定到快照生命周期——新快照自带空缓存
    _path_map: name → 文件系统路径 (str)，构建时由 os.walk 记录
    _source_map: name → 来源 ("pip"/"remote"/"user")，构建时记录
    _conflicts: 规则歧义冲突组 — (rule_name, ...) 元组，空元组 = 无冲突
    """

    rules: Tuple["RuleSchema", ...] = ()
    by_name: Dict[str, "RuleSchema"] = field(default_factory=dict)
    by_domain: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    _generic_rules: Tuple[str, ...] = ()
    _match_cache: RuleMatchCache = field(default_factory=RuleMatchCache)
    _path_map: Dict[str, str] = field(default_factory=dict)
    _source_map: Dict[str, str] = field(default_factory=dict)
    _conflicts: Tuple[Tuple[str, ...], ...] = ()

    @classmethod
    def default_only(cls) -> "RuleSnapshot":
        from astrocrawl.rules._schema import RuleSchema

        d = RuleSchema(name=DEFAULT_EXTRACTION_TYPE, enabled=True)  # type: ignore[call-arg]
        return cls(
            rules=(),
            by_name={DEFAULT_EXTRACTION_TYPE: d},
            by_domain={},
        )

    def get_rule(self, name: str) -> Optional["RuleSchema"]:
        return self.by_name.get(name)

    def get_path(self, name: str) -> Optional[Path]:
        """获取规则文件的文件系统路径（构建快照时由 os.walk 记录）。"""
        p = self._path_map.get(name)
        return Path(p) if p else None

    def get_source(self, name: str) -> Optional[str]:
        """获取规则的来源标识 ("pip"/"remote"/"user")。"""
        return self._source_map.get(name)


# ── 资源生命周期合约 ───────────────────────────────────────────


@runtime_checkable
class AsyncCloseable(Protocol):
    """任何内部创建后台 task 的组件必须实现此协议。

    对标: Trio Nursery (所有 task 属于 nursery)、Go errgroup (g.Go → g.Wait)、
          asyncio.TaskGroup (__aexit__ 自动等待完成)。
    """

    async def aclose(self) -> None:
        """取消所有后台任务，等待完成（幂等）。"""
        ...

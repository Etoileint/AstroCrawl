"""零依赖共享内核 — 纯数据/纯策略对象，位于依赖图最底层。

对标: aiohttp typedefs.py (包根专用类型模块)、DDD Shared Kernel (跨层共享纯类型在最底层)。

模块级零内部导入 (from astrocrawl. 不在模块顶层出现)。
RuleSnapshot.default_only() 体内的延迟导入用于打破与 rules._schema 的循环依赖。
"""

from __future__ import annotations

import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Optional, Tuple

from astrobasis._types import AsyncCloseable  # noqa: F401 — re-export for backward compat

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


# ── Chromium 错误码分类表 ──────────────────────────────────────
# Chromium net_error_list.h 定义了稳定的错误码命名约定。
# 错误码前缀是 Chromium 的公开 API——新增错误码遵循同一命名规范，
# 前缀匹配即可自动覆盖。无需遇到一个加一个。
#
# 规则：列表按特异性降序排列。
#  - 精确码 (如 net::ERR_TIMED_OUT) 优先于前缀 (如 net::ERR_SSL_) 优先于通配 (net::ERR_)
#  - 长前缀 (如 net::ERR_CONNECTION_TIMED_OUT) 优先于短前缀 (如 net::ERR_CONNECTION_)
#
# Tier 1: 提取 "net::ERR_XXX" / "net::HTTP_XXX" 前缀 → 按此表分类
# Tier 2: 非 Chromium 错误回退到 _FALLBACK_PATTERNS
# Tier 3: 默认 GENERIC
# ─────────────────────────────────────────────────────────────────

_CHROMIUM_ERROR_RE = re.compile(r"net::(?:ERR_|HTTP_)\w+")

# 精确匹配 + 前缀族。每项: (pattern, category)。
# pattern 以 "_" 结尾为前缀匹配，否则为精确匹配。
_CHROMIUM_ERROR_TABLE: list[tuple[str, FetchErrorCategory]] = [
    # -- DNS / 地址解析 --
    ("net::ERR_NAME_NOT_RESOLVED", FetchErrorCategory.DNS),
    ("net::ERR_ADDRESS_UNREACHABLE", FetchErrorCategory.DNS),
    ("net::ERR_ADDRESS_INVALID", FetchErrorCategory.DNS),
    ("net::ERR_INTERNET_DISCONNECTED", FetchErrorCategory.DNS),
    ("net::ERR_HOST_RESOLVER_QUEUE_TOO_LARGE", FetchErrorCategory.DNS),
    # -- SSL / 证书（族） --
    ("net::ERR_SSL_", FetchErrorCategory.SSL),
    ("net::ERR_CERT_", FetchErrorCategory.SSL),
    # -- 超时 --
    ("net::ERR_CONNECTION_TIMED_OUT", FetchErrorCategory.TIMEOUT),
    ("net::ERR_TIMED_OUT", FetchErrorCategory.TIMEOUT),
    # -- 连接拒绝 --
    ("net::ERR_CONNECTION_REFUSED", FetchErrorCategory.CONNECTION_REFUSED),
    ("net::ERR_NETWORK_ACCESS_DENIED", FetchErrorCategory.CONNECTION_REFUSED),
    # -- 连接重置 / 中断 --
    ("net::ERR_CONNECTION_RESET", FetchErrorCategory.CONNECTION_RESET),
    ("net::ERR_CONNECTION_CLOSED", FetchErrorCategory.CONNECTION_RESET),
    ("net::ERR_CONNECTION_ABORTED", FetchErrorCategory.CONNECTION_RESET),
    ("net::ERR_CONNECTION_FAILED", FetchErrorCategory.CONNECTION_RESET),
    ("net::ERR_NETWORK_IO_SUSPENDED", FetchErrorCategory.CONNECTION_RESET),
    ("net::ERR_NETWORK_CHANGED", FetchErrorCategory.CONNECTION_RESET),
    # -- 代理（族） --
    ("net::ERR_PROXY_", FetchErrorCategory.PROXY),
    ("net::ERR_TUNNEL_", FetchErrorCategory.PROXY),
    ("net::ERR_SOCKS_", FetchErrorCategory.PROXY),
    # -- 中止 / 被拦截（族） --
    ("net::ERR_ABORTED", FetchErrorCategory.ABORTED),
    ("net::ERR_BLOCKED_BY_", FetchErrorCategory.ABORTED),
    # -- 重定向 --
    ("net::ERR_TOO_MANY_REDIRECTS", FetchErrorCategory.TOO_MANY_REDIRECTS),
    # -- HTTP 状态码（按具体码分类） --
    ("net::HTTP_403", FetchErrorCategory.HTTP_4XX),
    ("net::HTTP_404", FetchErrorCategory.HTTP_4XX),
    ("net::HTTP_410", FetchErrorCategory.HTTP_4XX),
    ("net::HTTP_429", FetchErrorCategory.HTTP_4XX),
    ("net::HTTP_451", FetchErrorCategory.HTTP_4XX),
    ("net::HTTP_502", FetchErrorCategory.HTTP_5XX),
    ("net::HTTP_503", FetchErrorCategory.HTTP_5XX),
    ("net::HTTP_500", FetchErrorCategory.HTTP_5XX),
    ("net::HTTP_", FetchErrorCategory.GENERIC),
    # -- 未识别的 Chromium 错误 → GENERIC --
    ("net::ERR_", FetchErrorCategory.GENERIC),
]

# 非 Chromium 错误回退模式——错误字符串中不包含 net::ERR_ / net::HTTP_ 前缀时使用。
# Playwright 特有错误消息格式（不含 Chromium 错误码，但属于 Playwright 公开 API 约定）。
# 这些模式由 Playwright 源码固定，不会随浏览器版本变动。
_FALLBACK_PATTERNS: list[tuple[str, FetchErrorCategory]] = [
    ("Download is starting", FetchErrorCategory.DOWNLOAD),
    ("Timeout exceeded (asyncio safety net)", FetchErrorCategory.TIMEOUT),
    ("Page.goto: Timeout", FetchErrorCategory.TIMEOUT),
    ("Navigation timeout", FetchErrorCategory.TIMEOUT),
    ("Target page, context or browser has been closed", FetchErrorCategory.TARGET_CLOSED),
    ("Target closed", FetchErrorCategory.TARGET_CLOSED),
    ("Session closed", FetchErrorCategory.TARGET_CLOSED),
    ("Browser closed", FetchErrorCategory.TARGET_CLOSED),
    ("Page crashed", FetchErrorCategory.TARGET_CLOSED),
    ("Execution context was destroyed", FetchErrorCategory.TARGET_CLOSED),
    ("上下文恢复失败", FetchErrorCategory.CONTEXT_FAILURE),
    ("上下文槽位修复失败", FetchErrorCategory.CONTEXT_FAILURE),
    ("代理轮换失败", FetchErrorCategory.PROXY_EXHAUSTED),
]


def _extract_chromium_error(error_str: str) -> str | None:
    """从错误字符串中提取 Chromium 错误码（net::ERR_XXX 或 net::HTTP_XXX）。"""
    m = _CHROMIUM_ERROR_RE.search(error_str)
    return m.group(0) if m else None


def classify_fetch_error(error_str: str) -> FetchErrorCategory:
    """根据错误字符串将其分类到 FetchErrorCategory。

    Tier 1: 提取 Chromium 错误码（稳定命名约定），查 _CHROMIUM_ERROR_TABLE。
    Tier 2: 回退到 _FALLBACK_PATTERNS 处理非 Chromium 错误。
    Tier 3: 默认 GENERIC。
    """
    if not error_str:
        return FetchErrorCategory.GENERIC

    code = _extract_chromium_error(error_str)
    if code is not None:
        for pattern, category in _CHROMIUM_ERROR_TABLE:
            if code == pattern or (pattern.endswith("_") and code.startswith(pattern)):
                return category
        return FetchErrorCategory.GENERIC

    for pattern, category in _FALLBACK_PATTERNS:
        if pattern in error_str:
            return category

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
# AsyncCloseable is now defined in astrobasis._types, re-exported above.

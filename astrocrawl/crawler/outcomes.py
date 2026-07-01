"""URL 处理结果分类与统计容器。

FetchErrorCategory, DropReason, classify_fetch_error 已提取至
astrocrawl._types (共享内核)。此模块从 _types re-export 以保持向下兼容。
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import Dict, NamedTuple, Optional, Set, TypedDict

from astrocrawl._types import (  # noqa: F401 — re-export 保持向下兼容
    DropReason,
    FetchErrorCategory,
    classify_fetch_error,
)

# ── URL 处理内容结果（互斥，优先级从高到低） ──────────────────────


class UrlOutcome(Enum):
    """URL 处理的内容产出结果。"""

    OK = "ok"
    TRUNCATED = "truncated"
    DUPLICATE = "duplicate"
    NOINDEX = "noindex"
    PARSE_FAILED = "parse_failed"
    ROBOTS_DENIED = "robots_denied"
    FETCH_ERROR = "fetch_error"
    INTERNAL_ERROR = "internal_error"
    STOPPED = "stopped"

    @property
    def is_success(self) -> bool:
        """非技术故障——URL 被正确处理，即使未产出保存的内容。"""
        return self in (
            UrlOutcome.OK,
            UrlOutcome.TRUNCATED,
            UrlOutcome.DUPLICATE,
            UrlOutcome.NOINDEX,
            UrlOutcome.PARSE_FAILED,
            UrlOutcome.ROBOTS_DENIED,
        )

    @property
    def is_failure(self) -> bool:
        """技术故障——重试用尽或未预期错误。"""
        return self in (
            UrlOutcome.FETCH_ERROR,
            UrlOutcome.INTERNAL_ERROR,
            UrlOutcome.STOPPED,
        )


class OriginDiscovery(TypedDict, total=False):
    """单个源站的发现结果。"""

    robots_status: str
    sitemap_urls_found: int


# ── 统一统计容器 ────────────────────────────────────────────────


class CrawlStats:
    """线程安全的爬取统计容器，替代分散的 _stats + _domain_stats。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        # 全局 outcome 计数
        self.outcomes: Dict[str, int] = defaultdict(int)
        # 会话前计数（从 DB 恢复，用于崩溃恢复）
        self.initial_outcomes: Dict[str, int] = {}
        # 每域名明细
        self.domain_outcomes: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self.domain_timing: Dict[str, float] = defaultdict(float)
        self.domain_timing_count: Dict[str, int] = defaultdict(int)
        # 抓取错误分类
        self.fetch_errors: Dict[str, int] = defaultdict(int)
        # 丢弃原因
        self.drops: Dict[str, int] = defaultdict(int)
        # 重定向计数（正交属性，可与任意 outcome 共存）
        self.redirects: int = 0
        # 发现阶段
        self.robots_fetch_ok: int = 0
        self.robots_fetch_fail: int = 0
        self.robots_not_checked: int = 0
        self.sitemap_fetch_ok: int = 0
        self.sitemap_fetch_fail: int = 0
        self.sitemap_discovered: int = 0
        # 每源站发现明细
        self.origin_discovery: Dict[str, "OriginDiscovery"] = {}
        # 从 engine 迁移的追踪字段
        self.initial_completed: int = 0
        self.session_completed: int = 0
        self.discovery_total_origins: int = 0
        self.discovery_robots_done: int = 0
        self.discovery_sitemap_done: int = 0
        self.robots_origins_recorded: Set[str] = set()
        # 计时
        self.start_time: float = 0.0
        self.end_time: float = 0.0
        # S8: 规则维度统计 (N39)
        self.rule_hits: Dict[str, int] = defaultdict(int)
        self.rule_fields_filled: Dict[str, int] = defaultdict(int)
        self.rule_fields_total: Dict[str, int] = defaultdict(int)
        self.rule_timing: Dict[str, float] = defaultdict(float)
        self.rule_timing_count: Dict[str, int] = defaultdict(int)
        self.rule_slow_count: Dict[str, int] = defaultdict(int)  # N70: >1s

    # ── S8: 规则维度统计 (N39) ──

    async def record_rule_hit(
        self,
        rule_name: str,
        fields_filled: int,
        fields_total: int,
        elapsed_ms: float,
    ) -> None:
        """记录一次规则命中及其提取统计。"""
        async with self._lock:
            self.rule_hits[rule_name] += 1
            self.rule_fields_filled[rule_name] += fields_filled
            self.rule_fields_total[rule_name] += fields_total
            self.rule_timing[rule_name] += elapsed_ms
            self.rule_timing_count[rule_name] += 1
            if elapsed_ms > 1000:
                self.rule_slow_count[rule_name] += 1

    async def get_rule_stats_snapshot(self) -> Dict[str, object]:
        """规则维度统计快照 (N39)。"""
        async with self._lock:
            rules = {}
            for name in self.rule_hits:
                total = self.rule_timing_count.get(name, 0)
                rules[name] = {
                    "hits": self.rule_hits.get(name, 0),
                    "fields_filled": self.rule_fields_filled.get(name, 0),
                    "fields_total": self.rule_fields_total.get(name, 0),
                    "fill_rate": round(
                        self.rule_fields_filled.get(name, 0) / max(self.rule_fields_total.get(name, 1), 1), 3
                    ),
                    "avg_ms": round(self.rule_timing.get(name, 0.0) / max(total, 1), 1),
                    "slow_count": self.rule_slow_count.get(name, 0),
                }
            return rules  # type: ignore[return-value]

    async def record_outcome(
        self,
        outcome: UrlOutcome,
        domain: str = "",
        elapsed_ms: float = 0.0,
    ) -> None:
        """记录一个 URL 的处理结果。"""
        async with self._lock:
            key = outcome.value
            self.outcomes[key] += 1
            if domain:
                self.domain_outcomes[domain][key] += 1
                self.domain_timing[domain] += elapsed_ms
                self.domain_timing_count[domain] += 1

    async def record_drop(self, reason: DropReason, count: int = 1) -> None:
        """记录一次链接/URL 丢弃。"""
        async with self._lock:
            key = reason.value
            self.drops[key] += count

    async def record_fetch_error(self, category: FetchErrorCategory) -> None:
        """记录一次抓取错误分类。"""
        async with self._lock:
            key = category.value
            self.fetch_errors[key] += 1

    async def record_redirect(self) -> None:
        """记录一次重定向（与内容 outcome 正交）。"""
        async with self._lock:
            self.redirects += 1

    async def record_origin_discovery(
        self,
        origin: str,
        robots_status: str,
        sitemap_urls_found: int,
    ) -> None:
        """记录单个源站的发现结果。"""
        async with self._lock:
            self.origin_discovery[origin] = {
                "robots_status": robots_status,
                "sitemap_urls_found": sitemap_urls_found,
            }

    # ── 发现阶段锁保护方法（替代 engine 直接赋值，消除竞争） ──

    async def record_robots_fetch(self, status: str) -> None:
        async with self._lock:
            if status == "ok" or status.startswith("http_"):
                self.robots_fetch_ok += 1
            else:
                self.robots_fetch_fail += 1

    async def record_robots_not_checked(self) -> None:
        async with self._lock:
            self.robots_not_checked += 1

    async def record_sitemap_fetch(self, ok: bool) -> None:
        async with self._lock:
            if ok:
                self.sitemap_fetch_ok += 1
            else:
                self.sitemap_fetch_fail += 1

    async def record_sitemap_discovered(self, count: int) -> None:
        async with self._lock:
            self.sitemap_discovered += count

    # ── 从 engine 迁移的进度追踪方法 ──

    async def get_discovery_total_origins(self) -> int:
        async with self._lock:
            return self.discovery_total_origins

    async def get_sitemap_discovered(self) -> int:
        async with self._lock:
            return self.sitemap_discovered

    async def set_discovery_total_origins(self, n: int, reset_counters: bool = False) -> None:
        async with self._lock:
            self.discovery_total_origins = n
            if reset_counters:
                self.discovery_robots_done = 0
                self.discovery_sitemap_done = 0

    async def increment_discovery_total_origins(self) -> int:
        """原子 +1，返回新值。用于动态发现新源站时避免 TOCTOU 竞态。"""
        async with self._lock:
            self.discovery_total_origins += 1
            return self.discovery_total_origins

    async def inc_discovery_robots_done(self) -> None:
        async with self._lock:
            self.discovery_robots_done += 1

    async def inc_discovery_sitemap_done(self) -> None:
        async with self._lock:
            self.discovery_sitemap_done += 1

    async def add_robots_origin(self, origin: str) -> bool:
        """记录一个 origin 的 robots.txt 已获取，返回是否首次。"""
        async with self._lock:
            if origin in self.robots_origins_recorded:
                return False
            self.robots_origins_recorded.add(origin)
            return True

    async def set_initial_completed(self, n: int) -> None:
        async with self._lock:
            self.initial_completed = n
            self.session_completed = 0

    async def inc_session_completed(self) -> None:
        async with self._lock:
            self.session_completed += 1

    async def set_initial_outcomes(self, outcomes: Dict[str, int]) -> None:
        """设置初始 outcome 计数（从 DB 恢复）。仅在爬取启动时调用一次。"""
        async with self._lock:
            self.initial_outcomes = dict(outcomes)

    @property
    def completed_urls(self) -> int:
        """已完成的成功 URL 数（initial + session）。

        int 读写在 CPython 中是原子的，且两值只增不减，
        因此不加锁读取是安全的：偏差有界，仅影响显示/配额。
        """
        return self.initial_completed + self.session_completed

    # ── 快照（供 ProgressReporter 读取） ──

    async def get_snapshot(self) -> Dict[str, object]:
        """线程安全的统计快照。所有字段加锁读取，返回全时累计值。"""
        async with self._lock:
            # 合并初始（DB 恢复）+ 本轮 outcome 计数，保证所有消费者看到全时累计
            merged: Dict[str, int] = dict(self.initial_outcomes)
            for k, c in self.outcomes.items():
                merged[k] = merged.get(k, 0) + c
            return {
                "outcomes": merged,
                "drops": dict(self.drops),
                "fetch_errors": dict(self.fetch_errors),
                "redirects": self.redirects,
                "origin_discovery": dict(self.origin_discovery),
                "completed_urls": self.initial_completed + self.session_completed,
                "session_completed": self.session_completed,
                "robots_fetch_ok": self.robots_fetch_ok,
                "robots_fetch_fail": self.robots_fetch_fail,
                "robots_not_checked": self.robots_not_checked,
                "sitemap_fetch_ok": self.sitemap_fetch_ok,
                "sitemap_fetch_fail": self.sitemap_fetch_fail,
                "sitemap_discovered": self.sitemap_discovered,
                "discovery_total_origins": self.discovery_total_origins,
                "discovery_robots_done": self.discovery_robots_done,
                "discovery_sitemap_done": self.discovery_sitemap_done,
            }

    async def to_snapshot(self) -> dict:
        """导出统计快照，用于崩溃恢复持久化。加锁保护防止数据竞争。"""
        async with self._lock:
            return {
                "fetch_errors": dict(self.fetch_errors),
                "drops": dict(self.drops),
                "redirects": self.redirects,
                "domain_timing": dict(self.domain_timing),
                "domain_timing_count": dict(self.domain_timing_count),
                "rule_hits": dict(self.rule_hits),
                "rule_fields_filled": dict(self.rule_fields_filled),
                "rule_fields_total": dict(self.rule_fields_total),
                "rule_timing": dict(self.rule_timing),
                "rule_timing_count": dict(self.rule_timing_count),
                "rule_slow_count": dict(self.rule_slow_count),
            }

    def restore_snapshot(self, snapshot: dict) -> None:
        """从持久化快照恢复统计。使用加法累积合并跨会话数据。

        同步方法（无 await），asyncio 协作调度中天然原子执行。
        调用方确保在启动阶段（worker/sitemap 启动前）调用。
        """
        for k, v in snapshot.get("fetch_errors", {}).items():
            self.fetch_errors[k] += v
        for k, v in snapshot.get("drops", {}).items():
            self.drops[k] += v
        self.redirects += snapshot.get("redirects", 0)
        for k, v in snapshot.get("domain_timing", {}).items():
            self.domain_timing[k] += v
            snapshot_count = snapshot.get("domain_timing_count", {}).get(k, 0)
            if snapshot_count:
                self.domain_timing_count[k] += snapshot_count
        # S8: 规则统计恢复
        for k, v in snapshot.get("rule_hits", {}).items():
            self.rule_hits[k] += v
        for k, v in snapshot.get("rule_fields_filled", {}).items():
            self.rule_fields_filled[k] += v
        for k, v in snapshot.get("rule_fields_total", {}).items():
            self.rule_fields_total[k] += v
        for k, v in snapshot.get("rule_timing", {}).items():
            self.rule_timing[k] += v
        for k, v in snapshot.get("rule_timing_count", {}).items():
            self.rule_timing_count[k] += v
        for k, v in snapshot.get("rule_slow_count", {}).items():
            self.rule_slow_count[k] += v


# ── 数据模型（从 crawler/models.py 合并） ──────────────────────


@dataclass(frozen=True)
class FetchResult:
    url: str
    html: str
    status_code: int = 200


class FetchAttempt(NamedTuple):
    """_fetch_with_retry 返回值。

    result:   成功时为 FetchResult，失败时为 None
    error:    失败时的错误描述字符串，成功时为 None
    category: BrowserPool 预分类的 FetchErrorCategory.value，避免 engine 二次分类
    is_infra: 失败是否由基础设施（代理/上下文故障）引起
    """

    result: Optional[FetchResult]
    error: Optional[str]
    category: str = ""
    is_infra: bool = False

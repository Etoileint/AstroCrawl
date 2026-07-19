from __future__ import annotations

import asyncio
import enum
import json
import os
import re
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from dataclasses import replace as dataclass_replace
from hashlib import md5
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

import aiohttp
from aiohttp import TCPConnector
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from astrobase import LogfmtLogger
from astrocrawl._constants import (
    BROWSER_GOTO_BUFFER_S,
    CONCURRENT_LIMITER_CLEANUP_INTERVAL,
    CONNECTOR_LIMIT,
    CONNECTOR_LIMIT_PER_HOST,
    DNS_CACHE_TTL,
    FETCH_PROCESSOR_OVERHEAD,
    HASH_CLEANUP_INTERVAL,
    HASH_MAX_AGE,
    IN_FLIGHT_DRAIN_TIMEOUT,
    PROCESS_URL_TIMEOUT,
    RATE_LIMITER_CLEANUP_INTERVAL,
    RESOURCE_MONITOR_INTERVAL,
    RETRY_MONITOR_BATCH,
    RETRY_MONITOR_INTERVAL,
    RUN_EMPTY_QUEUE_CONFIRM,
    RUN_EMPTY_QUEUE_WAIT,
    STUCK_IN_FLIGHT_TIMEOUT,
    WORKER_IDLE_SLEEP,
    WORKER_STUCK_TIMEOUT,
)
from astrocrawl._startup import verify_chromium
from astrocrawl._types import DEFAULT_EXTRACTION_TYPE, RuleSnapshot
from astrocrawl.browser.browser_pool import BrowserPool, FetchError, FetchRequest, FetchResponse
from astrocrawl.config import CrawlerConfig, GlobalSettings
from astrocrawl.crawler._url_gate import AdmitResult, UrlGate
from astrocrawl.crawler.liveness import LivenessTracker
from astrocrawl.crawler.outcomes import (
    CrawlStats,
    DropReason,
    FetchAttempt,
    FetchErrorCategory,
    FetchResult,
    UrlOutcome,
    classify_fetch_error,
)
from astrocrawl.crawler.progress import ProgressReporter
from astrocrawl.crawler.supervisors import WorkerSupervisor
from astrocrawl.health import Health
from astrocrawl.health_monitor import CheckOnUnhealthy, HealthCheckSpec, HealthMonitor
from astrocrawl.network.robots import RobotsCache
from astrocrawl.network.sitemap import SitemapDiscovery
from astrocrawl.network.throttling import DomainConcurrencyLimiter, DomainRateLimiter, DomainTracker
from astrocrawl.proxy import ProxyConfig, ProxyProfile, ProxySession
from astrocrawl.rules import (
    RuleLifecycle,
    SourceManager,
    apply_transforms,
    cleanup_tmp_files,
    ensure_no_rule_conflicts,
    extract_fields_from_soup,
    match_url_with_candidates,
    setup_rule_directories,
)
from astrocrawl.storage.db import CrawlState
from astrocrawl.storage.writer import AsyncJsonlWriter
from astrocrawl.utils.html import (
    ContentConfig,
    ParseResult,
    check_meta_robots,
    compute_robust_hash,
    extract_links_from_soup,
    extract_schema_org,
    extract_text_from_soup,
    extract_title,
    remove_noise_tags,
)
from astrocrawl.utils.url import normalize_url, parse_domain, safe_log_url

if TYPE_CHECKING:
    from astrocrawl.crawler.signals import CrawlerSignals
    from astrocrawl.diagnostics import CrawlDiagnostics
    from astrocrawl.rules._schema import FieldRule
    from astrocrawl.storage import CrawlStateProtocol


class UrlDisposition(enum.Enum):
    """URL 离开 in_flight 时的处置方式。"""

    COMPLETED = "completed"  # mark_completed 已调用，in_flight 已清理
    REQUEUED = "requeued"  # 重新入队成功，in_flight 已清理，stats 推迟
    FAILED = "failed"  # 永久失败，需 log_failure 清理 in_flight


@dataclass
class ProcessingContext:
    """链中所有 Processor 共享的请求级状态（对标 Heritrix CrawlURI）。"""

    url: str
    depth: int
    domain: str = ""
    # 中间结果
    fetch_result: FetchResult | None = None
    parsed: "ParseResult | None" = None
    content_hash: str = ""
    is_new_content: bool = False
    # 结构化提取结果 (S2)
    extraction_type: str = DEFAULT_EXTRACTION_TYPE
    extracted_fields: Dict[str, Any] = field(default_factory=dict)
    schema_org: Optional[Dict[str, Any]] = None
    rule_name: str = ""
    _rule_trace: Dict[str, Any] = field(default_factory=dict)  # S8: trace 诊断数据
    # 终止控制
    is_terminal: bool = False
    outcome: UrlOutcome = UrlOutcome.OK
    disposition: UrlDisposition = UrlDisposition.COMPLETED
    error_message: str = ""


@dataclass
class PipelineDeps:
    """所有 Processor 共享的外部资源与辅助方法（窄接口注入）。

    对标 Heritrix Controller: 所有 Processor 共享单一大对象，统一签名为
    (ctx, deps) → ctx。PipelineDeps 将 Engine 暴露面从 ~30 方法收敛到 16 字段。
    """

    # External resources — typed
    state: CrawlStateProtocol = None  # type: ignore[assignment]
    stats: CrawlStats = None  # type: ignore[assignment]
    writer: AsyncJsonlWriter = None  # type: ignore[assignment]
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    config: CrawlerConfig = None  # type: ignore[assignment]
    global_settings: GlobalSettings = field(default_factory=GlobalSettings)
    signals: Any = None
    max_depth: int = 0
    robots_cache: Optional[RobotsCache] = None
    sitemap_discovery: Optional[SitemapDiscovery] = None
    domain_limiter: DomainRateLimiter = None  # type: ignore[assignment]
    domain_concurrency: DomainConcurrencyLimiter = None  # type: ignore[assignment]
    # Extraction rules (S2)
    rule_snapshot: RuleSnapshot = field(default_factory=RuleSnapshot.default_only)
    _hash_v2: bool = True
    # Engine internal state
    allowed_domains: Set[str] = field(default_factory=set)
    same_domain_only: bool = False
    exclude_res: List[re.Pattern] = field(default_factory=list)
    log: LogfmtLogger = None  # type: ignore[assignment]
    # Helpers (engine methods exposed as callables)
    fetch_url: Callable[[str, float], Awaitable[FetchAttempt]] | None = None
    increment_progress_plan: Callable[[int], Awaitable[None]] | None = None


# Processor 类型别名
Processor = Callable[[ProcessingContext, PipelineDeps], Awaitable[ProcessingContext]]


class Pipeline:
    """处理器链的容器。按序执行 Processor，遇到 is_terminal 则停止。

    对标 Heritrix Processor Chain：Pipeline 组合层只检查 is_terminal，不参与业务决策。
    """

    def __init__(self, *processors: Processor) -> None:
        self._processors = processors

    async def process(self, ctx: ProcessingContext, deps: PipelineDeps) -> ProcessingContext:
        for p in self._processors:
            try:
                ctx = await p(ctx, deps)
            except Exception as e:
                deps.log.exception("processor_error", processor=p.__name__)
                ctx.is_terminal = True
                ctx.outcome = UrlOutcome.INTERNAL_ERROR
                ctx.error_message = str(e)
                ctx.disposition = await _scheduled_requeue_or_fail(ctx.url, ctx.depth, deps)
            if ctx.is_terminal:
                break
        return ctx


# ═══════════════════════════════════════════════════════════════════════
# Processor 辅助函数（模块级，供 Processor 调用）
# ═══════════════════════════════════════════════════════════════════════


async def _free_requeue_or_fail(url: str, depth: int, deps: PipelineDeps) -> UrlDisposition:
    """免费重入队（绕过 max_requeue），用于基础设施/停止中断。"""
    domain = parse_domain(url)
    requeued = await deps.state.push_to_queue_as_owner(url, depth, domain)
    return UrlDisposition.REQUEUED if requeued else UrlDisposition.FAILED


async def _scheduled_requeue_or_fail(url: str, depth: int, deps: PipelineDeps) -> UrlDisposition:
    """计费重入队（计入 max_requeue），用于 URL 级可恢复错误。"""
    requeued = await deps.state.try_schedule_retry(url, depth)
    return UrlDisposition.REQUEUED if requeued else UrlDisposition.FAILED


# ═══════════════════════════════════════════════════════════════════════
# Processor 定义（模块级 async 函数，按链序排列）
# ═══════════════════════════════════════════════════════════════════════


async def _domain_concurrency_processor(ctx: ProcessingContext, deps: PipelineDeps) -> ProcessingContext:
    """获取域名并发许可。失败时标记 terminal。"""
    if deps.stop_event.is_set():
        ctx.is_terminal = True
        ctx.outcome = UrlOutcome.STOPPED
        ctx.disposition = await _free_requeue_or_fail(ctx.url, ctx.depth, deps)
        return ctx
    if not await deps.domain_concurrency.try_acquire(ctx.domain):
        ctx.is_terminal = True
        ctx.outcome = UrlOutcome.STOPPED
        ctx.disposition = await _free_requeue_or_fail(ctx.url, ctx.depth, deps)
        return ctx
    if deps.stop_event.is_set():
        await deps.domain_concurrency.release(ctx.domain)
        ctx.is_terminal = True
        ctx.outcome = UrlOutcome.STOPPED
        ctx.disposition = await _free_requeue_or_fail(ctx.url, ctx.depth, deps)
    return ctx


async def _robots_processor(ctx: ProcessingContext, deps: PipelineDeps) -> ProcessingContext:
    """检查 robots.txt。禁止时标记 terminal。

    data 层（统计 + 动态 sitemap 发现）始终执行；
    robots.txt 抓取仅在 robots_respect=True 时触发；
    policy 层（Disallow 拦截）仅 robots_respect=True 时执行。
    """
    if deps.robots_cache is None:
        return ctx
    origin = f"{urlparse(ctx.url).scheme}://{urlparse(ctx.url).netloc}"
    is_new_origin = await deps.stats.add_robots_origin(origin)

    # 数据层：sitemap 发现始终执行（关联 Bug #164）
    if is_new_origin and deps.config.use_sitemap and deps.sitemap_discovery is not None:
        deps.sitemap_discovery.discover_origin_if_new(origin, enqueue_depth=ctx.depth + 1)

    # 策略层：robots_respect=False → 提前返回，记录"未检查"
    if not deps.config.robots_respect:
        if is_new_origin:
            await deps.stats.record_robots_not_checked()
        return ctx

    # 策略层：检查 robots（is_allowed 触发 HTTP 抓取，_fetch_status 在此填充）
    allowed = await deps.robots_cache.is_allowed(ctx.url)

    # 数据层：抓取完成后记录真实状态（核心修复——status 不再为 "not_checked"）
    if is_new_origin:
        status = await deps.robots_cache.get_fetch_status(origin)
        await deps.stats.record_robots_fetch(status)

    if not allowed:
        await deps.state.mark_completed(ctx.url, depth=ctx.depth, outcome=UrlOutcome.ROBOTS_DENIED.value)
        ctx.is_terminal = True
        ctx.outcome = UrlOutcome.ROBOTS_DENIED
        ctx.disposition = UrlDisposition.COMPLETED
    return ctx


async def _rate_limit_processor(ctx: ProcessingContext, deps: PipelineDeps) -> ProcessingContext:
    """获取域名速率限制 + 停止事件检查。失败时标记 terminal。"""
    if deps.stop_event.is_set():
        ctx.is_terminal = True
        ctx.outcome = UrlOutcome.STOPPED
        ctx.disposition = await _free_requeue_or_fail(ctx.url, ctx.depth, deps)
        return ctx
    if not await deps.domain_limiter.acquire(ctx.domain, deps.stop_event):
        ctx.is_terminal = True
        ctx.outcome = UrlOutcome.STOPPED
        ctx.disposition = await _free_requeue_or_fail(ctx.url, ctx.depth, deps)
        return ctx
    if deps.stop_event.is_set():
        ctx.is_terminal = True
        ctx.outcome = UrlOutcome.STOPPED
        ctx.disposition = await _free_requeue_or_fail(ctx.url, ctx.depth, deps)
    return ctx


async def _fetch_processor(ctx: ProcessingContext, deps: PipelineDeps) -> ProcessingContext:
    """执行页面抓取（BrowserPool Actor 消息模式，含完整重试）。失败时标记 terminal。

    fetch 超时从 page_timeout + max_retries + backoff 动态计算，
    cap 于 PROCESS_URL_TIMEOUT - FETCH_PROCESSOR_OVERHEAD（deadline 传播）。
    """
    single_attempt = deps.config.page_timeout / 1000.0 + BROWSER_GOTO_BUFFER_S
    backoff_total = sum(deps.config.retry_backoff_base**i for i in range(deps.config.max_retries))
    fetch_budget = single_attempt * deps.config.max_retries + backoff_total
    max_budget = PROCESS_URL_TIMEOUT - FETCH_PROCESSOR_OVERHEAD
    if fetch_budget > max_budget:
        fetch_budget = max_budget
    attempt = await deps.fetch_url(ctx.url, fetch_budget)  # type: ignore[misc]
    if attempt.result is None:
        ctx.error_message = attempt.error or "unknown fetch error"
        if attempt.category:
            try:
                cat = FetchErrorCategory(attempt.category)
            except ValueError:
                cat = classify_fetch_error(attempt.error or "")
        else:
            cat = classify_fetch_error(attempt.error or "")
        await deps.stats.record_fetch_error(cat)
        if attempt.is_infra:
            disp = await _free_requeue_or_fail(ctx.url, ctx.depth, deps)
        else:
            disp = await _scheduled_requeue_or_fail(ctx.url, ctx.depth, deps)
        ctx.is_terminal = True
        ctx.outcome = UrlOutcome.FETCH_ERROR
        ctx.disposition = disp
        return ctx
    ctx.fetch_result = attempt.result
    return ctx


@dataclass
class ExtractionResult:
    """单次规则提取结果（_parse_processor 内部使用）。"""

    extraction_type: str = DEFAULT_EXTRACTION_TYPE
    rule_name: str = ""
    fields: Dict[str, Any] = field(default_factory=dict)


async def _parse_processor(ctx: ProcessingContext, deps: PipelineDeps) -> ProcessingContext:
    """解析 HTML + 规则匹配 + CSS 提取 + Transform + 链接过滤 + Schema.org。

    流程：match → content-free tag removal → CSS 提取 → text → links → schema_org
    仅移除 script/style/noscript/iframe/embed，保留 header/nav/footer/aside。

    """
    if ctx.fetch_result is None:
        return ctx
    html = ctx.fetch_result.html
    result_url = ctx.fetch_result.url

    if not html or not html.strip():
        # N89: HTML 为空优雅跳过
        ctx.parsed = ParseResult(text="", links=[], allow_index=True, allow_follow=True, title="")
        ctx.extraction_type = DEFAULT_EXTRACTION_TYPE
        ctx.schema_org = None
        return ctx

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        ctx.parsed = ParseResult(text="", links=[], allow_index=True, allow_follow=True, parse_error=True, title="")
        ctx.extraction_type = DEFAULT_EXTRACTION_TYPE
        ctx.schema_org = None
        return ctx

    # 1. 规则匹配
    rule_name, match_candidates = match_url_with_candidates(result_url, deps.rule_snapshot)
    rule = deps.rule_snapshot.get_rule(rule_name)
    is_default = rule_name == DEFAULT_EXTRACTION_TYPE

    # 2. meta robots + title
    allow_index, allow_follow = check_meta_robots(soup, deps.config.respect_meta_robots)
    title = extract_title(soup)

    # 3. 删除纯噪声标签（CSS 选择器不可达，链接提取和文本提取前）
    remove_noise_tags(soup)

    # 4. 执行提取（按规则类型分支）
    body_text = ""
    text_truncated = False
    original_text_len = 0
    extracted_fields: Dict[str, Any] = {}
    follow_links = True
    t_extract_start = time.monotonic()
    fields_filled = 0
    fields_total = 0

    if not is_default and rule:
        fields_config = rule.fields
        options = rule.options
        keep_body_text = options.keep_body_text
        follow_links = options.follow_links

        fields_total = len(fields_config)

        # CSS 提取
        if fields_config:
            raw_fields = await extract_fields_from_soup(soup, rule_name, fields_config, deps.config.max_text_length)
            extracted_fields = _apply_transforms_to_fields(raw_fields, fields_config, deps.config)
            fields_filled = sum(1 for v in extracted_fields.values() if v is not None)

        # 文本
        if keep_body_text:
            body_text, text_truncated, original_text_len = extract_text_from_soup(soup, deps.config)  # type: ignore[arg-type]

        # 检查所有字段是否全 null (N27, N48)
        if extracted_fields and all(v is None for v in extracted_fields.values()):
            deps.log.warning("rule_all_fields_null", rule=rule_name, url=result_url)
            rule_name = DEFAULT_EXTRACTION_TYPE
            is_default = True
            fields_filled = 0
            extracted_fields = {}
            # 回退到 default：重新提取完整文本
            body_text, text_truncated, original_text_len = extract_text_from_soup(soup, deps.config)  # type: ignore[arg-type]
    else:
        body_text, text_truncated, original_text_len = extract_text_from_soup(soup, deps.config)  # type: ignore[arg-type]

    elapsed_ms = (time.monotonic() - t_extract_start) * 1000

    # S8: 记录规则命中统计 (N39)
    if not is_default:
        await deps.stats.record_rule_hit(rule_name, fields_filled, fields_total, elapsed_ms)
        # N70: >1s 慢规则 WARNING
        if elapsed_ms > 1000:
            deps.log.warning(
                "rule_slow",
                rule=rule_name,
                elapsed_ms=round(elapsed_ms),
                fields=f"{fields_filled}/{fields_total}",
                url=result_url,
            )

    # S8: trace 信息 (N38)
    ctx._rule_trace = {
        "matched_rule": rule_name,
        "candidates": match_candidates,
        "used_default": is_default,
        "fields_filled": fields_filled,
        "fields_empty": fields_total - fields_filled,
        "elapsed_ms": round(elapsed_ms, 1),
    }

    # 5. 链接提取
    links_raw, link_stats = extract_links_from_soup(
        soup,
        result_url,
        deps.allowed_domains,
        deps.same_domain_only,
        allow_follow,
        deps.config,  # type: ignore[arg-type]
    )
    links = links_raw if follow_links else []

    # 6. Schema.org（始终执行，N109）
    ctx.schema_org = extract_schema_org(html)

    # 7. 填充 ctx
    ctx.extraction_type = rule_name
    ctx.rule_name = "" if is_default else rule_name
    ctx.extracted_fields = extracted_fields

    ctx.parsed = ParseResult(
        text=body_text,
        links=links,
        allow_index=allow_index,
        allow_follow=allow_follow,
        title=title,
        text_truncated=text_truncated,
        original_text_len=original_text_len,
        nofollow_skipped=link_stats.get("nofollow_skipped", 0),
        cross_domain_skipped=link_stats.get("cross_domain_skipped", 0),
        invalid_url_skipped=link_stats.get("invalid_url_skipped", 0),
        download_candidate_skipped=link_stats.get("download_candidate_skipped", 0),
        same_page_dupes=link_stats.get("same_page_dupes", 0),
    )

    # 链接过滤统计
    pr = ctx.parsed
    if pr.nofollow_skipped:
        await deps.stats.record_drop(DropReason.NOFOLLOW_LINK, pr.nofollow_skipped)
    if pr.cross_domain_skipped:
        await deps.stats.record_drop(DropReason.CROSS_DOMAIN, pr.cross_domain_skipped)
    if pr.invalid_url_skipped:
        await deps.stats.record_drop(DropReason.INVALID_URL, pr.invalid_url_skipped)
    if pr.download_candidate_skipped:
        await deps.stats.record_drop(DropReason.DOWNLOAD_CANDIDATE, pr.download_candidate_skipped)
    if pr.same_page_dupes:
        await deps.stats.record_drop(DropReason.SAME_PAGE_DUP, pr.same_page_dupes)

    return ctx


def _apply_transforms_to_fields(
    raw_fields: Dict[str, Any],
    fields_config: Dict[str, "FieldRule"],
    cfg: CrawlerConfig,
) -> Dict[str, Any]:
    """对提取的字段应用 transform 流水线。"""
    extra_currency = frozenset(cfg.extra_currency_symbols) if cfg.extra_currency_symbols else frozenset()
    result: Dict[str, Any] = {}
    for field_name, value in raw_fields.items():
        field_cfg = fields_config.get(field_name)
        transforms = field_cfg.transform if field_cfg else {}
        if transforms and value is not None:
            try:
                result[field_name] = apply_transforms(value, transforms, cfg.max_text_length, extra_currency)
            except Exception:
                result[field_name] = value  # transform 失败保留原值
        else:
            result[field_name] = value
    return result


async def _content_dedup_processor(ctx: ProcessingContext, deps: PipelineDeps) -> ProcessingContext:
    """内容哈希去重 + JSONL 写入 + 内容 outcome 判定。"""
    pr = ctx.parsed
    if pr is None:
        return ctx
    result_url = normalize_url(ctx.fetch_result.url, deps.config)  # type: ignore[arg-type,union-attr]
    is_new = False
    if pr.parse_error:
        pass
    elif not pr.allow_index:
        pass
    elif not pr.text and not ctx.extracted_fields:
        # N95: body+fields 均为空，跳过去重（允许重爬）
        is_new = True
    else:
        h = _compute_content_hash(pr.text, ctx.extracted_fields, deps._hash_v2, deps.config)  # type: ignore[arg-type]
        is_new = await deps.state.add_content_hash(h, url=result_url)
        ctx.content_hash = h
    ctx.is_new_content = is_new

    if is_new and not pr.parse_error:
        record: Dict[str, Any] = {
            "url": result_url,
            "depth": ctx.depth,
            "text": pr.text,
            "timestamp": time.time(),
            "extraction_type": ctx.extraction_type,
            "title": pr.title,
            "fields": ctx.extracted_fields,
        }
        if ctx.schema_org is not None:
            record["schema_org"] = ctx.schema_org
        # S8: trace_rules 诊断输出 (N38)
        if deps.global_settings.trace_rules and ctx._rule_trace:
            record["_rule_match"] = ctx._rule_trace
        if ctx._rule_trace and deps.signals:
            deps.signals.rule_matched.emit(ctx.rule_name or "default", ctx._rule_trace)
        await deps.writer.write_record(record)

    # 内容 outcome（互斥，优先级从高到低）
    if pr.parse_error:
        ctx.outcome = UrlOutcome.PARSE_FAILED
    elif not pr.allow_index:
        ctx.outcome = UrlOutcome.NOINDEX
    elif not is_new:
        ctx.outcome = UrlOutcome.DUPLICATE
    elif pr.text_truncated:
        ctx.outcome = UrlOutcome.TRUNCATED
    else:
        ctx.outcome = UrlOutcome.OK
    return ctx


def _compute_content_hash(
    body_text: str,
    fields: Dict[str, Any],
    hash_v2: bool,
    cfg: ContentConfig,
) -> str:
    """计算内容去重哈希，v2 含 fields 结构化数据。"""
    if hash_v2:
        payload = body_text + json.dumps(fields, sort_keys=True, ensure_ascii=False)
        return "v2:" + md5(payload.encode("utf-8", errors="ignore")).hexdigest()
    return compute_robust_hash(body_text, cfg)


async def _finalize_processor(ctx: ProcessingContext, deps: PipelineDeps) -> ProcessingContext:
    """标记完成：redirect 记录 + force_fail 竞态防护 + mark_done + 统计。"""
    result_url = ctx.fetch_result.url if ctx.fetch_result else ctx.url
    # 重定向记录（归一化前——捕获实际重定向行为）
    if ctx.fetch_result and result_url != ctx.url:
        await deps.stats.record_redirect()
    # 归一化——确保 urls 表存储值与链接提取的去重检查一致
    result_url = normalize_url(result_url, deps.config)  # type: ignore[arg-type]
    # force_fail 竞态防护
    if deps.stop_event.is_set() and not await deps.state.url_in_flight(ctx.url):
        ctx.is_terminal = True
        ctx.outcome = UrlOutcome.FETCH_ERROR
        ctx.disposition = UrlDisposition.FAILED
        return ctx
    # mark_done
    await deps.state.mark_completed(result_url, depth=ctx.depth, original_url=ctx.url, outcome=ctx.outcome.value)
    ctx.disposition = UrlDisposition.COMPLETED
    return ctx


async def _enqueue_links_processor(ctx: ProcessingContext, deps: PipelineDeps) -> ProcessingContext:
    """子链接入队：边界持久化 + 深度内入队。"""
    pr = ctx.parsed
    if pr is None or not pr.allow_follow or not pr.links:
        return ctx

    should_skip = ctx.outcome in (UrlOutcome.DUPLICATE, UrlOutcome.NOINDEX) and deps.config.skip_duplicate_links
    if should_skip:
        await deps.stats.record_drop(DropReason.SKIP_DUPLICATE_LINKS, len(pr.links))
        return ctx

    for link in pr.links:
        if deps.stop_event.is_set():
            break
        result = await UrlGate.admit(
            link,
            ctx.depth + 1,
            deps.state,
            max_depth=deps.max_depth,
            exclude_patterns=deps.exclude_res,
            parent_url=ctx.url,
        )
        if result == AdmitResult.ENQUEUED:
            if deps.increment_progress_plan:
                await deps.increment_progress_plan(ctx.depth + 1)
        elif result == AdmitResult.INVALID_URL:
            await deps.stats.record_drop(DropReason.INVALID_URL)
        elif result == AdmitResult.EXCLUDED:
            await deps.stats.record_drop(DropReason.EXCLUDE_PATTERN)
        elif result == AdmitResult.QUEUE_FULL:
            await deps.stats.record_drop(DropReason.QUEUE_FULL)
        elif result == AdmitResult.DUPLICATE:
            await deps.stats.record_drop(DropReason.ALREADY_VISITED)
        elif result == AdmitResult.BOUNDARY:
            pass  # admit() 内部已调用 save_boundary_links，无需额外操作
    return ctx


def create_crawler(
    start_urls: List[str],
    depth: int,
    concurrency: int,
    output_path: str,
    same_domain_only: bool,
    cfg: CrawlerConfig,
    signals: Optional[CrawlerSignals] = None,
    global_settings: GlobalSettings = GlobalSettings(),
    *,
    proxy_profile: ProxyProfile | None = None,
    proxy_mode_override: str | None = None,
    health_tracker: Any = None,
) -> AsyncCrawler:
    """CLI 与 GUI 共享的 AsyncCrawler 工厂。

    决策 1.2/3.2 — 消除两处重复的 8 参数构造逻辑。
    """
    verify_chromium()
    return AsyncCrawler(
        start_urls=start_urls,
        depth=depth,
        concurrency=concurrency,
        output_path=output_path,
        same_domain_only=same_domain_only,
        signals=signals,
        cfg=cfg,
        global_settings=global_settings,
        proxy_profile=proxy_profile,
        proxy_mode_override=proxy_mode_override,
        health_tracker=health_tracker,
    )


class AsyncCrawler:
    def __init__(
        self,
        start_urls: List[str],
        depth: int,
        concurrency: int,
        output_path: str,
        same_domain_only: bool,
        cfg: CrawlerConfig,
        signals: Optional[CrawlerSignals] = None,
        global_settings: GlobalSettings = GlobalSettings(),
        *,
        proxy_profile: ProxyProfile | None = None,
        proxy_mode_override: str | None = None,
        health_tracker: Any = None,
    ):
        self.start_urls = start_urls
        self.depth = depth
        self.concurrency = concurrency
        self._proxy_profile = proxy_profile
        self._proxy_mode_override = proxy_mode_override
        self._health_tracker = health_tracker
        self.output_path = Path(output_path)
        self.same_domain_only = same_domain_only
        self.signals = signals
        self.cfg = cfg
        self._global_settings = global_settings

        self.allowed_domains: Set[str] = set()
        if same_domain_only:
            for u in start_urls:
                self.allowed_domains.add(parse_domain(normalize_url(u, cfg)))  # type: ignore[arg-type]

        self._log = LogfmtLogger("astrocrawl.crawler")
        self._log.setLevel(self._global_settings.log_level)

        self._connector: Optional[TCPConnector] = None
        self._http_session: Optional[aiohttp.ClientSession] = None

        self._stop_event = asyncio.Event()
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._sitemap_discovery: Optional[SitemapDiscovery] = None

        self._state: Optional[CrawlStateProtocol] = None
        self._tracker: Optional[LivenessTracker] = None
        self._supervisor: Optional[WorkerSupervisor] = None
        self._health_monitor: Optional[HealthMonitor] = None
        self._domain_cursor: int = 0
        self._background_tasks: List[asyncio.Task] = []
        self._progress_lock = asyncio.Lock()

        self._writer = AsyncJsonlWriter(
            self.output_path, self._global_settings.output_gzip, cfg.output_buffer_size, cfg.output_flush_interval
        )
        self._hash_v2: bool = True  # S2: 内容去重哈希版本，resume 时从 DB meta 读取
        self._rule_lifecycle: Optional[RuleLifecycle] = None  # S4: 规则生命周期
        self._source_manager: Optional[SourceManager] = None  # S6: 远程源管理

        self._progress_layers: Dict[int, Tuple[int, int]] = dict.fromkeys(range(self.depth), (0, 0))
        self._start_time = 0.0
        self._crawl_stats = CrawlStats()
        self._reporter: Optional[ProgressReporter] = None
        self._last_report: Optional[dict] = None
        self._browser_pool: Optional[BrowserPool] = None  # 测试 seam
        self._diagnostics: Optional[CrawlDiagnostics] = None
        self.proxy_manager: Any = None  # GUI 健康条向后兼容别名（crawl_session.py 通过此属性访问 ProxySession）
        self.proxy_session: Optional[ProxySession] = None

        # 预编译 exclude_patterns，避免每次 enqueue 重复编译，同时捕获无效正则

        self._exclude_res: List[re.Pattern] = []
        for pat in cfg.exclude_patterns:
            try:
                self._exclude_res.append(re.compile(pat))
            except re.error as e:
                self._log.warning("config_warn", invalid_regex=pat, error=e)

    @property
    def _st(self) -> CrawlStateProtocol:
        assert self._state is not None, "AsyncCrawler._run() must be called first"
        return self._state

    async def _enqueue_url(self, url: str, depth: int) -> bool:
        """直接入队，委托 UrlGate 统一准入。同时记录丢弃统计。"""
        result = await UrlGate.admit(url, depth, self._state, max_depth=self.depth, exclude_patterns=self._exclude_res)  # type: ignore[arg-type]
        if result == AdmitResult.INVALID_URL:
            await self._crawl_stats.record_drop(DropReason.INVALID_URL)
        elif result == AdmitResult.EXCLUDED:
            await self._crawl_stats.record_drop(DropReason.EXCLUDE_PATTERN)
        elif result == AdmitResult.QUEUE_FULL:
            await self._crawl_stats.record_drop(DropReason.QUEUE_FULL)
        elif result == AdmitResult.DUPLICATE:
            await self._crawl_stats.record_drop(DropReason.ALREADY_VISITED)
        elif result == AdmitResult.BOUNDARY:
            pass  # admit() 内部已调用 save_boundary_links，无需额外操作
        return result == AdmitResult.ENQUEUED

    async def _get_reporter_queue_size(self) -> int:
        return await self._st.queue_size()

    async def _requeue_owned_url(self, url: str, depth: int) -> bool:
        """将当前 worker 拥有的 URL 放回队列（绕过 in_flight 检查）。"""
        domain = parse_domain(url)
        return await self._st.push_to_queue_as_owner(url, depth, domain)

    def _progress_snapshot(self) -> Dict[int, Tuple[int, int]]:
        """返回 _progress_layers 副本。

        _progress_layers 的 keys 在 __init__ 中一次性初始化，运行期不增删；
        值 Tuple[int, int] 为不可变类型，字典赋值是原子操作。
        因此不加锁复制是安全的——最坏情况为某一深度显示值滞后一帧。
        """
        return dict(self._progress_layers)

    async def _increment_progress_plan(self, depth: int) -> None:
        """原子递增指定深度的 plan 计数。"""
        async with self._progress_lock:
            proc, plan = self._progress_layers.get(depth, (0, 0))
            self._progress_layers[depth] = (proc, plan + 1)

    @property
    def reporter(self) -> Optional[ProgressReporter]:
        """公开的反馈器实例，供 CLI 在 run() 完成后调用 print_summary()。"""
        return self._reporter

    @property
    def last_report(self) -> Optional[dict]:
        """最后一次 generate_report() 的结果，供 CLI 复用。"""
        return self._last_report

    async def _on_sitemap_url_enqueued(self, url: str, depth: int) -> bool:
        if self.same_domain_only and self.allowed_domains:
            domain = parse_domain(url)
            if domain not in self.allowed_domains:
                await self._crawl_stats.record_drop(DropReason.CROSS_DOMAIN)
                return False
        ok = await self._enqueue_url(url, depth)
        if ok:
            await self._crawl_stats.record_sitemap_discovered(1)
            await self._increment_progress_plan(depth)
        return ok

    async def _seed_new_urls(self) -> None:
        for raw in self.start_urls:
            norm = normalize_url(raw, self.cfg)  # type: ignore[arg-type]
            if self._stop_event.is_set():
                return
            if self.same_domain_only and self.allowed_domains:
                if parse_domain(norm) not in self.allowed_domains:
                    continue
            ok = await self._enqueue_url(norm, 0)
            if ok:
                await self._increment_progress_plan(0)

    async def _heal_from_boundary_links(self, gap_depth: int) -> int:
        """从 boundary_links 中找回丢失的子链接并入队。

        在 promote 前（进度恢复前）调用——利用持久化的完整链接图，
        找到父深度中已完成页面的丢失子链接，无需重爬父页面。
        计划由恢复公式统一重建，不在方法内增量。
        返回恢复数量。
        """
        parent_depth = gap_depth - 1
        if parent_depth < 0:
            return 0
        lost = await self._st.get_lost_children(parent_depth)
        enqueued = 0
        for child_url in lost:
            if self._stop_event.is_set():
                break
            ok = await self._enqueue_url(child_url, gap_depth)
            if ok:
                enqueued += 1
        if enqueued:
            self._log.info("url_requeue", gap_depth=gap_depth, parents=len(lost), recovered=enqueued)
        return enqueued

    async def _pop_domain_aware(
        self,
    ) -> Tuple[Optional[str], int]:
        """域名感知调度器 — 从 DB 轮询各域队列，round-robin。

        Returns (url, depth) 或 (None, 0)。
        """
        domains = await self._st.get_active_domains()
        if not domains:
            return None, 0
        n = len(domains)
        cursor = getattr(self, "_domain_cursor", 0)
        for i in range(n):
            domain = domains[(cursor + i) % n]
            result = await self._st.pop_from_domain(domain)
            if result is not None:
                self._domain_cursor = (cursor + i + 1) % n
                return result
        return None, 0

    # ── 诊断 ──────────────────────────────────────────────────

    def get_health(self) -> Health:
        """HealthChecked 协议：聚合所有子组件健康状态（同步，快速返回）。"""
        details: Dict[str, Any] = {}
        statuses: List[str] = []

        # BrowserPool (A类 — 死亡导致所有抓取阻塞)
        if self._browser_pool:
            bp_health = self._browser_pool.get_health()
            details["browser_pool"] = bp_health.details
            statuses.append(bp_health.status)

        # Workers (A类 — 死亡导致处理停止)
        wcount = self._tracker.alive_count if self._tracker else 0
        details["workers"] = {"alive": wcount, "concurrency": self.concurrency}
        if wcount == 0 and self.concurrency > 0 and not self._stop_event.is_set():
            statuses.append("DOWN")
        elif wcount < self.concurrency:
            statuses.append("DEGRADED")
        else:
            statuses.append("UP")

        # Supervisor/Fuse (A类 — 快速重启循环检测，与 LivenessTracker 互补)
        if self._supervisor is not None:
            sv_health = self._supervisor.get_health()
            details["supervisor"] = sv_health.details
            statuses.append(sv_health.status)

        # CrawlState (C类 — DB 连接状态)
        if self._state is not None:
            details["crawl_state"] = {"db_path": getattr(self._state, "db_path", "")}
            statuses.append("UP")

        # Writer (A类 — 死亡导致数据丢失)
        if self._writer is not None:
            details["writer"] = {"started": self._writer._started}
            statuses.append("UP")

        # Stats (B类 — 丢失统计)
        details["stats"] = {"completed": self._crawl_stats.completed_urls}
        statuses.append("UP")

        # Aggregate status
        if "DOWN" in statuses:
            overall = "DOWN"
        elif "DEGRADED" in statuses:
            overall = "DEGRADED"
        else:
            overall = "UP"

        return Health(overall, f"{wcount}/{self.concurrency} workers alive", details)  # type: ignore[arg-type]

    # ── Worker 主循环 ─────────────────────────────────────
    async def _worker(
        self,
        idx: int,
        domain_limiter: DomainRateLimiter,
        domain_concurrency: DomainConcurrencyLimiter,
        robots_cache: Optional[RobotsCache],
        pipeline: Pipeline,
        deps: PipelineDeps,
    ) -> None:
        while not self._stop_event.is_set():
            await self._pause_event.wait()

            # ── 代理全 OPEN 暂停出队（proxy_only 模式）──
            if self._browser_pool and self._browser_pool.should_pause_dequeuing():
                recovery = self._browser_pool.proxy_recovery_event
                if recovery:
                    if recovery.is_set():
                        recovery.clear()
                    try:
                        await asyncio.wait_for(recovery.wait(), timeout=5.0)
                    except asyncio.TimeoutError:
                        pass
                else:
                    await asyncio.sleep(0.5)
                continue

            url: Optional[str] = None
            depth: int = 0
            domain: str = ""
            settle_entered = False
            try:
                result = await self._pop_domain_aware()
                if result[0] is None:
                    await self._st.wait_for_queue(timeout=0.5)
                    continue
                url, depth = result
                domain = parse_domain(url)  # type: ignore[arg-type]
                if self.signals:
                    self.signals.worker_state.emit(idx, "working")
                t0 = time.monotonic()

                ctx = ProcessingContext(
                    url=url,  # type: ignore[arg-type]
                    depth=depth,
                    domain=domain,
                )
                try:
                    ctx = await asyncio.wait_for(
                        pipeline.process(ctx, deps),
                        timeout=PROCESS_URL_TIMEOUT,
                    )
                finally:
                    await domain_concurrency.release(domain)
                elapsed = time.monotonic() - t0
                settle_entered = True
                await self._settle_url(
                    url,  # type: ignore[arg-type]
                    depth,
                    domain,
                    elapsed * 1000,
                    ctx.outcome,
                    ctx.disposition,
                    error=ctx.error_message,
                    state=self._state,
                )
                if self._tracker:
                    self._tracker.heartbeat(idx)
                if self.signals:
                    self.signals.worker_state.emit(idx, "idle")

            except asyncio.CancelledError:
                if url is not None and not settle_entered:
                    await self._requeue_owned_url(url, depth)
                if self._tracker:
                    self._tracker.remove(idx)
                if self.signals:
                    self.signals.worker_state.emit(idx, "done")
                return
            except asyncio.TimeoutError:
                if url is not None and not settle_entered:
                    self._log.warning(
                        "url_permanent_fail",
                        url=safe_log_url(url),
                        timeout=PROCESS_URL_TIMEOUT,
                    )
                    disp = await _scheduled_requeue_or_fail(url, depth, deps)
                    await self._settle_url(
                        url,
                        depth,
                        domain,
                        0,
                        UrlOutcome.FETCH_ERROR,
                        disp,
                        "Pipeline timed out",
                        state=self._state,
                    )
                if self.signals:
                    self.signals.worker_state.emit(idx, "idle")
            except Exception as exc:
                if url is not None and not settle_entered:
                    self._log.warning("worker_crash", url=safe_log_url(url), error=exc)
                    disp = await _scheduled_requeue_or_fail(url, depth, deps)
                    await self._settle_url(
                        url,
                        depth,
                        domain,
                        0,
                        UrlOutcome.INTERNAL_ERROR,
                        disp,
                        str(exc),
                        state=self._state,
                    )
                elif url is not None:
                    self._log.error("worker_crash", phase="finalize", url=safe_log_url(url), error=exc)
                if self.signals:
                    self.signals.worker_state.emit(idx, "idle")
        # while 循环正常退出（stop_event 触发）
        if self._tracker:
            self._tracker.remove(idx)
        if self.signals:
            self.signals.worker_state.emit(idx, "done")

    async def _check_retryable(self) -> Health:
        try:
            recovered = 0
            for _ in range(RETRY_MONITOR_BATCH):
                if self._stop_event.is_set():
                    break
                result = await self._st.atomic_retry_reclaim(self.cfg.max_requeue)
                if result is None:
                    break
                recovered += 1
                await self._crawl_stats.record_outcome(UrlOutcome.FETCH_ERROR)
            if recovered:
                self._log.debug("url_requeue", count=recovered)
            return Health("UP", f"recovered={recovered}")
        except Exception as e:
            return Health("DEGRADED", str(e))

    @staticmethod
    async def _check_cleanup(name: str, coro) -> Health:
        try:
            await coro
            return Health("UP")
        except Exception as e:
            return Health("DEGRADED", f"{name} cleanup failed: {e}")

    async def _resource_snapshot(self) -> Health:
        try:
            parts = []
            try:
                import psutil

                mem_mb = psutil.Process().memory_info().rss / 1024 / 1024
                parts.append(f"内存={mem_mb:.0f}MB")
            except ImportError:
                pass
            if self._state and self._st.db_path:
                try:
                    import os

                    db_size = os.path.getsize(self._st.db_path)
                    parts.append(f"DB={db_size / 1024 / 1024:.1f}MB")
                except Exception:
                    pass
            if self._state:
                qsize = await self._st.queue_size()
                parts.append(f"队列={qsize}")
            if self._state:
                iflight = await self._st.in_flight_count()
                if iflight:
                    parts.append(f"处理中={iflight}")
            parts.append(f"已完成={self._crawl_stats.completed_urls}")
            if parts:
                self._log.info("resource_snapshot", details=" ".join(parts))
            if self._state:
                try:
                    snapshot = await self._crawl_stats.to_snapshot()
                    await self._st.set_meta("stats_snapshot", json.dumps(snapshot))
                except Exception:
                    pass
            return Health("UP")
        except Exception as e:
            return Health("DEGRADED", str(e))

    async def _run_worker_loop(
        self,
        domain_limiter: "DomainRateLimiter",
        domain_concurrency: "DomainConcurrencyLimiter",
        robots_cache: Optional["RobotsCache"],
    ) -> None:
        """启动 Worker 协程、主事件循环、关闭序列（drain/cancel/force_fail）。

        生产路径在 async_playwright 上下文管理器内调用；
        测试路径（FakeBrowserPool 注入时）在无浏览器环境下直接调用。
        """
        # 构建 Processor Chain
        deps = self._build_pipeline_deps(domain_limiter, domain_concurrency, robots_cache)
        pipeline = Pipeline(
            _domain_concurrency_processor,
            _robots_processor,
            _rate_limit_processor,
            _fetch_processor,
            _parse_processor,
            _content_dedup_processor,
            _finalize_processor,
            _enqueue_links_processor,
        )

        self._tracker = LivenessTracker(self.concurrency, WORKER_STUCK_TIMEOUT)
        self._supervisor = WorkerSupervisor(
            on_fatal=self._diagnostics.on_fatal if self._diagnostics else None,
        )
        await self._supervisor.start(
            self.concurrency,
            lambda idx: self._worker(
                idx,
                domain_limiter,
                domain_concurrency,
                robots_cache,
                pipeline,
                deps,
            ),
        )
        sv_task = asyncio.create_task(self._supervisor.supervise(self._stop_event), name="WorkerSupervisor")
        self._background_tasks.append(sv_task)

        # 启动时稽核：page_timeout × max_retries 是否超出 pipeline 预算
        single_attempt = self.cfg.page_timeout / 1000.0 + BROWSER_GOTO_BUFFER_S
        backoff_total = sum(self.cfg.retry_backoff_base**i for i in range(self.cfg.max_retries))
        fetch_budget = single_attempt * self.cfg.max_retries + backoff_total
        max_budget = PROCESS_URL_TIMEOUT - FETCH_PROCESSOR_OVERHEAD
        if fetch_budget > max_budget:
            self._log.info(
                "timeout_budget",
                page_timeout=f"{self.cfg.page_timeout}ms",
                max_retries=self.cfg.max_retries,
                fetch_budget=f"{fetch_budget:.1f}s",
                max_budget=f"{max_budget:.1f}s",
                capped=f"{max_budget:.1f}s",
            )

        self._log.info("crawl_start", concurrency=self.concurrency, depth=self.depth)

        stuck_in_flight_since: Optional[float] = None
        while not self._stop_event.is_set():
            if self.cfg.max_runtime_seconds > 0 and (time.time() - self._start_time) > self.cfg.max_runtime_seconds:
                self._log.info("crawl_stop", reason="runtime_exceeded")
                break
            if self.cfg.max_total_pages > 0 and self._crawl_stats.completed_urls >= self.cfg.max_total_pages:
                self._log.info("crawl_stop", reason="max_pages_reached")
                break
            qsize = await self._st.queue_size()
            if qsize == 0:
                await asyncio.sleep(RUN_EMPTY_QUEUE_WAIT)
                if await self._st.queue_size() == 0:
                    await asyncio.sleep(RUN_EMPTY_QUEUE_CONFIRM)
                    if await self._st.queue_size() == 0:
                        iflight = await self._st.in_flight_count()
                        if (
                            iflight == 0
                            and await self._st.retryable_failure_count(self.cfg.max_requeue) == 0
                            and (
                                not self.cfg.use_sitemap
                                or self._sitemap_discovery is None
                                or self._sitemap_discovery.discovery_done.is_set()
                            )
                        ):
                            break
                        # 暂停期间 in_flight 悬挂是预期行为，不累计计时
                        if not self._pause_event.is_set():
                            stuck_in_flight_since = None
                        elif stuck_in_flight_since is None:
                            stuck_in_flight_since = time.time()
                        elif time.time() - stuck_in_flight_since > STUCK_IN_FLIGHT_TIMEOUT:
                            self._log.error(
                                "worker_stuck",
                                timeout=STUCK_IN_FLIGHT_TIMEOUT,
                                in_flight=await self._st.in_flight_count(),
                            )
                            break
            else:
                stuck_in_flight_since = None
                # 防御：若队列非空但活跃域名为空 → 全是 domain='' 的孤立条目。
                # 正常时不应存在；若因外部输入逃逸至此，立即清理避免卡死检测误触发。
                if not await self._st.get_active_domains():
                    orphans = await self._st.purge_orphaned_queue_entries()
                    if orphans:
                        self._log.warning("orphan_purge", count=orphans)
                        await asyncio.sleep(WORKER_IDLE_SLEEP)
                        continue
                # 暂停期间 worker 合法阻塞在 _pause_event.wait()，
                # 不调 heartbeat() 是预期行为，不应触发卡死检测。
                if self._pause_event.is_set():
                    if self._tracker and self._tracker.stale_count > 0:
                        if self._tracker.all_stale:
                            self._log.error(
                                "worker_stuck",
                                timeout=WORKER_STUCK_TIMEOUT,
                                in_flight=await self._st.in_flight_count(),
                                queue=await self._st.queue_size(),
                            )
                            if self._diagnostics:
                                await self._diagnostics.on_fatal("卡死检测: 所有 Worker 停滞")
                            break
                await asyncio.sleep(WORKER_IDLE_SLEEP)

        self._stop_event.set()
        drain_deadline = time.time() + IN_FLIGHT_DRAIN_TIMEOUT
        while time.time() < drain_deadline:
            remaining = await self._st.in_flight_count()
            if remaining == 0:
                break
            await asyncio.sleep(0.5)
        # 关闭 Supervisor + Worker
        # 对标 Kubernetes Pod 终止：先标记关闭（停新请求），再等现有任务排空
        if self._browser_pool:
            self._browser_pool.stop_accepting()
        sv_task.cancel()
        workers = self._supervisor.tasks
        for w in workers:
            w.cancel()
        try:
            await asyncio.wait_for(
                asyncio.gather(sv_task, *workers, return_exceptions=True),
                timeout=60.0,
            )
        except asyncio.TimeoutError:
            self._log.error("worker_stuck", timeout="60s")
        # 等待 BrowserPool 中所有 _handle 任务完成（对标 awaitTermination）
        if self._browser_pool:
            await self._browser_pool.drain()

        # 加固同步屏障：等待 LivenessTracker 确认所有 Worker 心跳停止
        if self._tracker:
            barrier_deadline = time.monotonic() + WORKER_STUCK_TIMEOUT + 5.0
            while time.monotonic() < barrier_deadline:
                if self._tracker.alive_count == 0:
                    break
                await asyncio.sleep(0.5)
            if self._tracker.alive_count > 0:
                self._log.error(
                    "worker_stuck",
                    alive=self._tracker.alive_count,
                    phase="shutdown",
                )
        _remaining = await self._st.in_flight_count()
        if _remaining > 0:
            self._log.debug("url_requeue", in_flight_residual=_remaining)
        stuck_count = await self._st.force_fail_all_in_flight(self.cfg.max_requeue)
        if stuck_count:
            for _ in range(stuck_count):
                await self._crawl_stats.record_outcome(UrlOutcome.FETCH_ERROR)
                await self._crawl_stats.record_fetch_error(FetchErrorCategory.GENERIC)
            self._log.warning("url_permanent_fail", count=stuck_count)

    async def _fetch_url(self, url: str, timeout: float) -> FetchAttempt:  # type: ignore[return]
        """统一抓取入口 — BrowserPool Actor 消息模式（含完整重试）。

        timeout 由 _fetch_processor 通过 deadline 传播动态计算，必传。
        契约：始终返回 FetchAttempt，不抛异常（Result 类型模式）。
        """
        try:
            result = await asyncio.wait_for(
                self._browser_pool.send(FetchRequest(url, timeout_ms=self.cfg.page_timeout)),  # type: ignore[union-attr]
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            return FetchAttempt(None, "Fetch timed out", "timeout", False)
        except asyncio.CancelledError:
            return FetchAttempt(None, "Fetch cancelled during shutdown", "generic", True)

        match result:
            case FetchResponse(url=final_url, html=html, status_code=status_code):
                return FetchAttempt(
                    FetchResult(url=final_url, html=html, status_code=status_code),
                    None,
                    "",
                    False,
                )
            case FetchError(error=error, category=category, is_infra=is_infra):
                return FetchAttempt(None, error, category, is_infra)

    def _build_pipeline_deps(
        self,
        domain_limiter: DomainRateLimiter,
        domain_concurrency: DomainConcurrencyLimiter,
        robots_cache: Optional[RobotsCache],
    ) -> PipelineDeps:
        """一次性构造 PipelineDeps — 无 post-construction setter injection。"""
        return PipelineDeps(
            state=self._state,  # type: ignore[arg-type]
            stats=self._crawl_stats,
            writer=self._writer,
            log=self._log,
            stop_event=self._stop_event,
            config=self.cfg,
            global_settings=self._global_settings,
            signals=self.signals,
            max_depth=self.depth,
            robots_cache=robots_cache,
            sitemap_discovery=self._sitemap_discovery,
            domain_limiter=domain_limiter,
            domain_concurrency=domain_concurrency,
            allowed_domains=self.allowed_domains,
            same_domain_only=self.same_domain_only,
            exclude_res=self._exclude_res,
            fetch_url=self._fetch_url,
            increment_progress_plan=self._increment_progress_plan,
            rule_snapshot=self._rule_lifecycle.get_snapshot() if self._rule_lifecycle else RuleSnapshot.default_only(),
            _hash_v2=self._hash_v2,
        )

    async def _send_webhook(self, report: dict) -> None:
        """发送爬取完成 Webhook 通知"""
        if self._http_session is None:
            return
        payload = json.dumps(report, ensure_ascii=False, default=str).encode("utf-8")
        webhook_headers = {"Content-Type": "application/json"}
        try:
            async with self._http_session.post(
                self.cfg.webhook_url, data=payload, headers=webhook_headers, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status < 400:
                    self._log.info("webhook_sent", status=resp.status)
                else:
                    self._log.warning("webhook_error", status=resp.status)
        except Exception as e:
            self._log.warning("webhook_error", error=e)

    async def run(self) -> None:
        self._start_time = time.time()
        self._crawl_stats.start_time = time.time()

        # Playwright 内部在导航超时/关闭时会创建 CDP future，异常 set 后无人
        # await，GC 时触发 "Future exception was never retrieved"。这是 Playwright
        # 实现细节，不影响 AstroCrawl 业务逻辑（所有业务 Future 均有 try/except
        # 兜底）。按模块名统一抑制，避免逐类型枚举漏掉未来新增的错误类型。
        _loop = asyncio.get_running_loop()

        def _async_exc_handler(loop, context):
            exc = context.get("exception")
            if exc is None:
                loop.default_exception_handler(context)
                return
            if isinstance(exc, Exception) and type(exc).__module__.startswith("playwright"):
                return
            loop.default_exception_handler(context)

        _loop.set_exception_handler(_async_exc_handler)

        db_path = self.cfg.db_path or str(self.output_path.with_suffix(".db"))
        if self._state is None:
            self._state = CrawlState(db_path, self.cfg)  # type: ignore[arg-type]
        await self._st.open()

        # ── S4: 规则引擎生命周期初始化 ──
        rule_dirs = setup_rule_directories(self.cfg)
        cleanup_tmp_files(rule_dirs["cache"])
        cleanup_tmp_files(rule_dirs["user"])
        self._rule_lifecycle = RuleLifecycle(
            self.cfg,
            extra_rules_dirs=list(self._global_settings.rules_dirs),
            rules_dirs_enabled=self._global_settings.rules_dirs_enabled,
        )
        self._rule_lifecycle.initial_load()

        # 规则歧义门控 — 阻断爬取以防止非确定性提取结果
        ensure_no_rule_conflicts(self._rule_lifecycle.get_snapshot())

        # 重新初始化 _progress_layers 以匹配当前 depth（必须在 _seed_new_urls 之前）
        self._progress_layers = dict.fromkeys(range(self.depth), (0, 0))

        await self._crawl_stats.set_initial_completed(await self._st.completed_count())
        # 从 DB 恢复会话前 outcome 计数（用于崩溃恢复后合并统计）
        initial_outcomes = await self._st.counts_by_outcome()
        await self._crawl_stats.set_initial_outcomes(initial_outcomes)
        # 恢复 fetch_errors、drops、redirects 等非 outcome 统计
        saved_stats = await self._st.get_meta("stats_snapshot")
        if saved_stats:
            try:
                self._crawl_stats.restore_snapshot(json.loads(saved_stats))
            except (json.JSONDecodeError, KeyError, TypeError):
                pass
        visited_empty = await self._st.urls_table_empty()

        resume = self.cfg.resume_if_exists

        # ── 哈希版本检测（S2: 向后兼容旧爬虫续爬）──
        hash_ver = await self._st.get_meta("hash_version")
        if hash_ver == "2":
            self._hash_v2 = True
        elif visited_empty or not resume:
            self._hash_v2 = True  # 新爬取 → v2，reset_all 后写入
        else:
            self._hash_v2 = False  # 旧爬虫续爬
            self._log.info("hash_compat", version="v1", reason="legacy_resume")

        # ── 深度减小清理 ─────────────────────────────────────
        # 旧任务可能遗留 depth >= 当前 depth 的队列项和失败记录。
        # 必须在 queue_empty 判断前清理，否则这些超深 URL 会被
        # worker 无差别处理（_worker / peek_retryable
        # 均不校验 depth 上限）。清理的队列项暂存至 boundary_links，
        # 待将来 depth 增大时自动恢复。
        if resume and not visited_empty:
            await self._st.purge_failures_depth_ge(self.depth)

        writer_resume = False

        if resume and not visited_empty:
            self._log.info("crawl_resume", completed=self._crawl_stats.initial_completed)
            writer_resume = True
        else:
            self._log.info("crawl_start", phase="fresh")
            await self._st.reset_all()
            await self._st.set_meta("hash_version", "2")
            await self._st.set_meta("progress_layers", "")  # 清除旧任务的进度层存量
            await self._crawl_stats.set_initial_completed(0)
            await self._seed_new_urls()

        # ── 恢复进度层 & 边界链接提升 ─────────────────────────
        # 注意：必须先 promote 再 restore，确保 queue_count_by_depth
        # 能计入刚入队的边界链接，进度条才能正确显示新增层的 plan 计数。
        if resume and not visited_empty:
            # ── 自愈：在 promote 前从 boundary_links 找回丢失的子链接 ─
            # 从持久化的 plan/proc 差值检测缺口（与恢复公式无关），
            # 在 boundary_links 被 promote DELETE 之前执行。
            saved_layers: Dict[int, Tuple[int, int]] = {}
            try:
                saved_json = await self._st.get_meta("progress_layers")
                if saved_json:
                    saved_layers = {int(k): (int(v[0]), int(v[1])) for k, v in json.loads(saved_json).items()}
            except (json.JSONDecodeError, ValueError, TypeError, KeyError):
                pass
            for d in range(1, self.depth):
                if d in saved_layers:
                    saved_proc, saved_plan = saved_layers[d]
                    if saved_plan > saved_proc:
                        await self._heal_from_boundary_links(d)

            # 提升边界链接：旧深度边界处暂存的子链接，若当前 depth 已增大则自动入队
            boundary = await self._st.promote_boundary_links(self.depth)
            if boundary:
                self._log.info("crawl_resume", boundary_links=len(boundary))
                for child_url, target_depth in boundary:
                    if self._stop_event.is_set():
                        break
                    await self._enqueue_url(child_url, target_depth)

            # ── 恢复进度层 ───────────────────────────────
            # plan = proc + db_queued（构造性可达公式）。
            # proc 用持久化兜底（max(saved_proc, db_done)），
            # 防止 crash 前来不及 commit 的 _settle_url 丢数。
            # plan 由 proc 加上 queue 表实有 URL 数确定，数学上必然可达。
            #
            # mark_completed / log_failure 已同步更新 depth 列，
            # completed_by_depth / failed_by_depth 自身准确。
            completed_by_depth = await self._st.completed_count_by_depth()
            failed_by_depth = await self._st.failed_count_by_depth()
            queued_by_depth = await self._st.queue_count_by_depth()

            for d in range(self.depth):
                db_done = completed_by_depth.get(d, 0) + failed_by_depth.get(d, 0)
                db_queued = queued_by_depth.get(d, 0)

                proc = max(saved_layers.get(d, (0, 0))[0], db_done)
                # plan = max(存量入队数, 构造性可达值)
                # proc + db_queued 确保不小于可达底线；
                # saved_plan 保留原始入队总数作为上限参考，
                # 当 saved_plan > proc + db_queued 时差异可见（URL 流失不会静默消失）
                sp = saved_layers.get(d, (0, 0))[1]
                plan = max(sp, proc + db_queued)

                self._progress_layers[d] = (proc, plan)

            await self._seed_new_urls()

        # 持久化当前 max_depth，供后续恢复时比较
        await self._st.set_meta("max_depth", str(self.depth))

        # ── proxy_mode_override 直接覆盖 CrawlerConfig.proxy_mode（独立维度，不进入 ProxyConfig）──
        if self._proxy_mode_override is not None:
            self.cfg = dataclass_replace(self.cfg, proxy_mode=self._proxy_mode_override)

        # ── ProxySession 提前创建（需在 session 之前，供 RobotsCache/SitemapDiscovery 消费）──
        proxy_session = None
        path_switch = self.cfg.get_path_switch()
        if (
            self._proxy_profile is not None
            and self._proxy_profile.proxies
            and (path_switch.main_is_proxy or path_switch.fallback_is_proxy)
        ):
            config = ProxyConfig.from_profile(self._proxy_profile)
            proxy_session = ProxySession(config, health_tracker=self._health_tracker)
            await proxy_session.__aenter__()  # 启动后台探针
        # 向后兼容别名 — GUI 健康条引用此属性（指向 ProxySession 而非 ProxyManager）
        self.proxy_manager = proxy_session  # type: ignore[assignment]
        self.proxy_session = proxy_session

        connector = TCPConnector(
            limit=CONNECTOR_LIMIT, limit_per_host=CONNECTOR_LIMIT_PER_HOST, ttl_dns_cache=DNS_CACHE_TTL
        )
        self._connector = connector
        self._http_session = aiohttp.ClientSession(
            connector=self._connector,
            timeout=aiohttp.ClientTimeout(total=30),
        )

        tracker = DomainTracker(max_concurrency=self.cfg.domain_max_concurrency)
        domain_limiter = DomainRateLimiter(self.cfg, tracker=tracker)  # type: ignore[arg-type]
        domain_concurrency = DomainConcurrencyLimiter(self.cfg.domain_max_concurrency, tracker=tracker)
        robots_cache = RobotsCache(
            self.cfg.robots_user_agent,
            self._http_session,
            self.cfg.robots_cache_ttl,
            self.cfg.robots_cache_max_size,
            domain_rate_limiter=domain_limiter,
            respect_crawl_delay=self.cfg.respect_crawl_delay,
            proxy_session=proxy_session,
            path_switch=path_switch if proxy_session else None,
            max_retries=self.cfg.max_retries,
            retry_backoff_base=self.cfg.retry_backoff_base,
            custom_headers=list(self.cfg.custom_headers),
            auth_basic_user=self.cfg.auth_basic_user,
            auth_basic_pass=self.cfg.auth_basic_pass,
            auth_bearer_token=self.cfg.auth_bearer_token,
        )

        # 预计算种子源站（供 sitemap 和进度行使用）
        origins: Set[str] = set()
        if self.cfg.use_sitemap:
            for url in self.start_urls:
                p = urlparse(url)
                origins.add(f"{p.scheme}://{p.netloc}")
            await self._crawl_stats.set_discovery_total_origins(len(origins), reset_counters=True)

        # 本地别名减少属性访问开销（字段已在 __init__ 中初始化）
        _bt = self._background_tasks

        # ── S6: 远程规则源初始化 ──
        if self.cfg.rules_sources and self._http_session:
            self._source_manager = SourceManager(
                self._http_session,
                rule_dirs["cache"],
                auto_update=self._global_settings.rules_auto_update,
            )
            for src in self.cfg.rules_sources:
                try:
                    self._source_manager.add_source(
                        src.get("name", ""),
                        src.get("url", ""),
                        title=src.get("title", ""),
                    )
                except Exception as exc:
                    self._log.warning("source_add_failed", source=src.get("name", ""), error=exc)

            # 后台更新所有源 (N84: 离线静默跳过)
            async def _background_source_update():
                try:
                    result = await self._source_manager.update_all()
                    if result.get("sources_updated"):
                        self._rule_lifecycle.reload()
                        self._log.info(
                            "rules_reloaded_after_source_update",
                            sources=result["sources_updated"],
                        )
                except Exception as exc:
                    self._log.debug("source_update_background_error", error=exc)

            _bt.append(asyncio.create_task(_background_source_update()))

        if self.cfg.use_sitemap:
            self._sitemap_discovery = SitemapDiscovery(
                http_session=self._http_session,
                robots_cache=robots_cache,
                stats=self._crawl_stats,
                enqueue_callback=self._on_sitemap_url_enqueued,
                stop_event=self._stop_event,
                config=self.cfg,  # type: ignore[arg-type]
                log=self._log,
                proxy_session=proxy_session,
                path_switch=path_switch if proxy_session else None,
            )

            async def _sitemap_wrapper():
                try:
                    self._log.info("sitemap_start", origins=len(origins))
                    await self._sitemap_discovery.start_discovery(origins, enqueue_depth=1)
                except Exception as e:
                    self._log.warning("sitemap_parse_error", error=e)
                finally:
                    snap = await self._crawl_stats.get_snapshot()
                    urls = snap["sitemap_discovered"]
                    if urls == 0:
                        self._log.warning(
                            "sitemap_done_empty",
                            robots_ok=snap["robots_fetch_ok"],
                            robots_fail=snap["robots_fetch_fail"],
                            sitemap_ok=snap["sitemap_fetch_ok"],
                            sitemap_fail=snap["sitemap_fetch_fail"],
                            urls=0,
                        )
                    else:
                        self._log.info(
                            "sitemap_done",
                            robots_ok=snap["robots_fetch_ok"],
                            robots_fail=snap["robots_fetch_fail"],
                            sitemap_ok=snap["sitemap_fetch_ok"],
                            sitemap_fail=snap["sitemap_fetch_fail"],
                            urls=urls,
                        )

            _bt.append(asyncio.create_task(_sitemap_wrapper()))

        await self._writer.start(resume=writer_resume)
        self._reporter = ProgressReporter(
            self._crawl_stats,
            self.signals,
            get_queue_size=self._get_reporter_queue_size,
            get_max_pages=lambda: self.cfg.max_total_pages,
            get_progress_snapshot=lambda: self._progress_snapshot(),
            get_sitemap_active=lambda: (
                self.cfg.use_sitemap
                and self._sitemap_discovery is not None
                and not self._sitemap_discovery.discovery_done.is_set()
            ),
            use_sitemap=self.cfg.use_sitemap,
        )
        _bt.append(asyncio.create_task(self._reporter.run()))

        # ── HealthMonitor: 统一管理所有后台健康检查/清理 ──
        self._health_monitor = HealthMonitor()

        # ── 诊断模块 ─────────────────────────────────────────
        from astrocrawl.diagnostics import CrawlDiagnostics

        self._diagnostics = CrawlDiagnostics(
            asyncio.get_running_loop(),
            health_monitor=self._health_monitor,
        )
        self._diagnostics.register("crawler", self)
        self._diagnostics.install_signal_handler()
        try:
            await self._diagnostics.start_http()
        except OSError as e:
            self._log.warning("diag_port_unavailable", error=e)

        # ── 注册 DB 可重试 URL 回收监视器 ──
        self._health_monitor.register(
            HealthCheckSpec(
                name="retry_monitor",
                interval=RETRY_MONITOR_INTERVAL,
                on_unhealthy=CheckOnUnhealthy.ALERT,
                check=self._check_retryable,
            )
        )
        self._health_monitor.register(
            HealthCheckSpec(
                name="rate_limiter_cleanup",
                interval=RATE_LIMITER_CLEANUP_INTERVAL,
                on_unhealthy=CheckOnUnhealthy.ALERT,
                check=lambda: self._check_cleanup("rate_limiter", domain_limiter.cleanup_periodic()),
                repair=lambda: None,  # type: ignore[arg-type,return-value]
            )
        )
        self._health_monitor.register(
            HealthCheckSpec(
                name="concurrency_limiter_cleanup",
                interval=CONCURRENT_LIMITER_CLEANUP_INTERVAL,
                on_unhealthy=CheckOnUnhealthy.ALERT,
                check=lambda: self._check_cleanup("concurrency", domain_concurrency.cleanup_stale()),
                repair=lambda: None,  # type: ignore[arg-type,return-value]
            )
        )
        self._health_monitor.register(
            HealthCheckSpec(
                name="content_hash_cleanup",
                interval=HASH_CLEANUP_INTERVAL,
                on_unhealthy=CheckOnUnhealthy.REPORT,
                check=lambda: self._check_cleanup(
                    "content_hashes", self._st.clean_content_hashes(time.time() - HASH_MAX_AGE)
                ),
            )
        )
        self._health_monitor.register(
            HealthCheckSpec(
                name="resource_monitor",
                interval=RESOURCE_MONITOR_INTERVAL,
                on_unhealthy=CheckOnUnhealthy.REPORT,
                check=self._resource_snapshot,
            )
        )
        if self._browser_pool:
            self._diagnostics.register("browser_pool", self._browser_pool)

        # ── S8: 规则引擎健康检查 (N64/N65 B 类) ──
        async def _check_rules_health() -> Health:
            if self._rule_lifecycle is None:
                return Health("UP", "not initialized")
            if self._rule_lifecycle.last_load_ok:
                count = len(self._rule_lifecycle.get_snapshot().rules)
                return Health("UP", f"loaded={count}")
            return Health("DEGRADED", self._rule_lifecycle.load_error or "load failed")

        self._health_monitor.register(
            HealthCheckSpec(
                name="rules_engine",
                interval=300,
                on_unhealthy=CheckOnUnhealthy.ALERT,
                check=_check_rules_health,
            )
        )
        if self._diagnostics:
            self._diagnostics.register("rules_engine", self._rule_lifecycle)

        await self._health_monitor.start()

        # ── Phase A: 爬虫执行 ────────────────────────────────
        crawl_error: Optional[str] = None
        try:
            if self._browser_pool is not None:
                # 测试 seam：FakeBrowserPool 由外部注入，跳过 Playwright/ContextPool
                await self._run_worker_loop(domain_limiter, domain_concurrency, robots_cache)
            else:
                async with async_playwright() as pw:
                    # BrowserPool — 多 Chromium 实例，对标 Puppeteer Cluster
                    self._browser_pool = BrowserPool(
                        self.concurrency,
                        self.cfg,
                        proxy_session,
                        global_settings=self._global_settings,
                    )
                    if proxy_session:
                        pre = await proxy_session.probe_all()
                        dead = sum(1 for r in pre.values() if not r.reachable)
                        if dead:
                            self._log.warning("proxy_dead", dead=dead, total=len(pre))
                    await self._browser_pool.start(pw)
                    if self._diagnostics:
                        self._diagnostics.register(
                            "browser_pool",
                            self._browser_pool,
                        )
                    _bt.append(self._browser_pool._actor_task)  # type: ignore[arg-type]
                    _bt.append(self._browser_pool._health_task)  # type: ignore[arg-type]

                    await self._run_worker_loop(domain_limiter, domain_concurrency, robots_cache)
        except Exception as exc:
            self._log.exception("crawl_error", error=exc)
            crawl_error = str(exc)

        # ── Phase B: 报告生成（总是执行，即使爬虫异常也尽力生成部分报告） ──
        self._crawl_stats.end_time = time.time()

        def _safe_is_failure(k: str) -> bool:
            try:
                return UrlOutcome(k).is_failure
            except ValueError:
                return False

        try:
            snap = await self._crawl_stats.get_snapshot()
            outcomes: dict[str, int] = snap["outcomes"]  # type: ignore[assignment]
            failed = sum(c for k, c in outcomes.items() if _safe_is_failure(k))
            dropped = sum(snap["drops"].values())  # type: ignore[attr-defined]
            self._log.info(
                "crawl_done",
                output=self.output_path,
                ok=outcomes.get(UrlOutcome.OK.value, 0),
                denied=outcomes.get(UrlOutcome.ROBOTS_DENIED.value, 0),
                noindex=outcomes.get(UrlOutcome.NOINDEX.value, 0),
                dupe=outcomes.get(UrlOutcome.DUPLICATE.value, 0),
                failed=failed,
                dropped=dropped,
            )
            report = await self.generate_report(str(self.output_path))
            self._last_report = report
            # Webhook 通知（独立 try，失败不影响信号发射）
            if self.cfg.webhook_url:
                try:
                    await self._send_webhook(report)
                except Exception:
                    self._log.warning("webhook_error", exc_info=True)
            if self.signals:
                if crawl_error:
                    self.signals.error.emit(crawl_error)
                self.signals.finished.emit(str(self.output_path), report)
        except Exception as exc:
            self._log.exception("report_error", error=exc)
            if self.signals:
                self.signals.error.emit(crawl_error or str(exc))

        finally:
            async with AsyncExitStack() as cleanup:
                # 注册顺序即资源依赖顺序，__aexit__ 按 LIFO 逆序执行

                if self._browser_pool:

                    @cleanup.push_async_callback
                    async def _():
                        try:
                            await asyncio.wait_for(self._browser_pool.shutdown(), timeout=15)
                        except asyncio.TimeoutError:
                            self._log.warning("cleanup_timeout", resource="browser_pool", timeout="15s")

                all_tasks = getattr(self, "_background_tasks", [])
                cancelled = [t for t in all_tasks if t and not t.done()]
                for t in cancelled:
                    t.cancel()
                if cancelled:  # type: ignore[misc]

                    @cleanup.push_async_callback
                    async def _():
                        try:
                            await asyncio.wait_for(
                                asyncio.gather(*cancelled, return_exceptions=True),
                                timeout=10.0,
                            )
                        except asyncio.TimeoutError:
                            self._log.warning("cleanup_timeout", resource="background_tasks", timeout="10s")

                # Phase 0: 优雅停止 ProxySession（注册在 blanket cancel 之后 → LIFO 先执行）
                # 遵循 Graceful-first, force-later 模式
                if self.proxy_session:  # type: ignore[misc]

                    @cleanup.push_async_callback
                    async def _():
                        try:
                            await self.proxy_session.close()
                        except Exception:
                            pass  # Phase 2 blanket cancel 兜底

                @cleanup.push_async_callback
                async def _():
                    await self._writer.aclose()

                if self._http_session:  # type: ignore[misc]

                    @cleanup.push_async_callback
                    async def _():
                        try:
                            await asyncio.wait_for(self._http_session.close(), timeout=5)
                        except asyncio.TimeoutError:
                            pass

                # 注册在 http_session 之后 → LIFO 先执行 → 在 session 关闭前取消 orphan tasks
                if self._sitemap_discovery is not None:  # type: ignore[misc]

                    @cleanup.push_async_callback
                    async def _():
                        try:
                            await asyncio.shield(self._sitemap_discovery.aclose())
                        except Exception:
                            pass

                if self._connector and not self._connector.closed:  # type: ignore[misc]

                    @cleanup.push_async_callback
                    async def _():
                        try:
                            await asyncio.wait_for(self._connector.close(), timeout=5)
                        except asyncio.TimeoutError:
                            pass

                if self._health_monitor:  # type: ignore[misc]

                    @cleanup.push_async_callback
                    async def _():
                        await self._health_monitor.stop()

                # DB 持久化 + 关闭
                # 注册顺序对应 LIFO 执行：flush → set_meta → close
                # close 最先注册以保证最后执行，flush 最后注册以保证最先执行
                if self._state:  # type: ignore[misc]

                    @cleanup.push_async_callback
                    async def _():
                        try:
                            await asyncio.wait_for(self._st.close(), timeout=10)
                        except asyncio.TimeoutError:
                            pass

                if self._state is not None:

                    @cleanup.push_async_callback
                    async def _():
                        try:
                            await self._st.set_meta(
                                "progress_layers",
                                json.dumps({str(d): [proc, plan] for d, (proc, plan) in self._progress_layers.items()}),
                            )
                        except Exception:
                            self._log.warning("db_error", phase="set_meta_progress", exc_info=True)

                    @cleanup.push_async_callback
                    async def _():
                        try:
                            snapshot = await self._crawl_stats.to_snapshot()
                            await self._st.set_meta("stats_snapshot", json.dumps(snapshot))
                        except Exception:
                            self._log.warning("db_error", phase="set_meta_stats", exc_info=True)

                if self._state:  # type: ignore[misc]

                    @cleanup.push_async_callback
                    async def _():
                        try:
                            await self._st.flush()
                        except Exception:
                            self._log.warning("db_error", phase="flush", exc_info=True)

                if self._diagnostics:  # type: ignore[misc]

                    @cleanup.push_async_callback
                    async def _():
                        try:
                            await self._diagnostics.stop_http()
                        except Exception:
                            pass

    async def generate_report(self, output_path: str) -> dict:
        """生成 JSON 格式的爬取统计报告（含完整的 outcome 分类）。"""
        report: Dict[str, Any] = {}
        cs = self._crawl_stats
        report["start_time"] = (
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(cs.start_time)) if cs.start_time > 0 else ""
        )
        report["end_time"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(cs.end_time)) if cs.end_time > 0 else ""

        # get_snapshot() 已返回全时累计 outcomes（含 DB 恢复的 initial_outcomes）
        snap = await cs.get_snapshot()
        outcomes = snap["outcomes"]
        report["outcome_summary"] = dict(sorted(outcomes.items()))  # type: ignore[attr-defined]

        # 使用 UrlOutcome 枚举属性的 is_success / is_failure，避免硬编码列表
        def _outcome_of(k: str) -> Optional[UrlOutcome]:
            try:
                return UrlOutcome(k)
            except ValueError:
                return None

        total_ok = sum(c for k, c in outcomes.items() if (o := _outcome_of(k)) and o.is_success)  # type: ignore[attr-defined]
        total_fail = sum(c for k, c in outcomes.items() if (o := _outcome_of(k)) and o.is_failure)  # type: ignore[attr-defined]
        total_dropped = sum(snap["drops"].values())  # type: ignore[attr-defined]
        report["total_pages_ok"] = total_ok
        report["total_pages_fail"] = total_fail
        report["total_pages_dropped"] = total_dropped
        report["total_pages_all"] = total_ok + total_fail + total_dropped

        # 内容分类（全时累计，snap 已合并 DB 恢复 + 本轮）
        report["content"] = {
            "saved": outcomes.get(UrlOutcome.OK.value, 0) + outcomes.get(UrlOutcome.TRUNCATED.value, 0),  # type: ignore[attr-defined]
            "noindex_skipped": outcomes.get(UrlOutcome.NOINDEX.value, 0),  # type: ignore[attr-defined]
            "duplicate_skipped": outcomes.get(UrlOutcome.DUPLICATE.value, 0),  # type: ignore[attr-defined]
            "truncated": outcomes.get(UrlOutcome.TRUNCATED.value, 0),  # type: ignore[attr-defined]
            "parse_failures": outcomes.get(UrlOutcome.PARSE_FAILED.value, 0),  # type: ignore[attr-defined]
        }
        report["redirects"] = cs.redirects

        # 抓取错误分类
        report["fetch_errors"] = dict(sorted(cs.fetch_errors.items()))

        # 丢弃原因统计
        report["drops"] = dict(sorted(cs.drops.items()))

        # S8: 规则性能分析（聚合快照）
        rule_perf = await cs.get_rule_stats_snapshot()
        if rule_perf:
            report["rule_performance"] = rule_perf

        # 发现阶段
        report["discovery"] = {
            "robots": {
                "ok": cs.robots_fetch_ok,
                "fetch_fail": cs.robots_fetch_fail,
                "not_checked": cs.robots_not_checked,
            },
            "sitemap": {
                "ok": cs.sitemap_fetch_ok,
                "fetch_fail": cs.sitemap_fetch_fail,
                "discovered_urls": cs.sitemap_discovered,
            },
            "per_origin": dict(sorted(cs.origin_discovery.items())),
        }

        # 代理状态
        report["proxy"] = {
            "mode": self.cfg.proxy_mode,
        }
        # 每代理健康详情
        if self.proxy_session:
            report["proxy"]["health"] = {}
            snapshot = self.proxy_session.get_all_stats()
            for url in self.proxy_session.proxies:
                s = snapshot.get(url)
                if s:
                    report["proxy"]["health"][url] = {
                        "health_score": round(s.health_score, 3),
                        "state": s.state.value,
                        "consecutive_failures": s.consecutive_failures,
                        "total_failures": s.total_failures,
                        "total_successes": s.total_successes,
                        "avg_latency_ms": round(s.avg_latency_ms, 1),
                        "last_failure_at": time.strftime(
                            "%H:%M:%S",
                            time.localtime(s.last_failure_at),
                        )
                        if s.last_failure_at > 0
                        else "",
                        "last_success_at": time.strftime(
                            "%H:%M:%S",
                            time.localtime(s.last_success_at),
                        )
                        if s.last_success_at > 0
                        else "",
                    }

        # 域名统计
        report["domain_stats"] = []
        for domain in sorted(cs.domain_outcomes.keys()):
            outcomes = cs.domain_outcomes[domain]
            dom_ok = sum(c for k, c in outcomes.items() if (o := _outcome_of(k)) and o.is_success)
            dom_fail = sum(c for k, c in outcomes.items() if (o := _outcome_of(k)) and o.is_failure)
            total_ms = cs.domain_timing.get(domain, 0)
            count = cs.domain_timing_count.get(domain, 0)
            avg_ms = total_ms / count if count > 0 else 0
            report["domain_stats"].append(
                {
                    "domain": domain,
                    "ok": dom_ok,
                    "fail": dom_fail,
                    "avg_ms": round(avg_ms, 1),
                    "outcomes": dict(sorted(outcomes.items())),
                }
            )

        # 深度层
        report["depth_layers"] = {}
        async with self._progress_lock:
            for d, (proc, plan) in self._progress_layers.items():
                report["depth_layers"][str(d)] = {"processed": proc, "planned": plan}

        report["total_session"] = cs.session_completed
        report["total_all_time"] = cs.completed_urls
        report["duration_seconds"] = round(cs.end_time - cs.start_time, 1) if cs.start_time > 0 else 0

        try:
            report_path = Path(output_path).with_suffix(".report.json")
            report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
            try:
                os.chmod(report_path, 0o600)
            except Exception:
                pass
            self._log.info("report_saved", path=report_path)
        except Exception as e:
            self._log.warning("report_error", error=e)
        return report

    async def _settle_url(
        self,
        url: str,
        depth: int,
        domain: str,
        elapsed_ms: float,
        outcome: UrlOutcome,
        disposition: UrlDisposition,
        error: str = "",
        state: Optional[CrawlStateProtocol] = None,
    ) -> None:
        """URL 离开 in_flight 的唯一出口。统一处理 in_flight 清理、stats 记录、进度更新。"""
        if disposition == UrlDisposition.REQUEUED:
            return

        # FAILED: 此路径仅记录日志，统计和进度由下方兜底处理
        if disposition == UrlDisposition.FAILED:
            self._log.warning(
                "url_permanent_fail",
                url=safe_log_url(url),
                depth=depth,
                error=error,
            )
            if state is not None:
                await state.log_failure(url, depth, error, permanent=True)

        await self._crawl_stats.record_outcome(outcome, domain, elapsed_ms)
        async with self._progress_lock:
            proc, plan = self._progress_layers.get(depth, (0, 0))
            self._progress_layers[depth] = (proc + 1, plan)
        if outcome.is_success:
            await self._crawl_stats.inc_session_completed()
        display_url = safe_log_url(url)
        status = "✓" if outcome.is_success else "✗"
        if outcome.is_success:
            self._log.debug("url_complete", status=status, url=display_url, depth=depth, outcome=outcome.value)
        else:
            self._log.warning("url_complete", status=status, url=display_url, depth=depth, outcome=outcome.value)

    def request_pause(self) -> None:
        self._pause_event.clear()
        self._log.info("crawl_pause", completed=self._crawl_stats.completed_urls)
        if self.signals:
            self.signals.pause_state.emit(True)

    def request_resume(self) -> None:
        self._pause_event.set()
        self._log.info("crawl_resume", completed=self._crawl_stats.completed_urls)
        if self.signals:
            self.signals.pause_state.emit(False)

    @property
    def crawl_stats(self) -> CrawlStats:
        """公开的统计容器，供 CLI/GUI 读取实时/最终统计。"""
        return self._crawl_stats

    def request_stop(self) -> None:
        self._stop_event.set()
        self._pause_event.set()

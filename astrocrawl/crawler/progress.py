"""统一的用户反馈发射器 — CLI 进度行、GUI 信号、完成摘要。"""

from __future__ import annotations

import asyncio
import sys
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional

from astrocrawl.crawler.outcomes import UrlOutcome

if TYPE_CHECKING:
    from astrocrawl.crawler.outcomes import CrawlStats
    from astrocrawl.crawler.signals import CrawlerSignals


class ProgressReporter:
    """从 CrawlStats + engine 只读获取数据，统一路由到所有反馈通道。

    职责:
    - CLI 模式: 每 5s 打印一行覆盖式进度到 stderr
    - GUI 模式: 每 1s 发射 stats_update / layer_progress, 每 5s 发射 outcome_update
    - 完成后: print_summary() 生成统一 CLI 摘要
    """

    def __init__(
        self,
        stats: "CrawlStats",
        signals: Optional["CrawlerSignals"],
        get_queue_size: Callable[[], "Any"],
        get_max_pages: Callable[[], int],
        get_progress_snapshot: Callable[[], "Any"],
        get_sitemap_active: Callable[[], bool],
        use_sitemap: bool,
    ) -> None:
        self._stats = stats
        self._signals = signals
        self._get_queue_size = get_queue_size
        self._get_max_pages = get_max_pages
        self._get_progress_snapshot = get_progress_snapshot
        self._get_sitemap_active = get_sitemap_active
        self._use_sitemap = use_sitemap
        self._counter = 0
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    async def run(self) -> None:
        """后台协程：每 1s 刷新一次进度。"""
        try:
            while not self._stop:
                try:
                    await asyncio.sleep(1.0)
                    await self._tick()
                except asyncio.CancelledError:
                    break
                except Exception:
                    pass  # 单次 tick 失败不影响后续
        finally:
            # 释放 CLI 进度行（\r 覆写不换行，print 空行）
            if not self._signals:
                print(file=sys.stderr)

    async def _tick(self) -> None:
        """单次进度时钟。"""
        snap = await self._stats.get_snapshot()
        rule_snap = await self._stats.get_rule_stats_snapshot()
        qsize = await self._get_queue_size()
        self._counter += 1

        if self._signals:
            self._emit_gui_signals(snap, qsize, rule_snap)
        else:
            if self._counter % 5 == 0:
                self._print_cli_line(snap, qsize)

    def _emit_gui_signals(self, snap: Dict[str, object], qsize: int, rule_snap: Dict[str, object]) -> None:
        assert self._signals is not None
        # 每秒发射
        self._signals.stats_update.emit(snap["completed_urls"], qsize, self._get_max_pages())
        layers = self._get_progress_snapshot()
        for d, (proc, plan) in layers.items():
            self._signals.layer_progress.emit(d, proc, plan)
        # 每 5 秒发射 outcome 摘要 + 规则统计快照
        if self._counter % 5 == 0:
            outcomes: dict = snap["outcomes"]  # type: ignore[assignment]
            payload: Dict[str, Any] = {
                "ok": outcomes.get(UrlOutcome.OK.value, 0),
                "robots_denied": outcomes.get(UrlOutcome.ROBOTS_DENIED.value, 0),
                "noindex": outcomes.get(UrlOutcome.NOINDEX.value, 0),
                "duplicate": outcomes.get(UrlOutcome.DUPLICATE.value, 0),
                "fetch_failures": (
                    outcomes.get(UrlOutcome.FETCH_ERROR.value, 0)
                    + outcomes.get(UrlOutcome.INTERNAL_ERROR.value, 0)
                    + outcomes.get(UrlOutcome.STOPPED.value, 0)
                ),
                "dropped": sum(snap["drops"].values()),  # type: ignore[attr-defined]
                # 发现阶段实时计数
                # 使用 discovery_* 计数器（发现阶段 per-origin 完成数），
                # 而非 robots_fetch_* (worker 触发，可能因重定向超出 origin 总数)
                "robots_done": snap["discovery_robots_done"],
                "robots_total": snap["discovery_total_origins"],
                "sitemap_done": snap["discovery_sitemap_done"],
                "sitemap_total": snap["discovery_total_origins"],
                "sitemap_urls": snap["sitemap_discovered"],
                "sitemap_active": self._get_sitemap_active(),
            }
            self._signals.outcome_update.emit(payload)
            # S8: 规则聚合统计快照
            if rule_snap:
                self._signals.rule_stats_updated.emit(rule_snap)

    def _print_cli_line(self, snap: Dict[str, object], qsize: int) -> None:
        total = self._get_max_pages() or "?"
        line = f"\r  已完成: {snap['completed_urls']}  |  队列: {qsize}  |  上限: {total}  "
        if self._use_sitemap and self._get_sitemap_active():
            robots_done = snap["discovery_robots_done"]
            sitemap_done = snap["discovery_sitemap_done"]
            total_origins = snap["discovery_total_origins"] or "?"
            discovered_urls = snap["sitemap_discovered"]
            line += f"|  robots: {robots_done}/{total_origins}  |  sitemap: {sitemap_done}/{total_origins}, {discovered_urls} URLs  "
        print(line, end="", file=sys.stderr)

    # ── 完成摘要 ──

    def print_summary(self, output_path: str, report: Optional[dict] = None) -> None:
        """打印 CLI 爬取完成摘要（从 generate_report() 的统一格式渲染）。"""
        if report is None:
            return
        self._print_from_report(output_path, report)

    def _print_from_report(self, output_path: str, report: dict) -> None:
        """从统一的 generate_report() dict 渲染 CLI 摘要。"""
        summary = report.get("outcome_summary", {})
        ok = summary.get("ok", 0)
        robots = summary.get("robots_denied", 0)
        noindex = summary.get("noindex", 0)
        dups = summary.get("duplicate", 0)
        truncated = summary.get("truncated", 0)
        parse_fail = summary.get("parse_failed", 0)
        fetch_fail = report.get("total_pages_fail", 0)
        dropped = report.get("total_pages_dropped", 0)
        all_pages = report.get("total_pages_all", 0)

        ds = report.get("duration_seconds")
        dur = f"  耗时:                  {ds:>6.1f}s" if ds else ""

        print(file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        print("  爬取摘要", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        print(f"  总计处理:       {all_pages:>6}", file=sys.stderr)
        print(f"    成功 (已保存):       {ok:>6}", file=sys.stderr)
        print(f"    robots.txt 拒绝:     {robots:>6}", file=sys.stderr)
        print(f"    noindex (未保存):     {noindex:>6}", file=sys.stderr)
        print(f"    重复内容 (未保存):   {dups:>6}", file=sys.stderr)
        print(f"    已截断 (部分保存):   {truncated:>6}", file=sys.stderr)
        print(f"    解析失败 (空内容):   {parse_fail:>6}", file=sys.stderr)
        print(f"    抓取失败:             {fetch_fail:>6}", file=sys.stderr)
        if dropped:
            print(f"    过滤丢弃:             {dropped:>6}", file=sys.stderr)
        if report.get("fetch_errors"):
            print("-" * 60, file=sys.stderr)
            print("  失败分类:", file=sys.stderr)
            for cat, cnt in sorted(report["fetch_errors"].items()):
                print(f"    {cat}: {cnt}", file=sys.stderr)
        discovery = report.get("discovery", {})
        robots = discovery.get("robots", {})
        sitemap = discovery.get("sitemap", {})
        print("-" * 60, file=sys.stderr)
        print(f"  重定向:                {report.get('redirects', 0):>6}", file=sys.stderr)
        print(
            f"  Sitemap:     ok={sitemap.get('ok', 0)}, "
            f"fail={sitemap.get('fetch_fail', 0)}, "
            f"发现={sitemap.get('discovered_urls', 0)}",
            file=sys.stderr,
        )
        print(
            f"  Robots.txt:  ok={robots.get('ok', 0)}, "
            f"fail={robots.get('fetch_fail', 0)}, "
            f"未检查={robots.get('not_checked', 0)}",
            file=sys.stderr,
        )
        rule_perf = report.get("rule_performance", {})
        if rule_perf:
            print("-" * 60, file=sys.stderr)
            print("  规则性能:", file=sys.stderr)
            for rule_name, stats in sorted(rule_perf.items()):
                print(
                    f"    {rule_name}: 命中={stats.get('hits', 0)}, "
                    f"填充率={stats.get('fill_rate', 0) * 100:.0f}%, "
                    f"平均={stats.get('avg_ms', 0):.0f}ms, "
                    f"慢查询={stats.get('slow_count', 0)}",
                    file=sys.stderr,
                )
        if dur:
            print(dur, file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        print(f"  输出: {output_path}", file=sys.stderr)
        from pathlib import Path

        op = Path(output_path)
        print(f"  报告: {op.with_suffix('.report.json')}", file=sys.stderr)
        print("=" * 60, file=sys.stderr)

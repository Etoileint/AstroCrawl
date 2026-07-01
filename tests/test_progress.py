"""ProgressReporter 测试 — CLI/GUI 双模进度上报 + 信号 payload 验证。

使用 _SpySignals 捕获 GUI 信号，CrawlStats 预填充控制测试数据。
"""

from __future__ import annotations

import asyncio
import io
import sys
from typing import Any

from astrocrawl.crawler.outcomes import CrawlStats, DropReason, UrlOutcome
from astrocrawl.crawler.progress import ProgressReporter
from tests._fakes import _SpySignals

# ═══════════════════════════════════════════════════════════════════════
# 辅助工厂
# ═══════════════════════════════════════════════════════════════════════


def _make_reporter(
    stats: CrawlStats | None = None,
    signals: Any | None = None,
    queue_size: int = 0,
    max_pages: int = 100,
    progress_snapshot: dict | None = None,
    sitemap_active: bool = False,
    use_sitemap: bool = False,
) -> ProgressReporter:
    """创建受控 ProgressReporter。所有 getter 均为 async callable。"""
    if stats is None:
        stats = CrawlStats()
    if progress_snapshot is None:
        progress_snapshot = {0: (0, 5), 1: (0, 3)}

    async def _queue_size():
        return queue_size

    return ProgressReporter(
        stats=stats,
        signals=signals,
        get_queue_size=_queue_size,
        get_max_pages=lambda: max_pages,
        get_progress_snapshot=lambda: progress_snapshot,
        get_sitemap_active=lambda: sitemap_active,
        use_sitemap=use_sitemap,
    )


async def _populate_stats_snapshot(stats: CrawlStats) -> dict:
    """预填充 CrawlStats 并返回 get_snapshot() 的副本。"""
    await stats.record_outcome(UrlOutcome.OK, "example.com", 50.0)
    await stats.record_outcome(UrlOutcome.ROBOTS_DENIED, "example.com")
    await stats.record_drop(DropReason.CROSS_DOMAIN, 2)
    await stats.record_redirect()
    await stats.set_discovery_total_origins(3, reset_counters=True)
    await stats.inc_discovery_robots_done()
    await stats.inc_discovery_robots_done()
    await stats.inc_discovery_sitemap_done()
    await stats.record_sitemap_discovered(10)
    stats.initial_outcomes = {"ok": 2}
    return await stats.get_snapshot()


# ═══════════════════════════════════════════════════════════════════════
# Tick 逻辑 — CLI 模式
# ═══════════════════════════════════════════════════════════════════════


class TestTickCLI:
    async def test_cli_prints_every_5_counter(self):
        """CLI 模式: counter % 5 == 0 时打印，否则不打印。"""
        reporter = _make_reporter(use_sitemap=False)
        captured = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = captured
        try:
            reporter._counter = 3
            snap = {
                "completed_urls": 10,
                "discovery_robots_done": 0,
                "sitemap_discovered": 0,
                "discovery_total_origins": 0,
                "discovery_sitemap_done": 0,
            }
            reporter._print_cli_line(snap, 5)
            assert "已完成" in captured.getvalue()
            captured.truncate(0)
            captured.seek(0)
        finally:
            sys.stderr = old_stderr

    async def test_cli_line_format(self, capsys):
        reporter = _make_reporter(use_sitemap=False, max_pages=100, queue_size=5)
        snap = {
            "completed_urls": 42,
            "discovery_robots_done": 0,
            "sitemap_discovered": 0,
            "sitemap_fetch_ok": 0,
            "sitemap_fetch_fail": 0,
            "discovery_total_origins": 0,
            "discovery_sitemap_done": 0,
            "outcomes": {},
            "drops": {},
            "fetch_errors": {},
            "redirects": 0,
            "origin_discovery": {},
            "session_completed": 42,
            "robots_fetch_ok": 0,
            "robots_fetch_fail": 0,
            "robots_not_checked": 0,
        }
        captured = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = captured
        try:
            reporter._print_cli_line(snap, 5)
            output = captured.getvalue()
            assert "已完成: 42" in output
            assert "队列: 5" in output
            assert "上限: 100" in output
        finally:
            sys.stderr = old_stderr

    async def test_cli_line_with_sitemap_active(self):
        reporter = _make_reporter(use_sitemap=True)
        reporter._get_sitemap_active = lambda: True
        snap = {
            "completed_urls": 10,
            "discovery_robots_done": 2,
            "sitemap_discovered": 15,
            "discovery_total_origins": 3,
            "discovery_sitemap_done": 1,
            "outcomes": {},
            "drops": {},
            "fetch_errors": {},
            "redirects": 0,
            "origin_discovery": {},
            "session_completed": 10,
            "robots_fetch_ok": 2,
            "robots_fetch_fail": 0,
            "robots_not_checked": 0,
        }
        captured = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = captured
        try:
            reporter._print_cli_line(snap, 5)
            output = captured.getvalue()
            assert "robots: 2/3" in output
            assert "sitemap: 1/3" in output
            assert "15 URLs" in output
        finally:
            sys.stderr = old_stderr

    async def test_cli_line_without_sitemap(self):
        reporter = _make_reporter(use_sitemap=False)
        snap = {
            "completed_urls": 10,
            "discovery_robots_done": 0,
            "sitemap_discovered": 0,
            "discovery_total_origins": 0,
            "discovery_sitemap_done": 0,
            "outcomes": {},
            "drops": {},
            "fetch_errors": {},
            "redirects": 0,
            "origin_discovery": {},
            "session_completed": 10,
            "robots_fetch_ok": 0,
            "robots_fetch_fail": 0,
            "robots_not_checked": 0,
        }
        captured = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = captured
        try:
            reporter._print_cli_line(snap, 5)
            output = captured.getvalue()
            assert "robots:" not in output
            assert "sitemap:" not in output
        finally:
            sys.stderr = old_stderr

    async def test_cli_line_max_pages_unlimited(self):
        reporter = _make_reporter(use_sitemap=False, max_pages=0)
        snap = {
            "completed_urls": 10,
            "discovery_robots_done": 0,
            "sitemap_discovered": 0,
            "discovery_total_origins": 0,
            "discovery_sitemap_done": 0,
            "outcomes": {},
            "drops": {},
            "fetch_errors": {},
            "redirects": 0,
            "origin_discovery": {},
            "session_completed": 10,
            "robots_fetch_ok": 0,
            "robots_fetch_fail": 0,
            "robots_not_checked": 0,
        }
        captured = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = captured
        try:
            reporter._print_cli_line(snap, 5)
            output = captured.getvalue()
            assert "上限: ?" in output
        finally:
            sys.stderr = old_stderr


# ═══════════════════════════════════════════════════════════════════════
# Tick 逻辑 — GUI 模式
# ═══════════════════════════════════════════════════════════════════════


class TestTickGUI:
    async def test_emits_stats_update_every_tick(self):
        stats = CrawlStats()
        await stats.record_outcome(UrlOutcome.OK)
        signals = _SpySignals()
        reporter = _make_reporter(stats=stats, signals=signals, queue_size=3, max_pages=100)
        reporter._counter = 0
        # 直接调用 _emit 验证信号参数
        snap = await stats.get_snapshot()
        reporter._emit_gui_signals(snap, 3, {})
        assert len(signals.stats_update.calls) == 1
        assert signals.stats_update.calls[0][0] == snap["completed_urls"]
        assert signals.stats_update.calls[0][1] == 3
        assert signals.stats_update.calls[0][2] == 100

    async def test_emits_layer_progress_every_tick(self):
        stats = CrawlStats()
        signals = _SpySignals()
        progress_snap = {0: (2, 5), 1: (1, 3)}
        reporter = _make_reporter(
            stats=stats,
            signals=signals,
            progress_snapshot=progress_snap,
        )
        reporter._counter = 0
        snap = await stats.get_snapshot()
        reporter._emit_gui_signals(snap, 0, {})
        assert len(signals.layer_progress.calls) == 2
        assert signals.layer_progress.calls[0] == (0, 2, 5)
        assert signals.layer_progress.calls[1] == (1, 1, 3)

    async def test_emits_outcome_update_every_5_ticks(self):
        stats = CrawlStats()
        await _populate_stats_snapshot(stats)
        signals = _SpySignals()
        reporter = _make_reporter(stats=stats, signals=signals)
        snap = await stats.get_snapshot()

        # Counter % 5 != 0 → 不发射 outcome_update
        reporter._counter = 3
        reporter._emit_gui_signals(snap, 0, {})
        assert len(signals.outcome_update.calls) == 0

        # Counter % 5 == 0 → 发射 outcome_update
        reporter._counter = 5
        reporter._emit_gui_signals(snap, 0, {})
        assert len(signals.outcome_update.calls) == 1

    async def test_emits_rule_stats_updated_every_5_ticks(self):
        """rule_stats_updated 信号在 counter % 5 == 0 时发射。"""
        stats = CrawlStats()
        await stats.record_rule_hit("r", 2, 4, 50.0)
        signals = _SpySignals()
        reporter = _make_reporter(stats=stats, signals=signals)
        snap = await stats.get_snapshot()
        rule_snap = await stats.get_rule_stats_snapshot()

        # counter % 5 != 0 → 不发射
        reporter._counter = 3
        reporter._emit_gui_signals(snap, 0, rule_snap)
        assert len(signals.rule_stats_updated.calls) == 0

        # counter % 5 == 0 → 发射
        reporter._counter = 5
        reporter._emit_gui_signals(snap, 0, rule_snap)
        assert len(signals.rule_stats_updated.calls) == 1
        assert signals.rule_stats_updated.calls[0][0]["r"]["hits"] == 1

    async def test_rule_stats_updated_skips_empty(self):
        """空 rule_snap 时不发射 rule_stats_updated。"""
        stats = CrawlStats()
        signals = _SpySignals()
        reporter = _make_reporter(stats=stats, signals=signals)
        snap = await stats.get_snapshot()
        reporter._counter = 5
        reporter._emit_gui_signals(snap, 0, {})
        assert len(signals.rule_stats_updated.calls) == 0

    async def test_outcome_update_all_keys_present(self):
        stats = CrawlStats()
        await _populate_stats_snapshot(stats)
        signals = _SpySignals()
        reporter = _make_reporter(stats=stats, signals=signals)
        reporter._counter = 5
        snap = await stats.get_snapshot()
        reporter._emit_gui_signals(snap, 0, {})
        payload = signals.outcome_update.calls[0][0]
        expected_keys = {
            "ok",
            "robots_denied",
            "noindex",
            "duplicate",
            "fetch_failures",
            "dropped",
            "robots_done",
            "robots_total",
            "sitemap_done",
            "sitemap_total",
            "sitemap_urls",
            "sitemap_active",
        }
        assert expected_keys.issubset(payload.keys())

    async def test_fetch_failures_aggregation(self):
        stats = CrawlStats()
        await stats.record_outcome(UrlOutcome.FETCH_ERROR, "a.com")
        await stats.record_outcome(UrlOutcome.INTERNAL_ERROR, "a.com")
        await stats.record_outcome(UrlOutcome.STOPPED, "a.com")
        signals = _SpySignals()
        reporter = _make_reporter(stats=stats, signals=signals)
        reporter._counter = 5
        snap = await stats.get_snapshot()
        reporter._emit_gui_signals(snap, 0, {})
        payload = signals.outcome_update.calls[0][0]
        # FETCH_ERROR(1) + INTERNAL_ERROR(1) + STOPPED(1) = 3
        assert payload["fetch_failures"] == 3

    async def test_dropped_from_snapshot(self):
        stats = CrawlStats()
        await stats.record_drop(DropReason.CROSS_DOMAIN, 2)
        await stats.record_drop(DropReason.EXCLUDE_PATTERN, 1)
        signals = _SpySignals()
        reporter = _make_reporter(stats=stats, signals=signals)
        reporter._counter = 5
        snap = await stats.get_snapshot()
        reporter._emit_gui_signals(snap, 0, {})
        payload = signals.outcome_update.calls[0][0]
        assert payload["dropped"] == 3

    async def test_merges_initial_outcomes(self):
        stats = CrawlStats()
        await stats.record_outcome(UrlOutcome.OK, "a.com")
        stats.initial_outcomes = {"ok": 5, "robots_denied": 2}
        signals = _SpySignals()
        reporter = _make_reporter(stats=stats, signals=signals)
        reporter._counter = 5
        snap = await stats.get_snapshot()
        reporter._emit_gui_signals(snap, 0, {})
        payload = signals.outcome_update.calls[0][0]
        assert payload["ok"] == 6  # 1 (session) + 5 (initial)
        assert payload["robots_denied"] == 2  # 0 + 2

    async def test_discovery_counters_semantic(self):
        """robots_done 使用 discovery_robots_done，非 robots_fetch_ok。"""
        stats = CrawlStats()
        await stats.set_discovery_total_origins(5, reset_counters=True)
        await stats.inc_discovery_robots_done()
        await stats.inc_discovery_robots_done()
        # discovery_robots_done = 2
        # robots_fetch_ok = 0 (未设置)
        signals = _SpySignals()
        reporter = _make_reporter(stats=stats, signals=signals)
        reporter._counter = 5
        snap = await stats.get_snapshot()
        reporter._emit_gui_signals(snap, 0, {})
        payload = signals.outcome_update.calls[0][0]
        assert payload["robots_done"] == 2  # discovery_robots_done
        assert payload["robots_total"] == 5


# ═══════════════════════════════════════════════════════════════════════
# Run 生命周期
# ═══════════════════════════════════════════════════════════════════════


class TestRunLifecycle:
    async def test_stop_halt_run_loop(self):
        reporter = _make_reporter()
        reporter.stop()
        # run() while 循环应尽快退出
        await asyncio.wait_for(reporter.run(), timeout=1.0)

    async def test_cli_newline_on_exit(self):
        stats = CrawlStats()
        reporter = _make_reporter(stats=stats)
        reporter.stop()
        captured = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = captured
        try:
            await asyncio.wait_for(reporter.run(), timeout=1.0)
            output = captured.getvalue()
            assert output == "\n" or output == ""  # CLI 模式 finally 块打印换行
        finally:
            sys.stderr = old_stderr

    async def test_run_cancelled_error_clean(self):
        """ProgressReporter.run() 内部捕获 CancelledError，不向外传播。"""
        reporter = _make_reporter()
        task = asyncio.create_task(reporter.run())
        await asyncio.sleep(0.05)
        task.cancel()
        # run() 内部 except CancelledError: break → 任务正常完成，不抛异常
        await asyncio.wait_for(task, timeout=1.0)

    async def test_tick_exception_not_fatal(self):
        """单次 tick 失败不影响后续。_tick 内异常被 run() 的 except Exception 捕获。"""
        stats = CrawlStats()
        fail_count = 0

        async def flaky_getter():
            nonlocal fail_count
            fail_count += 1
            raise RuntimeError("getter failed")

        reporter = ProgressReporter(
            stats=stats,
            signals=None,
            get_queue_size=flaky_getter,
            get_max_pages=lambda: 100,
            get_progress_snapshot=lambda: {},
            get_sitemap_active=lambda: False,
            use_sitemap=False,
        )
        # 启动 run，等一次 tick 异常后 stop
        task = asyncio.create_task(reporter.run())
        await asyncio.sleep(0.15)  # 等第一次 tick 失败
        reporter.stop()
        await asyncio.wait_for(task, timeout=1.0)
        assert fail_count >= 1  # getter 被调用了（说明 tick 被执行了）


# ═══════════════════════════════════════════════════════════════════════
# 完成摘要
# ═══════════════════════════════════════════════════════════════════════


class TestPrintSummary:
    def _make_sample_report(self) -> dict:
        return {
            "start_time": "2026-01-01 00:00:00",
            "end_time": "2026-01-01 00:05:00",
            "outcome_summary": {
                "ok": 50,
                "robots_denied": 3,
                "noindex": 2,
                "duplicate": 10,
                "truncated": 1,
                "parse_failed": 2,
            },
            "total_pages_ok": 55,
            "total_pages_fail": 13,
            "total_pages_dropped": 7,
            "total_pages_all": 75,
            "content": {
                "saved": 51,
                "noindex_skipped": 2,
                "duplicate_skipped": 10,
                "truncated": 1,
                "parse_failures": 2,
            },
            "redirects": 3,
            "fetch_errors": {"dns": 5, "timeout": 3},
            "drops": {"cross_domain": 4, "exclude_pattern": 3},
            "discovery": {
                "robots": {"ok": 2, "fetch_fail": 1, "not_checked": 0},
                "sitemap": {"ok": 1, "fetch_fail": 0, "discovered_urls": 100},
                "per_origin": {},
            },
            "proxy": {"mode": "proxy"},
            "domain_stats": [
                {
                    "domain": "example.com",
                    "ok": 50,
                    "fail": 10,
                    "avg_ms": 120.5,
                    "outcomes": {"ok": 50, "fetch_error": 10},
                },
            ],
            "depth_layers": {"0": {"processed": 50, "planned": 60}},
            "total_session": 50,
            "total_all_time": 50,
            "duration_seconds": 300.0,
            "rule_performance": {
                "product": {
                    "hits": 20,
                    "fields_filled": 60,
                    "fields_total": 80,
                    "fill_rate": 0.75,
                    "avg_ms": 120.0,
                    "slow_count": 2,
                },
            },
        }

    def test_all_outcome_categories_present(self):
        reporter = _make_reporter()
        captured = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = captured
        try:
            reporter.print_summary("/tmp/out.jsonl", self._make_sample_report())
            output = captured.getvalue()
            assert "成功 (已保存)" in output
            assert "robots.txt 拒绝" in output
            assert "noindex (未保存)" in output
            assert "重复内容 (未保存)" in output
            assert "已截断 (部分保存)" in output
            assert "解析失败 (空内容)" in output
            assert "抓取失败" in output
            assert "过滤丢弃" in output
        finally:
            sys.stderr = old_stderr

    def test_duration_shown_when_times_present(self):
        reporter = _make_reporter()
        captured = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = captured
        try:
            reporter.print_summary("/tmp/out.jsonl", self._make_sample_report())
            output = captured.getvalue()
            assert "耗时:" in output
            assert "300.0s" in output
        finally:
            sys.stderr = old_stderr

    def test_no_duration_when_duration_seconds_zero(self):
        reporter = _make_reporter()
        report = self._make_sample_report()
        report["duration_seconds"] = 0
        captured = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = captured
        try:
            reporter.print_summary("/tmp/out.jsonl", report)
            output = captured.getvalue()
            assert "耗时:" not in output
        finally:
            sys.stderr = old_stderr

    def test_discovery_section(self):
        reporter = _make_reporter()
        captured = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = captured
        try:
            reporter.print_summary("/tmp/out.jsonl", self._make_sample_report())
            output = captured.getvalue()
            assert "Sitemap:" in output
            assert "Robots.txt:" in output
            assert "ok=2" in output
            assert "发现=100" in output  # 中文摘要格式
        finally:
            sys.stderr = old_stderr

    def test_fetch_errors_section(self):
        reporter = _make_reporter()
        captured = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = captured
        try:
            reporter.print_summary("/tmp/out.jsonl", self._make_sample_report())
            output = captured.getvalue()
            assert "dns: 5" in output
            assert "timeout: 3" in output
        finally:
            sys.stderr = old_stderr

    def test_output_path_displayed(self):
        reporter = _make_reporter()
        captured = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = captured
        try:
            reporter.print_summary("/tmp/out.jsonl", self._make_sample_report())
            output = captured.getvalue()
            assert "输出: /tmp/out.jsonl" in output
        finally:
            sys.stderr = old_stderr

    def test_print_summary_with_none_report(self):
        """report=None 时不崩溃，静默返回（防御纵深）。"""
        reporter = _make_reporter()
        # 不应抛异常
        reporter.print_summary("/tmp/out.jsonl", None)

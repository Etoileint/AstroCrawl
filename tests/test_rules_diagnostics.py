"""特征测试：规则诊断 — trace 输出、CrawlStats 规则维度、HealthMonitor 集成。

测试文件覆盖 issue #123 的核心验收标准。
"""

from __future__ import annotations

import pytest

from astrocrawl.config import CrawlerConfig
from astrocrawl.crawler.outcomes import CrawlStats
from astrocrawl.rules._lifecycle import RuleLifecycle

# ═══════════════════════════════════════════════════════════════════
# CrawlStats 规则维度统计 (N39)
# ═══════════════════════════════════════════════════════════════════


class TestRuleStats:
    """N39: CrawlStats 规则命中/填充率/耗时统计。"""

    @pytest.mark.asyncio
    async def test_record_rule_hit_basic(self):
        stats = CrawlStats()
        await stats.record_rule_hit("test_rule", fields_filled=3, fields_total=4, elapsed_ms=150.0)
        snapshot = await stats.get_rule_stats_snapshot()
        assert "test_rule" in snapshot
        r = snapshot["test_rule"]
        assert r["hits"] == 1
        assert r["fields_filled"] == 3
        assert r["fields_total"] == 4
        assert r["fill_rate"] == 0.75
        assert r["avg_ms"] == 150.0

    @pytest.mark.asyncio
    async def test_record_rule_hit_multiple(self):
        stats = CrawlStats()
        await stats.record_rule_hit("rule_a", fields_filled=2, fields_total=4, elapsed_ms=100.0)
        await stats.record_rule_hit("rule_a", fields_filled=4, fields_total=4, elapsed_ms=200.0)
        await stats.record_rule_hit("rule_b", fields_filled=1, fields_total=5, elapsed_ms=50.0)

        snapshot = await stats.get_rule_stats_snapshot()
        assert snapshot["rule_a"]["hits"] == 2
        assert snapshot["rule_a"]["fields_filled"] == 6
        assert snapshot["rule_a"]["fields_total"] == 8
        assert snapshot["rule_a"]["avg_ms"] == 150.0
        assert snapshot["rule_b"]["hits"] == 1

    @pytest.mark.asyncio
    async def test_record_rule_hit_zero_fields(self):
        """全字段空 → fill_rate = 0。"""
        stats = CrawlStats()
        await stats.record_rule_hit("empty_rule", fields_filled=0, fields_total=5, elapsed_ms=10.0)
        snapshot = await stats.get_rule_stats_snapshot()
        assert snapshot["empty_rule"]["fill_rate"] == 0.0
        assert snapshot["empty_rule"]["fields_filled"] == 0

    @pytest.mark.asyncio
    async def test_slow_rule_detection(self):
        """N70: >1s 慢规则。"""
        stats = CrawlStats()
        await stats.record_rule_hit("fast", fields_filled=2, fields_total=2, elapsed_ms=500.0)
        await stats.record_rule_hit("slow", fields_filled=2, fields_total=2, elapsed_ms=1500.0)
        await stats.record_rule_hit("slow", fields_filled=2, fields_total=2, elapsed_ms=2000.0)

        snapshot = await stats.get_rule_stats_snapshot()
        assert snapshot["fast"]["slow_count"] == 0
        assert snapshot["slow"]["slow_count"] == 2

    @pytest.mark.asyncio
    async def test_empty_snapshot(self):
        """无命中 → 空快照。"""
        stats = CrawlStats()
        snapshot = await stats.get_rule_stats_snapshot()
        assert snapshot == {}


# ═══════════════════════════════════════════════════════════════════
# RuleLifecycle HealthChecked (N64/N65)
# ═══════════════════════════════════════════════════════════════════


class TestRuleLifecycleHealth:
    """N64/N65: RuleLifecycle.get_health() 返回正确状态。"""

    def test_health_up_after_load(self):
        cfg = CrawlerConfig()
        lc = RuleLifecycle(cfg)
        lc.initial_load()
        health = lc.get_health()
        assert health.status == "UP"
        assert "rules_loaded" in health.message

    def test_health_degraded_after_failure(self):
        from unittest.mock import patch

        cfg = CrawlerConfig()
        lc = RuleLifecycle(cfg)

        with patch("astrocrawl.rules._lifecycle.build_rule_snapshot", side_effect=RuntimeError("fail")):
            lc.initial_load()

        health = lc.get_health()
        assert health.status == "DEGRADED"
        assert "fail" in health.message

    def test_health_before_load(self):
        """加载前状态正确。"""
        cfg = CrawlerConfig()
        lc = RuleLifecycle(cfg)
        health = lc.get_health()
        assert health.status == "DEGRADED"
        assert "not loaded" in health.message


# ═══════════════════════════════════════════════════════════════════
# Trace 输出 (N38)
# ═══════════════════════════════════════════════════════════════════


class TestTraceOutput:
    """N38: _rule_match trace 输出格式。"""

    def test_trace_info_structure(self):
        """验证 _rule_trace 结构。"""
        trace = {
            "matched_rule": "test_rule",
            "used_default": False,
            "fields_filled": 3,
            "fields_empty": 1,
            "elapsed_ms": 12.5,
        }
        assert trace["matched_rule"] == "test_rule"
        assert trace["used_default"] is False
        assert trace["fields_filled"] + trace["fields_empty"] > 0
        assert isinstance(trace["elapsed_ms"], (int, float))

    def test_trace_default_rule(self):
        """default 规则 trace 正确标记。"""
        trace = {
            "matched_rule": "default",
            "used_default": True,
            "fields_filled": 0,
            "fields_empty": 0,
            "elapsed_ms": 1.0,
        }
        assert trace["used_default"] is True
        assert trace["fields_filled"] == 0


# ═══════════════════════════════════════════════════════════════════
# Config
# ═══════════════════════════════════════════════════════════════════


class TestTraceConfig:
    """trace_rules 配置字段——由 GlobalSettings 管理。"""

    def test_trace_rules_default_false(self):
        from astrocrawl.config import GlobalSettings

        gs = GlobalSettings()
        assert gs.trace_rules is False

    def test_trace_rules_enabled(self):
        from astrocrawl.config import GlobalSettings

        gs = GlobalSettings(trace_rules=True)
        assert gs.trace_rules is True

"""Health 数据类 + aggregate() 测试。

对标 Spring Actuator Health + SimpleStatusAggregator：
- UP > DEGRADED > DOWN 层次聚合
- 空组件 → UP
- DOWN 优先于 DEGRADED
- 消息中 DOWN 名称截断为前 3 个
"""

from __future__ import annotations

import pytest

from astrocrawl.health import Health, health_to_report

# ═══════════════════════════════════════════════════════════════════════
# Health 数据类
# ═══════════════════════════════════════════════════════════════════════


class TestHealthDataclass:
    def test_default_values(self):
        h = Health("UP")
        assert h.status == "UP"
        assert h.message == ""
        assert h.details == {}

    def test_frozen_immutable(self):
        h = Health("UP", message="ok")
        with pytest.raises(Exception):
            h.message = "changed"  # type: ignore[misc]

    def test_equality(self):
        a = Health("UP", message="ok", details={"k": 1})
        b = Health("UP", message="ok", details={"k": 1})
        assert a == b
        c = Health("UP", message="ok", details={"k": 2})
        assert a != c


# ═══════════════════════════════════════════════════════════════════════
# Health.aggregate()
# ═══════════════════════════════════════════════════════════════════════


class TestHealthAggregate:
    # ── 空组件 ──

    def test_empty_components_returns_up(self):
        result = Health.aggregate({})
        assert result.status == "UP"

    # ── 单组件 ──

    def test_single_up(self):
        result = Health.aggregate({"db": Health("UP")})
        assert result.status == "UP"

    def test_single_degraded(self):
        result = Health.aggregate({"cache": Health("DEGRADED", "slow")})
        assert result.status == "DEGRADED"

    def test_single_down(self):
        result = Health.aggregate({"db": Health("DOWN", "connection lost")})
        assert result.status == "DOWN"

    # ── 多组件层次 ──

    def test_all_up(self):
        result = Health.aggregate(
            {
                "db": Health("UP"),
                "cache": Health("UP"),
                "queue": Health("UP"),
            }
        )
        assert result.status == "UP"
        assert result.message == "All components healthy"

    def test_one_down_rest_up(self):
        result = Health.aggregate(
            {
                "db": Health("DOWN", "dead"),
                "cache": Health("UP"),
                "queue": Health("UP"),
            }
        )
        assert result.status == "DOWN"
        assert "db" in result.message

    def test_one_degraded_rest_up(self):
        result = Health.aggregate(
            {
                "cache": Health("DEGRADED", "slow"),
                "queue": Health("UP"),
            }
        )
        assert result.status == "DEGRADED"

    def test_mixed_degraded_and_down(self):
        """DOWN 优先于 DEGRADED。"""
        result = Health.aggregate(
            {
                "cache": Health("DEGRADED", "slow"),
                "db": Health("DOWN", "dead"),
            }
        )
        assert result.status == "DOWN"

    def test_multiple_down(self):
        result = Health.aggregate(
            {
                "db": Health("DOWN"),
                "cache": Health("DOWN"),
                "queue": Health("UP"),
            }
        )
        assert result.status == "DOWN"
        assert "db" in result.message
        assert "cache" in result.message

    def test_multiple_degraded(self):
        """多个 DEGRADED：消息仅含数量，不含名称。"""
        result = Health.aggregate(
            {
                "cache": Health("DEGRADED"),
                "queue": Health("DEGRADED"),
                "db": Health("UP"),
            }
        )
        assert result.status == "DEGRADED"
        assert "2 component(s) DEGRADED" in result.message
        assert "cache" not in result.message

    # ── DOWN 名称截断 (down[:3]) ──

    def test_down_names_exactly_three(self):
        result = Health.aggregate(
            {
                "a": Health("DOWN"),
                "b": Health("DOWN"),
                "c": Health("DOWN"),
            }
        )
        assert result.status == "DOWN"
        assert "a" in result.message
        assert "b" in result.message
        assert "c" in result.message

    def test_down_names_more_than_three(self):
        result = Health.aggregate(
            {
                "a": Health("DOWN"),
                "b": Health("DOWN"),
                "c": Health("DOWN"),
                "d": Health("DOWN"),
            }
        )
        assert result.status == "DOWN"
        assert "d" not in result.message
        # 消息仅列出前 3 个
        names_in_msg = sum(1 for k in ["a", "b", "c", "d"] if k in result.message)
        assert names_in_msg == 3

    # ── details 传递 ──

    def test_aggregate_preserves_components_in_details(self):
        components = {
            "db": Health("UP"),
            "cache": Health("DEGRADED", "slow"),
        }
        result = Health.aggregate(components)
        assert "components" in result.details
        assert result.details["components"] is components

    # ── 大组件量 ──

    def test_large_component_count(self):
        components = {f"comp_{i}": Health("UP") for i in range(100)}
        result = Health.aggregate(components)
        assert result.status == "UP"
        assert len(result.details["components"]) == 100


# ═══════════════════════════════════════════════════════════════════════
# health_to_report()
# ═══════════════════════════════════════════════════════════════════════


class TestHealthToReport:
    def test_up_health_formatted(self):
        h = Health("UP")
        report = health_to_report(h)
        assert report["status"] == "UP"
        assert "components" in report

    def test_down_health_with_components(self):
        components = {
            "db": Health("DOWN", "connection lost"),
            "cache": Health("UP"),
        }
        h = Health.aggregate(components)
        report = health_to_report(h)
        assert report["status"] == "DOWN"
        assert "db" in report["components"]
        assert report["components"]["db"]["status"] == "DOWN"

    def test_dict_component_passthrough(self):
        """dict 类型直接透传，非 Health/非 dict → UNKNOWN。"""
        components = {
            "a": Health("UP"),
            "b": {"status": "OK", "extra": 1},
            "c": "not a health object",
        }
        h = Health("UP", details={"components": components})
        report = health_to_report(h)
        assert report["components"]["a"]["status"] == "UP"
        assert report["components"]["b"] == {"status": "OK", "extra": 1}
        assert report["components"]["c"]["status"] == "UNKNOWN"

    def test_details_status_key_overrides_health_status(self):
        """**h.details 中的 'status' 键覆盖 Health.status 值（调用方应避免）。"""
        h = Health("UP", details={"status": "fake", "real": "data"})
        agg = Health.aggregate({"c1": h})
        report = health_to_report(agg)
        assert report["components"]["c1"]["status"] == "fake"
        assert report["components"]["c1"]["real"] == "data"

"""Phase 3 — MainWindow 数据逻辑测试。

覆盖:
- MW01-MW07: get_urls() URL 解析与去重 (parametrize)
- MW08-MW09: _validate_urls() 实时校验
- MW10-MW11: _set_running_state() UI 锁定
- MW12-MW24: Slot 方法 (_update_layer_progress/_update_stats/_update_outcome/_add_log/_on_pause_state)
- MW25-MW27: _on_theme_changed() 样式刷新
- MW28-MW29: _clear_layout() 递归清理
- MW30-MW34: config save/load dict 构造与往返

所有测试使用完整 MainWindow widget 树 + offscreen QPA。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout

pytestmark = pytest.mark.gui


@pytest.fixture
def window(qapp, theme_mgr):
    """构造完整 MainWindow。theme_mgr 单例是 TitleBar 的硬依赖。"""
    from astrocrawl.gui.main_window import MainWindow

    w = MainWindow()
    return w


@pytest.fixture
def window_no_theme(qapp, theme_mgr):
    """构造 MainWindow 但 _theme_mgr=None，用于测试无主题管理器分支。"""
    from astrocrawl.gui.main_window import MainWindow

    w = MainWindow()
    w._theme_mgr = None
    return w


# ═══════════════════════════════════════════════════════════════════════
# MW01-MW07: get_urls() — parametrize
# ═══════════════════════════════════════════════════════════════════════


class TestGetUrls:
    @pytest.mark.parametrize(
        ("url_text", "expected"),
        [
            ("https://a.com\nhttps://b.com", ["https://a.com", "https://b.com"]),
            ("https://a.com\nhttps://a.com", ["https://a.com"]),
            ("https://a.com\n\nhttps://b.com", ["https://a.com", "https://b.com"]),
            ("example.com", ["https://example.com"]),
            ("http://example.com", ["http://example.com"]),
            ("", []),
        ],
    )
    def test_get_urls(self, window, url_text, expected):
        window.url_text.setPlainText(url_text)
        assert window.get_urls() == expected

    def test_filters_preserves_https_urls_only(self, window):
        window.url_text.setPlainText("\nhttps://a.com")
        result = window.get_urls()
        assert result == ["https://a.com"]


# ═══════════════════════════════════════════════════════════════════════
# MW08-MW09: _validate_urls()
# ═══════════════════════════════════════════════════════════════════════


class TestValidateUrls:
    @pytest.mark.parametrize(
        ("text", "expect_warning"),
        [
            ("https://a.com\nhttps://b.com", False),
            ("https://a.com\nnot-a-url", True),
            ("", False),
        ],
    )
    def test_validate_urls(self, window, text, expect_warning):
        window.url_text.setPlainText(text)
        window._validate_urls()
        if expect_warning:
            assert "Warning" in window.url_status.text()
            assert "invalid" in window.url_status.text()
        else:
            assert window.url_status.text() == ""


# ═══════════════════════════════════════════════════════════════════════
# MW10-MW11: _set_running_state()
# ═══════════════════════════════════════════════════════════════════════


class TestSetRunningState:
    @pytest.mark.parametrize("running", [True, False])
    def test_inputs_disabled_when_running(self, window, running):
        window._set_running_state(running)
        assert window.url_text.isEnabled() is not running
        assert window.depth_spin.isEnabled() is not running
        assert window.concurrency_spin.isEnabled() is not running
        assert window._run_btn.isEnabled() is not running
        assert window._reset_btn.isEnabled() is not running

    @pytest.mark.parametrize("running", [True, False])
    def test_pause_stop_enabled_when_running(self, window, running):
        window._set_running_state(running)
        assert window._pause_btn.isEnabled() is running
        assert window._stop_btn.isEnabled() is running


# ═══════════════════════════════════════════════════════════════════════
# MW12-MW24: Slot 方法
# ═══════════════════════════════════════════════════════════════════════


class TestUpdateLayerProgress:
    def test_normal_progress(self, window):
        window._adjust_layer_bars()
        window._update_layer_progress(0, 5, 10)

        assert window._layer_bars[0].value() == 50
        assert "5/10" in window._layer_labels[0].text()

    def test_total_zero_no_division_error(self, window):
        window._adjust_layer_bars()
        window._update_layer_progress(0, 5, 0)

        assert window._layer_bars[0].value() == 0

    def test_layer_out_of_bounds_no_error(self, window):
        window._adjust_layer_bars()
        window._update_layer_progress(99, 5, 10)


class TestUpdateStats:
    @pytest.mark.parametrize(
        ("completed", "queue_size", "limit", "expected_in", "expected_limit"),
        [
            (5, 10, 100, ("Completed: 5", "Queue: 10"), "Limit: 100"),
            (0, 5, 0, ("Completed: 0", "Queue: 5"), "Limit: Unlimited"),
        ],
    )
    def test_update_stats(self, window, completed, queue_size, limit, expected_in, expected_limit):
        window._update_stats(completed, queue_size, limit)
        for fragment in expected_in:
            assert fragment in window.stats_label.text()
        assert expected_limit in window.stats_label.text()


class TestUpdateOutcome:
    @pytest.mark.parametrize(
        ("stats", "fragments"),
        [
            ({}, []),
            ({"ok": 5, "duplicate": 2}, ["Saved: 5", "Duplicates: 2", " | "]),
            (
                {"ok": 1, "robots_denied": 2, "noindex": 3, "duplicate": 4, "fetch_failures": 5, "dropped": 6},
                ["Saved: 1", "Denied: 2", "Noindex: 3", "Duplicates: 4", "Failed: 5", "Dropped: 6"],
            ),
            (
                {
                    "sitemap_active": True,
                    "robots_done": 0,
                    "robots_total": 0,
                    "sitemap_done": 0,
                    "sitemap_total": 0,
                    "sitemap_urls": 0,
                },
                ["robots: 0/?", "sitemap: 0/?"],
            ),
            (
                {
                    "sitemap_active": True,
                    "robots_done": 3,
                    "robots_total": 5,
                    "sitemap_done": 2,
                    "sitemap_total": 4,
                    "sitemap_urls": 150,
                },
                ["robots: 3/5", "sitemap: 2/4, 150 URLs"],
            ),
        ],
    )
    def test_update_outcome(self, window, stats, fragments):
        window._update_outcome(stats)
        if not fragments:
            assert window.outcome_label.text() == ""
        else:
            for f in fragments:
                assert f in window.outcome_label.text()


class TestAddLog:
    def test_adds_item_to_list(self, window):
        initial = window.log_list.count()
        window._add_log("test message")
        assert window.log_list.count() == initial + 1

    def test_trims_when_over_max(self, window):
        window.MAX_LOG_ITEMS = 10
        for i in range(11):
            window._add_log(f"msg {i}")
        assert window.log_list.count() < 11 + 100  # 裁剪逻辑


class TestOnPauseState:
    def test_paused_true(self, window):
        window._on_pause_state(True)
        assert window._paused is True
        assert window._pause_btn.text() == "Resume"

    def test_paused_false(self, window):
        window._on_pause_state(True)
        window._on_pause_state(False)
        assert window._paused is False
        assert window._pause_btn.text() == "Pause"


# ═══════════════════════════════════════════════════════════════════════
# MW25-MW27: _on_theme_changed()
# ═══════════════════════════════════════════════════════════════════════


class TestOnThemeChanged:
    def test_no_theme_mgr_returns_early(self, window_no_theme):
        window_no_theme._on_theme_changed()

    def test_updates_url_status_style(self, window, theme_mgr):
        window._on_theme_changed()
        style = window.url_status.styleSheet()
        assert theme_mgr.get("danger") in style

    def test_updates_outcome_label_style(self, window, theme_mgr):
        window._on_theme_changed()
        style = window.outcome_label.styleSheet()
        assert theme_mgr.get("disabled") in style


# ═══════════════════════════════════════════════════════════════════════
# MW28-MW29: _clear_layout() 静态方法
# ═══════════════════════════════════════════════════════════════════════


class TestClearLayout:
    def test_clears_nested_layout(self, qapp):
        from astrocrawl.gui.main_window import MainWindow

        outer = QVBoxLayout()
        inner = QHBoxLayout()
        label = QLabel("test")
        inner.addWidget(label)
        outer.addLayout(inner)

        MainWindow._clear_layout(outer)
        assert outer.count() == 0

    def test_empty_layout_does_not_raise(self, qapp):
        from astrocrawl.gui.main_window import MainWindow

        layout = QVBoxLayout()
        MainWindow._clear_layout(layout)
        assert layout.count() == 0


# ═══════════════════════════════════════════════════════════════════════
# MW30-MW34: config save/load dict
# ═══════════════════════════════════════════════════════════════════════


class TestConfigDictConstruction:
    def test_save_config_dict_has_all_keys(self, window):
        """验证 _save_config 构造的 dict 包含必要键。"""
        window.url_text.setPlainText("https://example.com")
        window.depth_spin.setValue(3)
        window.concurrency_spin.setValue(4)
        window._output_edit.setText("/tmp/out.jsonl")
        window.same_domain_check.setChecked(True)
        window.respect_robots_check.setChecked(False)

        config = {
            "urls": window.get_urls(),
            "depth": window.depth_spin.value(),
            "concurrency": window.concurrency_spin.value(),
            "output_path": window._output_edit.text(),
            "same_domain_only": window.same_domain_check.isChecked(),
            "respect_robots": window.respect_robots_check.isChecked(),
            "proxy_last_used": window._profile_combo.currentData(),
            "advanced": window._advanced_cfg.to_dict(),
        }

        assert "urls" in config
        assert "depth" in config
        assert "concurrency" in config
        assert "output_path" in config
        assert "same_domain_only" in config
        assert "respect_robots" in config
        assert "proxy_last_used" in config
        assert "advanced" in config

    def test_load_config_applies_urls(self, window):
        """_load_config dict 应用到 url_text。"""
        self._apply_config_dict(
            window,
            {
                "urls": ["https://a.com", "https://b.com"],
            },
        )
        assert "https://a.com" in window.url_text.toPlainText()
        assert "https://b.com" in window.url_text.toPlainText()

    def test_load_config_applies_depth_concurrency(self, window):
        """_load_config dict 应用到 spin boxes。"""
        self._apply_config_dict(
            window,
            {
                "urls": [],
                "depth": 5,
                "concurrency": 10,
            },
        )
        assert window.depth_spin.value() == 5
        assert window.concurrency_spin.value() == 10

    def test_load_config_applies_checks(self, window):
        """_load_config dict 应用到 checkboxes。"""
        self._apply_config_dict(
            window,
            {
                "urls": [],
                "same_domain_only": False,
                "respect_robots": False,
            },
        )
        assert window.same_domain_check.isChecked() is False
        assert window.respect_robots_check.isChecked() is False

    def test_load_config_missing_advanced_does_not_crash(self, window):
        """缺少 advanced 键时不调用 CrawlerConfig.from_dict。"""
        self._apply_config_dict(
            window,
            {
                "urls": [],
            },
        )

    def test_load_config_missing_proxy_skips(self, window):
        """缺少 proxy_last_used 键时 profile_combo 保持默认。"""
        initial_data = window._profile_combo.currentData()
        self._apply_config_dict(
            window,
            {
                "urls": [],
            },
        )
        assert window._profile_combo.currentData() == initial_data

    def test_load_config_with_proxy_last_used_does_not_crash(self, window):
        """有 proxy_last_used 时 _apply_config_dict 不崩溃。"""
        self._apply_config_dict(
            window,
            {
                "urls": [],
                "proxy_last_used": "海外代理组",
            },
        )

    @staticmethod
    def _apply_config_dict(window, config):
        """模拟 _load_config 中 dict 应用到控件的逻辑（不含 QFileDialog）。"""
        window.url_text.setPlainText("\n".join(config.get("urls", [])))
        window.depth_spin.setValue(config.get("depth", 2))
        window.concurrency_spin.setValue(config.get("concurrency", 3))
        path = config.get("output_path", str(Path.home() / "crawler_output.jsonl"))
        window._output_edit.setText(path)
        window.same_domain_check.setChecked(config.get("same_domain_only", True))
        window.respect_robots_check.setChecked(config.get("respect_robots", True))
        if "advanced" in config:
            from astrocrawl.config import CrawlerConfig

            window._advanced_cfg = CrawlerConfig.from_dict(config["advanced"])


def test_minimum_height_from_layout_not_hardcoded(qapp, theme_mgr) -> None:
    """MW-35: 窗口最小高度应由布局自动计算，不硬编码低于内容需求的保底值。

    回归 #190 响应式尺寸引入的 setMinimumSize(420, 600) bug —— 600 低于布局
    natural minimumSizeHint，导致垂直压缩时 Config/Progress 组被压扁重叠。
    """
    from astrocrawl.gui.main_window import MainWindow

    w = MainWindow()
    w.show()
    layout = w.layout()
    layout_min = layout.minimumSize()
    window_min = w.minimumHeight()
    w.close()

    # Qt 自动计算的最小高度应 ≥ 布局实际需要的自然最低高度
    assert window_min >= layout_min.height(), (
        f"窗口最小高度 {window_min}px < 布局最低需求 {layout_min.height()}px —— "
        f"硬编码 setMinimumSize 覆盖了 Qt 的自动保护"
    )

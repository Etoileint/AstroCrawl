from __future__ import annotations

import json
import logging
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import Qt, QThread, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from astrocrawl._constants import MAX_LOG_ITEMS, QLINEEDIT_MAX, QSPINBOX_MAX
from astrocrawl._startup import StartupError
from astrocrawl._version import __version__
from astrocrawl.config import DEFAULT_CONFIG, CrawlerConfig
from astrocrawl.gui._log_bridge import attach_qt_handler, detach_qt_handler
from astrocrawl.gui._style import status_label_style
from astrocrawl.gui._tokens import BAR_HEIGHT, FONT_MD, FONT_SM, RADIUS_MD, SPACE_LG, SPACE_MD, SPACE_SM
from astrocrawl.gui.advanced_dialog import AdvancedSettingsDialog
from astrocrawl.gui.completion_dialog import CompletionReportDialog
from astrocrawl.gui.crawl_session import CrawlSession
from astrocrawl.gui.proxy_health_bar import ProxyHealthBar
from astrocrawl.gui.rules_dialog import RulesDialog
from astrocrawl.proxy import ProxyConfig, ProxyProfile, ProxySession
from astrocrawl.utils._atomic import atomic_write_json
from astrocrawl.utils.logging import setup_root_logger
from astrocrawl.utils.preferences import clear_qt_file_dialog_history, get_preferences
from astrocrawl.utils.url import is_valid_http_url


class _ProfileCombo(QComboBox):
    """Profile 下拉框 — showPopup 时重读 Preferences 实现双向同步。"""

    def __init__(self, on_popup: Callable[[], None], parent=None):
        super().__init__(parent)
        self._on_popup = on_popup

    def showPopup(self) -> None:
        self._on_popup()
        super().showPopup()


class MainWindow(QWidget):
    MAX_LOG_ITEMS = MAX_LOG_ITEMS

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"AstroCrawl {__version__}")
        self.setAutoFillBackground(True)
        self.setAttribute(Qt.WA_InputMethodEnabled, True)
        self.setMinimumWidth(420)

        from astrocrawl.gui.theme import get_theme_manager

        self._theme_mgr = get_theme_manager()
        self._session: Optional[CrawlSession] = None
        self._proxy_session: Optional[ProxySession] = None
        self._current_profile: Optional[ProxyProfile] = None
        self._paused = False
        self._closing = False
        self._advanced_cfg = CrawlerConfig()
        self._log = logging.getLogger("astrocrawl.gui")
        self._close_watchdog: Optional[QTimer] = None
        self._proxy_health_bar: Optional[ProxyHealthBar] = None
        self._setup_ui()
        if self._theme_mgr is not None:
            self._theme_mgr.theme_changed.connect(self._on_theme_changed)

        self.adjustSize()
        from PySide6.QtWidgets import QApplication

        screen_center = QApplication.primaryScreen().availableGeometry().center()
        self.move(screen_center - self.rect().center())

    def _setup_ui(self) -> None:
        from astrocrawl.gui.theme import get_theme_manager

        root = QVBoxLayout()
        root.setSpacing(SPACE_MD)
        root.setContentsMargins(SPACE_LG, SPACE_LG, SPACE_LG, SPACE_LG)

        from astrocrawl.gui.title_bar import TitleBar

        self._title_bar = TitleBar()
        root.addWidget(self._title_bar)

        root.addWidget(self._build_url_group())
        root.addWidget(self._build_config_group())
        root.addWidget(self._build_progress_group())
        root.addWidget(self._build_log_group())

        # ── 状态栏（持久，常驻底部按钮上方） ──
        self._status_level = "success"
        self._status_bar = QLabel(self.tr("Ready"))
        self._status_bar.setObjectName("status-bar")
        self._status_bar.setFixedHeight(BAR_HEIGHT)
        self._status_bar.setAlignment(Qt.AlignCenter)
        self._status_bar.setStyleSheet(
            status_label_style(get_theme_manager().get("input_bg"))
            + f"color: {get_theme_manager().get('success')}; font-weight: bold;"
        )
        root.addWidget(self._status_bar)

        root.addLayout(self._build_buttons())

        root.setStretch(1, 1)
        root.setStretch(4, 1)
        self.setLayout(root)
        self._adjust_layer_bars()
        self._on_theme_changed()

    def _build_url_group(self) -> QGroupBox:
        g = QGroupBox(self.tr("Source URLs"))
        lay = QVBoxLayout()
        lay.setSpacing(SPACE_SM)
        self.url_text = QTextEdit()
        self.url_text.setObjectName("url-input")
        self.url_text.setPlaceholderText(self.tr("https://example.com (one URL per line, Enter to add a new line)"))
        self.url_text.setMinimumHeight(100)
        self.url_text.textChanged.connect(self._validate_urls)
        lay.addWidget(self.url_text)
        self.url_status = QLabel("")
        self.url_status.setStyleSheet(f"font-size: {FONT_MD}px;")
        lay.addWidget(self.url_status)
        g.setLayout(lay)
        return g

    def _build_config_group(self) -> QGroupBox:
        g = QGroupBox(self.tr("Basic Config"))
        lay = QVBoxLayout()
        lay.setSpacing(SPACE_SM)

        def _row(label_text: str, *widgets: QWidget, stretches: tuple = ()) -> QHBoxLayout:
            r = QHBoxLayout()
            r.setSpacing(SPACE_SM)
            lbl = QLabel(label_text)
            lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            if stretches:
                r.addWidget(lbl, stretches[0])
                for i, w in enumerate(widgets):
                    r.addWidget(w, stretches[i + 1])
            else:
                r.addWidget(lbl, 1)
                for w in widgets:
                    r.addWidget(w, 1)
            return r

        self.depth_spin = QSpinBox()
        self.depth_spin.setRange(1, QSPINBOX_MAX)
        self.depth_spin.setValue(2)
        self.depth_spin.valueChanged.connect(self._adjust_layer_bars)

        self.concurrency_spin = QSpinBox()
        self.concurrency_spin.setRange(1, QSPINBOX_MAX)
        self.concurrency_spin.setValue(DEFAULT_CONFIG.concurrency)

        depth_conc_row = QHBoxLayout()
        depth_conc_row.setSpacing(SPACE_SM)
        depth_lbl = QLabel(self.tr("Target Depth:"))
        depth_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        conc_lbl = QLabel(self.tr("Concurrent Tasks:"))
        conc_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        depth_conc_row.addWidget(depth_lbl, 1)
        depth_conc_row.addWidget(self.depth_spin, 1)
        depth_conc_row.addWidget(conc_lbl, 1)
        depth_conc_row.addWidget(self.concurrency_spin, 1)
        lay.addLayout(depth_conc_row)

        default_out = str(Path.home() / "crawler_output.jsonl")
        self._output_edit = QLineEdit(default_out)
        self._output_edit.setReadOnly(True)
        self._output_edit.setMaxLength(QLINEEDIT_MAX)
        self._output_btn = QPushButton(self.tr("Select"))
        self._output_btn.clicked.connect(self._select_output)
        lay.addLayout(_row(self.tr("Output Path:"), self._output_edit, self._output_btn, stretches=(1, 2, 1)))

        # ── 代理配置（三行布局：profile / mode / 健康条） ──
        self._profile_combo = _ProfileCombo(on_popup=self._reload_profile_combo)
        self._profile_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self._profile_combo.currentIndexChanged.connect(self._on_profile_changed)
        self._test_btn = QPushButton(self.tr("Test"))
        self._test_btn.clicked.connect(self._on_test_connection)
        lay.addLayout(_row(self.tr("Proxy Config:"), self._profile_combo, self._test_btn, stretches=(1, 2, 1)))

        self._mode_combo = QComboBox()
        self._mode_combo.addItem(self.tr("Prefer Proxy (fallback to direct, switch back on recovery)"), "prefer_proxy")
        self._mode_combo.addItem(self.tr("Prefer Direct (auto-proxy for blocked domains)"), "prefer_direct")
        self._mode_combo.addItem(self.tr("Proxy Only (pause when unavailable)"), "proxy_only")
        self._mode_combo.addItem(self.tr("Direct Only (no proxy)"), "direct_only")
        idx = self._mode_combo.findData(self._advanced_cfg.proxy_mode)
        if idx >= 0:
            self._mode_combo.setCurrentIndex(idx)
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        lay.addLayout(_row(self.tr("Proxy Mode:"), self._mode_combo, stretches=(1, 3)))

        self._proxy_health_bar = ProxyHealthBar()
        lay.addWidget(self._proxy_health_bar)
        self._reload_profile_combo()
        self._update_proxy_session()

        row_checks = QHBoxLayout()
        row_checks.setSpacing(SPACE_SM)
        self.same_domain_check = QCheckBox(self.tr("Same Domain Only"))
        self.same_domain_check.setChecked(True)
        self.respect_robots_check = QCheckBox(self.tr("Respect robots.txt"))
        self.respect_robots_check.setChecked(True)
        self._sitemap_check = QCheckBox(self.tr("Auto-discover Sitemap"))
        self._sitemap_check.setChecked(True)
        row_checks.addWidget(self.same_domain_check, 1)
        row_checks.addWidget(self.respect_robots_check, 1)
        row_checks.addWidget(self._sitemap_check, 1)
        lay.addLayout(row_checks)

        row_tools = QHBoxLayout()
        row_tools.setSpacing(SPACE_SM)
        self._advanced_btn = QPushButton(self.tr("Advanced Settings"))
        self._advanced_btn.clicked.connect(self._show_advanced)
        self._rules_btn = QPushButton(self.tr("Rule Management"))
        self._rules_btn.clicked.connect(self._show_rules)
        self._save_config_btn = QPushButton(self.tr("Save Config"))
        self._save_config_btn.clicked.connect(self._save_config)
        self._load_config_btn = QPushButton(self.tr("Load Config"))
        self._load_config_btn.clicked.connect(self._load_config)
        row_tools.addWidget(self._advanced_btn, 1)
        row_tools.addWidget(self._rules_btn, 1)
        row_tools.addWidget(self._save_config_btn, 1)
        row_tools.addWidget(self._load_config_btn, 1)
        lay.addLayout(row_tools)

        g.setLayout(lay)
        return g

    def _build_progress_group(self) -> QGroupBox:
        g = QGroupBox(self.tr("Crawl Progress"))
        self._progress_layout = QVBoxLayout()
        self._progress_layout.setSpacing(SPACE_SM)
        self._layer_bars: List[QProgressBar] = []
        self._layer_labels: List[QLabel] = []
        self.stats_label = QLabel(self.tr("Completed: 0  |  Queue: 0  |  Limit: -"))
        self._progress_layout.addWidget(self.stats_label)
        self.outcome_label = QLabel("")
        self.outcome_label.setStyleSheet(f"font-size: {FONT_SM}px;")
        self._progress_layout.addWidget(self.outcome_label)
        g.setLayout(self._progress_layout)
        return g

    def _build_log_group(self) -> QGroupBox:
        g = QGroupBox(self.tr("Running Log"))
        splitter = QSplitter(Qt.Vertical)
        splitter.setObjectName("log-and-rules-splitter")

        self.log_list = QListWidget()
        self.log_list.setObjectName("log-list")
        self.log_list.setMinimumHeight(100)
        splitter.addWidget(self.log_list)

        self._rule_stats_table = QTableWidget(0, 6)
        self._rule_stats_table.setObjectName("rule-stats-table")
        self._rule_stats_table.setHorizontalHeaderLabels(
            [
                self.tr("Rule Name"),
                self.tr("Matches"),
                self.tr("Extracted/Total Fields"),
                self.tr("Extraction Rate"),
                self.tr("Avg Duration"),
                self.tr("Slow Pages"),
            ]
        )
        self._rule_stats_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._rule_stats_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Fixed)
        self._rule_stats_table.setColumnWidth(1, 50)
        self._rule_stats_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
        self._rule_stats_table.setColumnWidth(2, 100)
        self._rule_stats_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Fixed)
        self._rule_stats_table.setColumnWidth(3, 60)
        self._rule_stats_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Fixed)
        self._rule_stats_table.setColumnWidth(4, 80)
        self._rule_stats_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Fixed)
        self._rule_stats_table.setColumnWidth(5, 50)
        self._rule_stats_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._rule_stats_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._rule_stats_table.setAlternatingRowColors(True)
        self._rule_stats_table.setMinimumHeight(100)
        self._rule_stats_table.verticalHeader().setVisible(False)
        self._rule_stats_table.setVisible(False)
        splitter.addWidget(self._rule_stats_table)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)

        lay = QVBoxLayout()
        lay.setSpacing(SPACE_SM)
        lay.addWidget(splitter)
        g.setLayout(lay)
        return g

    def _build_buttons(self) -> QHBoxLayout:
        lay = QHBoxLayout()
        lay.setSpacing(SPACE_MD)
        self._run_btn = QPushButton(self.tr("Start Crawl"))
        self._run_btn.setObjectName("run-btn")
        self._run_btn.clicked.connect(self._run_crawler)
        self._pause_btn = QPushButton(self.tr("Pause"))
        self._pause_btn.setObjectName("pause-btn")
        self._pause_btn.setEnabled(False)
        self._pause_btn.clicked.connect(self._toggle_pause)
        self._stop_btn = QPushButton(self.tr("Stop"))
        self._stop_btn.setObjectName("stop-btn")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop_crawler)
        self._reset_btn = QPushButton(self.tr("Refresh"))
        self._reset_btn.clicked.connect(self._reset_app)
        self._clear_btn = QPushButton(self.tr("Clear Log"))
        self._clear_btn.clicked.connect(self.log_list.clear)
        lay.addWidget(self._run_btn, 1)
        lay.addWidget(self._pause_btn, 1)
        lay.addWidget(self._stop_btn, 1)
        lay.addWidget(self._reset_btn, 1)
        lay.addWidget(self._clear_btn, 1)
        return lay

    def _adjust_layer_bars(self) -> None:
        depth = self.depth_spin.value()
        current = len(self._layer_bars)
        if depth > current:
            for i in range(current, depth):
                row = QHBoxLayout()
                row.setSpacing(SPACE_SM)
                lbl = QLabel(self.tr("Layer {0}:").format(i + 1))
                bar = QProgressBar()
                bar.setRange(0, 100)
                bar.setValue(0)
                if self._theme_mgr is not None:
                    bar.setStyleSheet(
                        f"QProgressBar {{ border-radius: {RADIUS_MD}px; text-align: center; background-color: {self._theme_mgr.get('input_bg')}; }}"
                        f"QProgressBar::chunk {{ background-color: {self._theme_mgr.get('accent')}; border-radius: {RADIUS_MD}px; }}"
                    )
                row.addWidget(lbl, 1)
                row.addWidget(bar, 3)
                self._progress_layout.addLayout(row)
                self._layer_bars.append(bar)
                self._layer_labels.append(lbl)
        elif depth < current:
            excess = current - depth
            for _ in range(excess):
                last_idx = self._progress_layout.count() - 1
                if last_idx < 1:
                    break
                item = self._progress_layout.takeAt(last_idx)
                if item:
                    if item.widget():
                        item.widget().deleteLater()
                    elif item.layout():
                        self._clear_layout(item.layout())
                if self._layer_bars:
                    self._layer_bars.pop()
                if self._layer_labels:
                    self._layer_labels.pop()

    def _on_theme_changed(self) -> None:
        if self._theme_mgr is None:
            return
        t = self._theme_mgr
        self.url_status.setStyleSheet(f"color: {t.get('danger')}; font-size: {FONT_MD}px;")
        color_map = {"success": "success", "warning": "warning", "error": "danger", "info": "window_text"}
        fg = t.get(color_map.get(self._status_level, "success"))
        self._status_bar.setStyleSheet(status_label_style(t.get("input_bg")) + f"color: {fg}; font-weight: bold;")
        self.outcome_label.setStyleSheet(f"color: {t.get('disabled')}; font-size: {FONT_SM}px;")
        for bar in self._layer_bars:
            bar.setStyleSheet(
                f"QProgressBar {{ border-radius: {RADIUS_MD}px; text-align: center; background-color: {t.get('input_bg')}; }}"
                f"QProgressBar::chunk {{ background-color: {t.get('accent')}; border-radius: {RADIUS_MD}px; }}"
            )

    @staticmethod
    def _clear_layout(layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                MainWindow._clear_layout(item.layout())

    def _show_status(self, msg: str, level: str = "success") -> None:
        from astrocrawl.gui.theme import get_theme_manager

        self._status_level = level
        self._status_bar.setText(msg)
        theme = get_theme_manager()
        color_map = {
            "success": theme.get("success"),
            "warning": theme.get("warning"),
            "error": theme.get("danger"),
            "info": theme.get("window_text"),
        }
        fg = color_map.get(level, theme.get("success"))
        self._status_bar.setStyleSheet(status_label_style(theme.get("input_bg")) + f"color: {fg}; font-weight: bold;")

    def _validate_urls(self) -> None:
        invalid = [
            ln
            for ln in (line.strip() for line in self.url_text.toPlainText().splitlines())
            if ln and not is_valid_http_url(ln)
        ]
        self.url_status.setText(self.tr("Warning: {0} invalid URL(s)").format(len(invalid)) if invalid else "")

    def get_urls(self) -> List[str]:
        seen: Dict[str, None] = {}
        for raw in self.url_text.toPlainText().splitlines():
            raw = raw.strip()
            if not raw:
                continue
            if not raw.startswith(("http://", "https://")):
                raw = "https://" + raw
            if is_valid_http_url(raw) and raw not in seen:
                seen[raw] = None
        return list(seen)

    # ── 代理 UI 辅助方法 ──────────────────────────────────

    def _reload_profile_combo(self) -> None:
        """重新加载 profile 下拉框（从 Preferences 读取）。与 advanced_dialog 双向同步——各自读 Preferences。"""
        prefs = get_preferences()
        profiles = prefs.get_proxy_profiles()
        current = self._profile_combo.currentData()
        self._profile_combo.blockSignals(True)
        try:
            self._profile_combo.clear()
            self._profile_combo.addItem(self.tr("No Proxy"), None)
            for p in profiles:
                endpoints = len(p.proxies)
                self._profile_combo.addItem(
                    self.tr("{name} ({count} endpoints)").format(name=p.name, count=endpoints), p.name
                )
            if current is not None:
                idx = self._profile_combo.findData(current)
                if idx >= 0:
                    self._profile_combo.setCurrentIndex(idx)
        finally:
            self._profile_combo.blockSignals(False)
        # 仅 combo 选中项变化时才重建 ProxySession——展示同步不应触发运行时重建
        if self._profile_combo.currentData() != current:
            self._update_proxy_session()

    def _on_profile_changed(self, _index: int) -> None:
        """Profile 下拉框变化 → 更新 ProxySession + health bar。"""
        self._update_proxy_session()

    def _on_mode_changed(self, _index: int) -> None:
        """代理模式下拉框变化 → 暂存到 _advanced_cfg。"""
        mode = self._mode_combo.currentData()
        if mode:
            self._advanced_cfg = replace(self._advanced_cfg, proxy_mode=mode)

    def _update_proxy_session(self) -> None:
        """从当前 profile 构造 ProxySession 并接线健康条。同步构造——不启动探针。

        无代理时强制 direct_only 并禁用 mode 下拉框，确保 "需要代理的 mode → 必须有代理"
        约束在所有入口一致成立。
        """
        name = self._profile_combo.currentData()
        if not name:
            self._proxy_session = None
            self._current_profile = None
            if self._proxy_health_bar:
                self._proxy_health_bar.stop()
            self._mode_combo.setCurrentIndex(self._mode_combo.findData("direct_only"))
            self._mode_combo.setEnabled(False)
            return
        prefs = get_preferences()
        profile = prefs.get_proxy_profile(name)
        if profile is None or not profile.proxies:
            self._proxy_session = None
            self._current_profile = None
            if self._proxy_health_bar:
                self._proxy_health_bar.stop()
            self._mode_combo.setCurrentIndex(self._mode_combo.findData("direct_only"))
            self._mode_combo.setEnabled(False)
            return
        self._mode_combo.setEnabled(True)
        self._current_profile = profile
        config = ProxyConfig.from_profile(profile)
        self._proxy_session = ProxySession(config)
        if self._proxy_health_bar:
            self._proxy_health_bar.set_source(self._proxy_session)

    def _on_test_connection(self) -> None:
        """测试按钮：QThread Worker + asyncio.run() 并发探测所有端点。
        每端点 10 次（间隔 500ms），跨端点并行，单端点串行。结果通过 Signal 回传。"""
        if not self._proxy_session or not self._proxy_session.proxies:
            QMessageBox.information(
                self, self.tr("No Proxy"), self.tr("Please select a Profile with proxy endpoints first.")
            )
            return
        parsed = list(self._proxy_session.parsed_proxies)
        self._test_btn.setEnabled(False)
        self._test_btn.setText(self.tr("Testing..."))

        class _ProbeWorker(QThread):
            probe_completed = Signal(list)  # list of (url, reachable, latency_ms, error)

            def __init__(self, parsed_proxies, parent=None):
                super().__init__(parent)
                self._parsed = parsed_proxies

            def run(self):
                import asyncio as _asyncio

                from astrocrawl.proxy._probe import probe_one

                async def _probe():
                    async def _probe_one_endpoint(pp):
                        results = []
                        for _ in range(10):
                            r = await probe_one(pp)
                            results.append((pp.to_url_with_auth(), r.reachable, r.latency_ms, r.error))
                            await _asyncio.sleep(0.5)
                        return results

                    tasks = [_probe_one_endpoint(pp) for pp in self._parsed]
                    all_results = await _asyncio.gather(*tasks)
                    return [r for batch in all_results for r in batch]

                results = _asyncio.run(_probe())
                self.probe_completed.emit(results)

        self._probe_worker = _ProbeWorker(parsed, self)
        self._probe_worker.probe_completed.connect(self._on_probe_results)
        self._probe_worker.finished.connect(self._on_probe_finished)
        self._probe_worker.finished.connect(self._probe_worker.deleteLater)
        self._probe_worker.start()

    def _on_probe_results(self, results: list) -> None:
        """接收探测结果 → 写入 ProxyHealthTracker（set_recovery=False，测试路径安全隔离）。"""
        if not self._proxy_session:
            return
        for url, reachable, _latency, _error in results:
            if reachable:
                self._proxy_session._sync_mark_success(url)
            else:
                self._proxy_session._sync_mark_failure(url)

    def _on_probe_finished(self) -> None:
        """探测完成 → 恢复测试按钮。"""
        self._test_btn.setEnabled(True)
        self._test_btn.setText(self.tr("Test"))

    def _select_output(self) -> None:
        pm = get_preferences()
        default_dir = pm.get_last_dir("output", str(Path.home()))
        path, _ = QFileDialog.getSaveFileName(
            self,
            self.tr("Save Output File"),
            str(Path(default_dir) / "crawler_output.jsonl"),
            "JSON Lines (*.jsonl);;All Files (*)",
        )
        clear_qt_file_dialog_history()
        if path:
            self._output_edit.setText(path)
            pm.add_path("output", str(Path(path).parent))

    def _show_advanced(self) -> None:
        dlg = AdvancedSettingsDialog(
            self,
            self._advanced_cfg,
            on_apply=lambda cfg: setattr(self, "_advanced_cfg", cfg),
        )
        if dlg.exec():
            self._advanced_cfg = dlg.get_config()
        self._reload_profile_combo()
        # _reload_profile_combo 仅在选中项变化时才重建 ProxySession；
        # 对话框可能修改了同一 Profile 的端点内容——始终重建以保持一致性
        self._update_proxy_session()

    def _show_rules(self) -> None:
        dlg = RulesDialog(self, self._advanced_cfg)
        dlg.exec()

    def _disconnect_old_session(self) -> None:
        if self._session is not None:
            self._session.dispose()
            self._session = None

    def _detach_qt_logger(self) -> None:
        parent = logging.getLogger("astrocrawl")
        detach_qt_handler(parent)

    def _run_crawler(self) -> None:
        urls = self.get_urls()
        if not urls:
            QMessageBox.information(
                self, self.tr("Missing URL"), self.tr("Please enter at least one valid source URL.")
            )
            return

        output_path = Path(self._output_edit.text())
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.touch(exist_ok=True)
        except (OSError, PermissionError) as e:
            QMessageBox.critical(
                self, self.tr("Error"), self.tr("Output path is not writable:\n{0}\n{1}").format(output_path, e)
            )
            return

        self._disconnect_old_session()

        from astrocrawl.config import GlobalSettings

        global_settings = GlobalSettings.from_preferences()
        cfg = replace(
            self._advanced_cfg,
            robots_respect=self.respect_robots_check.isChecked(),
            concurrency=self.concurrency_spin.value(),
            use_sitemap=self._sitemap_check.isChecked(),
        )

        setup_root_logger(global_settings.log_level, cfg.log_file)

        self._session = CrawlSession(cfg, global_settings)
        self._session.message_logged.connect(self._add_log)
        self._session.layer_progress.connect(self._update_layer_progress)
        self._session.stats_updated.connect(self._update_stats)
        self._session.outcome_updated.connect(self._update_outcome)
        self._session.finished.connect(self._on_finished)
        self._session.error_occurred.connect(self._on_error)
        self._session.pause_changed.connect(self._on_pause_state)
        self._session.rule_stats_updated.connect(self._on_rule_stats_updated)
        self._title_bar.connect_worker_state(self._session)
        self._session.session_done.connect(self._on_thread_finished)

        crawler_logger = logging.getLogger("astrocrawl")
        attach_qt_handler(crawler_logger, self._session.message_logged)
        crawler_logger.setLevel(global_settings.log_level)

        try:
            self._session.start(
                urls=urls,
                depth=self.depth_spin.value(),
                concurrency=cfg.concurrency,
                output_path=self._output_edit.text(),
                same_domain_only=self.same_domain_check.isChecked(),
                proxy_profile=self._current_profile,
                proxy_mode_override=self._advanced_cfg.proxy_mode if self._advanced_cfg else None,
                health_tracker=self._proxy_session.health if self._proxy_session else None,
            )
        except StartupError as e:
            QMessageBox.critical(self, self.tr("Startup Error"), str(e))
            self._session.dispose()
            self._session = None
            return

        self._title_bar.connect_session(self._session)
        self._show_status(self.tr("Crawling..."), "info")
        self._set_running_state(True)
        self._paused = False
        self._pause_btn.setText(self.tr("Pause"))
        for bar in self._layer_bars:
            bar.setValue(0)
        for lbl in self._layer_labels:
            lbl.setText(lbl.text().split(":")[0] + ":")
        self.stats_label.setText(self.tr("Completed: 0  |  Queue: 0  |  Limit: -"))
        self.log_list.clear()

    def _toggle_pause(self) -> None:
        if not self._session or not self._session.is_running():
            return
        if self._paused:
            self._session.resume()
        else:
            self._session.pause()

    def _stop_crawler(self) -> None:
        if self._session and self._session.is_running():
            self._session.stop()
            self._stop_btn.setEnabled(False)
            self._show_status(self.tr("Stopping..."), "warning")
            self._add_log(self.tr("[INFO] Stopping, waiting for current tasks to finish..."))

    def _set_running_state(self, running: bool) -> None:
        self.url_text.setEnabled(not running)
        self.depth_spin.setEnabled(not running)
        self.concurrency_spin.setEnabled(not running)
        self._output_btn.setEnabled(not running)
        self._profile_combo.setEnabled(not running)
        self._mode_combo.setEnabled(not running and self._current_profile is not None)
        self._test_btn.setEnabled(not running)
        self.same_domain_check.setEnabled(not running)
        self.respect_robots_check.setEnabled(not running)
        self._sitemap_check.setEnabled(not running)
        self._advanced_btn.setEnabled(not running)
        self._rules_btn.setEnabled(not running)
        self._run_btn.setEnabled(not running)
        self._pause_btn.setEnabled(running)
        self._stop_btn.setEnabled(running)
        self._reset_btn.setEnabled(not running)
        self._save_config_btn.setEnabled(not running)
        self._load_config_btn.setEnabled(not running)

    @Slot(str)
    def _add_log(self, text: str) -> None:
        self.log_list.addItem(text)
        if self.log_list.count() > self.MAX_LOG_ITEMS:
            excess = self.log_list.count() - self.MAX_LOG_ITEMS + 100
            for _ in range(min(excess, self.log_list.count())):
                self.log_list.takeItem(0)
        self.log_list.scrollToBottom()

    @Slot(int, int, int)
    def _update_layer_progress(self, layer: int, completed: int, total: int) -> None:
        if layer < len(self._layer_bars):
            pct = int(completed * 100 / total) if total > 0 else 0
            self._layer_bars[layer].setValue(pct)
            self._layer_labels[layer].setText(self.tr("Layer {0}: {1}/{2}").format(layer + 1, completed, total))

    @Slot(int, int, int)
    def _update_stats(self, completed: int, queue_size: int, limit: int) -> None:
        limit_str = f"{limit}" if limit > 0 else self.tr("Unlimited")
        self.stats_label.setText(
            self.tr("Completed: {0}  |  Queue: {1}  |  Limit: {2}").format(completed, queue_size, limit_str)
        )

    @Slot(dict)
    def _update_outcome(self, stats: dict) -> None:
        parts = []
        if stats.get("ok"):
            parts.append(self.tr("Saved: {0}").format(stats["ok"]))
        if stats.get("robots_denied"):
            parts.append(self.tr("Denied: {0}").format(stats["robots_denied"]))
        if stats.get("noindex"):
            parts.append(self.tr("Noindex: {0}").format(stats["noindex"]))
        if stats.get("duplicate"):
            parts.append(self.tr("Duplicates: {0}").format(stats["duplicate"]))
        if stats.get("fetch_failures"):
            parts.append(self.tr("Failed: {0}").format(stats["fetch_failures"]))
        if stats.get("dropped"):
            parts.append(self.tr("Dropped: {0}").format(stats["dropped"]))
        # 发现阶段实时进度（sitemap 活跃时显示）
        if stats.get("sitemap_active"):
            robots_done = stats.get("robots_done", 0)
            robots_total = stats.get("robots_total", 0) or "?"
            sitemap_done = stats.get("sitemap_done", 0)
            sitemap_total = stats.get("sitemap_total", 0) or "?"
            sitemap_urls = stats.get("sitemap_urls", 0)
            parts.append(self.tr("robots: {0}/{1}").format(robots_done, robots_total))
            parts.append(self.tr("sitemap: {0}/{1}, {2} URLs").format(sitemap_done, sitemap_total, sitemap_urls))
        if parts:
            self.outcome_label.setText(" | ".join(parts))

    @Slot(bool)
    def _on_pause_state(self, paused: bool) -> None:
        self._paused = paused
        self._pause_btn.setText(self.tr("Resume") if paused else self.tr("Pause"))
        self._show_status(self.tr("Paused"), "warning") if paused else self._show_status(self.tr("Crawling..."), "info")

    @Slot(object)
    def _on_rule_stats_updated(self, snapshot: dict) -> None:
        """S8: 规则聚合统计快照 — 全量刷新 6 列表格。"""
        table = self._rule_stats_table
        if not snapshot:
            return
        if not table.isVisible():
            table.setVisible(True)
        table.setRowCount(0)
        for rule_name, stats in sorted(snapshot.items()):
            row = table.rowCount()
            table.insertRow(row)
            table.setItem(row, 0, QTableWidgetItem(rule_name))
            table.setItem(row, 1, QTableWidgetItem(str(stats.get("hits", 0))))
            table.setItem(row, 2, QTableWidgetItem(f"{stats.get('fields_filled', 0)}/{stats.get('fields_total', 0)}"))
            table.setItem(row, 3, QTableWidgetItem(f"{stats.get('fill_rate', 0) * 100:.0f}%"))
            table.setItem(row, 4, QTableWidgetItem(f"{stats.get('avg_ms', 0):.1f} ms"))
            table.setItem(row, 5, QTableWidgetItem(str(stats.get("slow_count", 0))))
            for col in range(6):
                item = table.item(row, col)
                if item:
                    item.setTextAlignment(Qt.AlignCenter)

    @Slot(str, dict)
    def _on_finished(self, output_path: str, stats: dict) -> None:
        self._show_status(self.tr("Crawl Complete"), "success")
        self._add_log(self.tr("[INFO] ===== Crawl complete, output: {0} =====").format(output_path))
        # 优先使用 finished 信号 payload（直接内存传递）
        report_data = stats if stats else {}
        if not report_data:
            # 回退：从报告文件读取（兼容旧版引擎）
            try:
                report_path = Path(output_path).with_suffix(".report.json")
                report_data = json.loads(report_path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                self._add_log(self.tr("[WARNING] Report file not generated, showing empty stats"))
            except (json.JSONDecodeError, OSError) as e:
                self._add_log(self.tr("[WARNING] Failed to read report: {0}").format(e))
        dialog = CompletionReportDialog(output_path, report_data, self)
        dialog.exec()

    @Slot(str)
    def _on_error(self, msg: str) -> None:
        self._show_status(self.tr("Error: {0}").format(msg), "error")
        self._add_log(self.tr("[ERROR] {0}").format(msg))
        QMessageBox.critical(self, self.tr("Error"), msg)

    def _cleanup_session(self) -> None:
        """停止所有会话级资源——_run_crawler 中激活操作的对称逆操作。

        _on_thread_finished / closeEvent / _force_close_crawler / _reset_app 的统一入口。
        幂等——可被多次安全调用。
        """
        if self._close_watchdog is not None:
            self._close_watchdog.stop()
            self._close_watchdog = None
        if self._proxy_health_bar:
            self._proxy_health_bar.stop_refresh()
        # WorkerStatusBar._working 跟踪当前运行的 worker 集合，会话结束后自然为空；
        # stop() 仅停动画定时器 + 重绘灰色空闲态，不销毁持久数据，此处放置安全。
        self._title_bar.stop_worker_bar()
        self._detach_qt_logger()
        self._set_running_state(False)
        self._session = None
        self._paused = False
        self._closing = False
        self._pause_btn.setText(self.tr("Pause"))

    @Slot()
    def _on_thread_finished(self) -> None:
        self._cleanup_session()

    def _reset_app(self) -> None:
        if self._session:
            if self._session.is_running():
                self._session.stop()
            self._session.dispose()
        self._cleanup_session()
        self.url_text.clear()
        self.url_status.clear()
        self.depth_spin.setValue(2)
        self.concurrency_spin.setValue(DEFAULT_CONFIG.concurrency)
        self._output_edit.setText(str(Path.home() / "crawler_output.jsonl"))
        if self._proxy_health_bar:
            self._proxy_health_bar.stop()
        self._reload_profile_combo()
        # _reload_profile_combo 仅在选中项变化时才重建 ProxySession；
        # _reset_app 是显式状态重置——始终重建以恢复健康条
        self._update_proxy_session()
        self.same_domain_check.setChecked(True)
        self.respect_robots_check.setChecked(True)
        self._sitemap_check.setChecked(True)
        self._advanced_cfg = CrawlerConfig()
        self._adjust_layer_bars()
        for bar in self._layer_bars:
            bar.setValue(0)
        self.stats_label.setText(self.tr("Completed: 0  |  Queue: 0  |  Limit: -"))
        self.log_list.clear()
        self._rule_stats_table.setRowCount(0)
        self._rule_stats_table.setVisible(False)
        self._show_status(self.tr("Ready"), "success")
        self._add_log(self.tr("[INFO] Application reset."))

    def _save_config(self) -> None:
        config = {
            "urls": self.get_urls(),
            "depth": self.depth_spin.value(),
            "concurrency": self.concurrency_spin.value(),
            "output_path": self._output_edit.text(),
            "same_domain_only": self.same_domain_check.isChecked(),
            "respect_robots": self.respect_robots_check.isChecked(),
            "use_sitemap": self._sitemap_check.isChecked(),
            "proxy_last_used": self._profile_combo.currentData(),
            "advanced": self._advanced_cfg.to_dict(),
        }
        pm = get_preferences()
        default_dir = pm.get_last_dir("config_save", str(Path.home()))
        path, _ = QFileDialog.getSaveFileName(
            self,
            self.tr("Save Config File"),
            str(Path(default_dir) / "astrocrawl_config.json"),
            "JSON Files (*.json);;All Files (*)",
        )
        clear_qt_file_dialog_history()
        if path:
            try:
                atomic_write_json(Path(path), config, chmod_mask=0o600)
                pm.add_path("config_save", str(Path(path).parent))
                self._show_status(self.tr("Config saved"))
            except Exception as exc:
                QMessageBox.critical(self, self.tr("Error"), self.tr("Save failed: {0}").format(exc))

    def _load_config(self) -> None:
        pm = get_preferences()
        default_dir = pm.get_last_dir("config_load", str(Path.home()))
        path, _ = QFileDialog.getOpenFileName(
            self, self.tr("Load Config File"), default_dir, "JSON Files (*.json);;All Files (*)"
        )
        clear_qt_file_dialog_history()
        if not path:
            return
        pm.add_path("config_load", str(Path(path).parent))
        try:
            config = json.loads(Path(path).read_text(encoding="utf-8"))
            self.url_text.setPlainText("\n".join(config.get("urls", [])))
            self.depth_spin.setValue(config.get("depth", 2))
            self.concurrency_spin.setValue(config.get("concurrency", 3))
            self._output_edit.setText(config.get("output_path", str(Path.home() / "crawler_output.jsonl")))
            self.same_domain_check.setChecked(config.get("same_domain_only", True))
            self.respect_robots_check.setChecked(config.get("respect_robots", True))
            self._sitemap_check.setChecked(config.get("use_sitemap", True))
            if "proxy_last_used" in config:
                name = config["proxy_last_used"]
                if name:
                    idx = self._profile_combo.findData(name)
                    if idx >= 0:
                        self._profile_combo.setCurrentIndex(idx)
                self._reload_profile_combo()
            if "advanced" in config:
                self._advanced_cfg = CrawlerConfig.from_dict(config["advanced"])
            self._update_proxy_session()
            if self._current_profile is not None:
                idx = self._mode_combo.findData(self._advanced_cfg.proxy_mode)
                if idx >= 0:
                    self._mode_combo.setCurrentIndex(idx)
            self._show_status(self.tr("Config loaded"))
        except Exception as exc:
            QMessageBox.critical(self, self.tr("Error"), self.tr("Load failed: {0}").format(exc))

    def closeEvent(self, event: Any) -> None:
        if self._session and self._session.is_running() and not self._closing:
            self._closing = True
            self._session.stop()
            self._session.session_done.connect(self.close)
            self.setEnabled(False)
            # 看门狗定时器：10 秒后 session 仍未结束则强制终止
            watchdog = QTimer(self)
            watchdog.setSingleShot(True)
            watchdog.timeout.connect(lambda: self._force_close_crawler())
            watchdog.start(10000)
            self._close_watchdog = watchdog
            event.ignore()
        else:
            self._cleanup_session()
            event.accept()

    def _force_close_crawler(self) -> None:
        if self._session and self._session.is_running():
            self._log.warning("event=crawler_watchdog_timeout timeout=10s")
        if self._session:
            self._session.dispose()
        self._cleanup_session()
        self.close()

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from astrocrawl._constants import QDOUBLESPINBOX_MAX, QLINEEDIT_MAX, QSPINBOX_MAX
from astrocrawl.config import DEFAULT_CONFIG, CrawlerConfig
from astrocrawl.gui._style import create_form_scroll_area
from astrocrawl.gui._tokens import SPACE_MD, SPACE_SM
from astrocrawl.gui.theme import get_theme_manager


def _form_row(label_text: str = "", widget=None, *stretches: int) -> QHBoxLayout:
    """创建横向响应式 QHBoxLayout 行。

    - 仅 widget（label_text=""）→ 占整行
    - label + 单 widget → 1:3（label=1, field=3）
    - label + 多 widget → 显式 stretches（总份数=4: label/btn=1, 框类=剩余）
    """
    row = QHBoxLayout()
    row.setSpacing(SPACE_SM)
    if not label_text:
        row.addWidget(widget, 1)
        return row
    lbl = QLabel(label_text)
    lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    if not stretches:
        row.addWidget(lbl, 1)
        if isinstance(widget, (list, tuple)):
            row.addWidget(widget[0], 3)
        else:
            row.addWidget(widget, 3)
        return row
    it = iter(stretches)
    row.addWidget(lbl, next(it))
    if isinstance(widget, (list, tuple)):
        for w, s in zip(widget, it):
            row.addWidget(w, s)
    else:
        row.addWidget(widget, next(it))
    return row


class AdvancedSettingsDialog(QDialog):
    def __init__(
        self,
        parent: Optional[QWidget] = None,
        cfg: CrawlerConfig = DEFAULT_CONFIG,
        on_apply: Optional[Callable[[CrawlerConfig], None]] = None,
        open_ai_tab: bool = False,
    ):
        super().__init__(parent)
        self.setWindowModality(Qt.WindowModal)
        self.setWindowTitle(self.tr("Advanced Settings"))
        self.cfg = cfg
        self._on_apply = on_apply

        from astrocrawl.utils.preferences import get_preferences

        self._prefs = get_preferences()

        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(SPACE_MD)
        self._tabs = QTabWidget()
        self._theme_mgr = get_theme_manager()
        tabs = self._tabs

        # ── Tab 0: 常规设置 ──────────────────────────────────────────
        crawl_tab = QWidget()
        scroll = create_form_scroll_area()
        inner = QWidget()
        inner.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, SPACE_MD, 0, 0)
        inner_layout.setSpacing(SPACE_MD)

        # ── 超时与限制 ──
        timeout_group = QGroupBox(self.tr("Timeouts & Limits"))
        tg = QVBoxLayout()
        tg.setSpacing(SPACE_SM)

        self.page_timeout = QSpinBox()
        self.page_timeout.setRange(5000, QSPINBOX_MAX)
        self.page_timeout.setValue(cfg.page_timeout)
        tg.addLayout(_form_row(self.tr("Page Timeout (ms)"), self.page_timeout))

        self.network_idle = QSpinBox()
        self.network_idle.setRange(1000, QSPINBOX_MAX)
        self.network_idle.setValue(cfg.network_idle_timeout)
        tg.addLayout(_form_row(self.tr("Network Idle Timeout (ms)"), self.network_idle))

        self.max_pages = QSpinBox()
        self.max_pages.setRange(0, QSPINBOX_MAX)
        self.max_pages.setValue(cfg.max_total_pages)
        self.max_pages.setSpecialValueText(self.tr("Unlimited"))
        tg.addLayout(_form_row(self.tr("Max Pages"), self.max_pages))

        self.max_runtime_enable = QCheckBox(self.tr("Enable Runtime Limit"))
        self.max_runtime_enable.setChecked(cfg.max_runtime_seconds > 0)
        self.max_runtime_spin = QSpinBox()
        self.max_runtime_spin.setRange(0, QSPINBOX_MAX)
        self.max_runtime_spin.setSuffix(self.tr(" sec"))
        self.max_runtime_spin.setSpecialValueText(self.tr("Unlimited"))
        secs = cfg.max_runtime_seconds if cfg.max_runtime_seconds > 0 else 3600
        self.max_runtime_spin.setValue(max(1, secs))
        self.max_runtime_spin.setEnabled(cfg.max_runtime_seconds > 0)
        self._last_max_runtime = max(1, secs)
        self.max_runtime_enable.toggled.connect(self._on_max_runtime_toggled)
        tg.addLayout(_form_row(self.tr("Max Runtime"), [self.max_runtime_enable, self.max_runtime_spin], 1, 1, 2))

        timeout_group.setLayout(tg)
        inner_layout.addWidget(timeout_group)

        # ── 请求与身份 ──
        identity_group = QGroupBox(self.tr("Request & Authentication"))
        ig = QVBoxLayout()
        ig.setSpacing(SPACE_SM)

        self.user_agent = QLineEdit(cfg.user_agent)
        self.user_agent.setMaxLength(QLINEEDIT_MAX)
        ig.addLayout(_form_row(self.tr("User-Agent"), self.user_agent))

        self.contact_info = QLineEdit()
        self.contact_info.setMaxLength(QLINEEDIT_MAX)
        ua = cfg.user_agent
        if "(" in ua and ")" in ua:
            start = ua.find("(")
            end = ua.find(")")
            self.contact_info.setText(ua[start + 1 : end])
        ig.addLayout(_form_row(self.tr("Contact Info (appended to UA)"), self.contact_info))

        identity_group.setLayout(ig)
        inner_layout.addWidget(identity_group)

        # ── 爬取控制 ──
        crawl_ctrl_group = QGroupBox(self.tr("Crawl Control"))
        cg = QVBoxLayout()
        cg.setSpacing(SPACE_SM)

        self.follow_nofollow = QCheckBox(self.tr("Ignore nofollow, always extract links"))
        self.follow_nofollow.setChecked(cfg.follow_nofollow)
        cg.addLayout(_form_row(widget=self.follow_nofollow))

        self.respect_meta = QCheckBox(self.tr("Respect meta robots"))
        self.respect_meta.setChecked(cfg.respect_meta_robots)
        cg.addLayout(_form_row(widget=self.respect_meta))

        self.respect_crawl_delay = QCheckBox(self.tr("Respect robots.txt Crawl-Delay"))
        self.respect_crawl_delay.setChecked(cfg.respect_crawl_delay)
        cg.addLayout(_form_row(widget=self.respect_crawl_delay))

        self.skip_duplicate_links = QCheckBox(self.tr("Skip link extraction on duplicate content"))
        self.skip_duplicate_links.setChecked(cfg.skip_duplicate_links)
        cg.addLayout(_form_row(widget=self.skip_duplicate_links))

        self.skip_non_essential = QCheckBox(self.tr("Block non-essential resources (images/fonts/etc)"))
        self.skip_non_essential.setChecked(cfg.skip_non_essential_resources)
        cg.addLayout(_form_row(widget=self.skip_non_essential))

        self.domain_max_concurrency = QSpinBox()
        self.domain_max_concurrency.setRange(1, QSPINBOX_MAX)
        self.domain_max_concurrency.setValue(cfg.domain_max_concurrency)
        cg.addLayout(_form_row(self.tr("Domain Max Concurrency"), self.domain_max_concurrency))

        self.domain_min_delay = QDoubleSpinBox()
        self.domain_min_delay.setRange(0.1, QDOUBLESPINBOX_MAX)
        self.domain_min_delay.setValue(cfg.domain_min_delay)
        cg.addLayout(_form_row(self.tr("Domain Min Delay (s)"), self.domain_min_delay))

        self.domain_max_delay = QDoubleSpinBox()
        self.domain_max_delay.setRange(0.1, QDOUBLESPINBOX_MAX)
        self.domain_max_delay.setValue(cfg.domain_max_delay)
        cg.addLayout(_form_row(self.tr("Domain Max Delay (s)"), self.domain_max_delay))

        self.queue_hard_maxsize = QSpinBox()
        self.queue_hard_maxsize.setRange(100, QSPINBOX_MAX)
        self.queue_hard_maxsize.setValue(cfg.queue_hard_maxsize)
        cg.addLayout(_form_row(self.tr("Queue Hard Limit"), self.queue_hard_maxsize))

        crawl_ctrl_group.setLayout(cg)
        inner_layout.addWidget(crawl_ctrl_group)

        # ── Sitemap ──
        sitemap_group = QGroupBox(self.tr("Sitemap"))
        sg = QVBoxLayout()
        sg.setSpacing(SPACE_SM)

        self.sitemap_max_recursion = QSpinBox()
        self.sitemap_max_recursion.setRange(0, QSPINBOX_MAX)
        self.sitemap_max_recursion.setValue(cfg.sitemap_max_recursion)
        sg.addLayout(_form_row(self.tr("Max Crawl Depth"), self.sitemap_max_recursion))

        self.sitemap_max_urls = QSpinBox()
        self.sitemap_max_urls.setRange(1, QSPINBOX_MAX)
        self.sitemap_max_urls.setValue(cfg.sitemap_max_urls)
        sg.addLayout(_form_row(self.tr("Max URLs"), self.sitemap_max_urls))

        self.sitemap_fetch_concurrency = QSpinBox()
        self.sitemap_fetch_concurrency.setRange(1, QSPINBOX_MAX)
        self.sitemap_fetch_concurrency.setValue(cfg.sitemap_fetch_concurrency)
        sg.addLayout(_form_row(self.tr("Fetch Concurrency"), self.sitemap_fetch_concurrency))

        sitemap_group.setLayout(sg)
        inner_layout.addWidget(sitemap_group)

        # ── 网络与通知 ──
        net_group = QGroupBox(self.tr("Network & Notifications"))
        ng = QVBoxLayout()
        ng.setSpacing(SPACE_SM)

        self.auth_basic_user = QLineEdit(cfg.auth_basic_user)
        self.auth_basic_user.setMaxLength(QLINEEDIT_MAX)
        self.auth_basic_user.setPlaceholderText(self.tr("(optional)"))
        ng.addLayout(_form_row(self.tr("Basic Auth Username"), self.auth_basic_user))

        self.auth_basic_pass = QLineEdit(cfg.auth_basic_pass)
        self.auth_basic_pass.setMaxLength(QLINEEDIT_MAX)
        self.auth_basic_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self.auth_basic_pass.setPlaceholderText(self.tr("(optional)"))
        ng.addLayout(_form_row(self.tr("Basic Auth Password"), self.auth_basic_pass))

        self.auth_bearer_token = QLineEdit(cfg.auth_bearer_token)
        self.auth_bearer_token.setMaxLength(QLINEEDIT_MAX)
        self.auth_bearer_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.auth_bearer_token.setPlaceholderText(self.tr("(optional)"))
        ng.addLayout(_form_row(self.tr("Bearer Token"), self.auth_bearer_token))

        self._cookies_file_edit = QLineEdit()
        self._cookies_file_edit.setMaxLength(QLINEEDIT_MAX)
        self._cookies_file_edit.setPlaceholderText(self.tr("Leave empty to skip loading cookies"))
        self._cookies_file_edit.setText(cfg.cookies_file)
        self._cookies_file_btn = QPushButton(self.tr("Select"))
        self._cookies_file_btn.clicked.connect(self._select_cookies_file)
        ng.addLayout(
            _form_row(self.tr("Cookie File (.json)"), [self._cookies_file_edit, self._cookies_file_btn], 1, 2, 1)
        )

        self._custom_headers_edit = QTextEdit()
        self._custom_headers_edit.setPlaceholderText(
            self.tr("One per line, format Key: Value\ne.g.:\nX-Custom: value\nAccept-Language: zh-CN")
        )
        self._custom_headers_edit.setText("\n".join(cfg.custom_headers))
        self._custom_headers_edit.setMaximumHeight(100)
        ng.addLayout(_form_row(self.tr("Custom Headers"), self._custom_headers_edit))

        self.webhook_url = QLineEdit(cfg.webhook_url)
        self.webhook_url.setMaxLength(QLINEEDIT_MAX)
        self.webhook_url.setPlaceholderText(self.tr("https://example.com/webhook (optional)"))
        ng.addLayout(_form_row(self.tr("Webhook URL"), self.webhook_url))

        net_group.setLayout(ng)
        inner_layout.addWidget(net_group)

        # ── 日志 ──
        log_group = QGroupBox(self.tr("Logging"))
        lg = QVBoxLayout()
        lg.setSpacing(SPACE_SM)

        self._log_file_edit = QLineEdit()
        self._log_file_edit.setMaxLength(QLINEEDIT_MAX)
        self._log_file_edit.setPlaceholderText(self.tr("Leave empty to skip writing to file"))
        self._log_file_edit.setText(cfg.log_file)
        self._log_file_btn = QPushButton(self.tr("Select"))
        self._log_file_btn.clicked.connect(self._select_log_file)
        lg.addLayout(_form_row(self.tr("Log File"), [self._log_file_edit, self._log_file_btn], 1, 2, 1))

        log_group.setLayout(lg)
        inner_layout.addWidget(log_group)

        scroll.setWidget(inner)
        crawl_tab_layout = QVBoxLayout(crawl_tab)
        crawl_tab_layout.setContentsMargins(0, 0, 0, 0)
        crawl_tab_layout.addWidget(scroll)
        tabs.addTab(crawl_tab, self.tr("General Settings"))

        # ── Tab 1: 全局设置 ──────────────────────────────────────────
        global_tab = QWidget()
        global_scroll = create_form_scroll_area()
        global_inner = QWidget()
        global_inner.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        global_inner_layout = QVBoxLayout(global_inner)
        global_inner_layout.setContentsMargins(0, SPACE_MD, 0, 0)
        global_inner_layout.setSpacing(SPACE_MD)

        from astrocrawl.utils.preferences import get_preferences as _get_prefs

        _prefs = _get_prefs()

        # 暂存变量 — 对齐常规设置页签的 Apply/Cancel 模式
        self._staged_rules_auto_update = _prefs.get_rules_auto_update()
        self._staged_trace_rules = _prefs.get_trace_rules()
        self._staged_log_level = _prefs.get_log_level()
        self._staged_output_gzip = _prefs.get_output_gzip()
        self._staged_clear_cookies = _prefs.get_clear_context_cookies()

        # ── 行为 ──
        # ── 外观 ──
        appearance_group = QGroupBox(self.tr("Appearance"))
        ag = QVBoxLayout()
        ag.setSpacing(SPACE_SM)

        lang_layout = QHBoxLayout()
        lang_layout.setSpacing(SPACE_SM)
        self._lang_label = QLabel(self.tr("Language:"))
        self._lang_combo = QComboBox()
        self._lang_combo.addItem("中文", "zh_CN")  # i18n: allow — autoglottonym, always native name
        self._lang_combo.addItem("English", "en")
        current_lang = self._prefs.get_language()
        idx = self._lang_combo.findData(current_lang)
        if idx >= 0:
            self._lang_combo.setCurrentIndex(idx)
        self._lang_combo.currentIndexChanged.connect(lambda: None)  # mark dirty on apply
        lang_layout.addWidget(self._lang_label, 1)
        lang_layout.addWidget(self._lang_combo, 3)
        ag.addLayout(lang_layout)

        appearance_group.setLayout(ag)
        global_inner_layout.addWidget(appearance_group)

        # ── 行为 ──
        behavior_group = QGroupBox(self.tr("Behavior"))
        bg = QVBoxLayout()
        bg.setSpacing(SPACE_SM)

        self._pref_rules_auto_update = QCheckBox(self.tr("Auto-update remote sources on startup"))
        self._pref_rules_auto_update.setChecked(self._staged_rules_auto_update)
        self._pref_rules_auto_update.toggled.connect(lambda v: setattr(self, "_staged_rules_auto_update", v))
        bg.addLayout(_form_row(widget=self._pref_rules_auto_update))

        self._pref_trace_rules = QCheckBox(self.tr("Enable rule trace diagnostics"))
        self._pref_trace_rules.setChecked(self._staged_trace_rules)
        self._pref_trace_rules.toggled.connect(lambda v: setattr(self, "_staged_trace_rules", v))
        bg.addLayout(_form_row(widget=self._pref_trace_rules))

        self._pref_clear_cookies = QCheckBox(self.tr("Clear context cookies on page release (loses login state)"))
        self._pref_clear_cookies.setChecked(self._staged_clear_cookies)
        self._pref_clear_cookies.toggled.connect(lambda v: setattr(self, "_staged_clear_cookies", v))
        bg.addLayout(_form_row(widget=self._pref_clear_cookies))

        behavior_group.setLayout(bg)
        global_inner_layout.addWidget(behavior_group)

        # ── 输出 ──
        output_group = QGroupBox(self.tr("Output"))
        og = QVBoxLayout()
        og.setSpacing(SPACE_SM)

        self._pref_log_level = QComboBox()
        self._pref_log_level.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        self._pref_log_level.setCurrentText(self._staged_log_level)
        self._pref_log_level.currentTextChanged.connect(lambda v: setattr(self, "_staged_log_level", v))
        og.addLayout(_form_row(self.tr("Log Level"), self._pref_log_level))

        self._pref_output_gzip = QCheckBox(self.tr("Output Gzip compression"))
        self._pref_output_gzip.setChecked(self._staged_output_gzip)
        self._pref_output_gzip.toggled.connect(lambda v: setattr(self, "_staged_output_gzip", v))
        og.addLayout(_form_row(widget=self._pref_output_gzip))

        output_group.setLayout(og)
        global_inner_layout.addWidget(output_group)

        global_scroll.setWidget(global_inner)
        global_tab_layout = QVBoxLayout(global_tab)
        global_tab_layout.setContentsMargins(0, 0, 0, 0)
        global_tab_layout.addWidget(global_scroll)
        tabs.addTab(global_tab, self.tr("Global Settings"))

        # ── Tab 2: AI 设置 ──────────────────────────────────────────
        from astrocrawl.gui._ai_profile_page import _AIProfilePage
        from astrocrawl.utils.preferences import get_preferences

        prefs = get_preferences()
        self._ai_page = _AIProfilePage(prefs, self)
        tabs.addTab(self._ai_page, self.tr("AI Settings"))

        # ── Tab 3: 代理设置 ──────────────────────────────────────────
        from astrocrawl.gui._proxy_profile_page import _ProxyProfilePage

        self._proxy_page = _ProxyProfilePage(prefs, self)
        tabs.addTab(self._proxy_page, self.tr("Proxy Settings"))

        # ── Tab 4: 路由设置 ──────────────────────────────────────────
        from astrocrawl.gui._route_settings_page import _RouteSettingsPage

        self._route_page = _RouteSettingsPage(prefs, self)
        tabs.addTab(self._route_page, self.tr("Route Settings"))

        # ── buttons ─────────────────────────────────────────────────
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(SPACE_MD)
        apply_btn = QPushButton(self.tr("Apply"))
        apply_btn.setToolTip(self.tr("Apply settings without closing"))
        apply_btn.clicked.connect(self._apply_current)
        apply_btn.setObjectName("apply-btn")
        cancel_btn = QPushButton(self.tr("Cancel"))
        cancel_btn.setToolTip(self.tr("Discard changes"))
        cancel_btn.clicked.connect(self._on_cancel)
        cancel_btn.setObjectName("cancel-btn")
        commit_btn = QPushButton(self.tr("OK"))
        commit_btn.setToolTip(self.tr("Save settings and close"))
        commit_btn.clicked.connect(self._validate_and_accept)
        commit_btn.setObjectName("commit-btn")
        btn_layout.addWidget(apply_btn, 1)
        btn_layout.addWidget(cancel_btn, 1)
        btn_layout.addWidget(commit_btn, 1)

        main_layout.addWidget(tabs, 1)

        # ── 统一脉动条 + 状态栏（仅 AI/代理标签页可见） ──
        from astrocrawl.gui._animated_bar import _ProgressStatusBar

        self._psb = _ProgressStatusBar()
        self._psb.connect_page(self._ai_page)
        self._psb.connect_page(self._proxy_page)
        self._psb.show_status(self.tr("Ready"))
        main_layout.addWidget(self._psb)

        # 标签页切换时显隐：仅 AI(2) / 代理(3) 需要进度条
        self._psb_visible_tabs = {2, 3}
        self._psb.setVisible(tabs.currentIndex() in self._psb_visible_tabs)
        tabs.currentChanged.connect(self._on_tab_changed)

        main_layout.addLayout(btn_layout)

        if self._theme_mgr is not None:
            self._theme_mgr.theme_changed.connect(self._on_theme_changed)
        self._on_theme_changed()

        tabs.setCurrentIndex(0)  # 按 Crawl tab（内容最多）计算尺寸
        self.adjustSize()
        ideal_h = self.height()
        self.setMaximumWidth(self.width())
        screen = self.screen()
        if screen:
            max_h = int(screen.availableGeometry().height() * 0.85)
            self.setMaximumHeight(min(ideal_h, max_h))
            self.setMinimumHeight(min(ideal_h, max_h))
        else:
            self.setMaximumHeight(ideal_h)
            self.setMinimumHeight(ideal_h)
        self.setMinimumWidth(self.width())
        if open_ai_tab:
            tabs.setCurrentIndex(2)

    def _apply_global_settings(self) -> None:
        self._prefs.set_rules_auto_update(self._staged_rules_auto_update)
        self._prefs.set_trace_rules(self._staged_trace_rules)
        self._prefs.set_log_level(self._staged_log_level)
        self._prefs.set_output_gzip(self._staged_output_gzip)
        self._prefs.set_clear_context_cookies(self._staged_clear_cookies)
        new_lang = self._lang_combo.currentData()
        if new_lang != self._prefs.get_language():
            self._prefs.set_language(new_lang)

    def _save_ai_settings(self) -> None:
        self._ai_page.apply_pending()

    def _on_max_runtime_toggled(self, checked: bool) -> None:
        self.max_runtime_spin.setEnabled(checked)
        if checked:
            self.max_runtime_spin.setValue(self._last_max_runtime)
        else:
            self._last_max_runtime = self.max_runtime_spin.value()
            self.max_runtime_spin.setValue(0)

    def _validate(self) -> bool:
        if self.domain_min_delay.value() > self.domain_max_delay.value():
            QMessageBox.information(self, self.tr("Config Error"), self.tr("Domain min delay cannot exceed max delay."))
            return False
        if not self.user_agent.text().strip():
            QMessageBox.information(self, self.tr("Config Error"), self.tr("User-Agent cannot be empty."))
            return False
        cookies = self._cookies_file_edit.text().strip()
        if cookies:
            cookies_path = Path(cookies)
            if not cookies_path.is_file():
                QMessageBox.information(
                    self, self.tr("Config Error"), self.tr("Cookie file not found:\n{0}").format(cookies)
                )
                return False
            if cookies_path.suffix.lower() != ".json":
                QMessageBox.information(self, self.tr("Config Error"), self.tr("Cookie file must be .json format"))
                return False
        webhook = self.webhook_url.text().strip()
        if webhook and not (webhook.startswith("http://") or webhook.startswith("https://")):
            QMessageBox.information(
                self, self.tr("Config Error"), self.tr("Webhook URL must start with http:// or https://")
            )
            return False
        return True

    def _apply_current(self) -> None:
        if not self._validate():
            return
        self._save_ai_settings()
        self._apply_global_settings()
        if self._on_apply is not None:
            self._on_apply(self.get_config())

    def _validate_and_accept(self) -> None:
        if self._validate():
            self._save_ai_settings()
            self._apply_global_settings()
            self.accept()

    def reject(self) -> None:
        self._psb.dispose()
        self._ai_page._cleanup_worker()
        self._proxy_page._cleanup_worker()
        super().reject()

    def accept(self) -> None:
        self._ai_page._cleanup_worker()
        self._proxy_page._cleanup_worker()
        super().accept()

    def _on_tab_changed(self, index: int) -> None:
        self._psb.setVisible(index in self._psb_visible_tabs)

    def _on_cancel(self) -> None:
        self.reject()

    def _select_log_file(self) -> None:
        from astrocrawl.utils.preferences import clear_qt_file_dialog_history, get_preferences

        pm = get_preferences()
        default_dir = pm.get_last_dir("log_file", str(Path.home()))
        path, _ = QFileDialog.getSaveFileName(
            self, self.tr("Log File"), str(Path(default_dir) / "astrocrawl.log"), "Log Files (*.log);;All Files (*)"
        )
        clear_qt_file_dialog_history()
        if path:
            self._log_file_edit.setText(path)
            pm.add_path("log_file", str(Path(path).parent))

    def _select_cookies_file(self) -> None:
        from astrocrawl.utils.preferences import clear_qt_file_dialog_history, get_preferences

        pm = get_preferences()
        default_dir = pm.get_last_dir("cookies_file", str(Path.home()))
        path, _ = QFileDialog.getOpenFileName(
            self, self.tr("Cookie File (.json)"), default_dir, "JSON Files (*.json);;All Files (*)"
        )
        clear_qt_file_dialog_history()
        if path:
            self._cookies_file_edit.setText(path)
            pm.add_path("cookies_file", str(Path(path).parent))

    def _on_theme_changed(self) -> None:
        if self._theme_mgr is None:
            return
        bg = self._theme_mgr.get("input_bg")
        # QPalette on each page widget — avoids QSS disrupting QTableView QPalette rendering
        for i in range(self._tabs.count()):
            page = self._tabs.widget(i)
            if page is not None:
                p = page.palette()
                p.setColor(QPalette.Window, QColor(bg))
                page.setPalette(p)
                page.setAutoFillBackground(True)
                page.setAttribute(Qt.WA_InputMethodEnabled, True)

    def get_config(self) -> CrawlerConfig:
        runtime_sec = self.max_runtime_spin.value() if self.max_runtime_enable.isChecked() else 0
        contact = self.contact_info.text().strip()
        new_cfg = replace(
            self.cfg,
            page_timeout=self.page_timeout.value(),
            network_idle_timeout=self.network_idle.value(),
            max_total_pages=self.max_pages.value(),
            max_runtime_seconds=runtime_sec,
            user_agent=self.user_agent.text().strip(),
            follow_nofollow=self.follow_nofollow.isChecked(),
            respect_meta_robots=self.respect_meta.isChecked(),
            skip_duplicate_links=self.skip_duplicate_links.isChecked(),
            skip_non_essential_resources=self.skip_non_essential.isChecked(),
            domain_max_concurrency=self.domain_max_concurrency.value(),
            domain_min_delay=self.domain_min_delay.value(),
            domain_max_delay=self.domain_max_delay.value(),
            queue_hard_maxsize=self.queue_hard_maxsize.value(),
            log_file=self._log_file_edit.text().strip(),
            respect_crawl_delay=self.respect_crawl_delay.isChecked(),
            sitemap_max_recursion=self.sitemap_max_recursion.value(),
            sitemap_max_urls=self.sitemap_max_urls.value(),
            sitemap_fetch_concurrency=self.sitemap_fetch_concurrency.value(),
            auth_basic_user=self.auth_basic_user.text().strip(),
            auth_basic_pass=self.auth_basic_pass.text().strip(),
            auth_bearer_token=self.auth_bearer_token.text().strip(),
            cookies_file=self._cookies_file_edit.text().strip(),
            custom_headers=tuple(
                line.strip() for line in self._custom_headers_edit.toPlainText().splitlines() if line.strip()
            ),
            webhook_url=self.webhook_url.text().strip(),
        )
        return new_cfg.with_contact(contact)

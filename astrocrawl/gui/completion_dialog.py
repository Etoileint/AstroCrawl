"""CompletionReportDialog — 爬取完成报告弹窗。

从 MainWindow._on_finished() 提取，使用 generate_report() 的统一 dict 渲染。
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from astrocrawl.crawler.outcomes import UrlOutcome
from astrocrawl.gui._tokens import SPACE_MD


class CompletionReportDialog(QDialog):
    """爬取完成报告弹窗。使用 generate_report() 统一输出格式。"""

    def __init__(
        self,
        output_path: str,
        report_data: dict,
        parent: Optional[QDialog] = None,
    ):
        super().__init__(parent)
        self.setWindowModality(Qt.WindowModal)
        self._output_path = output_path
        self._report = report_data
        self._build()

    def _build(self) -> None:
        self.setWindowTitle(self.tr("Crawl Complete"))
        self.setSizeGripEnabled(True)

        layout = QVBoxLayout(self)
        layout.setSpacing(SPACE_MD)

        # ── 头信息 ──
        layout.addWidget(QLabel(self.tr("<b>Output:</b> {0}").format(self._output_path)), 0)

        meta_row = QHBoxLayout()
        meta_row.setSpacing(SPACE_MD)
        start_lbl = QLabel(self.tr("<b>Start:</b> {0}").format(self._report.get("start_time", "")))
        end_lbl = QLabel(self.tr("<b>End:</b> {0}").format(self._report.get("end_time", "")))
        dur_lbl = QLabel(self.tr("<b>Duration:</b> {0:.1f}s").format(self._report.get("duration_seconds", 0)))
        for lbl in (start_lbl, end_lbl, dur_lbl):
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        meta_row.addWidget(start_lbl, 4)
        meta_row.addWidget(end_lbl, 4)
        meta_row.addWidget(dur_lbl, 2)
        layout.addLayout(meta_row, 0)

        # ── 分隔线 ──
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(line, 0)

        # ── 表格 ──
        rows = self._build_rows()
        table = self._build_table(rows)
        layout.addWidget(table, 1)

        # ── 按钮 ──
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(SPACE_MD)
        close_btn = QPushButton(self.tr("Close"))
        close_btn.setObjectName("close-btn")
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn, 1)
        layout.addLayout(btn_layout, 0)

        # ── 初始尺寸：adjustSize 锁定 + 屏幕高度上限 ──
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

    def _build_rows(self) -> List[tuple]:
        """构建报告行列表。每项: ("section", text) | ("label", value) | ("sep",)"""
        rows: List[tuple] = []
        outcomes = self._report.get("outcome_summary", {})

        # Content Results
        rows.append(("section", self.tr("Content Results")))
        rows.append((self.tr("Saved"), outcomes.get(UrlOutcome.OK.value, 0)))
        rows.append((self.tr("Truncated"), outcomes.get(UrlOutcome.TRUNCATED.value, 0)))
        rows.append((self.tr("Robots.txt Denied"), outcomes.get(UrlOutcome.ROBOTS_DENIED.value, 0)))
        rows.append((self.tr("Noindex Skipped"), outcomes.get(UrlOutcome.NOINDEX.value, 0)))
        rows.append((self.tr("Content Duplicate"), outcomes.get(UrlOutcome.DUPLICATE.value, 0)))
        rows.append((self.tr("Parse Failed"), outcomes.get(UrlOutcome.PARSE_FAILED.value, 0)))
        pf = (
            outcomes.get(UrlOutcome.FETCH_ERROR.value, 0)
            + outcomes.get(UrlOutcome.INTERNAL_ERROR.value, 0)
            + outcomes.get(UrlOutcome.STOPPED.value, 0)
        )
        if pf:
            rows.append((self.tr("Fetch Failed"), pf))
        rows.append((self.tr("Redirected"), self._report.get("redirects", 0)))

        # 发现统计
        discovery = self._report.get("discovery", {})
        robots_d = discovery.get("robots", {})
        sitemap_d = discovery.get("sitemap", {})
        rows.append(("sep",))
        rows.append(("section", self.tr("Discovery Stats")))
        rows.append((self.tr("Robots.txt OK"), robots_d.get("ok", 0)))
        rows.append((self.tr("Robots.txt Failed"), robots_d.get("fetch_fail", 0)))
        rows.append((self.tr("Sitemap OK"), sitemap_d.get("ok", 0)))
        rows.append((self.tr("Sitemap Failed"), sitemap_d.get("fetch_fail", 0)))
        rows.append((self.tr("Sitemap Discovered"), sitemap_d.get("discovered_urls", 0)))

        # 失败分类
        fetch_errs = self._report.get("fetch_errors", {})
        if fetch_errs:
            rows.append(("sep",))
            rows.append(("section", self.tr("Failure Categories")))
            for cat, cnt in sorted(fetch_errs.items()):
                if cnt:
                    rows.append((cat, cnt))

        # 过滤丢弃
        drops = self._report.get("drops", {})
        if drops:
            total_drop = sum(drops.values())
            rows.append(("sep",))
            rows.append(("section", self.tr("Dropped ({0} total)").format(total_drop)))
            for reason, cnt in sorted(drops.items()):
                if cnt:
                    rows.append((reason, cnt))

        return rows

    def _build_table(self, rows: List[tuple]) -> QTableWidget:
        data_rows = [r for r in rows if r[0] not in ("sep",)]
        table = QTableWidget(len(data_rows), 2)
        table.setHorizontalHeaderLabels([self.tr("Item"), self.tr("Count")])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        table.setShowGrid(False)
        table.setAlternatingRowColors(True)
        table.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        bold_font = QFont()
        bold_font.setBold(True)

        table_row = 0
        for item in rows:
            if item[0] == "sep":
                continue
            if item[0] == "section":
                label_widget = QTableWidgetItem(item[1])
                label_widget.setFont(bold_font)
                table.setItem(table_row, 0, label_widget)
                table.setItem(table_row, 1, QTableWidgetItem(""))
                table.setSpan(table_row, 0, 1, 2)
                table_row += 1
            else:
                label_item = QTableWidgetItem(item[0])
                val_item = QTableWidgetItem(str(item[1]))
                val_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                table.setItem(table_row, 0, label_item)
                table.setItem(table_row, 1, val_item)
                table_row += 1

        row_h = table.rowHeight(0)
        for r in range(len(data_rows)):
            table.setRowHeight(r, int(row_h * 1.4))  # 1.4x 行高提升表格可读性
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        return table

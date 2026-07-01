"""GUI 样式工具 — 消除重复 CSS 片段、控件模式和表格创建样板。"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QCheckBox, QHBoxLayout, QHeaderView, QScrollArea, QTableView, QWidget

from astrocrawl.gui._tokens import FONT_MD, RADIUS_MD, SPACE_SM, SPACE_XS

# ── ColumnDef — 声明式列配置（ADR-0007）───────────────────────────────────


@dataclass(frozen=True)
class ColumnDef:
    """QTableView 列声明 — 消除三处表格创建样板。

    Attributes:
        key: 逻辑 key，Model 用此标识列
        label: 表头显示文本
        resize: "stretch" | "fixed" | "resize_to_contents"
        width: fixed 模式的列宽（px）
        alignment: 单元格文字对齐（Qt.AlignmentFlag）
    """

    key: str
    label: str
    resize: str = "stretch"
    width: int = 100
    alignment: int = Qt.AlignLeft | Qt.AlignVCenter


def create_managed_table(object_name: str = "") -> QTableView:
    """创建并初始化 QTableView（不含 model 依赖的 header 配置）。

    统一设置：行选择模式、禁止编辑、交替行色、隐藏纵向表头。
    Header resize mode 在 setModel 后通过 configure_table_header() 设置。
    """
    table = QTableView()
    if object_name:
        table.setObjectName(object_name)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setAlternatingRowColors(True)
    table.verticalHeader().setVisible(False)
    return table


def configure_table_header(table: QTableView, columns: list[ColumnDef]) -> None:
    """配置 QTableView 横向表头的 resize mode 和固定宽度（在 setModel 之后调用）。"""
    header = table.horizontalHeader()
    for col_idx, col_def in enumerate(columns):
        header.setSectionResizeMode(col_idx, _resize_mode(col_def.resize))
        if col_def.resize == "fixed":
            header.resizeSection(col_idx, col_def.width)


def _resize_mode(key: str) -> QHeaderView.ResizeMode:
    if key == "fixed":
        return QHeaderView.ResizeMode.Fixed
    if key == "resize_to_contents":
        return QHeaderView.ResizeMode.ResizeToContents
    return QHeaderView.ResizeMode.Stretch


# ── QScrollArea 工厂 — 表单类页面标准容器 ────────────────────────────────────


def create_form_scroll_area() -> QScrollArea:
    """创建标准化表单滚动区域——gui-standards.md §10.1 纯表单/分区表单模式的 SSOT 入口。

    统一配置：widgetResizable、隐藏横向滚动条、NoFrame、WA_InputMethodEnabled。
    声明 QScrollArea 及其 viewport 支持输入法（在所有平台上无害且语义正确）。
    """
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setFrameShape(QScrollArea.NoFrame)
    scroll.setAttribute(Qt.WA_InputMethodEnabled, True)
    scroll.viewport().setAttribute(Qt.WA_InputMethodEnabled, True)
    return scroll


# ── CSS helpers ───────────────────────────────────────────────────────────


def status_label_style(bg_color: str) -> str:
    """状态标签 — 圆角背景 + 内边距。消除 main_window 中 4 处重复。"""
    return f"background: {bg_color}; padding: {SPACE_XS}px {SPACE_SM}px; border-radius: {RADIUS_MD}px;"


def monospace_style() -> str:
    """等宽字体文本区样式。"""
    return f"font-family: monospace; font-size: {FONT_MD}px;"


def centered_checkbox_container(cb: QCheckBox) -> QWidget:
    """复选框在表格单元格中居中。消除 rules_dialog 中 ~5 处重复。"""
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(cb)
    return container

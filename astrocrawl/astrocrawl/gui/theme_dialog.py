"""主题设置对话框 — 三模式 radio 选择 + 15 令牌颜色自定义。

模式与自定义颜色均使用 QGroupBox 统一视觉。
自定义颜色采用 QFormLayout 双列响应式布局：左列名称，右列色块（横向拉伸）+ hex。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QColorDialog,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from astrocrawl.gui._style import create_form_scroll_area
from astrocrawl.gui._tokens import RADIUS_MD, SPACE_LG, SPACE_MD, SPACE_SM, SPACE_XS
from astrocrawl.gui.theme import get_theme_manager

_TOKEN_LABELS: list[tuple[str, str]] = [
    ("window_bg", "Window Background"),
    ("window_text", "Window Text"),
    ("button_bg", "Button Background"),
    ("button_text", "Button Text"),
    ("input_bg", "Input Background"),
    ("input_bg_alt", "Table Alt Row"),
    ("input_text", "Input Text"),
    ("accent", "Accent"),
    ("border", "Border"),
    ("disabled", "Disabled Text"),
    ("success", "Success"),
    ("warning", "Warning"),
    ("danger", "Danger"),
    ("worker_grad_start", "Worker Gradient Start"),
    ("worker_grad_end", "Worker Gradient End"),
]


class _SwatchField(QWidget):
    """色块按钮（横向拉伸）+ hex 标签，作为 QFormLayout 的 field 控件。"""

    def __init__(self, token: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._token = token
        self._color_hex = "#000000"

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_SM)

        self._swatch = QPushButton()
        self._swatch.setMinimumHeight(20)
        self._swatch.setSizePolicy(
            self._swatch.sizePolicy().horizontalPolicy(),
            self._swatch.sizePolicy().verticalPolicy(),
        )
        self._swatch.clicked.connect(self._pick_color)
        layout.addWidget(self._swatch, 1)

        self._hex_label = QLabel()
        self._hex_label.setFixedWidth(72)
        self._hex_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        layout.addWidget(self._hex_label)

    def _pick_color(self) -> None:
        initial = QColor(self._color_hex)
        new_color = QColorDialog.getColor(initial, self, self.tr("Choose {0}").format(self._token))
        if new_color.isValid():
            self._color_hex = new_color.name()
            self._update_swatch()

    @property
    def token(self) -> str:
        return self._token

    @property
    def color_hex(self) -> str:
        return self._color_hex

    def set_color(self, hex_val: str) -> None:
        self._color_hex = hex_val
        self._update_swatch()

    def _update_swatch(self) -> None:
        bc = get_theme_manager().get("border")
        self._swatch.setStyleSheet(
            f"background-color: {self._color_hex}; border: 1px solid {bc}; border-radius: {RADIUS_MD}px;"
        )
        self._hex_label.setText(self._color_hex)

    def set_enabled(self, enabled: bool) -> None:
        self._swatch.setEnabled(enabled)


class ThemeDialog(QDialog):
    """主题设置弹窗。"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowModality(Qt.WindowModal)
        self.setWindowTitle(self.tr("Theme Settings"))

        self._theme_mgr = get_theme_manager()

        config = self._theme_mgr.get_config()
        self._orig_mode = config["mode"]
        self._orig_base = config["base"]
        self._orig_overrides = config["overrides"]

        self._temp_mode = self._orig_mode
        self._temp_base = self._orig_base
        self._swatch_fields: dict[str, _SwatchField] = {}

        self._setup_ui()
        self._sync_ui()

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

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(SPACE_MD)

        # ── 模式选择（QGroupBox，与下方颜色组统一） ──
        mode_group = QGroupBox(self.tr("Mode"))
        mode_layout = QHBoxLayout(mode_group)
        mode_layout.setSpacing(SPACE_SM)
        self._mode_btn_group = QButtonGroup(self)
        self._radio_light = QRadioButton(self.tr("☀ Light"))
        self._radio_dark = QRadioButton(self.tr("★ Dark"))
        self._radio_custom = QRadioButton(self.tr("✿ Custom"))
        self._mode_btn_group.addButton(self._radio_light, 0)
        self._mode_btn_group.addButton(self._radio_dark, 1)
        self._mode_btn_group.addButton(self._radio_custom, 2)
        self._mode_btn_group.buttonClicked.connect(self._on_mode_changed)
        mode_layout.addWidget(self._radio_light, 1)
        mode_layout.addWidget(self._radio_dark, 1)
        mode_layout.addWidget(self._radio_custom, 1)
        layout.addWidget(mode_group)

        # ── 自定义颜色（QFormLayout 双列，色块横向拉伸） ──
        self._color_group = QGroupBox(self.tr("Custom Colors (editable in ✿ Custom mode only)"))
        self._color_group.setEnabled(False)
        form = QFormLayout(self._color_group)
        form.setVerticalSpacing(SPACE_XS)
        form.setHorizontalSpacing(SPACE_LG)
        # QFormLayout 列伸缩策略：field 列可伸展
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        for token, label in _TOKEN_LABELS:
            swatch = _SwatchField(token)
            self._swatch_fields[token] = swatch
            form.addRow(self.tr(label), swatch)

        scroll = create_form_scroll_area()
        inner = QWidget()
        inner.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, 0, 0, 0)
        inner_layout.addWidget(self._color_group)
        scroll.setWidget(inner)
        layout.addWidget(scroll, 1)

        # ── 按钮行 ──
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(SPACE_SM)
        self._reset_btn = QPushButton(self.tr("Restore Default"))
        self._reset_btn.setToolTip(self.tr("Reset all custom colors to theme defaults"))
        self._reset_btn.clicked.connect(self._reset_to_preset)
        self._apply_btn = QPushButton(self.tr("Apply"))
        self._apply_btn.setToolTip(self.tr("Preview current settings without closing"))
        self._apply_btn.clicked.connect(self._on_apply)
        self._cancel_btn = QPushButton(self.tr("Cancel"))
        self._cancel_btn.setToolTip(self.tr("Discard changes, revert to state before opening"))
        self._cancel_btn.clicked.connect(self._on_cancel)
        self._commit_btn = QPushButton(self.tr("OK"))
        self._commit_btn.setToolTip(self.tr("Save settings and close"))
        self._commit_btn.clicked.connect(self._on_commit)
        btn_layout.addWidget(self._reset_btn, 1)
        btn_layout.addWidget(self._apply_btn, 1)
        btn_layout.addWidget(self._cancel_btn, 1)
        btn_layout.addWidget(self._commit_btn, 1)
        layout.addLayout(btn_layout)

    # ── 事件处理 ────────────────────────────────────────────────────────────

    def _on_mode_changed(self, btn: QRadioButton) -> None:
        if btn is self._radio_light:
            self._temp_mode = "light"
            self._temp_base = "light"
            self._color_group.setEnabled(False)
            preset = self._theme_mgr.get_preset_tokens("light")
            self._refresh_swatches(preset)
        elif btn is self._radio_dark:
            self._temp_mode = "dark"
            self._temp_base = "dark"
            self._color_group.setEnabled(False)
            preset = self._theme_mgr.get_preset_tokens("dark")
            self._refresh_swatches(preset)
        else:
            self._temp_mode = "custom"
            self._color_group.setEnabled(True)
            current = self._theme_mgr.get_all_tokens()
            self._temp_base = self._orig_base
            self._refresh_swatches(current)

    def _refresh_swatches(self, tokens: dict) -> None:
        is_custom = self._temp_mode == "custom"
        for token, swatch in self._swatch_fields.items():
            swatch.set_color(tokens.get(token, "#000000"))
            swatch.set_enabled(is_custom)

    def _reset_to_preset(self) -> None:
        if self._temp_mode == "custom":
            preset = self._theme_mgr.get_preset_tokens(self._temp_base)
            self._refresh_swatches(preset)

    def _collect_overrides(self) -> dict:
        if self._temp_mode != "custom":
            return {}
        base_preset = self._theme_mgr.get_preset_tokens(self._temp_base)
        overrides = {}
        for k, swatch in self._swatch_fields.items():
            val = swatch.color_hex
            if val.upper() != base_preset.get(k, "").upper():
                overrides[k] = val
        return overrides

    def _apply_current(self) -> None:
        self._theme_mgr.apply(self._temp_mode, self._temp_base, self._collect_overrides())

    def _on_apply(self) -> None:
        self._apply_current()
        self._orig_mode = self._temp_mode
        self._orig_base = self._temp_base
        self._orig_overrides = self._collect_overrides()

    def _on_commit(self) -> None:
        self._apply_current()
        self.accept()

    def _on_cancel(self) -> None:
        self._theme_mgr.apply(self._orig_mode, self._orig_base, self._orig_overrides)
        self.reject()

    # ── 初始化 ──────────────────────────────────────────────────────────────

    def _sync_ui(self) -> None:
        if self._orig_mode == "light":
            self._radio_light.setChecked(True)
        elif self._orig_mode == "dark":
            self._radio_dark.setChecked(True)
        else:
            self._radio_custom.setChecked(True)

        self._color_group.setEnabled(self._orig_mode == "custom")
        current = self._theme_mgr.get_all_tokens()
        self._refresh_swatches(current)

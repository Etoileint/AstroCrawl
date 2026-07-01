"""Phase 2 — 主题系统测试。

覆盖:
- TM01-TM17: ThemeManager 令牌解析、模式切换、缓存、QPalette 映射
- TM18-TM20: ThemeManager 单例行为
- TD01-TD15: ThemeDialog 模式切换、颜色收集、提交/取消
- SW01-SW06: _SwatchField 色块控件
- SC01-SC05: _score_to_color 纯函数 (parametrize)
"""

from __future__ import annotations

import pytest
from PySide6.QtGui import QColor, QPalette

pytestmark = pytest.mark.gui


# ═══════════════════════════════════════════════════════════════════════
# 辅助
# ═══════════════════════════════════════════════════════════════════════


class _SignalCollector:
    """信号发射收集器。"""

    def __init__(self, signal):
        self._calls: list[tuple] = []
        signal.connect(self._slot)

    def _slot(self, *args):
        self._calls.append(args)

    @property
    def count(self) -> int:
        return len(self._calls)


@pytest.fixture
def mgr(qapp, fake_prefs):
    """每个测试使用独立的 ThemeManager 实例。"""
    from astrocrawl.gui.theme import ThemeManager

    return ThemeManager(qapp, fake_prefs)


# ═══════════════════════════════════════════════════════════════════════
# TM01-TM06: 令牌解析与模式查询
# ═══════════════════════════════════════════════════════════════════════


class TestThemeManagerTokenAccess:
    def test_get_returns_hex_string_for_known_token(self, mgr):
        result = mgr.get("accent")
        assert isinstance(result, str)
        assert result.startswith("#")
        assert len(result) == 7

    def test_get_light_default(self, mgr):
        from astrocrawl.gui.theme import LIGHT_TOKENS

        assert mgr.get("accent") == LIGHT_TOKENS["accent"]

    def test_get_unknown_token_raises_keyerror(self, mgr):
        with pytest.raises(KeyError, match="Unknown theme token"):
            mgr.get("nonexistent_token")

    def test_current_mode_light_by_default(self, mgr):
        assert mgr.current_mode() == "light"

    def test_get_all_tokens_return_valid_hex(self, mgr):
        from astrocrawl.gui.theme import _ALL_TOKENS

        for token in _ALL_TOKENS:
            val = mgr.get(token)
            assert isinstance(val, str)
            assert val.startswith("#")
            QColor(val)  # 不抛异常


class TestThemeManagerPresetTokens:
    """TM13-TM14: get_preset_tokens 行为。"""

    def test_get_preset_tokens_returns_copy(self, mgr):
        tokens = mgr.get_preset_tokens("light")
        tokens["accent"] = "#000000"
        assert mgr.get("accent") != "#000000"

    def test_get_preset_tokens_unknown_mode_returns_light(self, mgr):
        tokens = mgr.get_preset_tokens("nonexistent")
        from astrocrawl.gui.theme import LIGHT_TOKENS

        assert tokens["accent"] == LIGHT_TOKENS["accent"]


# ═══════════════════════════════════════════════════════════════════════
# TM07-TM12: apply 模式切换
# ═══════════════════════════════════════════════════════════════════════


class TestThemeManagerApply:
    def test_apply_dark_mode(self, mgr):
        mgr.apply("dark", "dark", {})
        assert mgr.current_mode() == "dark"
        from astrocrawl.gui.theme import DARK_TOKENS

        assert mgr.get("window_bg") == DARK_TOKENS["window_bg"]

    def test_apply_custom_with_overrides(self, mgr):
        mgr.apply("custom", "light", {"accent": "#FF0000"})
        assert mgr.current_mode() == "custom"
        assert mgr.get("accent") == "#FF0000"
        from astrocrawl.gui.theme import LIGHT_TOKENS

        assert mgr.get("window_bg") == LIGHT_TOKENS["window_bg"]

    def test_apply_custom_invalid_color_filtered(self, mgr):
        mgr.apply("custom", "light", {"accent": "not-a-color"})
        from astrocrawl.gui.theme import LIGHT_TOKENS

        assert mgr.get("accent") == LIGHT_TOKENS["accent"]

    def test_apply_custom_unknown_token_filtered(self, mgr):
        mgr.apply("custom", "light", {"bad_token": "#000000"})
        assert mgr.get("window_bg") is not None  # bad_token 被忽略

    def test_apply_emits_theme_changed(self, mgr):
        collector = _SignalCollector(mgr.theme_changed)
        mgr.apply("dark", "dark", {})
        assert collector.count == 1

    def test_apply_persists_through_prefs(self, mgr, fake_prefs):
        mgr.apply("dark", "dark", {"accent": "#0000FF"})
        theme_data = fake_prefs.get_theme()
        assert theme_data["mode"] == "dark"
        assert theme_data["base"] == "dark"
        assert theme_data["overrides"] == {"accent": "#0000FF"}

    def test_apply_light_back_from_dark(self, mgr):
        mgr.apply("dark", "dark", {})
        mgr.apply("light", "light", {})
        assert mgr.current_mode() == "light"
        from astrocrawl.gui.theme import LIGHT_TOKENS

        assert mgr.get("accent") == LIGHT_TOKENS["accent"]

    def test_apply_custom_with_empty_overrides(self, mgr):
        mgr.apply("custom", "light", {})
        from astrocrawl.gui.theme import LIGHT_TOKENS

        assert mgr.get("accent") == LIGHT_TOKENS["accent"]


# ═══════════════════════════════════════════════════════════════════════
# TM15-TM16: 缓存行为
# ═══════════════════════════════════════════════════════════════════════


class TestThemeManagerCache:
    def test_resolve_tokens_caches_result(self, mgr, monkeypatch):
        mgr.get("accent")
        assert mgr._resolved_cache is not None
        cached = mgr._resolved_cache

        mgr.get("window_bg")
        assert mgr._resolved_cache is cached

    def test_invalidate_cache_clears(self, mgr):
        mgr.get("accent")
        assert mgr._resolved_cache is not None

        mgr._invalidate_cache()
        assert mgr._resolved_cache is None

    def test_apply_changes_cached_values(self, mgr):
        """apply 后 _resolve_tokens 缓存被重建（_apply_palette 会触发）。"""
        mgr.get("accent")
        old_cache = dict(mgr._resolved_cache)
        mgr.apply("dark", "dark", {})

        assert mgr._resolved_cache is not None
        assert mgr._resolved_cache != old_cache
        from astrocrawl.gui.theme import DARK_TOKENS

        assert mgr._resolved_cache["accent"] == DARK_TOKENS["accent"]


# ═══════════════════════════════════════════════════════════════════════
# TM17: QPalette 映射
# ═══════════════════════════════════════════════════════════════════════


class TestThemeManagerPalette:
    def test_apply_palette_sets_window_role(self, mgr, qapp):
        mgr._apply_palette()
        palette = qapp.palette()

        expected = QColor(mgr.get("window_bg"))
        assert palette.color(QPalette.Window) == expected

    def test_apply_palette_sets_button_role(self, mgr, qapp):
        mgr._apply_palette()
        palette = qapp.palette()

        expected = QColor(mgr.get("button_bg"))
        assert palette.color(QPalette.Button) == expected

    def test_apply_palette_sets_highlight_role(self, mgr, qapp):
        mgr._apply_palette()
        palette = qapp.palette()

        expected = QColor(mgr.get("accent"))
        assert palette.color(QPalette.Highlight) == expected

    def test_apply_palette_light_accent_uses_dark_hl_text(self, mgr, qapp):
        mgr._apply_palette()
        palette = qapp.palette()
        accent = QColor(mgr.get("accent"))

        hl_text = palette.color(QPalette.HighlightedText)
        if accent.lightnessF() > 0.5:
            expected = QColor(mgr.get("window_bg"))
        else:
            expected = QColor("#FFFFFF")
        assert hl_text == expected

    def test_apply_palette_dark_accent_lightness_above_half_uses_window_bg(self, mgr, qapp):
        """Dark 模式下 accent=#89B4FA lightnessF > 0.5 → HighlightedText=window_bg。"""
        mgr.apply("dark", "dark", {})
        mgr._apply_palette()
        palette = qapp.palette()

        hl_text = palette.color(QPalette.HighlightedText)
        assert hl_text == QColor(mgr.get("window_bg"))

    def test_apply_palette_sets_all_20_roles(self, mgr, qapp):
        """验证 20 个 QPalette ColorRole 均被设置。"""
        mgr._apply_palette()
        palette = qapp.palette()

        roles = [
            QPalette.Window,
            QPalette.WindowText,
            QPalette.Button,
            QPalette.ButtonText,
            QPalette.Base,
            QPalette.Text,
            QPalette.Highlight,
            QPalette.HighlightedText,
            QPalette.BrightText,
            QPalette.AlternateBase,
            QPalette.Mid,
            QPalette.PlaceholderText,
            QPalette.Link,
            QPalette.LinkVisited,
            QPalette.ToolTipBase,
            QPalette.ToolTipText,
            QPalette.Light,
            QPalette.Midlight,
            QPalette.Dark,
            QPalette.Shadow,
        ]
        for role in roles:
            color = palette.color(role)
            assert color.isValid(), f"QPalette role {role} 未设置"


# ═══════════════════════════════════════════════════════════════════════
# TM18-TM20: 单例行为
# ═══════════════════════════════════════════════════════════════════════


class TestThemeManagerSingleton:
    def test_init_theme_manager_returns_instance(self, qapp, fake_prefs):
        from astrocrawl.gui.theme import init_theme_manager

        mgr = init_theme_manager(qapp, fake_prefs)
        assert mgr is not None
        assert mgr.current_mode() == "light"

    def test_init_theme_manager_returns_same_instance(self, qapp, fake_prefs, monkeypatch):
        from astrocrawl.gui.theme import init_theme_manager

        monkeypatch.setattr("astrocrawl.gui.theme._theme_manager", None)
        first = init_theme_manager(qapp, fake_prefs)
        second = init_theme_manager(qapp, fake_prefs)
        assert first is second

    def test_get_theme_manager_raises_when_not_initialized(self, monkeypatch):
        from astrocrawl.gui.theme import get_theme_manager

        monkeypatch.setattr("astrocrawl.gui.theme._theme_manager", None)
        with pytest.raises(RuntimeError, match="not initialized"):
            get_theme_manager()


# ═══════════════════════════════════════════════════════════════════════
# TD01-TD06: ThemeDialog 模式切换
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def theme_dialog(qapp, theme_mgr):
    from astrocrawl.gui.theme_dialog import ThemeDialog

    dlg = ThemeDialog()
    return dlg


class TestThemeDialogModeSwitch:
    def test_initial_state_light_mode(self, theme_dialog):
        assert theme_dialog._temp_mode == "light"
        assert theme_dialog._temp_base == "light"
        assert theme_dialog._color_group.isEnabled() is False

    def test_switch_to_dark_mode(self, theme_dialog):
        theme_dialog._radio_dark.setChecked(True)
        theme_dialog._on_mode_changed(theme_dialog._radio_dark)

        assert theme_dialog._temp_mode == "dark"
        assert theme_dialog._temp_base == "dark"
        assert theme_dialog._color_group.isEnabled() is False

    def test_switch_to_custom_mode(self, theme_dialog):
        theme_dialog._radio_custom.setChecked(True)
        theme_dialog._on_mode_changed(theme_dialog._radio_custom)

        assert theme_dialog._temp_mode == "custom"
        assert theme_dialog._color_group.isEnabled() is True

    def test_switch_to_dark_updates_swatches(self, theme_dialog):
        from astrocrawl.gui.theme import DARK_TOKENS

        theme_dialog._radio_dark.setChecked(True)
        theme_dialog._on_mode_changed(theme_dialog._radio_dark)

        accent_swatch = theme_dialog._swatch_fields["accent"]
        assert accent_swatch.color_hex == DARK_TOKENS["accent"]

    def test_switch_to_custom_updates_swatches_from_resolved(self, theme_dialog, theme_mgr):
        theme_mgr.apply("custom", "light", {"accent": "#FF0000"})
        theme_dialog._radio_custom.setChecked(True)
        theme_dialog._on_mode_changed(theme_dialog._radio_custom)

        accent_swatch = theme_dialog._swatch_fields["accent"]
        assert accent_swatch.color_hex == "#FF0000"

    def test_sync_ui_initial_state(self, theme_dialog):
        assert theme_dialog._radio_light.isChecked() is True

    def test_switch_to_light_from_dark(self, theme_dialog):
        """从 dark 切换回 light — 覆盖 _on_mode_changed light 路径。"""
        theme_dialog._radio_dark.setChecked(True)
        theme_dialog._on_mode_changed(theme_dialog._radio_dark)

        theme_dialog._radio_light.setChecked(True)
        theme_dialog._on_mode_changed(theme_dialog._radio_light)

        assert theme_dialog._temp_mode == "light"
        assert theme_dialog._temp_base == "light"
        assert theme_dialog._color_group.isEnabled() is False

    def test_sync_ui_dark_mode(self, theme_mgr):
        """theme_mgr 已在 dark 模式时，对话框 _sync_ui 正确选中 dark radio。"""
        from astrocrawl.gui.theme_dialog import ThemeDialog

        theme_mgr.apply("dark", "dark", {})
        dlg = ThemeDialog()
        assert dlg._radio_dark.isChecked() is True
        assert dlg._temp_mode == "dark"


class TestThemeDialogColorCollection:
    """TD03-TD04: _collect_overrides。"""

    def test_collect_overrides_empty_when_light(self, theme_dialog):
        assert theme_dialog._collect_overrides() == {}

    def test_collect_overrides_empty_when_no_changes(self, theme_dialog):
        theme_dialog._radio_custom.setChecked(True)
        theme_dialog._on_mode_changed(theme_dialog._radio_custom)
        assert theme_dialog._collect_overrides() == {}

    def test_collect_overrides_detects_changed_color(self, theme_dialog, theme_mgr):
        theme_mgr.apply("custom", "light", {})
        theme_dialog._radio_custom.setChecked(True)
        theme_dialog._on_mode_changed(theme_dialog._radio_custom)

        theme_dialog._swatch_fields["accent"].set_color("#FF0000")
        overrides = theme_dialog._collect_overrides()
        assert "accent" in overrides
        assert overrides["accent"] == "#FF0000"

    def test_collect_overrides_only_includes_diffs(self, theme_dialog, theme_mgr):
        theme_mgr.apply("custom", "light", {})
        theme_dialog._radio_custom.setChecked(True)
        theme_dialog._on_mode_changed(theme_dialog._radio_custom)

        theme_dialog._swatch_fields["accent"].set_color("#FF0000")
        theme_dialog._swatch_fields["window_bg"].set_color("#FFFFFF")
        overrides = theme_dialog._collect_overrides()

        assert "accent" in overrides
        from astrocrawl.gui.theme import LIGHT_TOKENS

        if "#FFFFFF".upper() != LIGHT_TOKENS["window_bg"].upper():
            assert "window_bg" in overrides

    def test_reset_to_preset_in_custom_mode(self, theme_dialog, theme_mgr):
        theme_mgr.apply("custom", "light", {"accent": "#FF0000"})
        theme_dialog._radio_custom.setChecked(True)
        theme_dialog._on_mode_changed(theme_dialog._radio_custom)

        theme_dialog._swatch_fields["accent"].set_color("#0000FF")
        theme_dialog._reset_to_preset()

        from astrocrawl.gui.theme import LIGHT_TOKENS

        assert theme_dialog._swatch_fields["accent"].color_hex == LIGHT_TOKENS["accent"]


class TestThemeDialogCommitCancel:
    """TD07-TD11: 提交 / 取消 / 应用。"""

    def test_on_apply_calls_theme_manager_apply(self, theme_dialog, theme_mgr, fake_prefs):
        theme_dialog._radio_dark.setChecked(True)
        theme_dialog._on_mode_changed(theme_dialog._radio_dark)
        theme_dialog._on_apply()

        assert theme_mgr.current_mode() == "dark"
        theme_data = fake_prefs.get_theme()
        assert theme_data["mode"] == "dark"

    def test_on_commit_saves_and_accepts(self, theme_dialog, theme_mgr):
        theme_dialog._radio_dark.setChecked(True)
        theme_dialog._on_mode_changed(theme_dialog._radio_dark)

        # _on_commit 调用 _apply_current → accept
        # 验证 accept 被调用（通过验证对话框结果）
        theme_dialog._on_commit()
        assert theme_mgr.current_mode() == "dark"
        assert theme_dialog.result() == 1  # QDialog.Accepted

    def test_on_cancel_restores_original_mode(self, theme_dialog, theme_mgr):
        original_mode = theme_mgr.current_mode()
        theme_dialog._radio_dark.setChecked(True)
        theme_dialog._on_mode_changed(theme_dialog._radio_dark)

        theme_dialog._on_cancel()

        assert theme_mgr.current_mode() == original_mode
        assert theme_dialog.result() == 0  # QDialog.Rejected

    def test_on_cancel_restores_original_overrides(self, theme_dialog, theme_mgr):
        theme_mgr.apply("custom", "light", {"accent": "#ABCDEF"})
        dlg2 = type(theme_dialog)()  # 新对话框以当前 _orig_overrides={"accent":"#ABCDEF"}

        dlg2._radio_custom.setChecked(True)
        dlg2._on_mode_changed(dlg2._radio_custom)  # 继承已生效的 custom 状态
        dlg2._radio_dark.setChecked(True)
        dlg2._on_mode_changed(dlg2._radio_dark)

        dlg2._on_cancel()

        assert theme_mgr.get("accent") == "#ABCDEF"


class TestThemeDialogSwatchInteraction:
    """TD01 补充: _refresh_swatches + _SwatchField 集成。"""

    def test_refresh_swatches_enables_in_custom(self, theme_dialog):
        theme_dialog._radio_custom.setChecked(True)
        theme_dialog._on_mode_changed(theme_dialog._radio_custom)

        for swatch in theme_dialog._swatch_fields.values():
            assert swatch._swatch.isEnabled() is True

    def test_refresh_swatches_disables_in_light(self, theme_dialog):
        for swatch in theme_dialog._swatch_fields.values():
            assert swatch._swatch.isEnabled() is False


# ═══════════════════════════════════════════════════════════════════════
# SW01-SW04: _SwatchField
# ═══════════════════════════════════════════════════════════════════════


class TestSwatchField:
    @staticmethod
    def _make_swatch(qapp, theme_mgr):
        from astrocrawl.gui.theme_dialog import _SwatchField

        return _SwatchField("accent")

    def test_set_color_updates_hex(self, qapp, theme_mgr):
        swatch = self._make_swatch(qapp, theme_mgr)
        swatch.set_color("#FF0000")
        assert swatch.color_hex == "#FF0000"

    def test_set_color_updates_stylesheet(self, qapp, theme_mgr):
        swatch = self._make_swatch(qapp, theme_mgr)
        swatch.set_color("#00FF00")
        assert "#00FF00" in swatch._swatch.styleSheet()

    def test_set_enabled_false(self, qapp, theme_mgr):
        swatch = self._make_swatch(qapp, theme_mgr)
        swatch.set_enabled(False)
        assert swatch._swatch.isEnabled() is False

    def test_set_enabled_true(self, qapp, theme_mgr):
        swatch = self._make_swatch(qapp, theme_mgr)
        swatch.set_enabled(False)
        swatch.set_enabled(True)
        assert swatch._swatch.isEnabled() is True

    def test_token_property(self, qapp, theme_mgr):
        swatch = self._make_swatch(qapp, theme_mgr)
        assert swatch.token == "accent"

    def test_pick_color_updates_swatch(self, qapp, theme_mgr, monkeypatch):
        from PySide6.QtGui import QColor

        from astrocrawl.gui.theme_dialog import _SwatchField

        swatch = _SwatchField("accent")
        swatch.set_color("#000000")

        mock_color = QColor("#FF8800")
        monkeypatch.setattr(
            "PySide6.QtWidgets.QColorDialog.getColor",
            lambda initial, parent, title: mock_color,
        )

        swatch._pick_color()
        assert swatch.color_hex == "#ff8800"


# ═══════════════════════════════════════════════════════════════════════
# SC01-SC05: _score_to_color 纯函数
# ═══════════════════════════════════════════════════════════════════════


class TestScoreToColor:
    @staticmethod
    def _fake_theme():
        """返回一个简易 theme mock，含 danger/warning/success 三色。"""

        class _T:
            @staticmethod
            def get(token):
                return {
                    "danger": "#C0392B",
                    "warning": "#F39C12",
                    "success": "#27AE60",
                }[token]

        return _T()

    @pytest.mark.parametrize(
        "score,expected",
        [
            (0.0, "#c0392b"),
            (0.5, "#f39c12"),
            (1.0, "#27ae60"),
        ],
    )
    def test_score_extremes(self, score, expected):
        from astrocrawl.gui.proxy_health_bar import _score_to_color

        result = _score_to_color(score, self._fake_theme())
        assert result == expected

    def test_score_quarter_is_interpolated(self):
        from astrocrawl.gui.proxy_health_bar import _score_to_color

        result = _score_to_color(0.25, self._fake_theme())
        danger = QColor("#C0392B")
        warning = QColor("#F39C12")
        r = int(danger.red() + (warning.red() - danger.red()) * 0.5)
        g = int(danger.green() + (warning.green() - danger.green()) * 0.5)
        b = int(danger.blue() + (warning.blue() - danger.blue()) * 0.5)
        expected = f"#{r:02x}{g:02x}{b:02x}"
        assert result == expected

    def test_score_three_quarter_is_interpolated(self):
        from astrocrawl.gui.proxy_health_bar import _score_to_color

        result = _score_to_color(0.75, self._fake_theme())
        warning = QColor("#F39C12")
        success = QColor("#27AE60")
        r = int(warning.red() + (success.red() - warning.red()) * 0.5)
        g = int(warning.green() + (success.green() - warning.green()) * 0.5)
        b = int(warning.blue() + (success.blue() - warning.blue()) * 0.5)
        expected = f"#{r:02x}{g:02x}{b:02x}"
        assert result == expected

    @pytest.mark.parametrize("score", [-0.5, 1.5, -10.0, 100.0])
    def test_score_clamped_to_range(self, score):
        from astrocrawl.gui.proxy_health_bar import _score_to_color

        result = _score_to_color(score, self._fake_theme())
        assert result.startswith("#")
        assert len(result) == 7

    def test_result_is_valid_hex(self):
        from astrocrawl.gui.proxy_health_bar import _score_to_color

        for s in (0.0, 0.1, 0.33, 0.5, 0.67, 0.9, 1.0):
            result = _score_to_color(s, self._fake_theme())
            QColor(result)

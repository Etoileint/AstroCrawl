"""主题系统 — 15 令牌 + 深浅预设 + QPalette 引擎。

QPalette 自动传播到所有标准控件，非标准控件通过 theme_changed 信号动态适配。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QToolTip

# ── 15 设计令牌：浅色预设（Catppuccin Latte 色板参考）────────────────────
LIGHT_TOKENS = {
    "window_bg": "#F5F5F5",
    "window_text": "#2D2D2D",
    "button_bg": "#E0E0E0",
    "button_text": "#2D2D2D",
    "input_bg": "#F8F8F8",
    "input_bg_alt": "#EEEEEE",
    "input_text": "#2D2D2D",
    "accent": "#2B6CB0",
    "border": "#CCCCCC",
    "disabled": "#999999",
    "success": "#27AE60",
    "warning": "#F39C12",
    "danger": "#C0392B",
    "worker_grad_start": "#6366F1",
    "worker_grad_end": "#A855F7",
}

# ── 15 设计令牌：深色预设（Catppuccin Mocha 色板参考）────────────────────
DARK_TOKENS = {
    "window_bg": "#1E1E2E",
    "window_text": "#CDD6F4",
    "button_bg": "#313244",
    "button_text": "#CDD6F4",
    "input_bg": "#313244",
    "input_bg_alt": "#3B3D52",
    "input_text": "#CDD6F4",
    "accent": "#89B4FA",
    "border": "#45475A",
    "disabled": "#6C7086",
    "success": "#A6E3A1",
    "warning": "#F9E2AF",
    "danger": "#F38BA8",
    "worker_grad_start": "#818CF8",
    "worker_grad_end": "#C084FC",
}

_PRESETS = {"light": LIGHT_TOKENS, "dark": DARK_TOKENS}
_ALL_TOKENS = set(LIGHT_TOKENS.keys())


class ThemeManager(QObject):
    """主题管理器 — QPalette + 令牌缓存 + 自定义覆盖。

    通过 ``init_theme_manager(app, prefs)`` 构造单例，
    其余模块通过 ``get_theme_manager()`` 访问。
    """

    theme_changed = Signal()

    def __init__(self, app: QApplication, prefs) -> None:
        super().__init__()
        self._app = app
        self._prefs = prefs
        theme_data = prefs.get_theme()
        self._mode: str = theme_data["mode"]
        self._base: str = theme_data.get("base", "light")
        self._overrides: dict = theme_data.get("overrides", {})
        self._resolved_cache: Optional[dict[str, str]] = None
        self._apply_palette()

    # ── public API ────────────────────────────────────────────────────────

    def current_mode(self) -> str:
        """返回当前模式：``"light"`` | ``"dark"`` | ``"custom"``。"""
        return self._mode

    def get(self, token: str) -> str:
        """返回解析后的 hex 颜色字符串。未知 token 抛出 KeyError。"""
        if token not in _ALL_TOKENS:
            raise KeyError(f"Unknown theme token: {token}")
        return self._resolve_tokens()[token]

    def get_preset_tokens(self, mode: str) -> dict:
        """返回指定模式的原始预设值（不受 overrides 影响）。"""
        return dict(_PRESETS.get(mode, LIGHT_TOKENS))

    def apply(self, mode: str, base: str, overrides: dict) -> None:
        """应用主题：持久化 + 清缓存 + 重建 QPalette + 发射信号。"""
        self._mode = mode
        self._base = base
        self._overrides = {k: v for k, v in overrides.items() if k in _ALL_TOKENS and isinstance(v, str)}
        self._prefs.set_theme(mode, base, self._overrides)
        self._invalidate_cache()
        self._apply_palette()
        self.theme_changed.emit()

    def get_config(self) -> dict:
        """返回当前主题配置 ``{mode, base, overrides}``，供快照/恢复。"""
        return {"mode": self._mode, "base": self._base, "overrides": dict(self._overrides)}

    def get_all_tokens(self) -> dict:
        """返回当前全部 15 个解析后的令牌颜色 ``{token: hex}``。"""
        return dict(self._resolve_tokens())

    # ── internal ───────────────────────────────────────────────────────────

    def _resolve_tokens(self) -> dict[str, str]:
        """解析当前有效令牌，结果缓存。"""
        if self._resolved_cache is not None:
            return self._resolved_cache
        if self._mode != "custom":
            result = dict(_PRESETS.get(self._mode, LIGHT_TOKENS))
        else:
            base_preset = _PRESETS.get(self._base, LIGHT_TOKENS)
            result = dict(base_preset)
            for key, val in self._overrides.items():
                color = QColor(val)
                if color.isValid():
                    result[key] = val
        self._resolved_cache = result
        return result

    def _invalidate_cache(self) -> None:
        self._resolved_cache = None

    def _apply_palette(self) -> None:
        t = self._resolve_tokens()
        p = QPalette()
        p.setColor(QPalette.Window, QColor(t["window_bg"]))
        p.setColor(QPalette.WindowText, QColor(t["window_text"]))
        p.setColor(QPalette.Button, QColor(t["button_bg"]))
        p.setColor(QPalette.ButtonText, QColor(t["button_text"]))
        p.setColor(QPalette.Base, QColor(t["input_bg"]))
        p.setColor(QPalette.Text, QColor(t["input_text"]))
        p.setColor(QPalette.Highlight, QColor(t["accent"]))
        accent_color = QColor(t["accent"])
        hl_text = t["window_bg"] if accent_color.lightnessF() > 0.5 else "#FFFFFF"
        p.setColor(QPalette.HighlightedText, QColor(hl_text))
        p.setColor(QPalette.BrightText, QColor(hl_text))
        p.setColor(QPalette.AlternateBase, QColor(t["input_bg_alt"]))
        p.setColor(QPalette.Mid, QColor(t["border"]))
        p.setColor(QPalette.PlaceholderText, QColor(t["disabled"]))
        p.setColor(QPalette.Link, QColor(t["accent"]))
        p.setColor(QPalette.LinkVisited, QColor(t["accent"]).darker(120))
        p.setColor(QPalette.ToolTipBase, QColor(t["input_bg"]))
        p.setColor(QPalette.ToolTipText, QColor(t["window_text"]))
        p.setColor(QPalette.Light, QColor(t["border"]).lighter(150))
        p.setColor(QPalette.Midlight, QColor(t["border"]).lighter(120))
        p.setColor(QPalette.Dark, QColor(t["border"]).darker(150))
        p.setColor(QPalette.Shadow, QColor(t["border"]).darker(200))
        self._app.setPalette(p)
        QToolTip.setPalette(p)


# ── module-level singleton ──────────────────────────────────────────────────
_theme_manager: Optional[ThemeManager] = None


def init_theme_manager(app: QApplication, prefs) -> ThemeManager:
    """构造并缓存全局 ThemeManager 单例。由 ``main.py`` 调用。"""
    global _theme_manager
    if _theme_manager is None:
        _theme_manager = ThemeManager(app, prefs)
    return _theme_manager


def get_theme_manager() -> ThemeManager:
    """返回全局 ThemeManager 单例。未初始化时抛出 RuntimeError。"""
    if _theme_manager is None:
        raise RuntimeError("ThemeManager not initialized — call init_theme_manager() first")
    return _theme_manager

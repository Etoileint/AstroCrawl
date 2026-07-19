"""CLI i18n — 复用 GUI .ts 翻译字典作为 SSOT。

英文源文本 → 当前语言（en 原样，zh_CN 翻译）。
与 GUI self.tr() 共享同一翻译源（.ts 文件）。
"""

from __future__ import annotations

from pathlib import Path

from astrobase import LogfmtLogger

_LOG = LogfmtLogger("astrocrawl.cli.i18n")

_TRANSLATIONS: dict[str, str] = {}
_LOADED = False


def _load() -> None:
    """从 .ts 文件加载全部翻译到内存字典（首次调用时解析）。"""
    global _LOADED, _TRANSLATIONS
    if _LOADED:
        return
    _LOADED = True

    ts_path = Path(__file__).resolve().parent.parent / "gui" / "translations" / "astrocrawl_gui_zh_CN.ts"
    if not ts_path.exists():
        _LOG.debug("gui_ts_missing", path=str(ts_path))
        return

    import xml.etree.ElementTree as ET

    try:
        tree = ET.parse(str(ts_path))
        for msg in tree.findall(".//message"):
            src = msg.find("source")
            trs = msg.find("translation")
            if src is not None and trs is not None and src.text and trs.text:
                _TRANSLATIONS.setdefault(src.text, trs.text)
    except Exception:
        _LOG.warning("gui_ts_parse_failed", exc_info=True)


def tr(text: str) -> str:
    """英文源文本 → 当前语言输出。"""
    _load()
    from astrocrawl.utils.preferences import get_preferences

    lang = get_preferences().get_language()
    if lang == "zh_CN":
        return _TRANSLATIONS.get(text, text)
    return text


def clear_cache() -> None:
    """清除翻译缓存（测试 teardown 用）。"""
    global _LOADED, _TRANSLATIONS
    _TRANSLATIONS = {}
    _LOADED = False

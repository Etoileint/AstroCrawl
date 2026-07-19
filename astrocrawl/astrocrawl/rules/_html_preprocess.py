"""HTML 预处理 — 三级清洗，移除对 CSS 选择器生成无用的非渲染元素。

Tier 0: off — 原样返回
Tier 1: canonical (默认) — 移除 script/style/noscript/注释/svg/math/head 元数据
Tier 2: strict — canonical + nav/footer/aside/header
"""

from __future__ import annotations

from enum import IntEnum
from typing import cast

from astrobasis import LogfmtLogger

logger = LogfmtLogger("astrocrawl.rules.html_preprocess")

_MAX_INPUT_BYTES = 5 * 1024 * 1024  # 5MB

# lxml 的 HTML parser 对大多数现实世界的 HTML 都能容错解析
# 这些标签的内容永远不参与 CSS 渲染，移除不影响选择器可达性
_TIER1_REMOVE = {
    "script",
    "style",
    "noscript",
    "svg",
    "math",
    "meta",
    "link",
    "title",
}
_TIER2_EXTRA = {"nav", "footer", "aside", "header"}


class PreprocessTier(IntEnum):
    OFF = 0
    CANONICAL = 1
    STRICT = 2


def preprocess_html(html: str, tier: PreprocessTier = PreprocessTier.CANONICAL) -> str:
    if tier == PreprocessTier.OFF:
        return html
    if not html or not html.strip():
        return html

    input_bytes = len(html.encode("utf-8", errors="replace"))
    if input_bytes > _MAX_INPUT_BYTES:
        logger.warning("html_preprocess_too_large", bytes=input_bytes, limit=_MAX_INPUT_BYTES)
        return html

    try:
        from lxml import etree
        from lxml import html as lxml_html
    except ImportError:
        logger.warning("html_preprocess_lxml_unavailable")
        return html

    try:
        doc = lxml_html.fromstring(html)
    except etree.ParserError:
        logger.warning("html_preprocess_parse_error")
        return html

    _remove_tags(doc, _TIER1_REMOVE)
    if tier >= PreprocessTier.STRICT:
        _remove_tags(doc, _TIER2_EXTRA)

    _remove_comments(doc)

    body = doc.find("body")
    if body is None:
        body = doc

    try:
        result = etree.tostring(body, encoding="unicode", method="html")
    except Exception:
        logger.warning("html_preprocess_serialize_error")
        return html

    if not result or not result.strip():
        return ""

    return cast("str", result)


def _remove_tags(doc, tag_names: set) -> None:
    parent_map = {c: p for p in doc.iter() for c in p}
    for el in list(doc.iter()):
        tag = el.tag if isinstance(el.tag, str) else ""
        if tag.lower() in tag_names:
            parent = parent_map.get(el)
            if parent is not None:
                parent.remove(el)


def _remove_comments(doc) -> None:
    from lxml import etree

    parent_map = {c: p for p in doc.iter() for c in p}
    for el in list(doc.iter()):
        if isinstance(el, etree._Comment):
            parent = parent_map.get(el)
            if parent is not None:
                parent.remove(el)

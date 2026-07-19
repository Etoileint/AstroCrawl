"""Transform 流水线 — 选择器提取后处理。

五种操作：strip / strip_currency / regex (re2) / replace / join。
所有 regex 通过 re2 执行（线性时间，ReDoS 免疫）。
"""

from __future__ import annotations

from typing import Any, Dict

import re2

from astrobase import LogfmtLogger
from astrocrawl._constants import CURRENCY_SYMBOLS, TRANSFORM_MEMORY_MULTIPLIER

logger = LogfmtLogger("astrocrawl.rules.transform")


def apply_transforms(
    value: Any,
    transforms: Dict[str, Any],
    max_text_length: int = 500000,
    extra_currency: frozenset = frozenset(),
) -> Any:
    """对提取值依次应用 transform 列表。"""
    if not transforms or value is None:
        return value

    result = value

    if transforms.get("strip"):
        result = _strip(result)

    if transforms.get("strip_currency"):
        result = _strip_currency(result, extra_currency)

    if "regex" in transforms and isinstance(result, str):
        result = _regex_transform(result, transforms["regex"])

    if "replace" in transforms and isinstance(result, str):
        result = _replace_transform(
            result,
            transforms["replace"],
            max_text_length,
        )

    if "join" in transforms:
        result = _join_transform(result, transforms["join"], max_text_length)

    return result


def _strip(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return [v.strip() if isinstance(v, str) else v for v in value]
    return value


def _strip_currency(value: Any, extra_currency: frozenset = frozenset()) -> Any:
    symbols = CURRENCY_SYMBOLS | extra_currency

    def _clean(text: str) -> str:
        for sym in symbols:
            text = text.replace(sym, "")
        return text.strip()

    if isinstance(value, str):
        return _clean(value)
    if isinstance(value, list):
        return [_clean(v) if isinstance(v, str) else v for v in value]
    return value


def _regex_transform(value: str, pattern: str):
    try:
        m = re2.search(pattern, value)
    except re2.error as exc:
        logger.warning("transform_regex_error", pattern=pattern, error=exc)
        return value
    if m:
        groups = m.groups()
        return groups[0] if groups else m.group(0)
    return None


def _replace_transform(value: str, config: Dict[str, str], max_text_length: int) -> str:
    """S27+N104：替换 + 内存放大防护——两道独立门。"""
    fr = config.get("from", "")
    to = config.get("to", "")
    if not fr:
        return value
    result = value.replace(fr, to)
    result_bytes = len(result.encode("utf-8", errors="ignore"))
    input_bytes = len(value.encode("utf-8", errors="ignore"))

    # S27: 绝对值天花板
    if result_bytes > max_text_length:
        logger.warning("transform_replace_oversize", from_str=fr, result_bytes=result_bytes, max=max_text_length)
        return value

    # N104: 比例天花板（防放大攻击）
    if result_bytes > input_bytes * TRANSFORM_MEMORY_MULTIPLIER:
        logger.warning("transform_replace_amplified", from_str=fr, input=input_bytes, result=result_bytes)
        return value

    return result


def _join_transform(value: Any, separator: str, max_text_length: int = 500000) -> Any:
    """M9: 拼接结果超限截断 + WARNING。按字节截断，UTF-8 边界感知。"""
    if isinstance(value, list):
        joined = separator.join(str(v) for v in value)
        raw = joined.encode("utf-8", errors="ignore")
        if len(raw) > max_text_length:
            logger.warning("transform_join_truncated", length=len(joined), max=max_text_length)
            return raw[:max_text_length].decode("utf-8", errors="ignore")
        return joined
    return value

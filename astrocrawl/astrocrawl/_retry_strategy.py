"""Fetch error → RetryStrategy classification — transport-agnostic domain logic.

Shared by BrowserPool (Playwright) and aiohttp_retry_fetch (robots/sitemap).
Follows Heritrix RetryPolicy pattern: strategy is independent of transport layer,
consumed by all adapters below it.
"""

from __future__ import annotations

from enum import Enum

from astrocrawl._types import FetchErrorCategory


class RetryStrategy(Enum):
    ROTATE_PROXY = "rotate"
    REPLACE_CONTEXT = "replace"
    TRANSIENT = "transient"
    FATAL = "fatal"


FATAL_HTTP_STATUS = frozenset({404, 410, 403, 451})
_TRANSIENT_HTTP_STATUS = frozenset({429, 502, 503})

_CATEGORY_TO_STRATEGY = {
    FetchErrorCategory.DNS: RetryStrategy.FATAL,
    FetchErrorCategory.SSL: RetryStrategy.FATAL,
    FetchErrorCategory.CONNECTION_REFUSED: RetryStrategy.FATAL,
    FetchErrorCategory.DOWNLOAD: RetryStrategy.FATAL,
    FetchErrorCategory.TOO_MANY_REDIRECTS: RetryStrategy.FATAL,
    FetchErrorCategory.HTTP_4XX: RetryStrategy.FATAL,
    FetchErrorCategory.PROXY: RetryStrategy.ROTATE_PROXY,
    FetchErrorCategory.TARGET_CLOSED: RetryStrategy.REPLACE_CONTEXT,
    FetchErrorCategory.CONNECTION_RESET: RetryStrategy.TRANSIENT,
    FetchErrorCategory.ABORTED: RetryStrategy.TRANSIENT,
    FetchErrorCategory.HTTP_5XX: RetryStrategy.TRANSIENT,
    FetchErrorCategory.GENERIC: RetryStrategy.TRANSIENT,
}


def classify_http(status: int) -> RetryStrategy:
    if status in FATAL_HTTP_STATUS:
        return RetryStrategy.FATAL
    if status in _TRANSIENT_HTTP_STATUS:
        return RetryStrategy.TRANSIENT
    if 400 <= status < 600:
        return RetryStrategy.TRANSIENT
    return RetryStrategy.TRANSIENT


def classify_from_category(category: FetchErrorCategory, has_proxy: bool = False) -> RetryStrategy:
    """Accept pre-translated FetchErrorCategory → RetryStrategy.

    Used by aiohttp_retry_fetch where classification is done separately
    from strategy mapping.
    """
    if category == FetchErrorCategory.TIMEOUT:
        return RetryStrategy.ROTATE_PROXY if has_proxy else RetryStrategy.TRANSIENT
    if has_proxy and category in (
        FetchErrorCategory.CONNECTION_REFUSED,
        FetchErrorCategory.DNS,
    ):
        return RetryStrategy.ROTATE_PROXY
    return _CATEGORY_TO_STRATEGY.get(category, RetryStrategy.TRANSIENT)

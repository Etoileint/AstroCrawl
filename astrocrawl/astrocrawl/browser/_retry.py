"""ProxyFailureClassifier — Playwright error string → RetryStrategy.

Error classification (string → category) is handled by _types.py's classify_fetch_error() (SSOT).
Retry strategy definitions and transport-agnostic mapping live in _retry_strategy.py (kernel).
This module provides only the Playwright-specific classify() entry point.
"""

from __future__ import annotations

from astrocrawl._retry_strategy import _CATEGORY_TO_STRATEGY, RetryStrategy, classify_http
from astrocrawl._types import FetchErrorCategory, classify_fetch_error


class ProxyFailureClassifier:
    """Stateless classifier: Playwright error string + context → RetryStrategy.

    Delegates HTTP status path to classify_http() (kernel) and
    category-based path to _CATEGORY_TO_STRATEGY (kernel).
    """

    @staticmethod
    def classify(
        error_str: str,
        has_proxy: bool = False,
        http_status: int = 0,
    ) -> RetryStrategy:
        if http_status:
            return classify_http(http_status)

        category = classify_fetch_error(error_str)

        if category == FetchErrorCategory.TIMEOUT:
            return RetryStrategy.ROTATE_PROXY if has_proxy else RetryStrategy.REPLACE_CONTEXT

        if has_proxy and category in (
            FetchErrorCategory.CONNECTION_REFUSED,
            FetchErrorCategory.DNS,
        ):
            return RetryStrategy.ROTATE_PROXY

        return _CATEGORY_TO_STRATEGY.get(category, RetryStrategy.TRANSIENT)

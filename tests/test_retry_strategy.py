"""_retry_strategy 单元测试 — RetryStrategy / classify_http / classify_from_category 边界值覆盖."""

from __future__ import annotations

import pytest

from astrocrawl._retry_strategy import (
    _CATEGORY_TO_STRATEGY,
    _TRANSIENT_HTTP_STATUS,
    FATAL_HTTP_STATUS,
    RetryStrategy,
    classify_from_category,
    classify_http,
)
from astrocrawl._types import FetchErrorCategory

# ═══════════════════════════════════════════════════════════════════════
# RetryStrategy 枚举
# ═══════════════════════════════════════════════════════════════════════


class TestRetryStrategy:
    def test_four_values(self):
        assert len(RetryStrategy) == 4

    def test_values_distinct(self):
        vals = [s.value for s in RetryStrategy]
        assert len(vals) == len(set(vals))

    @pytest.mark.parametrize(
        "name,value",
        [("ROTATE_PROXY", "rotate"), ("REPLACE_CONTEXT", "replace"), ("TRANSIENT", "transient"), ("FATAL", "fatal")],
    )
    def test_value_names(self, name, value):
        assert RetryStrategy[name].value == value


# ═══════════════════════════════════════════════════════════════════════
# FATAL_HTTP_STATUS / _TRANSIENT_HTTP_STATUS — SSOT frozenset
# ═══════════════════════════════════════════════════════════════════════


class TestFatalHttpStatus:
    def test_contains_expected_codes(self):
        assert 404 in FATAL_HTTP_STATUS
        assert 410 in FATAL_HTTP_STATUS
        assert 403 in FATAL_HTTP_STATUS
        assert 451 in FATAL_HTTP_STATUS

    def test_exact_size(self):
        assert len(FATAL_HTTP_STATUS) == 4

    def test_is_frozenset(self):
        assert isinstance(FATAL_HTTP_STATUS, frozenset)


class TestTransientHttpStatus:
    def test_contains_expected_codes(self):
        assert 429 in _TRANSIENT_HTTP_STATUS
        assert 502 in _TRANSIENT_HTTP_STATUS
        assert 503 in _TRANSIENT_HTTP_STATUS

    def test_exact_size(self):
        assert len(_TRANSIENT_HTTP_STATUS) == 3


# ═══════════════════════════════════════════════════════════════════════
# _CATEGORY_TO_STRATEGY — SSOT 映射表
# ═══════════════════════════════════════════════════════════════════════


CATEGORY_MAPPING = [
    (FetchErrorCategory.DNS, RetryStrategy.FATAL),
    (FetchErrorCategory.SSL, RetryStrategy.FATAL),
    (FetchErrorCategory.CONNECTION_REFUSED, RetryStrategy.FATAL),
    (FetchErrorCategory.DOWNLOAD, RetryStrategy.FATAL),
    (FetchErrorCategory.TOO_MANY_REDIRECTS, RetryStrategy.FATAL),
    (FetchErrorCategory.HTTP_4XX, RetryStrategy.FATAL),
    (FetchErrorCategory.PROXY, RetryStrategy.ROTATE_PROXY),
    (FetchErrorCategory.TARGET_CLOSED, RetryStrategy.REPLACE_CONTEXT),
    (FetchErrorCategory.CONNECTION_RESET, RetryStrategy.TRANSIENT),
    (FetchErrorCategory.ABORTED, RetryStrategy.TRANSIENT),
    (FetchErrorCategory.HTTP_5XX, RetryStrategy.TRANSIENT),
    (FetchErrorCategory.GENERIC, RetryStrategy.TRANSIENT),
]


class TestCategoryToStrategy:
    def test_exact_entry_count(self):
        assert len(_CATEGORY_TO_STRATEGY) == 12

    @pytest.mark.parametrize("category,expected", CATEGORY_MAPPING)
    def test_category_maps_to_expected_strategy(self, category, expected):
        assert _CATEGORY_TO_STRATEGY[category] == expected

    def test_no_duplicate_categories(self):
        cats = list(_CATEGORY_TO_STRATEGY.keys())
        assert len(cats) == len(set(cats))


# ═══════════════════════════════════════════════════════════════════════
# classify_http — HTTP 状态码 → RetryStrategy
# ═══════════════════════════════════════════════════════════════════════


class TestClassifyHttp:
    @pytest.mark.parametrize("status", FATAL_HTTP_STATUS)
    def test_fatal_codes_return_fatal(self, status):
        assert classify_http(status) == RetryStrategy.FATAL

    @pytest.mark.parametrize("status", _TRANSIENT_HTTP_STATUS)
    def test_transient_codes_return_transient(self, status):
        assert classify_http(status) == RetryStrategy.TRANSIENT

    # 4xx range (non-FATAL, non-TRANSIENT) → TRANSIENT
    @pytest.mark.parametrize("status", [400, 401, 402, 405, 409, 413, 418, 422, 499])
    def test_other_4xx_returns_transient(self, status):
        assert classify_http(status) == RetryStrategy.TRANSIENT

    # 5xx range (non-TRANSIENT specific) → TRANSIENT
    @pytest.mark.parametrize("status", [500, 501, 504, 511, 598])
    def test_other_5xx_returns_transient(self, status):
        assert classify_http(status) == RetryStrategy.TRANSIENT

    # Range boundary: below 400
    @pytest.mark.parametrize("status", [100, 200, 301, 302, 304, 399])
    def test_below_400_returns_transient(self, status):
        """非错误状态码走 TRANSIENT——aiohttp 重试环只在非 200 时进入此路径。"""
        assert classify_http(status) == RetryStrategy.TRANSIENT

    # Range boundary: above 599
    def test_above_599_returns_transient(self):
        assert classify_http(600) == RetryStrategy.TRANSIENT

    # Boundary: 399 (non-error), 400 (error start)
    def test_boundary_399_400(self):
        assert classify_http(399) == RetryStrategy.TRANSIENT
        assert classify_http(400) == RetryStrategy.TRANSIENT


# ═══════════════════════════════════════════════════════════════════════
# classify_from_category — 错误类别 → RetryStrategy
# ═══════════════════════════════════════════════════════════════════════


class TestClassifyFromCategory:
    # ── TIMEOUT — 上下文感知 ──

    def test_timeout_with_proxy_returns_rotate(self):
        assert classify_from_category(FetchErrorCategory.TIMEOUT, has_proxy=True) == RetryStrategy.ROTATE_PROXY

    def test_timeout_without_proxy_returns_transient(self):
        """aiohttp 路径：无浏览器上下文可替换，回退 TRANSIENT。"""
        assert classify_from_category(FetchErrorCategory.TIMEOUT, has_proxy=False) == RetryStrategy.TRANSIENT

    # ── 代理路径上 DNS/CONNECTION_REFUSED → ROTATE_PROXY ──

    def test_dns_with_proxy_returns_rotate(self):
        assert classify_from_category(FetchErrorCategory.DNS, has_proxy=True) == RetryStrategy.ROTATE_PROXY

    def test_dns_without_proxy_returns_fatal(self):
        """无代理时走 _CATEGORY_TO_STRATEGY 默认映射。"""
        assert classify_from_category(FetchErrorCategory.DNS, has_proxy=False) == RetryStrategy.FATAL

    def test_connection_refused_with_proxy_returns_rotate(self):
        assert (
            classify_from_category(FetchErrorCategory.CONNECTION_REFUSED, has_proxy=True) == RetryStrategy.ROTATE_PROXY
        )

    def test_connection_refused_without_proxy_returns_fatal(self):
        assert classify_from_category(FetchErrorCategory.CONNECTION_REFUSED, has_proxy=False) == RetryStrategy.FATAL

    # ── 静态映射表中的类别（_CATEGORY_TO_STRATEGY 回退路径）──

    @pytest.mark.parametrize(
        "category,expected",
        [
            (FetchErrorCategory.SSL, RetryStrategy.FATAL),
            (FetchErrorCategory.DOWNLOAD, RetryStrategy.FATAL),
            (FetchErrorCategory.TOO_MANY_REDIRECTS, RetryStrategy.FATAL),
            (FetchErrorCategory.HTTP_4XX, RetryStrategy.FATAL),
            (FetchErrorCategory.PROXY, RetryStrategy.ROTATE_PROXY),
            (FetchErrorCategory.TARGET_CLOSED, RetryStrategy.REPLACE_CONTEXT),
            (FetchErrorCategory.CONNECTION_RESET, RetryStrategy.TRANSIENT),
            (FetchErrorCategory.ABORTED, RetryStrategy.TRANSIENT),
            (FetchErrorCategory.HTTP_5XX, RetryStrategy.TRANSIENT),
            (FetchErrorCategory.GENERIC, RetryStrategy.TRANSIENT),
        ],
    )
    def test_static_category_mapping(self, category, expected):
        assert classify_from_category(category, has_proxy=False) == expected

    # ── 未知类别 → TRANSIENT（安全默认）──

    def test_unknown_category_returns_transient(self):
        """不在 _CATEGORY_TO_STRATEGY 中的类别回退 TRANSIENT。"""
        fake = object()
        assert classify_from_category(fake, has_proxy=False) == RetryStrategy.TRANSIENT  # type: ignore[arg-type]

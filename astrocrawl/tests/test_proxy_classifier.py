"""代理故障分类器测试 — ProxyFailureClassifier.classify()"""

from __future__ import annotations

import pytest

from astrocrawl._retry_strategy import FATAL_HTTP_STATUS, RetryStrategy
from astrocrawl.browser._retry import ProxyFailureClassifier

# ── 静态类别 → 策略映射（_CATEGORY_TO_STRATEGY） ──────────────────

STATIC_MAPPING = [
    # (error_str, expected_strategy)
    ("net::ERR_NAME_NOT_RESOLVED", RetryStrategy.FATAL),
    ("net::ERR_SSL_PROTOCOL_ERROR", RetryStrategy.FATAL),
    ("net::ERR_CONNECTION_REFUSED", RetryStrategy.FATAL),
    ("Download is starting", RetryStrategy.FATAL),
    ("net::ERR_TOO_MANY_REDIRECTS", RetryStrategy.FATAL),
    ("net::HTTP_403", RetryStrategy.FATAL),
    ("net::ERR_TUNNEL_CONNECTION_FAILED", RetryStrategy.ROTATE_PROXY),
    ("Target closed", RetryStrategy.REPLACE_CONTEXT),
    ("net::ERR_CONNECTION_RESET", RetryStrategy.TRANSIENT),
    ("net::ERR_ABORTED", RetryStrategy.TRANSIENT),
    ("net::HTTP_500", RetryStrategy.TRANSIENT),
]


@pytest.mark.parametrize(("error_str", "expected"), STATIC_MAPPING)
def test_static_category_to_strategy(error_str, expected):
    assert ProxyFailureClassifier.classify(error_str) == expected


# ── TIMEOUT 上下文感知 ────────────────────────────────────────────


def test_timeout_with_proxy_rotates():
    assert (
        ProxyFailureClassifier.classify(
            "net::ERR_TIMED_OUT",
            has_proxy=True,
        )
        == RetryStrategy.ROTATE_PROXY
    )


def test_timeout_without_proxy_replaces_context():
    assert (
        ProxyFailureClassifier.classify(
            "net::ERR_TIMED_OUT",
            has_proxy=False,
        )
        == RetryStrategy.REPLACE_CONTEXT
    )


def test_timeout_default_no_proxy():
    assert (
        ProxyFailureClassifier.classify(
            "net::ERR_TIMED_OUT",
        )
        == RetryStrategy.REPLACE_CONTEXT
    )


# ── HTTP 状态码优先（绕过错误字符串分类） ─────────────────────────


def test_http_status_takes_priority():
    """http_status 非零时走 _classify_http，忽略 error_str"""
    assert (
        ProxyFailureClassifier.classify(
            "net::ERR_NAME_NOT_RESOLVED",
            http_status=404,
        )
        == RetryStrategy.FATAL
    )


@pytest.mark.parametrize("status", sorted(FATAL_HTTP_STATUS))
def test_fatal_http_status(status):
    assert (
        ProxyFailureClassifier.classify(
            "any error",
            http_status=status,
        )
        == RetryStrategy.FATAL
    )


@pytest.mark.parametrize("status", [429, 502, 503])
def test_transient_http_status(status):
    assert (
        ProxyFailureClassifier.classify(
            "any error",
            http_status=status,
        )
        == RetryStrategy.TRANSIENT
    )


def test_other_4xx_transient():
    assert (
        ProxyFailureClassifier.classify(
            "any error",
            http_status=418,
        )
        == RetryStrategy.TRANSIENT
    )


def test_other_5xx_transient():
    assert (
        ProxyFailureClassifier.classify(
            "any error",
            http_status=511,
        )
        == RetryStrategy.TRANSIENT
    )


# ── 边界情况 ─────────────────────────────────────────────────────


def test_unknown_error_fallsback_to_transient():
    assert (
        ProxyFailureClassifier.classify(
            "completely unknown error string",
        )
        == RetryStrategy.TRANSIENT
    )


def test_empty_error_string_transient():
    assert ProxyFailureClassifier.classify("") == RetryStrategy.TRANSIENT


# ── CONNECTION_REFUSED / DNS 上下文感知 ────────────────────────────


def test_connection_refused_with_proxy_rotates():
    """代理路径上连接被拒 → 代理不通，应轮换"""
    assert (
        ProxyFailureClassifier.classify(
            "net::ERR_CONNECTION_REFUSED",
            has_proxy=True,
        )
        == RetryStrategy.ROTATE_PROXY
    )


def test_connection_refused_without_proxy_fatal():
    """直连路径上连接被拒 → URL 不可达，终止"""
    assert (
        ProxyFailureClassifier.classify(
            "net::ERR_CONNECTION_REFUSED",
            has_proxy=False,
        )
        == RetryStrategy.FATAL
    )


def test_dns_failure_with_proxy_rotates():
    """代理主机名解析失败 → 代理不可用，应轮换"""
    assert (
        ProxyFailureClassifier.classify(
            "net::ERR_NAME_NOT_RESOLVED",
            has_proxy=True,
        )
        == RetryStrategy.ROTATE_PROXY
    )


def test_dns_failure_without_proxy_fatal():
    """目标 URL 主机名解析失败 → URL 不可达，终止"""
    assert (
        ProxyFailureClassifier.classify(
            "net::ERR_NAME_NOT_RESOLVED",
            has_proxy=False,
        )
        == RetryStrategy.FATAL
    )


def test_proxy_error_still_rotates_without_proxy_flag():
    """PROXY 类别错误（隧道连接失败等）无论 has_proxy 都轮换"""
    assert (
        ProxyFailureClassifier.classify(
            "net::ERR_TUNNEL_CONNECTION_FAILED",
            has_proxy=False,
        )
        == RetryStrategy.ROTATE_PROXY
    )


# ── 429 双路径一致性文档化（设计态疣，运行时不触发） ──────────


def test_429_without_http_status_returns_fatal():
    """classify_fetch_error("net::HTTP_429") → HTTP_4XX → FATAL。

    此路径在运行时不触发（429 始终带 http_status 参数进入 classify()），
    但测试作为回归守卫：若将来有人新增代码路径传入 net::HTTP_429 字符串
    而不带 http_status，此测试会捕获该不一致性。
    """
    assert ProxyFailureClassifier.classify("net::HTTP_429") == RetryStrategy.FATAL


def test_429_with_http_status_returns_transient():
    """http_status=429 总是经 _classify_http(429) → TRANSIENT。"""
    assert ProxyFailureClassifier.classify("net::HTTP_429", http_status=429) == RetryStrategy.TRANSIENT


def test_429_with_proxy_and_http_status_still_transient():
    """http_status 路径优先于 has_proxy 上下文——429 永为 TRANSIENT。"""
    assert (
        ProxyFailureClassifier.classify(
            "net::HTTP_429",
            has_proxy=True,
            http_status=429,
        )
        == RetryStrategy.TRANSIENT
    )


# ── 未映射类别默认回退到 TRANSIENT ────────────────────────────


def test_proxy_exhausted_defaults_to_transient():
    """PROXY_EXHAUSTED 不在 _CATEGORY_TO_STRATEGY 中——由 BrowserPool
    Phase 2 回退层处理，此处默认 TRANSIENT。"""
    assert ProxyFailureClassifier.classify("代理轮换失败——无可用替代代理") == RetryStrategy.TRANSIENT


def test_context_failure_defaults_to_transient():
    """CONTEXT_FAILURE 不在 _CATEGORY_TO_STRATEGY 中——由 BrowserPool
    _do_fetch is_infra 裁决层处理，此处默认 TRANSIENT。"""
    assert ProxyFailureClassifier.classify("上下文恢复失败，槽位已失效") == RetryStrategy.TRANSIENT


# ── _classify_http 防御分支 ────────────────────────────────────


def test_classify_http_non_error_status_returns_transient():
    """_classify_http 对非错误状态码（1xx/2xx/3xx）的防御分支。

    此行在生产环境中不可达（http_status 仅在 resp.ok==False 时传入，
    此时 status∈[400,599]），但作为防御：若将来新代码路径传入
    http_status=200，应安全返回 TRANSIENT 而非崩溃。
    """
    assert ProxyFailureClassifier.classify("any", http_status=200) == RetryStrategy.TRANSIENT

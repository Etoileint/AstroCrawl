"""Aiohttp 重试获取引擎 — 与 BrowserPool._retry_loop + _do_fetch 策略等价。

URL 爬取（Playwright）与 robots/sitemap 获取（aiohttp）的底层重试逻辑完全一致，
唯一的差异是传输层。本模块为 aiohttp 提供与 BrowserPool 等价的策略行为：
  classify → dispatch(ROTATE_PROXY|TRANSIENT|FATAL) → PathSwitch fallback → backoff → loop

共享策略组件：RetryStrategy (kernel _retry_strategy.py)、PathSwitch (kernel _path_strategy.py)、ProxySession (proxy/)。
"""

from __future__ import annotations

import asyncio
import errno
import random
import ssl
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

import aiohttp
from aiohttp import ClientTimeout

from astrocrawl._retry_strategy import RetryStrategy, classify_from_category, classify_http
from astrocrawl._types import FetchErrorCategory

if TYPE_CHECKING:
    from astrocrawl._path_strategy import PathSwitch
    from astrocrawl.proxy import ProxySession


@dataclass
class AiohttpFetchResult:
    content: Optional[bytes] = None
    http_status: int = 0


def _classify_aiohttp_error(exc: Exception) -> FetchErrorCategory:
    if isinstance(exc, asyncio.TimeoutError):
        return FetchErrorCategory.TIMEOUT
    if isinstance(exc, aiohttp.ClientSSLError):
        return FetchErrorCategory.SSL
    if isinstance(exc, aiohttp.ClientConnectorError):
        oserr = getattr(exc, "os_error", None)
        if oserr is not None:
            if isinstance(oserr, ssl.SSLError):
                return FetchErrorCategory.SSL
            if oserr.errno in (errno.ECONNREFUSED,):
                return FetchErrorCategory.CONNECTION_REFUSED
            if oserr.errno in (errno.ECONNRESET,):
                return FetchErrorCategory.CONNECTION_RESET
        return FetchErrorCategory.GENERIC
    if isinstance(exc, aiohttp.ClientError):
        return FetchErrorCategory.GENERIC
    return FetchErrorCategory.GENERIC


def _backoff_with_jitter(backoff: float) -> float:
    return random.uniform(0, backoff)


async def aiohttp_retry_fetch(
    *,
    url: str,
    http_session: aiohttp.ClientSession,
    proxy_session: Optional["ProxySession"],
    path_switch: "Optional[PathSwitch]",
    timeout: float,
    max_retries: int,
    retry_backoff_base: float,
    headers: Optional[dict[str, str]] = None,
    max_bytes: Optional[int] = None,
) -> AiohttpFetchResult:
    retries_remaining = max(1, max_retries)
    proxy_url: Optional[str] = None
    if proxy_session is not None and path_switch is not None and path_switch.main_is_proxy:
        proxy_url = await proxy_session.get_proxy()

    path_switched = False
    attempt = 0

    while retries_remaining > 0:
        has_proxy = proxy_url is not None

        try:
            async with http_session.get(
                url,
                timeout=ClientTimeout(total=timeout),
                proxy=proxy_url,
                headers=headers or {},
            ) as resp:
                if resp.status == 200:
                    raw = await resp.content.read(max_bytes + 1) if max_bytes else await resp.content.read()
                    if proxy_url and proxy_session:
                        await proxy_session.mark_success(proxy_url)
                    content = raw[:max_bytes] if max_bytes and len(raw) > max_bytes else raw
                    return AiohttpFetchResult(content=content, http_status=200)

                strategy = classify_http(resp.status)
                if strategy == RetryStrategy.FATAL:
                    return AiohttpFetchResult(http_status=resp.status)

                retries_remaining -= 1
                if retries_remaining <= 0:
                    return AiohttpFetchResult(http_status=resp.status)
                await asyncio.sleep(_backoff_with_jitter(retry_backoff_base**attempt))
                attempt += 1
                continue

        except Exception as exc:
            category = _classify_aiohttp_error(exc)
            strategy = classify_from_category(category, has_proxy=has_proxy)

            if strategy == RetryStrategy.FATAL:
                if not path_switched and proxy_session is not None and path_switch is not None:
                    if proxy_url is None and path_switch.should_fallback_for_error(category):
                        proxy_url = await proxy_session.get_proxy()
                        if proxy_url is not None:
                            path_switched = True
                            continue
                    if proxy_url is not None and path_switch.should_fallback_for_proxy_exhaustion(category):
                        proxy_url = None
                        path_switched = True
                        continue
                return AiohttpFetchResult()

            if strategy == RetryStrategy.ROTATE_PROXY:
                if proxy_session is not None and proxy_url is not None:
                    await proxy_session.mark_failure(proxy_url)
                    new_proxy = await proxy_session.get_proxy(prefer_different_than=proxy_url)
                    if new_proxy is not None and new_proxy != proxy_url:
                        proxy_url = new_proxy
                        continue

            if not path_switched and proxy_session is not None and path_switch is not None:
                if proxy_url is not None and path_switch.should_fallback_for_proxy_exhaustion(category):
                    proxy_url = None
                    path_switched = True
                    continue
                if proxy_url is None and path_switch.should_fallback_for_error(category):
                    proxy_url = await proxy_session.get_proxy()
                    if proxy_url is not None:
                        path_switched = True
                        continue

            retries_remaining -= 1
            if retries_remaining <= 0:
                return AiohttpFetchResult()
            await asyncio.sleep(_backoff_with_jitter(retry_backoff_base**attempt))
            attempt += 1

    return AiohttpFetchResult()

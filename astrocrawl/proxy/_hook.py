"""冷路径钩子协议 — 对标 HikariCP MetricsTracker + AIHook。

热路径（get_proxy / mark_success / mark_failure）不设钩子——
使用 ProxyStats 内联计数器 + get_all_stats() 快照 poll。
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from astrocrawl.utils.url import redact_proxy_url


@runtime_checkable
class ProxyHook(Protocol):
    """可观测性钩子协议（冷路径）——断路器状态变更通知。

    对标 HikariCP MetricsTracker。2 个同步钩子，仅在断路器状态变更时调用。
    """

    def on_circuit_open(self, proxy_url: str) -> None:
        """断路器跳闸时调用。proxy_url 含 auth 凭证，调用方需在展示前脱敏。"""
        ...

    def on_circuit_recover(self, proxy_url: str) -> None:
        """断路器恢复时调用。proxy_url 含 auth 凭证，调用方需在展示前脱敏。"""
        ...


class LoggingProxyHook:
    """默认日志钩子 — logfmt 格式记录断路器事件。

    内部使用 redact_proxy_url() 脱敏后记录日志。
    """

    def __init__(self) -> None:
        self._log = logging.getLogger("astrocrawl.proxy.hook")

    def on_circuit_open(self, proxy_url: str) -> None:
        self._log.warning("event=proxy.circuit_open proxy=%s", redact_proxy_url(proxy_url))

    def on_circuit_recover(self, proxy_url: str) -> None:
        self._log.info("event=proxy.circuit_recover proxy=%s", redact_proxy_url(proxy_url))

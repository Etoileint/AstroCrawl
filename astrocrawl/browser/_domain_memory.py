"""域名→路径 TTL 记忆。prefer_direct / prefer_proxy 成功路径缓存。

对标 Envoy endpoint pinning——成功路径被缓存以加速后续请求。
"""

from __future__ import annotations

import time


class DomainPathMemory:
    """域名→路径 TTL 双缓存。

    prefer_direct 时直连失败后代理成功 → remember(domain) → 后续跳过直连。
    prefer_proxy 时代理耗尽后直连成功 → remember_direct(domain) → 后续跳过代理遍历。
    """

    def __init__(self, ttl: float = 3600.0) -> None:
        self._ttl = ttl
        self._entries: dict[str, float] = {}
        self._direct_entries: dict[str, float] = {}

    def remember(self, domain: str, ttl: float | None = None) -> None:
        self._entries[domain] = time.monotonic() + (ttl if ttl is not None else self._ttl)

    def needs_proxy(self, domain: str) -> bool:
        expiry = self._entries.get(domain)
        if expiry is None:
            return False
        if expiry <= time.monotonic():
            del self._entries[domain]
            return False
        return True

    def remember_direct(self, domain: str, ttl: float | None = None) -> None:
        self._direct_entries[domain] = time.monotonic() + (ttl if ttl is not None else self._ttl)

    def needs_direct(self, domain: str) -> bool:
        expiry = self._direct_entries.get(domain)
        if expiry is None:
            return False
        if expiry <= time.monotonic():
            del self._direct_entries[domain]
            return False
        return True

    def forget(self, domain: str) -> None:
        self._entries.pop(domain, None)
        self._direct_entries.pop(domain, None)

    def cleanup_expired(self) -> None:
        now = time.monotonic()
        self._entries = {d: t for d, t in self._entries.items() if t > now}
        self._direct_entries = {d: t for d, t in self._direct_entries.items() if t > now}

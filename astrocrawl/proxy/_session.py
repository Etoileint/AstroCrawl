"""ProxySession — 组合根 + 生命周期门面（ADR-0010 决策 5）。

对标：SQLAlchemy Engine (组合根) + HikariCP DataSource (生命周期) + AIClient (委托模式)。
不对标：httpx Client (无连接池)，redis-py Redis (不做单例)。

职责一: 组合根 — ProxyConfig → ProxyManager + ProxyHealthTracker
职责二: 生命周期 — __aenter__ 启动探针，__aexit__ 有序停止
职责三: 委托门面 — get_proxy/mark_success/mark_failure 一行委托
职责四: 纯查询 — is_bypass/is_available/has_healthy/all_dead/get_all_stats（全部同步，快照读取）

不做: 单例管理、连接池复用、Profile→Config 翻译、路由决策
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from astrocrawl._constants import PROXY_PROBE_TIMEOUT
from astrocrawl.proxy._hook import LoggingProxyHook, ProxyHook
from astrocrawl.proxy._probe import ProbeResult, probe_one
from astrocrawl.proxy._proxy import CircuitState, ProxyHealthTracker, ProxyManager, ProxyStats

if TYPE_CHECKING:
    from astrocrawl.proxy._config import ParsedProxy, ProxyConfig


class ProxySession:
    """代理运行时门面 — 组合 ProxyManager + ProxyHealthTracker。

    对标: HikariCP DataSource (生命周期) + SQLAlchemy Engine (组合根)。
    """

    def __init__(
        self,
        config: ProxyConfig,
        *,
        health_tracker: ProxyHealthTracker | None = None,
        manager: ProxyManager | None = None,
        hook: ProxyHook | None = None,
    ) -> None:
        self._config = config
        self._closed = False
        self._parsed_proxies = list(config.proxies)
        self._hook = hook or LoggingProxyHook()

        # DI: 注入优先，否则默认构造
        if health_tracker is not None:
            self._health = health_tracker
        else:
            self._health = ProxyHealthTracker(hook=self._hook)

        if manager is not None:
            self._manager = manager
        else:
            self._manager = ProxyManager(list(config.proxies), health_tracker=self._health)

    # ── 异步上下文管理器 ──────────────────────────────────

    async def __aenter__(self) -> ProxySession:
        if self._parsed_proxies:
            await self._health.start_background_probes(self._parsed_proxies)
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def close(self) -> None:
        """有序关闭（对标 HikariCP close()）。幂等。"""
        if self._closed:
            return
        self._closed = True
        await self._health.stop_background_probes()
        self._health.recovery_event.set()  # 唤醒所有等待者
        self._manager = None  # type: ignore[assignment]
        self._health = None  # type: ignore[assignment]
        self._hook = None  # type: ignore[assignment]

    # ── 代理选择（纯 mechanism，不做路由决策） ──────────

    async def get_proxy(
        self,
        prefer_different_than: str | None = None,
    ) -> str | None:
        """返回代理 URL 字符串（含 auth，内部从 ParsedProxy.to_url_with_auth() 转换）。

        None 仅表示代理池无可用端点。不包含 bypass 判断——调用方应先用 is_bypass()
        决定是否调用此方法。

        WARNING: 返回的 URL 含 auth 凭证，禁止直接进入日志。
        """
        return await self._manager.get_proxy(prefer_different_than)

    # ── bypass 查询（纯 mechanism — 匹配数据，不做决策） ──

    def is_bypass(self, url: str) -> bool:
        """检查目标 URL 是否命中 bypass_domains 白名单——纯查询，由调用方做路由决策。

        大小写不敏感，不考虑端口号。不做正则、CIDR、PAC 文件。
        对标 curl --noproxy / NO_PROXY 环境变量事实标准。
        """
        if not self._config.bypass_domains:
            return False
        hostname = urlparse(url).hostname
        if not hostname:
            return False
        hostname_lower = hostname.lower()
        for pattern in self._config.bypass_domains:
            if self._match_bypass(hostname, hostname_lower, pattern):
                return True
        return False

    @staticmethod
    def _match_bypass(hostname: str, hostname_lower: str, pattern: str) -> bool:
        """单条 bypass 模式匹配。"""
        pattern_lower = pattern.lower()

        # 全局通配 * → 匹配所有主机
        if pattern_lower == "*":
            return True

        # IP glob — 前缀匹配 (如 192.168.*)
        if pattern_lower.endswith(".*") and "." in pattern_lower[:-2]:
            prefix = pattern_lower[:-2]  # 去掉 .*
            return hostname_lower.startswith(prefix + ".")

        # .example.com → 匹配根域及所有子域
        if pattern_lower.startswith("."):
            suffix = pattern_lower  # ".example.com"
            return hostname_lower == suffix[1:] or hostname_lower.endswith(suffix)

        # *.example.com → 仅匹配子域，不匹配根域（* 至少吃掉一个 label）
        if pattern_lower.startswith("*."):
            suffix = pattern_lower[1:]  # ".example.com"
            return hostname_lower.endswith(suffix) and hostname_lower != suffix[1:]

        # 精确匹配（域名或 IP）
        return hostname_lower == pattern_lower

    # ── 健康反馈 ──────────────────────────────────────

    async def mark_success(self, proxy_url: str) -> None:
        """记录成功。proxy_url 为 get_proxy() 返回的完整 URL（含 auth），
        直接作为 stats key——stats key 即为完整 URL，无需剥离/反查。"""
        await self._manager.mark_success(proxy_url)

    async def mark_failure(self, proxy_url: str, weight: int = 1) -> CircuitState:
        """记录失败。proxy_url 为 get_proxy() 返回的完整 URL（含 auth），
        直接作为 stats key。返回断路器状态枚举值。"""
        return await self._manager.mark_failure(proxy_url, weight=weight)

    def _sync_mark_success(self, proxy_url: str) -> None:
        """同步记录成功（set_recovery=False，测试按钮 QThread 路径安全隔离）。"""
        self._health.record_success(proxy_url, set_recovery=False)

    def _sync_mark_failure(self, proxy_url: str) -> None:
        """同步记录失败（测试按钮 QThread 路径）。"""
        self._health.record_failure(proxy_url)

    # ── 批量验证 ──────────────────────────────────────

    async def probe_all(self) -> dict[str, ProbeResult]:
        """并发 TCP 探测所有代理端点。替换旧 ProxyHealthTracker.probe_all()（旧方法删除）。

        启动前预检 + CLI proxy test 用。返回 {完整代理 URL: ProbeResult}。
        """
        if not self._parsed_proxies:
            return {}
        results = await asyncio.gather(
            *[probe_one(p, timeout=PROXY_PROBE_TIMEOUT) for p in self._parsed_proxies],
            return_exceptions=True,
        )
        out: dict[str, ProbeResult] = {}
        for proxy, result in zip(self._parsed_proxies, results, strict=False):
            if isinstance(result, BaseException):
                out[proxy.to_url_with_auth()] = ProbeResult(reachable=False, error=type(result).__name__)
            else:
                out[proxy.to_url_with_auth()] = result
        return out

    # ── 查询（全部同步，快照读取，无锁） ──────────────────

    @property
    def proxies(self) -> tuple[str, ...]:
        """返回代理 URL 列表（含 auth = to_url_with_auth()），供健康报告/GUI 迭代。
        stats 键与此列表一致——get_all_stats() 的 dict key = to_url_with_auth()。
        GUI 展示前用 redact_proxy_url() 脱敏。
        """
        return tuple(self._manager.proxies)

    @property
    def parsed_proxies(self) -> tuple[ParsedProxy, ...]:
        """返回已解析的代理端点列表，供 probe_one() 等需要结构化端点的调用方使用。"""
        return tuple(self._parsed_proxies)

    def get_all_stats(self) -> dict[str, ProxyStats]:
        """返回所有代理统计快照。无锁快照读取。"""
        return self._health.get_all_stats()

    def is_available(self, proxy_url: str) -> bool:
        """纯查询：代理是否可用。快照读取，无需锁。"""
        return self._health.is_available(proxy_url)

    def has_healthy(self) -> bool:
        """是否有可用代理（含未使用过的 = 不在 _stats 中，视为健康）。同步快照。"""
        if not self._manager.proxies:
            return False
        snapshot = self._health.get_all_stats()
        return any(p not in snapshot or snapshot[p].is_available for p in self._manager.proxies)

    def healthy_proxies_in_pool(self) -> list[str]:
        """返回代理池中所有健康的代理 URL 列表（含未使用过的）。
        供 ContextPool.rotate_proxy() 消费。"""
        return self._manager.healthy_proxies_in_pool()

    def all_dead(self) -> bool:
        """全部已知代理 health_score == 0.0（全部 OPEN）。"""
        return self._health.all_proxies_dead()

    @property
    def recovery_event(self) -> asyncio.Event:
        """代理恢复时被 set()。等待者负责在 wait() 前 clear()——这是标准
        asyncio.Event 用法：clear() 表示"我已注意到"，下次恢复时再次 set()。
        当前唯一消费者：engine.py proxy_only 模式暂停出队循环。"""
        return self._health.recovery_event

    @property
    def health(self) -> ProxyHealthTracker:
        """供 GUI 健康条直接访问。"""
        return self._health

    @property
    def closed(self) -> bool:
        return self._closed

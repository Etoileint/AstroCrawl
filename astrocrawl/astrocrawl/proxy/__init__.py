"""astrocrawl/proxy/ — 纯 mechanism 代理底座（ADR-0010）。

能力：结构化代理端点 / 加权轮询 / 三级断路器 / TCP 健康探针 / bypass 白名单。
路由策略（PathSwitch）留在 kernel 层——proxy/ 只回答"哪个端点可用"，不做"该不该走代理"。

对标：Envoy OutlierDetection (健康驱逐)、HikariCP DataSource (生命周期门面)、
     SQLAlchemy Engine (组合根)、FoxyProxy (端点配置格式)、HAProxy (SWRR)。
"""

from __future__ import annotations

from astrocrawl.proxy._config import (
    HasEndpointIdentity,
    ParsedProxy,
    ProxyAuth,
    ProxyConfig,
    ProxyEndpointSpec,
    ProxyProfile,
    ProxyType,
    endpoint_display,
    endpoint_key,
    find_duplicate_endpoint,
)
from astrocrawl.proxy._consumers import PROXY_CONSUMERS
from astrocrawl.proxy._hook import LoggingProxyHook, ProxyHook
from astrocrawl.proxy._probe import ProbeResult, probe_one
from astrocrawl.proxy._proxy import CircuitState, ProxyHealthTracker, ProxyManager, ProxyStats
from astrocrawl.proxy._session import ProxySession

__all__ = [
    # config
    "HasEndpointIdentity",
    "ParsedProxy",
    "ProxyAuth",
    "ProxyConfig",
    "ProxyEndpointSpec",
    "ProxyProfile",
    "ProxyType",
    "endpoint_display",
    "endpoint_key",
    "find_duplicate_endpoint",
    # proxy core
    "CircuitState",
    "ProxyHealthTracker",
    "ProxyManager",
    "ProxyStats",
    # session
    "ProxySession",
    # probe
    "ProbeResult",
    "probe_one",
    # hook
    "LoggingProxyHook",
    "ProxyHook",
    # consumers
    "PROXY_CONSUMERS",
]

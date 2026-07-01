"""TCP 连通性预检 — 对标 Envoy TcpHealthCheck。

独立函数，GUI "测试连接" + CLI proxy test + 后台探针共用此实现。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from astrocrawl._constants import PROXY_PROBE_TIMEOUT
from astrocrawl.proxy._config import ParsedProxy


@dataclass(frozen=True)
class ProbeResult:
    """TCP 探测结果。"""

    reachable: bool
    latency_ms: float | None = None
    error: str | None = None


async def probe_one(proxy: ParsedProxy, *, timeout: float = PROXY_PROBE_TIMEOUT) -> ProbeResult:
    """TCP 连通性验证 — 对标 Envoy TcpHealthCheck。

    接收 ParsedProxy 完整端点——当前仅使用 host 和 port 字段执行 TCP 连通性检测，
    其余字段（type/auth/weight）预留未来 HTTP/SOCKS 握手探测扩展。
    """
    try:
        start = time.monotonic()
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(proxy.host, proxy.port),
            timeout=timeout,
        )
        latency_ms = (time.monotonic() - start) * 1000
        writer.close()
        await writer.wait_closed()
        return ProbeResult(reachable=True, latency_ms=latency_ms)
    except asyncio.TimeoutError:
        return ProbeResult(reachable=False, error="timed out")
    except OSError as exc:
        return ProbeResult(reachable=False, error=str(exc))
    except Exception as exc:
        return ProbeResult(reachable=False, error=type(exc).__name__)

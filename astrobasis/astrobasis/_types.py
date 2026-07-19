"""AsyncCloseable Protocol — 异步资源生命周期接口（aclose / close）。"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class AsyncCloseable(Protocol):
    """Any component that creates background tasks must implement this protocol."""

    async def aclose(self) -> None:
        """Cancel all background tasks, wait for completion (idempotent)."""
        ...

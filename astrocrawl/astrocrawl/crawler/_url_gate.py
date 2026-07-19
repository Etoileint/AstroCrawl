"""UrlGate — 对标 Heritrix CrawlScope 的统一 URL 准入门禁。

所有 URL 发现路径（种子、Sitemap、链接提取、Heal/Promote）统一通过
UrlGate.admit() 判断是否入队、存入边界链接或拒绝。

纯策略组件 — admit() 参数化全部可变上下文（state、max_depth、exclude_patterns），
无实例状态，构造后零配置。
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, List, Protocol, runtime_checkable

from astrocrawl._types import EnqueueResult
from astrocrawl.utils.url import is_valid_http_url

if TYPE_CHECKING:
    import re


class AdmitResult(Enum):
    ENQUEUED = "enqueued"
    BOUNDARY = "boundary"
    INVALID_URL = "invalid_url"
    EXCLUDED = "excluded"
    QUEUE_FULL = "queue_full"
    DUPLICATE = "duplicate"


@runtime_checkable
class _QueueState(Protocol):
    """UrlGate 所需的 CrawlState 窄接口（ISP — 仅依赖实际调用的方法）。"""

    async def push_to_queue_single(self, url: str, depth: int, domain: str = "") -> EnqueueResult: ...
    async def save_boundary_links(self, parent_url: str, child_urls: list, parent_depth: int) -> int: ...


class UrlGate:
    """统一 URL 准入门禁 — 纯策略组件，admit() 参数化全部上下文。

    admit() 为 @staticmethod — 零实例状态，无需通过 PipelineDeps DI。
    对照有状态依赖（CrawlState、CrawlStats、AsyncJsonlWriter）均通过
    PipelineDeps 注入，无状态纯策略函数直接以静态方法调用。
    """

    @staticmethod
    async def admit(
        url: str,
        depth: int,
        state: _QueueState,
        max_depth: int,
        exclude_patterns: List["re.Pattern"],
        parent_url: str = "",
    ) -> AdmitResult:
        """判断 URL 是否应入队。

        Returns:
            ENQUEUED  — 已推入队列
            BOUNDARY  — 超出深度限制，已存入 boundary_links
            INVALID_URL — URL 格式无效
            EXCLUDED  — 命中排除模式
            QUEUE_FULL — 队列已满
            DUPLICATE — URL 已存在于 urls/queue/in_flight
        """
        if not is_valid_http_url(url):
            return AdmitResult.INVALID_URL

        for compiled in exclude_patterns:
            if compiled.search(url):
                return AdmitResult.EXCLUDED

        if depth >= max_depth:
            await state.save_boundary_links(parent_url, [url], depth - 1)
            return AdmitResult.BOUNDARY

        result = await state.push_to_queue_single(url, depth)
        if result == EnqueueResult.ENQUEUED:
            return AdmitResult.ENQUEUED
        elif result == EnqueueResult.QUEUE_FULL:
            return AdmitResult.QUEUE_FULL
        elif result == EnqueueResult.DUPLICATE:
            return AdmitResult.DUPLICATE
        return AdmitResult.INVALID_URL  # defensive

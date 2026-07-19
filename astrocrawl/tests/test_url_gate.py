"""UrlGate 统一准入 — AdmitResult 全路径 + 深度边界测试。"""

from __future__ import annotations

import pytest

from astrocrawl.config import CrawlerConfig
from astrocrawl.crawler._url_gate import AdmitResult, UrlGate
from astrocrawl.storage.db import CrawlState


@pytest.fixture
async def state():
    cfg = CrawlerConfig(
        domain_min_delay=0.0,
        domain_max_delay=0.0,
        domain_max_concurrency=1,
        queue_hard_maxsize=100,
        max_retries=1,
        max_requeue=1,
        page_timeout=20000,
        network_idle_timeout=8000,
        skip_non_essential_resources=False,
        robots_respect=False,
        use_sitemap=False,
    )
    s = CrawlState(":memory:", cfg)
    await s.open()
    yield s
    await s.close()


class TestUrlGateAdmit:
    async def test_valid_url_enqueued(self, state):
        result = await UrlGate.admit("https://example.com/page", 1, state, max_depth=2, exclude_patterns=[])
        assert result == AdmitResult.ENQUEUED
        assert await state.queue_size() == 1

    async def test_invalid_url_rejected(self, state):
        result = await UrlGate.admit("not-a-url", 0, state, max_depth=2, exclude_patterns=[])
        assert result == AdmitResult.INVALID_URL

    async def test_exclude_pattern_rejected(self, state):
        import re

        result = await UrlGate.admit(
            "https://example.com/doc.pdf", 0, state, max_depth=2, exclude_patterns=[re.compile(r"\.pdf$")]
        )
        assert result == AdmitResult.EXCLUDED

    async def test_depth_overshoot_boundary(self, state):
        result = await UrlGate.admit(
            "https://example.com/page", 1, state, max_depth=1, exclude_patterns=[], parent_url="https://example.com"
        )
        assert result == AdmitResult.BOUNDARY
        assert await state.queue_size() == 0

    async def test_depth_overshoot_no_parent_still_saved(self, state):
        """Sitemap URL 无 parent_url 时超限也应存入 boundary_links。"""
        await UrlGate.admit("https://example.com/sitemap-url", 1, state, max_depth=1, exclude_patterns=[])
        boundary = await state.promote_boundary_links(2)
        assert len(boundary) == 1
        assert boundary[0][0] == "https://example.com/sitemap-url"

    async def test_depth_overshoot_saved_to_boundary(self, state):
        await UrlGate.admit(
            "https://example.com/deep",
            1,
            state,
            max_depth=1,
            exclude_patterns=[],
            parent_url="https://example.com/seed",
        )
        boundary = await state.promote_boundary_links(2)
        assert len(boundary) == 1
        assert boundary[0][0] == "https://example.com/deep"

    async def test_queue_full(self, state):
        for i in range(100):
            await UrlGate.admit(f"https://example.com/{i}", 1, state, max_depth=2, exclude_patterns=[])
        result = await UrlGate.admit("https://example.com/overflow", 1, state, max_depth=2, exclude_patterns=[])
        assert result in (AdmitResult.QUEUE_FULL, AdmitResult.ENQUEUED)

    async def test_depth_zero_seed_always_allowed(self, state):
        result = await UrlGate.admit("https://example.com/seed", 0, state, max_depth=1, exclude_patterns=[])
        assert result == AdmitResult.ENQUEUED

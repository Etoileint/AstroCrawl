"""共享测试夹具 — 供 test_*.py 中的测试复用。

夹具遵循最小权限原则：scope="function"（默认），保证测试间隔离。
"""

from __future__ import annotations

import pytest

# 确保 re2 在测试环境中可用（_startup.py 的 check_dependencies 在测试路径中不会执行）
from astrocrawl._startup import _check_re2

_check_re2()  # noqa: E402

from astrocrawl.config import CrawlerConfig  # noqa: E402
from astrocrawl.storage.db import CrawlState  # noqa: E402
from tests._fakes import FakeBrowserPool, FakeWriter  # noqa: E402


@pytest.fixture
def test_config():
    """零延迟、小队列的测试用 CrawlerConfig。

    覆盖所有常规模块的构造需求（DomainRateLimiter/CrawlerConfig/AsyncCrawler/
    CrawlState），消除各测试文件中的重复 Config 构造。
    """
    return CrawlerConfig(
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


@pytest.fixture
def test_config_with_robots():
    """test_config 变体：robots_respect=True，用于 _robots_processor 集成测试。"""
    return CrawlerConfig(
        domain_min_delay=0.0,
        domain_max_delay=0.0,
        domain_max_concurrency=1,
        queue_hard_maxsize=100,
        max_retries=1,
        max_requeue=1,
        page_timeout=20000,
        network_idle_timeout=8000,
        skip_non_essential_resources=False,
        robots_respect=True,
        use_sitemap=False,
    )


@pytest.fixture
async def fake_state(test_config):
    """真实 CrawlState + aiosqlite :memory:，自动 cleanup。

    对标 Google "Prefer Real Implementations" — 使用真实 SQLite 替代 FakeCrawlState。
    每个测试独立 :memory: 数据库，无磁盘 I/O。
    """
    state = CrawlState(":memory:", test_config)
    await state.open()
    yield state
    await state.close()


@pytest.fixture
def fake_browser_pool():
    """默认成功的 FakeBrowserPool——所有 URL 返回 HTTP 200, html=<html></html>。

    测试中可通过 browser_pool._responses["url"] = FetchError(...) 注入特定 URL 的返回值。
    """
    return FakeBrowserPool()


@pytest.fixture
def fake_writer():
    """不写磁盘的 FakeWriter——write_record() 追加到内存列表。"""
    return FakeWriter()


# ═══════════════════════════════════════════════════════════════════════
# GUI 测试夹具
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="function")
def qapp():
    """确保每个 GUI 测试有 QApplication 实例（offscreen 平台）。

    所有使用 QWidget / QObject / Signal 的测试都需要此夹具。
    """
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture
def fake_prefs():
    """FakePreferences — 内存 dict 存储，无 QSettings 依赖。"""
    from tests._fakes_gui import FakePreferences

    return FakePreferences()


@pytest.fixture
def theme_mgr(qapp, fake_prefs, monkeypatch):
    """ThemeManager 单例 — 注入 fake_prefs，替换全局 _theme_manager。"""
    from astrocrawl.gui.theme import ThemeManager

    mgr = ThemeManager(qapp, fake_prefs)
    monkeypatch.setattr("astrocrawl.gui.theme._theme_manager", mgr)
    return mgr


@pytest.fixture
def fake_crawl_session():
    """FakeCrawlSession — 预编程信号发射，无真实爬虫线程。"""
    from tests._fakes_gui import FakeCrawlSession

    return FakeCrawlSession()

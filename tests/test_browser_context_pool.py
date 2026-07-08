"""ContextPool 代理绑定 + 上下文生命周期测试。

使用 FakeBrowser + FakeBrowserContext + FakeProxyManager 验证完整的槽位-代理协调逻辑。
"""

from __future__ import annotations

import pytest

from astrocrawl.browser.context_pool import ContextPool
from astrocrawl.config import ConfigError
from astrocrawl.utils.logging import LogfmtLogger
from tests._fakes import FakeBrowser, FakeProxyManager

# ── 测试用最小配置 ───────────────────────────────────────────────────


class _TestContextPoolConfig:
    viewport_width = 1920
    viewport_height = 1080
    user_agent = "test-agent"
    page_pool_size_per_context = 2
    auth_basic_user = ""
    auth_basic_pass = ""
    auth_bearer_token = ""
    cookies_file = ""
    custom_headers: list = []
    proxy_mode = "direct_only"

    def get_path_switch(self):
        class _PS:
            main_is_proxy = False
            fallback_is_proxy = False
            on_exhausted = "fail"

        return _PS()


_log = LogfmtLogger("astrocrawl.test.contextpool")


# ═══════════════════════════════════════════════════════════════════════
# ContextPool 构造
# ═══════════════════════════════════════════════════════════════════════


class TestContextPoolConstructor:
    """ContextPool.__init__ 构造校验。"""

    def test_normal_init(self):
        """正常接受 Browser + ProxyManager。"""
        browser = FakeBrowser()
        pm = FakeProxyManager(["http://p1:8080"])
        cfg = _TestContextPoolConfig()
        pool = ContextPool(browser, max_slots=2, proxy_session=pm, cfg=cfg)
        assert pool._max_slots == 2

    def test_proxy_mode_missing_manager_raises(self):
        """proxy_only 模式缺 ProxySession → ConfigError。"""
        browser = FakeBrowser()
        cfg = _TestContextPoolConfig()
        cfg.proxy_mode = "proxy_only"
        with pytest.raises(ConfigError):
            ContextPool(browser, max_slots=1, proxy_session=None, cfg=cfg)

    def test_direct_only_accepts_none_manager(self):
        """direct_only 模式接受 None proxy_manager。"""
        browser = FakeBrowser()
        cfg = _TestContextPoolConfig()
        cfg.proxy_mode = "direct_only"
        pool = ContextPool(browser, max_slots=1, proxy_session=None, cfg=cfg)
        assert pool._max_slots == 1
        assert pool._proxy_session is None


# ═══════════════════════════════════════════════════════════════════════
# ContextPool.init
# ═══════════════════════════════════════════════════════════════════════


class TestContextPoolInit:
    """ContextPool.init 批量槽位创建。"""

    async def test_batch_create_all_slots(self, monkeypatch):
        """init() 并行创建全部 slot。"""
        monkeypatch.setattr("astrocrawl.browser._slot_pool.SLOT_CREATE_BACKOFF", 0.0)
        browser = FakeBrowser()
        cfg = _TestContextPoolConfig()
        pool = ContextPool(browser, max_slots=4, proxy_session=None, cfg=cfg)
        await pool.init()
        for i in range(4):
            assert pool.slot_is_valid(i)

    async def test_all_failures_raise_runtime_error(self, monkeypatch):
        """全部 slot 创建失败 → RuntimeError。"""
        monkeypatch.setattr("astrocrawl.browser._slot_pool.SLOT_CREATE_BACKOFF", 0.0)
        browser = FakeBrowser()
        browser._raise_on_new_context = RuntimeError("fail")
        cfg = _TestContextPoolConfig()
        pool = ContextPool(browser, max_slots=1, proxy_session=None, cfg=cfg)
        with pytest.raises(RuntimeError, match="无法创建任何浏览器上下文"):
            await pool.init()

    async def test_partial_failures_no_raise(self, monkeypatch):
        """部分 slot 失败不抛异常——至少一个有效即可。"""
        monkeypatch.setattr("astrocrawl.browser._slot_pool.SLOT_CREATE_BACKOFF", 0.0)
        monkeypatch.setattr("astrocrawl._constants.SLOT_CREATE_RETRIES", 1)
        browser = FakeBrowser()
        cfg = _TestContextPoolConfig()
        pool = ContextPool(browser, max_slots=2, proxy_session=None, cfg=cfg)
        # 手动使 slot 1 创建失败
        _orig = pool._slot_pool.create

        async def _fail_slot1(idx, proxy_url, max_attempts=1):
            if idx == 1:
                return False
            return await _orig(idx, proxy_url, max_attempts)

        monkeypatch.setattr(pool._slot_pool, "create", _fail_slot1)
        await pool.init()
        assert pool.slot_is_valid(0)


# ═══════════════════════════════════════════════════════════════════════
# ContextPool 代理操作
# ═══════════════════════════════════════════════════════════════════════


class TestContextPoolProxyOps:
    """ContextPool 代理轮换和上下文替换。"""

    async def test_rotate_proxy_no_manager_returns_false(self, monkeypatch):
        """无 proxy_session → rotate_proxy 返回 False。"""
        monkeypatch.setattr("astrocrawl.browser._slot_pool.SLOT_CREATE_BACKOFF", 0.0)
        browser = FakeBrowser()
        cfg = _TestContextPoolConfig()
        pool = ContextPool(browser, max_slots=1, proxy_session=None, cfg=cfg)
        assert await pool.rotate_proxy(0) is False

    async def test_rotate_proxy_gets_new_and_replaces(self, monkeypatch):
        """获取新代理并替换 context。"""
        monkeypatch.setattr("astrocrawl.browser._slot_pool.SLOT_CREATE_BACKOFF", 0.0)
        browser = FakeBrowser()
        pm = FakeProxyManager(["http://p1:8080", "http://p2:8080"])
        cfg = _TestContextPoolConfig()
        cfg.proxy_mode = "proxy_only"

        def _path_switch():
            class _PS:
                main_is_proxy = True
                fallback_is_proxy = False
                on_exhausted = "fail"

            return _PS()

        cfg.get_path_switch = _path_switch

        pool = ContextPool(browser, max_slots=1, proxy_session=pm, cfg=cfg)
        await pool.init()
        old_proxy = pool.get_proxy_for_slot(0)
        assert old_proxy == "http://p1:8080"
        result = await pool.rotate_proxy(0)
        assert result is True
        new_proxy = pool.get_proxy_for_slot(0)
        assert new_proxy == "http://p2:8080"

    async def test_replace_context_without_proxy(self, monkeypatch):
        """无代理时 replace_context 使用直连。"""
        monkeypatch.setattr("astrocrawl.browser._slot_pool.SLOT_CREATE_BACKOFF", 0.0)
        browser = FakeBrowser()
        cfg = _TestContextPoolConfig()
        pool = ContextPool(browser, max_slots=1, proxy_session=None, cfg=cfg)
        await pool.init()
        old_ctx = pool._slot_pool.get_context(0)
        await pool.replace_context(0)
        new_ctx = pool._slot_pool.get_context(0)
        assert new_ctx is not old_ctx

    async def test_rotate_proxy_unhealthy_no_alternatives_returns_false(self, monkeypatch):
        """旧代理不健康且无替代代理 → rotate_proxy 返回 False。"""
        monkeypatch.setattr("astrocrawl.browser._slot_pool.SLOT_CREATE_BACKOFF", 0.0)
        browser = FakeBrowser()
        pm = FakeProxyManager(["http://p1:8080"])
        pm._available_flag = False
        cfg = _TestContextPoolConfig()
        cfg.proxy_mode = "proxy_only"

        def _path_switch():
            class _PS:
                main_is_proxy = True
                fallback_is_proxy = False
                on_exhausted = "fail"

            return _PS()

        cfg.get_path_switch = _path_switch

        pool = ContextPool(browser, max_slots=1, proxy_session=pm, cfg=cfg)
        await pool.init()
        result = await pool.rotate_proxy(0)
        assert result is False

    async def test_replace_context_with_proxy(self, monkeypatch):
        """槽位有代理时 replace_context 获取新代理并替换。"""
        monkeypatch.setattr("astrocrawl.browser._slot_pool.SLOT_CREATE_BACKOFF", 0.0)
        browser = FakeBrowser()
        pm = FakeProxyManager(["http://p1:8080", "http://p2:8080"])
        cfg = _TestContextPoolConfig()
        cfg.proxy_mode = "proxy_only"

        def _path_switch():
            class _PS:
                main_is_proxy = True
                fallback_is_proxy = False
                on_exhausted = "fail"

            return _PS()

        cfg.get_path_switch = _path_switch

        pool = ContextPool(browser, max_slots=1, proxy_session=pm, cfg=cfg)
        await pool.init()
        assert pool.get_proxy_for_slot(0) == "http://p1:8080"
        old_ctx = pool._slot_pool.get_context(0)
        await pool.replace_context(0)
        new_ctx = pool._slot_pool.get_context(0)
        assert new_ctx is not old_ctx
        assert pool.get_proxy_for_slot(0) is not None


# ═══════════════════════════════════════════════════════════════════════
# ContextPool.scoped_path
# ═══════════════════════════════════════════════════════════════════════


class TestContextPoolScopedPath:
    """ContextPool.scoped_path 临时路径切换。"""

    async def test_noop_same_path(self, monkeypatch):
        """同路径 → 无操作。"""
        monkeypatch.setattr("astrocrawl.browser._slot_pool.SLOT_CREATE_BACKOFF", 0.0)
        browser = FakeBrowser()
        cfg = _TestContextPoolConfig()
        pool = ContextPool(browser, max_slots=1, proxy_session=None, cfg=cfg)
        await pool.init()
        ctx_before = pool._slot_pool.get_context(0)
        async with pool.scoped_path(0, "direct"):
            pass
        assert pool._slot_pool.get_context(0) is ctx_before

    async def test_switch_to_proxy_then_restore(self, monkeypatch):
        """直连→代理→恢复直连。"""
        monkeypatch.setattr("astrocrawl.browser._slot_pool.SLOT_CREATE_BACKOFF", 0.0)
        browser = FakeBrowser()
        pm = FakeProxyManager(["http://p1:8080"])
        cfg = _TestContextPoolConfig()
        pool = ContextPool(browser, max_slots=1, proxy_session=pm, cfg=cfg)
        await pool.init()
        assert pool.get_proxy_for_slot(0) is None
        async with pool.scoped_path(0, "proxy"):
            assert pool.get_proxy_for_slot(0) is not None
        assert pool.get_proxy_for_slot(0) is None

    async def test_switch_to_direct_then_restore_to_proxy(self, monkeypatch):
        """代理→直连→恢复代理。"""
        monkeypatch.setattr("astrocrawl.browser._slot_pool.SLOT_CREATE_BACKOFF", 0.0)
        browser = FakeBrowser()
        pm = FakeProxyManager(["http://p1:8080"])
        cfg = _TestContextPoolConfig()

        def _path_switch():
            class _PS:
                main_is_proxy = True
                fallback_is_proxy = False
                on_exhausted = "fail"

            return _PS()

        cfg.get_path_switch = _path_switch

        pool = ContextPool(browser, max_slots=1, proxy_session=pm, cfg=cfg)
        await pool.init()
        assert pool.get_proxy_for_slot(0) is not None
        async with pool.scoped_path(0, "direct"):
            assert pool.get_proxy_for_slot(0) is None
        assert pool.get_proxy_for_slot(0) is not None

    async def test_scoped_path_restore_failure_graceful(self, monkeypatch):
        """scoped_path 恢复失败时记录警告但不传播异常。"""
        monkeypatch.setattr("astrocrawl.browser._slot_pool.SLOT_CREATE_BACKOFF", 0.0)
        browser = FakeBrowser()
        pm = FakeProxyManager(["http://p1:8080"])
        cfg = _TestContextPoolConfig()

        def _path_switch():
            class _PS:
                main_is_proxy = True
                fallback_is_proxy = False
                on_exhausted = "fail"

            return _PS()

        cfg.get_path_switch = _path_switch

        pool = ContextPool(browser, max_slots=1, proxy_session=pm, cfg=cfg)
        await pool.init()
        assert pool.get_proxy_for_slot(0) is not None

        call_count = 0
        _orig_replace = pool._slot_pool.replace

        async def _replace_second_fails(idx, proxy_url, max_attempts=3):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("restore failed")
            await _orig_replace(idx, proxy_url, max_attempts)

        monkeypatch.setattr(pool._slot_pool, "replace", _replace_second_fails)
        # 不应抛出异常
        async with pool.scoped_path(0, "direct"):
            pass
        assert call_count == 2


# ═══════════════════════════════════════════════════════════════════════
# ContextPool 生命周期
# ═══════════════════════════════════════════════════════════════════════


class TestContextPoolLifecycle:
    """ContextPool mark_proxy_success/failure + close_all。"""

    async def test_mark_proxy_success(self, monkeypatch):
        """mark_proxy_success 转发到 ProxyManager。"""
        monkeypatch.setattr("astrocrawl.browser._slot_pool.SLOT_CREATE_BACKOFF", 0.0)
        browser = FakeBrowser()
        pm = FakeProxyManager(["http://p1:8080"])
        cfg = _TestContextPoolConfig()

        def _path_switch():
            class _PS:
                main_is_proxy = True
                fallback_is_proxy = False
                on_exhausted = "fail"

            return _PS()

        cfg.get_path_switch = _path_switch

        pool = ContextPool(browser, max_slots=1, proxy_session=pm, cfg=cfg)
        await pool.init()
        await pool.mark_proxy_success(0)
        assert "http://p1:8080" in pm.success_calls

    async def test_mark_proxy_failure(self, monkeypatch):
        """mark_proxy_failure 转发到 ProxyManager。"""
        monkeypatch.setattr("astrocrawl.browser._slot_pool.SLOT_CREATE_BACKOFF", 0.0)
        browser = FakeBrowser()
        pm = FakeProxyManager(["http://p1:8080"])
        cfg = _TestContextPoolConfig()

        def _path_switch():
            class _PS:
                main_is_proxy = True
                fallback_is_proxy = False
                on_exhausted = "fail"

            return _PS()

        cfg.get_path_switch = _path_switch

        pool = ContextPool(browser, max_slots=1, proxy_session=pm, cfg=cfg)
        await pool.init()
        await pool.mark_proxy_failure(0, weight=2)
        assert ("http://p1:8080", 2) in pm.failure_calls

    async def test_close_all_cascades(self, monkeypatch):
        """close_all 级联关闭 SlotPool。"""
        monkeypatch.setattr("astrocrawl.browser._slot_pool.SLOT_CREATE_BACKOFF", 0.0)
        browser = FakeBrowser()
        cfg = _TestContextPoolConfig()
        pool = ContextPool(browser, max_slots=1, proxy_session=None, cfg=cfg)
        await pool.init()
        await pool.close_all()
        assert pool._slot_pool.is_closed

    async def test_stop_accepting_cascades(self, monkeypatch):
        """stop_accepting 设置 _closed 并级联到 SlotPool。"""
        monkeypatch.setattr("astrocrawl.browser._slot_pool.SLOT_CREATE_BACKOFF", 0.0)
        browser = FakeBrowser()
        cfg = _TestContextPoolConfig()
        pool = ContextPool(browser, max_slots=1, proxy_session=None, cfg=cfg)
        await pool.init()
        pool.stop_accepting()
        assert pool._closed
        assert pool._slot_pool._closed

    def test_path_switch_property(self):
        """path_switch 返回初始化时的 PathSwitch 对象。"""
        browser = FakeBrowser()
        cfg = _TestContextPoolConfig()
        pool = ContextPool(browser, max_slots=1, proxy_session=None, cfg=cfg)
        ps = pool.path_switch
        assert ps.main_is_proxy is False

    async def test_get_page_pool(self, monkeypatch):
        """get_page_pool 委托到 SlotPool。"""
        monkeypatch.setattr("astrocrawl.browser._slot_pool.SLOT_CREATE_BACKOFF", 0.0)
        browser = FakeBrowser()
        cfg = _TestContextPoolConfig()
        pool = ContextPool(browser, max_slots=1, proxy_session=None, cfg=cfg)
        await pool.init()
        pp = pool.get_page_pool(0)
        from astrocrawl.browser.page_pool import PagePool

        assert isinstance(pp, PagePool)

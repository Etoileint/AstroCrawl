"""SlotPool 浏览器上下文槽位池 + _safe_close_context 测试。

使用 FakeBrowser + FakeBrowserContext 验证创建/替换/销毁/查询/关闭生命周期。
"""

from __future__ import annotations

import logging

import pytest

from astrocrawl.browser._slot_pool import SlotCreateError, SlotPool, _safe_close_context
from tests._fakes import FakeBrowser, FakeBrowserContext

# ── 测试用最小配置 ───────────────────────────────────────────────────


class _TestSlotConfig:
    viewport_width = 1920
    viewport_height = 1080
    user_agent = "test-agent"
    page_pool_size_per_context = 2
    auth_basic_user = ""
    auth_basic_pass = ""
    auth_bearer_token = ""
    cookies_file = ""
    custom_headers: list = []


_log = logging.getLogger("astrocrawl.test.slotpool")


# ═══════════════════════════════════════════════════════════════════════
# _safe_close_context
# ═══════════════════════════════════════════════════════════════════════


class TestSafeCloseContext:
    """_safe_close_context 安全关闭。"""

    async def test_none_noop(self):
        """None → 无操作。"""
        await _safe_close_context(None, _log)

    async def test_normal_close(self):
        """正常关闭上下文。"""
        ctx = FakeBrowserContext()
        await _safe_close_context(ctx, _log)
        assert ctx._closed


# ═══════════════════════════════════════════════════════════════════════
# SlotPool.create
# ═══════════════════════════════════════════════════════════════════════


class TestSlotPoolCreate:
    """SlotPool.create 槽位创建。"""

    async def test_create_success(self):
        """指定索引创建上下文成功。"""
        browser = FakeBrowser()
        pool = SlotPool(browser, max_slots=2, cfg=_TestSlotConfig(), log=_log)
        ok = await pool.create(0)
        assert ok is True
        assert pool.slot_is_valid(0)
        assert pool.get_proxy_url(0) is None

    async def test_retry_succeeds(self, monkeypatch):
        """前 2 次失败, 第 3 次成功——重试逻辑生效。"""
        monkeypatch.setattr("astrocrawl.browser._slot_pool.SLOT_CREATE_BACKOFF", 0.0)
        browser = FakeBrowser()
        pool = SlotPool(browser, max_slots=1, cfg=_TestSlotConfig(), log=_log)
        call_count = [0]

        async def _flaky_new_context(proxy_url):
            call_count[0] += 1
            if call_count[0] < 3:
                raise RuntimeError("fail")
            ctx = FakeBrowserContext()
            return ctx

        monkeypatch.setattr(pool, "_new_context", _flaky_new_context)
        ok = await pool.create(0)
        assert ok is True
        assert call_count[0] == 3
        assert pool.slot_is_valid(0)

    async def test_all_attempts_fail_returns_false(self, monkeypatch):
        """全部重试失败返回 False。"""
        monkeypatch.setattr("astrocrawl.browser._slot_pool.SLOT_CREATE_BACKOFF", 0.0)
        browser = FakeBrowser()
        browser._raise_on_new_context = RuntimeError("persistent fail")
        pool = SlotPool(browser, max_slots=1, cfg=_TestSlotConfig(), log=_log)
        ok = await pool.create(0, max_attempts=2)
        assert ok is False
        assert not pool.slot_is_valid(0)

    async def test_closed_pool_rejects(self):
        """stop_accepting 后 create 返回 False。"""
        browser = FakeBrowser()
        pool = SlotPool(browser, max_slots=1, cfg=_TestSlotConfig(), log=_log)
        pool.stop_accepting()
        ok = await pool.create(0)
        assert ok is False

    async def test_proxy_url_passed_through(self):
        """proxy_url → browser.new_context(proxy=...)。"""
        browser = FakeBrowser()
        pool = SlotPool(browser, max_slots=1, cfg=_TestSlotConfig(), log=_log)
        await pool.create(0, proxy_url="http://proxy:8080")
        assert "proxy" in browser.last_context_kwargs
        assert browser.last_context_kwargs["proxy"] == {"server": "http://proxy:8080"}


# ═══════════════════════════════════════════════════════════════════════
# SlotPool.replace
# ═══════════════════════════════════════════════════════════════════════


class TestSlotPoolReplace:
    """SlotPool.replace 原子交换。"""

    async def test_atomic_swap_preserves_validity(self, monkeypatch):
        """新上下文先创建, 旧上下文后销毁——中间状态无 nullptr 窗口。"""
        monkeypatch.setattr("astrocrawl.browser._slot_pool.SLOT_CREATE_BACKOFF", 0.0)
        browser = FakeBrowser()
        pool = SlotPool(browser, max_slots=1, cfg=_TestSlotConfig(), log=_log)
        await pool.create(0)
        old_ctx = pool.get_context(0)
        assert old_ctx is not None
        await pool.replace(0, None)
        new_ctx = pool.get_context(0)
        assert new_ctx is not None
        assert new_ctx is not old_ctx

    async def test_old_context_closed(self, monkeypatch):
        """交换后旧上下文被关闭。"""
        monkeypatch.setattr("astrocrawl.browser._slot_pool.SLOT_CREATE_BACKOFF", 0.0)
        browser = FakeBrowser()
        pool = SlotPool(browser, max_slots=1, cfg=_TestSlotConfig(), log=_log)
        await pool.create(0)
        old_ctx = pool.get_context(0)
        await pool.replace(0, None)
        assert old_ctx._closed

    async def test_all_attempts_fail_raises(self, monkeypatch):
        """全部失败抛 SlotCreateError。"""
        monkeypatch.setattr("astrocrawl.browser._slot_pool.SLOT_CREATE_BACKOFF", 0.0)
        browser = FakeBrowser()
        pool = SlotPool(browser, max_slots=1, cfg=_TestSlotConfig(), log=_log)
        await pool.create(0)
        browser._raise_on_new_context = RuntimeError("fail")
        with pytest.raises(SlotCreateError):
            await pool.replace(0, None, max_attempts=2)

    async def test_closed_pool_rejects(self):
        """stop_accepting 后 replace 静默返回。"""
        browser = FakeBrowser()
        pool = SlotPool(browser, max_slots=1, cfg=_TestSlotConfig(), log=_log)
        pool.stop_accepting()
        await pool.replace(0, None)


# ═══════════════════════════════════════════════════════════════════════
# SlotPool.destroy / 查询
# ═══════════════════════════════════════════════════════════════════════


class TestSlotPoolDestroy:
    """SlotPool.destroy 槽位销毁。"""

    async def test_clears_resources(self, monkeypatch):
        """清空 context + page_pool + proxy_map。"""
        monkeypatch.setattr("astrocrawl.browser._slot_pool.SLOT_CREATE_BACKOFF", 0.0)
        browser = FakeBrowser()
        pool = SlotPool(browser, max_slots=1, cfg=_TestSlotConfig(), log=_log)
        await pool.create(0, proxy_url="http://p:8080")
        await pool.destroy(0)
        assert not pool.slot_is_valid(0)
        assert pool.get_context(0) is None
        assert pool.get_page_pool(0) is None
        assert pool.get_proxy_url(0) is None

    async def test_double_destroy_noop(self, monkeypatch):
        """重复 destroy 无操作, 不抛异常。"""
        monkeypatch.setattr("astrocrawl.browser._slot_pool.SLOT_CREATE_BACKOFF", 0.0)
        browser = FakeBrowser()
        pool = SlotPool(browser, max_slots=1, cfg=_TestSlotConfig(), log=_log)
        await pool.create(0)
        await pool.destroy(0)
        assert not pool.slot_is_valid(0)
        await pool.destroy(0)


class TestSlotPoolQuery:
    """SlotPool 查询方法——越界与空槽位边界。"""

    def test_get_context_out_of_range(self):
        """越界索引返回 None。"""
        browser = FakeBrowser()
        pool = SlotPool(browser, max_slots=1, cfg=_TestSlotConfig(), log=_log)
        assert pool.get_context(5) is None
        assert pool.get_context(-1) is None

    def test_get_page_pool_out_of_range(self):
        """越界索引返回 None。"""
        browser = FakeBrowser()
        pool = SlotPool(browser, max_slots=1, cfg=_TestSlotConfig(), log=_log)
        assert pool.get_page_pool(5) is None
        assert pool.get_page_pool(-1) is None

    def test_slot_is_valid_out_of_range(self):
        """越界索引返回 False，未创建槽位返回 False。"""
        browser = FakeBrowser()
        pool = SlotPool(browser, max_slots=2, cfg=_TestSlotConfig(), log=_log)
        assert not pool.slot_is_valid(5)
        assert not pool.slot_is_valid(0)

    def test_get_proxy_url_empty_slot(self):
        """未创建槽位返回 None。"""
        browser = FakeBrowser()
        pool = SlotPool(browser, max_slots=1, cfg=_TestSlotConfig(), log=_log)
        assert pool.get_proxy_url(0) is None

    def test_max_slots_and_browser_properties(self):
        """max_slots 和 browser 属性正确返回。"""
        browser = FakeBrowser()
        pool = SlotPool(browser, max_slots=3, cfg=_TestSlotConfig(), log=_log)
        assert pool.max_slots == 3
        assert pool.browser is browser


class TestSlotPoolConfig:
    """SlotPool._new_context 配置组装。"""

    async def test_new_context_kwargs(self, monkeypatch):
        """_new_context 完整 kwargs: proxy + auth + headers。"""
        monkeypatch.setattr("astrocrawl.browser._slot_pool.CONTEXT_CREATE_TIMEOUT", 30)
        cfg = _TestSlotConfig()
        cfg.auth_basic_user = "u"
        cfg.auth_basic_pass = "p"
        cfg.custom_headers = ["X-Custom: val"]
        browser = FakeBrowser()
        pool = SlotPool(browser, max_slots=1, cfg=cfg, log=_log)
        ctx = await pool._new_context("http://proxy:8080")
        kwargs = browser.last_context_kwargs
        assert kwargs["proxy"] == {"server": "http://proxy:8080"}
        assert kwargs["http_credentials"] == {"username": "u", "password": "p"}
        assert kwargs["extra_http_headers"] == {"X-Custom": "val"}
        assert kwargs["viewport"] == {"width": 1920, "height": 1080}
        assert kwargs["user_agent"] == "test-agent"
        assert ctx is not None


# ═══════════════════════════════════════════════════════════════════════
# SlotPool.close_all
# ═══════════════════════════════════════════════════════════════════════


class TestSlotPoolCloseAll:
    """SlotPool.close_all 全槽位关闭。"""

    async def test_closes_all_contexts_and_pools(self, monkeypatch):
        """所有上下文和页面池均被关闭，is_closed=True。"""
        monkeypatch.setattr("astrocrawl.browser._slot_pool.SLOT_CREATE_BACKOFF", 0.0)
        browser = FakeBrowser()
        pool = SlotPool(browser, max_slots=2, cfg=_TestSlotConfig(), log=_log)
        await pool.create(0)
        await pool.create(1)
        await pool.close_all()
        assert pool.is_closed is True
        assert pool.get_context(0) is not None  # 引用仍保留
        assert pool.get_context(0)._closed  # 但已关闭

    async def test_double_close_idempotent(self, monkeypatch):
        """重复 close_all 不抛异常。"""
        monkeypatch.setattr("astrocrawl.browser._slot_pool.SLOT_CREATE_BACKOFF", 0.0)
        browser = FakeBrowser()
        pool = SlotPool(browser, max_slots=1, cfg=_TestSlotConfig(), log=_log)
        await pool.create(0)
        await pool.close_all()
        await pool.close_all()

    async def test_close_all_with_empty_slots(self):
        """部分槽位为空时 close_all 不崩溃。"""
        browser = FakeBrowser()
        pool = SlotPool(browser, max_slots=3, cfg=_TestSlotConfig(), log=_log)
        await pool.close_all()
        assert pool.is_closed is True

    async def test_close_all_on_empty_pool(self):
        """空池 close_all 无操作。"""
        browser = FakeBrowser()
        pool = SlotPool(browser, max_slots=0, cfg=_TestSlotConfig(), log=_log)
        await pool.close_all()
        assert pool.is_closed is True


# ═══════════════════════════════════════════════════════════════════════
# SlotPool._load_cookies
# ═══════════════════════════════════════════════════════════════════════


class TestSlotPoolLoadCookies:
    """SlotPool._load_cookies Cookie 加载。"""

    async def test_load_valid_cookies(self, monkeypatch, tmp_path):
        """有效 JSON Cookie 文件 → ctx.add_cookies 被调用。"""
        monkeypatch.setattr("astrocrawl.browser._slot_pool.CONTEXT_CREATE_TIMEOUT", 30)
        cookies_file = tmp_path / "cookies.json"
        cookies_file.write_text('[{"name":"sid","value":"abc","domain":".example.com"}]')
        cfg = _TestSlotConfig()
        cfg.cookies_file = str(cookies_file)
        browser = FakeBrowser()
        pool = SlotPool(browser, max_slots=1, cfg=cfg, log=_log)
        ctx = FakeBrowserContext()
        await pool._load_cookies(ctx)
        assert len(ctx._cookies) == 1
        assert ctx._cookies[0]["name"] == "sid"

    async def test_cookie_file_not_found(self, monkeypatch, caplog):
        """Cookie 文件不存在 → warning 日志。"""
        monkeypatch.setattr("astrocrawl.browser._slot_pool.CONTEXT_CREATE_TIMEOUT", 30)
        cfg = _TestSlotConfig()
        cfg.cookies_file = "/nonexistent/cookies.json"
        browser = FakeBrowser()
        pool = SlotPool(browser, max_slots=1, cfg=cfg, log=_log)
        ctx = FakeBrowserContext()
        await pool._load_cookies(ctx)
        assert "event=cookie_file_invalid" in caplog.text

    async def test_cookie_file_not_json_extension(self, monkeypatch, tmp_path, caplog):
        """扩展名非 .json → warning。"""
        monkeypatch.setattr("astrocrawl.browser._slot_pool.CONTEXT_CREATE_TIMEOUT", 30)
        f = tmp_path / "cookies.txt"
        f.write_text("[]")
        cfg = _TestSlotConfig()
        cfg.cookies_file = str(f)
        browser = FakeBrowser()
        pool = SlotPool(browser, max_slots=1, cfg=cfg, log=_log)
        ctx = FakeBrowserContext()
        await pool._load_cookies(ctx)
        assert "event=cookie_file_invalid" in caplog.text

    async def test_cookie_invalid_json(self, monkeypatch, tmp_path, caplog):
        """无效 JSON → 异常被捕获。"""
        monkeypatch.setattr("astrocrawl.browser._slot_pool.CONTEXT_CREATE_TIMEOUT", 30)
        f = tmp_path / "cookies.json"
        f.write_text("not json")
        cfg = _TestSlotConfig()
        cfg.cookies_file = str(f)
        browser = FakeBrowser()
        pool = SlotPool(browser, max_slots=1, cfg=cfg, log=_log)
        ctx = FakeBrowserContext()
        await pool._load_cookies(ctx)
        assert "event=cookie_load_failed" in caplog.text

    async def test_cookie_non_array_raises(self, monkeypatch, tmp_path, caplog):
        """JSON 非数组 → warning。"""
        monkeypatch.setattr("astrocrawl.browser._slot_pool.CONTEXT_CREATE_TIMEOUT", 30)
        f = tmp_path / "cookies.json"
        f.write_text('{"key":"value"}')
        cfg = _TestSlotConfig()
        cfg.cookies_file = str(f)
        browser = FakeBrowser()
        pool = SlotPool(browser, max_slots=1, cfg=cfg, log=_log)
        ctx = FakeBrowserContext()
        await pool._load_cookies(ctx)
        assert "event=cookie_load_failed" in caplog.text

    async def test_cookie_invalid_entries_dropped(self, monkeypatch, tmp_path, caplog):
        """缺少 name/value 的条目被丢弃并记录 warning。"""
        monkeypatch.setattr("astrocrawl.browser._slot_pool.CONTEXT_CREATE_TIMEOUT", 30)
        f = tmp_path / "cookies.json"
        f.write_text('[{"name":"ok","value":"1"},{"bad":"entry"},{"name":"ok2","value":"2"}]')
        cfg = _TestSlotConfig()
        cfg.cookies_file = str(f)
        browser = FakeBrowser()
        pool = SlotPool(browser, max_slots=1, cfg=cfg, log=_log)
        ctx = FakeBrowserContext()
        await pool._load_cookies(ctx)
        assert "event=cookie_entries_dropped" in caplog.text
        assert len(ctx._cookies) == 2

    async def test_cookie_add_timeout(self, monkeypatch, tmp_path, caplog):
        """add_cookies 超时 → warning。"""
        monkeypatch.setattr("astrocrawl.browser._slot_pool.CONTEXT_CREATE_TIMEOUT", 30)
        f = tmp_path / "cookies.json"
        f.write_text('[{"name":"s","value":"v"}]')
        cfg = _TestSlotConfig()
        cfg.cookies_file = str(f)
        browser = FakeBrowser()
        pool = SlotPool(browser, max_slots=1, cfg=cfg, log=_log)

        class _TimeoutContext(FakeBrowserContext):
            async def add_cookies(self, cookies):
                raise TimeoutError("add_cookies timeout")

        await pool._load_cookies(_TimeoutContext())
        assert "event=cookie_load_failed" in caplog.text

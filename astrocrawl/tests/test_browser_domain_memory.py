"""测试: astrocrawl/browser/_domain_memory.py — DomainPathMemory TTL 记忆。

域名→路径双缓存: prefer_direct / prefer_proxy 成功路径被缓存以加速后续请求。
对标 Envoy endpoint pinning。
"""

from __future__ import annotations

import time

from astrocrawl.browser._domain_memory import DomainPathMemory

# ═══════════════════════════════════════════════════════════════════════
# DomainPathMemory.__init__
# ═══════════════════════════════════════════════════════════════════════


class TestDomainPathMemoryInit:
    def test_default_ttl_is_3600(self):
        mem = DomainPathMemory()
        assert mem._ttl == 3600.0

    def test_custom_ttl(self):
        mem = DomainPathMemory(ttl=600.0)
        assert mem._ttl == 600.0

    def test_initial_entries_empty(self):
        mem = DomainPathMemory()
        assert mem._entries == {}
        assert mem._direct_entries == {}


# ═══════════════════════════════════════════════════════════════════════
# remember + needs_proxy
# ═══════════════════════════════════════════════════════════════════════


class TestProxyMemory:
    """remember / needs_proxy 流程。"""

    def test_needs_proxy_false_for_unknown_domain(self):
        mem = DomainPathMemory()
        assert not mem.needs_proxy("example.com")

    def test_remember_then_needs_proxy(self):
        mem = DomainPathMemory()
        mem.remember("example.com")
        assert mem.needs_proxy("example.com")

    def test_needs_proxy_after_expiry(self):
        mem = DomainPathMemory(ttl=0.01)  # 10ms TTL
        mem.remember("example.com")
        time.sleep(0.02)
        assert not mem.needs_proxy("example.com")

    def test_needs_proxy_cleans_expired_on_check(self):
        mem = DomainPathMemory(ttl=0.01)
        mem.remember("example.com")
        time.sleep(0.02)
        mem.needs_proxy("example.com")
        assert "example.com" not in mem._entries

    def test_remember_custom_ttl(self):
        mem = DomainPathMemory()
        mem.remember("example.com", ttl=0.01)
        time.sleep(0.02)
        assert not mem.needs_proxy("example.com")

    def test_remember_overwrites_existing(self):
        mem = DomainPathMemory(ttl=3600.0)
        mem.remember("example.com")
        mem.remember("example.com", ttl=0.01)
        time.sleep(0.02)
        assert not mem.needs_proxy("example.com")

    def test_multiple_domains_independent(self):
        mem = DomainPathMemory()
        mem.remember("a.com")
        mem.remember("b.com")
        assert mem.needs_proxy("a.com")
        assert mem.needs_proxy("b.com")


# ═══════════════════════════════════════════════════════════════════════
# remember_direct + needs_direct
# ═══════════════════════════════════════════════════════════════════════


class TestDirectMemory:
    """remember_direct / needs_direct 流程。"""

    def test_needs_direct_false_for_unknown_domain(self):
        mem = DomainPathMemory()
        assert not mem.needs_direct("example.com")

    def test_remember_direct_then_needs_direct(self):
        mem = DomainPathMemory()
        mem.remember_direct("example.com")
        assert mem.needs_direct("example.com")

    def test_needs_direct_after_expiry(self):
        mem = DomainPathMemory(ttl=0.01)
        mem.remember_direct("example.com")
        time.sleep(0.02)
        assert not mem.needs_direct("example.com")

    def test_needs_direct_cleans_expired_on_check(self):
        mem = DomainPathMemory(ttl=0.01)
        mem.remember_direct("example.com")
        time.sleep(0.02)
        mem.needs_direct("example.com")
        assert "example.com" not in mem._direct_entries

    def test_remember_direct_custom_ttl(self):
        mem = DomainPathMemory()
        mem.remember_direct("example.com", ttl=0.01)
        time.sleep(0.02)
        assert not mem.needs_direct("example.com")


# ═══════════════════════════════════════════════════════════════════════
# proxy 和 direct 独立
# ═══════════════════════════════════════════════════════════════════════


class TestIndependentCaches:
    """proxy memory 和 direct memory 互不影响。"""

    def test_proxy_and_direct_are_independent(self):
        mem = DomainPathMemory()
        mem.remember("example.com")
        assert not mem.needs_direct("example.com")
        assert mem.needs_proxy("example.com")

    def test_direct_and_proxy_are_independent(self):
        mem = DomainPathMemory()
        mem.remember_direct("example.com")
        assert not mem.needs_proxy("example.com")
        assert mem.needs_direct("example.com")

    def test_both_can_be_active(self):
        mem = DomainPathMemory()
        mem.remember("example.com")
        mem.remember_direct("example.com")
        assert mem.needs_proxy("example.com")
        assert mem.needs_direct("example.com")


# ═══════════════════════════════════════════════════════════════════════
# forget
# ═══════════════════════════════════════════════════════════════════════


class TestForget:
    def test_forget_clears_proxy_entry(self):
        mem = DomainPathMemory()
        mem.remember("example.com")
        mem.forget("example.com")
        assert not mem.needs_proxy("example.com")

    def test_forget_clears_direct_entry(self):
        mem = DomainPathMemory()
        mem.remember_direct("example.com")
        mem.forget("example.com")
        assert not mem.needs_direct("example.com")

    def test_forget_clears_both(self):
        mem = DomainPathMemory()
        mem.remember("example.com")
        mem.remember_direct("example.com")
        mem.forget("example.com")
        assert not mem.needs_proxy("example.com")
        assert not mem.needs_direct("example.com")

    def test_forget_nonexistent_no_error(self):
        mem = DomainPathMemory()
        mem.forget("nonexistent.com")  # does not raise


# ═══════════════════════════════════════════════════════════════════════
# cleanup_expired
# ═══════════════════════════════════════════════════════════════════════


class TestCleanupExpired:
    def test_cleanup_removes_expired_proxy(self):
        mem = DomainPathMemory(ttl=0.01)
        mem.remember("expired.com")
        time.sleep(0.02)
        mem.cleanup_expired()
        assert len(mem._entries) == 0

    def test_cleanup_removes_expired_direct(self):
        mem = DomainPathMemory(ttl=0.01)
        mem.remember_direct("expired.com")
        time.sleep(0.02)
        mem.cleanup_expired()
        assert len(mem._direct_entries) == 0

    def test_cleanup_keeps_valid_entries(self):
        mem = DomainPathMemory(ttl=3600.0)
        mem.remember("valid.com")
        mem.remember_direct("valid.com")
        mem.cleanup_expired()
        assert len(mem._entries) == 1
        assert len(mem._direct_entries) == 1

    def test_cleanup_mixed(self):
        mem = DomainPathMemory(ttl=0.01)
        mem.remember("expired1.com")
        mem.remember("expired2.com")
        time.sleep(0.02)
        mem.remember("valid.com", ttl=3600.0)
        mem.cleanup_expired()
        assert len(mem._entries) == 1
        assert "valid.com" in mem._entries

    def test_cleanup_empty_is_noop(self):
        mem = DomainPathMemory()
        mem.cleanup_expired()
        assert mem._entries == {}
        assert mem._direct_entries == {}

"""测试：_constants.py — 模块完整性 + 关键常量值验证。

- TestAllIntegrity: __all__ 与模块顶层 UPPER_CASE 赋值完全同步
- TestSecurityCritical: 安全相关常量值回归锚点
- TestThresholdInvariants: 阈值间的数值关系不变式
- TestRegexPatterns: 正则模式可被 re2 编译
"""

from __future__ import annotations

import ast
import inspect

import astrocrawl._constants as c


class TestAllIntegrity:
    """__all__ 与模块顶层 UPPER_CASE 赋值完全同步。"""

    def test_all_in_sync(self):
        src = inspect.getsource(c)
        tree = ast.parse(src)
        defined = {
            target.id
            for node in ast.iter_child_nodes(tree)
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name) and target.id.isupper()
        }

        all_set = set(c.__all__)

        missing = defined - all_set
        extra = all_set - defined

        assert not missing, f"defined but missing from __all__: {sorted(missing)}"
        assert not extra, f"in __all__ but not defined: {sorted(extra)}"
        assert len(defined) == len(all_set)

    def test_all_count_regression(self):
        """__all__ 条目数回归锚点 — 新增常量有意识地更新此数字。"""
        assert len(c.__all__) == 90


class TestSecurityCritical:
    """安全相关常量的值回归 — 误删/误改后测试立即失败。"""

    def test_chromium_launch_args_contains_log_level_3(self):
        """--log-level=3 阻止 Chromium 调试输出泄漏代理凭据。"""
        assert "--log-level=3" in c.CHROMIUM_LAUNCH_ARGS

    def test_blocked_resource_types_exact_members(self):
        assert c.BLOCKED_RESOURCE_TYPES == frozenset({"image", "font", "media", "websocket", "prefetch", "manifest"})

    def test_download_extensions_is_frozenset(self):
        assert isinstance(c.DOWNLOAD_EXTENSIONS, frozenset)

    def test_download_extensions_min_size(self):
        """96 项回归锚点 — 防止静默成员丢失。"""
        assert len(c.DOWNLOAD_EXTENSIONS) >= 96

    def test_currency_symbols_contains_key(self):
        assert "¥" in c.CURRENCY_SYMBOLS
        assert "$" in c.CURRENCY_SYMBOLS
        assert "€" in c.CURRENCY_SYMBOLS


class TestThresholdInvariants:
    """阈值间的数值关系不变式 — 倒置将导致状态机行为错误。"""

    def test_proxy_cooldown_less_than_max(self):
        """PROXY_COOLDOWN 必须 < PROXY_COOLDOWN_MAX — 否则指数退避永不收敛。"""
        assert c.PROXY_COOLDOWN < c.PROXY_COOLDOWN_MAX

    def test_hard_cleanup_timeout_positive(self):
        assert c.HARD_CLEANUP_TIMEOUT > 0

    def test_worker_stuck_timeout_positive(self):
        assert c.WORKER_STUCK_TIMEOUT > 0


class TestRegexPatterns:
    """规则引擎正则模式可被 re2 编译 — 无效正则导致规则加载崩溃。"""

    def test_rule_name_pattern_compilable(self):
        import re2

        re2.compile(c.RULE_NAME_PATTERN)

    def test_attr_name_pattern_compilable(self):
        import re2

        re2.compile(c.ATTR_NAME_PATTERN)

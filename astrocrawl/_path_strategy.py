"""路径路由策略 — 主/回退双路径决策（ADR-0010 决策 11a）。

纯策略对象，零内部依赖（仅从 _types 导入 FetchErrorCategory 枚举）。
消费者按需取用：proxy/ 底座 + _path_strategy.py 自行组装回退逻辑。

对标: _retry_strategy.py 模式 — kernel 层独立文件，纯策略，消费者按需引入。
"""

from __future__ import annotations

from typing import Optional

from astrocrawl._types import FetchErrorCategory


class PathSwitch:
    """封装主路径与回退路径的通用策略。

    Attributes:
        main:          主抓取路径 ("proxy" | "direct")
        fallback:      主路径耗尽时的次路径 (None | "proxy" | "direct")
        trigger:       触发回退的条件 (None | "all_proxies_dead" | "connectivity_error")
        scope:         回退生效的层级 ("slot" | "url")
        on_exhausted:  次路径也耗尽时的行为 ("pause" | "fail")
    """

    # 四种 proxy_mode 的预置配置。for_mode() 据此查找模式 → 构造 PathSwitch。
    # 增删模式只需修改此 dict，_VALID_MODES 和 for_mode() 自动同步。
    _MODE_CONFIGS = {
        "direct_only": {"main": "direct"},
        "proxy_only": {"main": "proxy", "on_exhausted": "pause"},
        "prefer_direct": {"main": "direct", "fallback": "proxy", "trigger": "connectivity_error", "scope": "url"},
        "prefer_proxy": {"main": "proxy", "fallback": "direct", "trigger": "all_proxies_dead", "scope": "url"},
    }
    _VALID_MODES = frozenset(_MODE_CONFIGS.keys())

    def __init__(
        self,
        main: str,
        fallback: Optional[str] = None,
        trigger: Optional[str] = None,
        scope: str = "url",
        on_exhausted: str = "fail",
    ) -> None:
        if main not in ("proxy", "direct"):
            raise ValueError(f"main must be 'proxy' or 'direct', got {main!r}")
        if fallback not in (None, "proxy", "direct"):
            raise ValueError(f"fallback must be 'proxy', 'direct', or None, got {fallback!r}")
        if trigger not in (None, "all_proxies_dead", "connectivity_error"):
            raise ValueError(f"invalid trigger: {trigger!r}")
        if scope not in ("slot", "url"):
            raise ValueError(f"scope must be 'slot' or 'url', got {scope!r}")
        if on_exhausted not in ("pause", "fail"):
            raise ValueError(f"on_exhausted must be 'pause' or 'fail', got {on_exhausted!r}")

        self.main = main
        self.fallback = fallback
        self.trigger = trigger
        self.scope = scope
        self.on_exhausted = on_exhausted

    @classmethod
    def for_mode(cls, mode: str) -> "PathSwitch":
        """从 proxy_mode 配置字符串创建。

        direct_only:   单路径直连
        proxy_only:    单路径代理，全死暂停
        prefer_direct: 直连优先，连通性错误时升级代理
        prefer_proxy:  代理优先，全死时降级直连
        """
        config = cls._MODE_CONFIGS.get(mode)
        if config is None:
            raise ValueError(f"Unknown proxy_mode: {mode!r}, valid: {sorted(cls._VALID_MODES)}")
        return cls(**config)

    # ── 属性查询 ────────────────────────────────────────

    @property
    def main_is_proxy(self) -> bool:
        """新槽位默认是否使用代理。"""
        return self.main == "proxy"

    @property
    def has_fallback(self) -> bool:
        """是否存在次路径。"""
        return self.fallback is not None

    @property
    def fallback_is_proxy(self) -> bool:
        """次路径是否为代理 (prefer_direct 升级)。"""
        return self.fallback == "proxy"

    @property
    def fallback_is_direct(self) -> bool:
        """次路径是否为直连 (prefer_proxy 降级)。"""
        return self.fallback == "direct"

    # ── 决策方法 ────────────────────────────────────────

    def should_fallback_for_error(self, category: FetchErrorCategory) -> bool:
        """URL 级决策：给定的抓取错误是否应触发回退。

        仅 prefer_direct 需要：直连连通性错误 → 升级代理。
        """
        if not self.has_fallback or self.trigger != "connectivity_error":
            return False
        return category in _CONNECTIVITY_ERRORS

    def should_fallback_for_proxy_exhaustion(self, category: Optional[FetchErrorCategory] = None) -> bool:
        """prefer_proxy: 代理池耗尽时触发直连回退。

        Args:
            category: 错误分类 (可选)。传入时排除确定非代理的错误类别
                      (DNS/SSL/HTTP_4XX/redirects — 这些与代理状态无关)。
        """
        if not self.has_fallback or self.trigger != "all_proxies_dead":
            return False
        if category is not None and category in _NON_PROXY_ERRORS:
            return False
        return True


_CONNECTIVITY_ERRORS = frozenset(
    {
        FetchErrorCategory.CONNECTION_REFUSED,
        FetchErrorCategory.TIMEOUT,
        FetchErrorCategory.CONNECTION_RESET,
        FetchErrorCategory.GENERIC,
    }
)

# 确定非代理错误——prefer_proxy 在这些错误上回退到直连无意义
_NON_PROXY_ERRORS = frozenset(
    {
        FetchErrorCategory.DNS,
        FetchErrorCategory.SSL,
        FetchErrorCategory.HTTP_4XX,
        FetchErrorCategory.DOWNLOAD,
        FetchErrorCategory.TOO_MANY_REDIRECTS,
    }
)


__all__ = [
    "PathSwitch",
    "_CONNECTIVITY_ERRORS",
    "_NON_PROXY_ERRORS",
]

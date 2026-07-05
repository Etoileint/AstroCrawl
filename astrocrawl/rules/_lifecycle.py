"""规则生命周期管理 — 快照持有、热重载、加载降级、目录初始化。

RuleLifecycle 是 RuleSnapshot 的唯一所有者，所有读取者通过
get_snapshot() 获取当前快照的引用。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Optional

from astrocrawl._types import RuleSnapshot
from astrocrawl.rules._loader import build_rule_snapshot

if TYPE_CHECKING:
    from astrocrawl.config import CrawlerConfig

logger = logging.getLogger("astrocrawl.rules.lifecycle")


class RuleLifecycle:
    """规则快照的唯一持有者。

    支持热重载 (N22)、加载失败降级 (N49)、竞态保护 (N91)。
    """

    def __init__(
        self,
        cfg: CrawlerConfig,
        *,
        state_file: Path | None = None,
        extra_rules_dirs: list | None = None,
        rules_dirs_enabled: bool = True,
    ) -> None:
        self._cfg = cfg
        self._state_file = state_file
        self._extra_rules_dirs = extra_rules_dirs or []
        self._rules_dirs_enabled = rules_dirs_enabled
        self._snapshot: Optional[RuleSnapshot] = None
        self._last_load_ok: bool = False
        self._loaded_at: float = 0.0
        self._load_error: str = ""

    # ── 公开属性 ──────────────────────────────────────

    @property
    def snapshot(self) -> RuleSnapshot:
        """获取当前快照（首次调用时触发首次加载）。"""
        if self._snapshot is None:
            self.initial_load()
        return self._snapshot  # type: ignore[return-value]

    @property
    def last_load_ok(self) -> bool:
        return self._last_load_ok

    @property
    def load_error(self) -> str:
        return self._load_error

    @property
    def loaded_at(self) -> float:
        return self._loaded_at

    # ── 加载 ──────────────────────────────────────────

    def initial_load(self) -> RuleSnapshot:
        """首次加载规则——失败时降级为 default-only (N49)。"""
        try:
            self._snapshot = build_rule_snapshot(
                self._cfg,
                state_file=self._state_file,
                extra_rules_dirs=self._extra_rules_dirs,
                rules_dirs_enabled=self._rules_dirs_enabled,
            )
            self._last_load_ok = True
            self._load_error = ""
            self._loaded_at = time.time()
            enabled = len(self._snapshot.rules)
            total = len(self._snapshot.by_name) - 1
            logger.info("event=rules_loaded enabled=%d total=%d disabled=%d", enabled, total, total - enabled)
        except Exception as exc:
            logger.warning("event=rule_load_failed error=%s — 降级为 default", exc)
            self._snapshot = RuleSnapshot.default_only()
            self._last_load_ok = False
            self._load_error = str(exc)
            self._loaded_at = time.time()
            logger.info("event=rules_loaded enabled=0 total=0 disabled=0 reason=fallback_default")
        return self._snapshot

    def reload(self) -> RuleSnapshot:
        """热重载—原子替换快照 (N22)。

        加载新快照 → 校验 → 原子替换。旧快照 GC 时缓存自然回收。
        失败时保留旧快照 (N49)。未初始化时先执行首次加载。
        """
        old = self._snapshot

        try:
            new_snapshot = build_rule_snapshot(
                self._cfg,
                state_file=self._state_file,
                extra_rules_dirs=self._extra_rules_dirs,
                rules_dirs_enabled=self._rules_dirs_enabled,
            )
        except Exception as exc:
            logger.warning("event=rule_reload_failed error=%s — 保留旧快照", exc)
            self._last_load_ok = False
            self._load_error = str(exc)
            if self._snapshot is None:
                self._snapshot = RuleSnapshot.default_only()
            return self._snapshot  # N49

        # N22: 原子替换——新快照自带空 _match_cache，无需显式失效
        self._snapshot = new_snapshot
        self._last_load_ok = True
        self._load_error = ""
        self._loaded_at = time.time()

        old_enabled = len(old.rules) if old else 0
        new_enabled = len(new_snapshot.rules)
        new_total = len(new_snapshot.by_name) - 1
        logger.info(
            "event=rules_reloaded old_count=%d enabled=%d total=%d disabled=%d",
            old_enabled,
            new_enabled,
            new_total,
            new_total - new_enabled,
        )
        return new_snapshot

    # ── 生命周期 ──────────────────────────────────────

    def get_snapshot(self) -> RuleSnapshot:
        """获取当前快照引用（不触发加载）。"""
        if self._snapshot is None:
            return self.initial_load()
        return self._snapshot

    def get_health(self):
        """HealthChecked 协议 (S8/N64)：返回规则加载健康状态。"""
        from astrocrawl.health import Health

        if self._last_load_ok:
            count = len(self._snapshot.rules) if self._snapshot else 0
            return Health("UP", f"rules_loaded={count}")
        return Health("DEGRADED", self._load_error or "rules not loaded")


def setup_rule_directories(cfg: CrawlerConfig) -> Dict[str, Path]:
    """创建规则相关目录 (S13 权限)。

    Returns: {"user": user_dir, "cache": cache_dir}
    """
    home = Path.home()
    user_dir = home / ".astrocrawl" / "rules"
    cache_dir = (
        Path(cfg.rules_cache_dir).expanduser().resolve()
        if cfg.rules_cache_dir
        else home / ".astrocrawl" / "rules_cache"
    )

    for d in (user_dir, cache_dir):
        d.mkdir(parents=True, exist_ok=True)
        try:
            d.chmod(0o700)  # 目录需要 execute 位才能遍历
        except OSError:
            pass

    return {"user": user_dir, "cache": cache_dir}

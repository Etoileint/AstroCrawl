"""插件能力注册表 — COLLECTOR / CHAIN 调度与 (group, name) 唯一约束（ADR-0011 决策 3/12）。

COLLECTOR: 收集所有实现，返回 dict[str, T]（key="{group}/{name}"，无序）
CHAIN:     按 before/after 命名约束拓扑排序 + is_terminal 早期终止
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from astrobase import LogfmtLogger
from astroframe._errors import CapabilityConflictError
from astroframe._types import CapabilityRef, PluginRef, PluginStatus

log = LogfmtLogger("astroframe.registry")


class PluginRegistry:
    """全局能力注册表 — COLLECTOR/CHAIN 调度 + 唯一键冲突检测。

    对标 pytest hook 注册表（但无 tryfirst/trylast 排序）+
    Airflow provider registry（扁平命名 + 冲突检测）+
    Kubernetes Informer（无顺序语义的注册表）。

    COLLECTOR 返回 dict 而非 list：9 个使用场景中 8 个需要按名查找。
    """

    def __init__(self) -> None:
        # key = f"{group}/{name}" → CapabilityRef
        self._capabilities: dict[str, CapabilityRef] = {}
        # group → [key, ...]
        self._by_group: dict[str, list[str]] = defaultdict(list)
        # package_name → [key, ...]
        self._by_plugin: dict[str, list[str]] = defaultdict(list)
        # key → PluginRef (for status lookup)
        self._sources: dict[str, PluginRef] = {}
        # group → default chain order (tiebreaker for topological sort)
        self._chain_default_order: dict[str, tuple[str, ...]] = {}

    # ── 注册 ───────────────────────────────────────────────────────────────────

    def register(self, plugin_ref: PluginRef) -> int:
        """注册一个插件的所有 capability。

        仅注册 LOADED 状态的插件。PENDING_REVIEW / DISABLED / FAILED /
        INCOMPATIBLE 等状态的 capability 不注册。

        Returns:
            成功注册的 capability 数量。
        """
        if plugin_ref.status != PluginStatus.LOADED:
            log.debug(
                "plugin_registry_skip",
                package=plugin_ref.package_name,
                status=plugin_ref.status.value,
            )
            return 0

        count = 0
        for cap in plugin_ref.manifest.capabilities:
            key = cap.global_key

            # (group, name) 全局唯一冲突检测
            # global_key = f"{group}/{name}" → 同 key 冲突覆盖跨插件和同插件场景
            if key in self._capabilities:
                existing_pkg = self._find_package_for_key(key)
                raise CapabilityConflictError(
                    f"capability '{key}' 已由 '{existing_pkg}' 注册，"
                    f"'{plugin_ref.package_name}' 无法重复注册。"
                    f"请禁用其中一个或联系插件作者。"
                )

            self._capabilities[key] = cap
            self._by_group[cap.group].append(key)
            self._by_plugin[plugin_ref.package_name].append(key)
            self._sources[key] = plugin_ref
            count += 1

        log.debug(
            "plugin_registry_registered",
            package=plugin_ref.package_name,
            capability_count=count,
        )
        return count

    def unregister_plugin(self, package_name: str) -> int:
        """移除一个插件的所有 capability。返回移除数量。"""
        keys = self._by_plugin.pop(package_name, [])
        for key in keys:
            cap = self._capabilities.pop(key, None)
            self._sources.pop(key, None)
            if cap:
                self._by_group[cap.group].remove(key)

        if keys:
            log.debug("plugin_registry_unregistered", package=package_name, count=len(keys))
        return len(keys)

    # ── COLLECTOR 调度 ─────────────────────────────────────────────────────────

    def collect(self, group: str) -> dict[str, Any]:
        """COLLECTOR 调度——返回按 capability 唯一键索引的注册表。

        返回 dict[str, CapabilityRef]（key = f"{group}/{name}"），无序。
        消费者如需排序，自行 sorted(registry.values(), key=...)。

        这是底座提供的默认调度语义——对标 Kubernetes Informer（无序 watch 事件流）、
        Airflow provider registry（name → class dict）。
        """
        result: dict[str, Any] = {}
        for key in self._by_group.get(group, []):
            result[key] = self._capabilities.get(key)
        return result

    # ── CHAIN 调度 ─────────────────────────────────────────────────────────────

    def chain_order(self, group: str) -> list[CapabilityRef]:
        """CHAIN 调度——按 before/after 命名约束拓扑排序。

        返回有序的 CapabilityRef 列表。
        未声明约束的节点位于所有约束节点之后（保持插入顺序）。
        不可排序的循环约束 → 按插入顺序返回并记录 WARNING。
        """
        keys = self._by_group.get(group, [])
        if not keys:
            return []

        caps = [self._capabilities[k] for k in keys]
        if len(caps) <= 1:
            return caps

        # 构建邻接图：after 声明的节点 → 当前节点
        # "I run after X" → X 应在我之前执行
        cap_map = {c.name: c for c in caps}
        in_degree: dict[str, int] = {c.name: 0 for c in caps}
        adj: dict[str, list[str]] = {c.name: [] for c in caps}
        name_order: dict[str, int] = {c.name: i for i, c in enumerate(caps)}

        for cap in caps:
            constraints = cap.constraints or {}
            after = constraints.get("after")
            before = constraints.get("before")

            # "after: X" → X → cap (X runs before cap)
            if after and after in cap_map:
                adj.setdefault(after, []).append(cap.name)
                in_degree[cap.name] = in_degree.get(cap.name, 0) + 1

            # "before: Y" → cap → Y (cap runs before Y)
            if before and before in cap_map:
                adj.setdefault(cap.name, []).append(before)
                in_degree[before] = in_degree.get(before, 0) + 1

        # Kahn 拓扑排序，按原始插入顺序打破平局
        queue: list[str] = []
        for name, deg in in_degree.items():
            if deg == 0:
                queue.append(name)

        # 排序键：先按 default_order 位置，再按原始插入顺序
        default_order = self._chain_default_order.get(group, ())
        default_pos = {name: i for i, name in enumerate(default_order)}

        def _sort_key(name: str) -> tuple[int, int]:
            # 不在 default_order 中的排在末尾
            return (0, default_pos[name]) if name in default_pos else (1, name_order.get(name, 999))

        queue.sort(key=_sort_key)

        ordered: list[str] = []
        while queue:
            node = queue.pop(0)
            ordered.append(node)
            for neighbor in adj.get(node, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)
                    queue.sort(key=_sort_key)

        # 检测循环：未排序的节点按原始插入顺序追加
        if len(ordered) != len(caps):
            remaining = [c.name for c in caps if c.name not in ordered]
            remaining.sort(key=lambda n: name_order.get(n, 999))
            log.warning(
                "plugin_chain_cycle",
                group=group,
                unresolved=",".join(remaining),
            )
            ordered.extend(remaining)

        # 映射回 CapabilityRef
        return [cap_map[name] for name in ordered if name in cap_map]

    def set_chain_default_order(self, group: str, order: tuple[str, ...]) -> None:
        """设置 CHAIN group 的默认排序（tiebreaker）。仅内置 manifest 调用。"""
        self._chain_default_order[group] = order

    # ── 查询 ───────────────────────────────────────────────────────────────────

    def resolve(self, group: str, name: str) -> CapabilityRef | None:
        """按 (group, name) 查找单个 capability。"""
        key = f"{group}/{name}"
        return self._capabilities.get(key)

    def resolve_key(self, key: str) -> CapabilityRef | None:
        """按全局唯一键查找 capability。"""
        return self._capabilities.get(key)

    def list_group(self, group: str) -> list[CapabilityRef]:
        """列出指定 group 的所有 capability（按注册顺序）。"""
        return [self._capabilities[k] for k in self._by_group.get(group, []) if k in self._capabilities]

    def list_all(self) -> dict[str, CapabilityRef]:
        """返回所有已注册 capability 的 dict 快照。"""
        return dict(self._capabilities)

    def list_plugin_capabilities(self, package_name: str) -> list[CapabilityRef]:
        """列出指定插件的所有 capability。"""
        return [self._capabilities[k] for k in self._by_plugin.get(package_name, []) if k in self._capabilities]

    def has_group(self, group: str) -> bool:
        return group in self._by_group and len(self._by_group[group]) > 0

    @property
    def capability_count(self) -> int:
        return len(self._capabilities)

    @property
    def group_count(self) -> int:
        return len(self._by_group)

    # ── internal ───────────────────────────────────────────────────────────────

    def _find_package_for_key(self, key: str) -> str:
        """找到注册了指定 capability 的包名。"""
        for pkg, keys in self._by_plugin.items():
            if key in keys:
                return pkg
        return "unknown"

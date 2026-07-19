"""插件能力注册表测试 — COLLECTOR/CHAIN 调度 + 唯一键冲突检测 (issue #249)。"""

from __future__ import annotations

import pytest

from astroframe._errors import CapabilityConflictError
from astroframe._registry import PluginRegistry
from astroframe._types import CapabilityRef, PluginManifest, PluginRef, PluginStatus

# ── helpers ────────────────────────────────────────────────────────────────────


def _make_cap(**overrides: object) -> CapabilityRef:
    defaults: dict = {
        "group": "processor.chain",
        "name": "test",
        "display_name": "Test",
        "description": "Test capability",
        "factory": "test_pkg.processor:TestProcessor",
        "implements": "Processor",
    }
    defaults.update(overrides)
    return CapabilityRef(**defaults)


def _make_plugin_ref(
    name: str = "test-pkg",
    caps: list[CapabilityRef] | None = None,
    status: PluginStatus = PluginStatus.LOADED,
    requires_plugins: dict | None = None,
) -> PluginRef:
    manifest = PluginManifest(
        manifest_version=1,
        name=name,
        requires_engine=">=0.2",
        capabilities=tuple(caps or []),
        requires_plugins=requires_plugins or {},
    )
    return PluginRef(
        manifest=manifest,
        status=status,
        package_name=name,
        version="1.0.0",
    )


# ── 注册 ───────────────────────────────────────────────────────────────────────


def test_register_single_capability():
    registry = PluginRegistry()
    cap = _make_cap(group="processor.chain", name="pdf-detect")
    ref = _make_plugin_ref("astrocrawl-pdf", [cap])
    count = registry.register(ref)
    assert count == 1
    assert registry.capability_count == 1


def test_register_multiple_capabilities():
    registry = PluginRegistry()
    caps = [
        _make_cap(group="processor.chain", name="cap-a"),
        _make_cap(group="rules.transform", name="cap-b"),
        _make_cap(group="output.exporter", name="cap-c"),
    ]
    ref = _make_plugin_ref("multi-pkg", caps)
    count = registry.register(ref)
    assert count == 3
    assert registry.capability_count == 3
    assert registry.group_count == 3


def test_register_skip_non_loaded():
    registry = PluginRegistry()
    cap = _make_cap()
    ref = _make_plugin_ref("test-pkg", [cap], status=PluginStatus.PENDING_REVIEW)
    count = registry.register(ref)
    assert count == 0
    assert registry.capability_count == 0


# ── 唯一键冲突 ─────────────────────────────────────────────────────────────────


def test_unique_key_conflict_across_plugins():
    """两个不同插件注册同名 (group, name) → 后加载被拒绝"""
    registry = PluginRegistry()
    cap1 = _make_cap(group="processor.chain", name="pdf-detect")
    ref1 = _make_plugin_ref("astrocrawl-pdf", [cap1])
    registry.register(ref1)

    cap2 = _make_cap(group="processor.chain", name="pdf-detect")
    ref2 = _make_plugin_ref("astrocrawl-pdf-v2", [cap2])
    with pytest.raises(CapabilityConflictError, match="已由"):
        registry.register(ref2)


def test_unique_key_conflict_same_plugin():
    """同一插件内两个 capability 重名 → 第一个注册成功，第二个触发冲突。
    这是因为每个 cap 的 global_key 相同，第二个 cap 的 key 已由第一个占用。"""
    registry = PluginRegistry()
    caps = [
        _make_cap(group="processor.chain", name="dupe"),
        _make_cap(group="processor.chain", name="dupe"),
    ]
    ref = _make_plugin_ref("bad-pkg", caps)
    with pytest.raises(CapabilityConflictError, match="已由"):
        registry.register(ref)


def test_conflict_error_contains_both_package_names():
    registry = PluginRegistry()
    cap1 = _make_cap(group="processor.chain", name="pdf-detect")
    ref1 = _make_plugin_ref("astrocrawl-pdf", [cap1])
    registry.register(ref1)

    cap2 = _make_cap(group="processor.chain", name="pdf-detect")
    ref2 = _make_plugin_ref("astrocrawl-pdf-v2", [cap2])
    with pytest.raises(CapabilityConflictError) as exc_info:
        registry.register(ref2)
    msg = str(exc_info.value)
    assert "astrocrawl-pdf" in msg
    assert "astrocrawl-pdf-v2" in msg


# ── COLLECTOR 调度 ─────────────────────────────────────────────────────────────


def test_collector_returns_dict():
    registry = PluginRegistry()
    caps = [
        _make_cap(group="ai.provider", name="openai", implements="ChatProvider"),
        _make_cap(group="ai.provider", name="anthropic", implements="ChatProvider"),
    ]
    ref = _make_plugin_ref("ai-pkg", caps)
    registry.register(ref)

    collected = registry.collect("ai.provider")
    assert isinstance(collected, dict)
    assert len(collected) == 2
    assert "ai.provider/openai" in collected
    assert "ai.provider/anthropic" in collected


def test_collector_returns_capability_ref_values():
    registry = PluginRegistry()
    cap = _make_cap(group="output.exporter", name="json", implements="Exporter")
    ref = _make_plugin_ref("export-pkg", [cap])
    registry.register(ref)

    collected = registry.collect("output.exporter")
    val = collected["output.exporter/json"]
    assert isinstance(val, CapabilityRef)
    assert val.name == "json"


def test_collector_empty_for_unknown_group():
    registry = PluginRegistry()
    assert registry.collect("nonexistent.group") == {}


# ── CHAIN 调度 ─────────────────────────────────────────────────────────────────


def test_chain_order_single_capability():
    registry = PluginRegistry()
    cap = _make_cap(group="processor.chain", name="only")
    ref = _make_plugin_ref("test-pkg", [cap])
    registry.register(ref)

    ordered = registry.chain_order("processor.chain")
    assert len(ordered) == 1
    assert ordered[0].name == "only"


def test_chain_order_no_constraints_preserves_insertion():
    registry = PluginRegistry()
    for i, name in enumerate(["c", "a", "b"]):
        cap = _make_cap(group="processor.chain", name=name, factory=f"pkg.mod:Proc{i}")
        ref = _make_plugin_ref(f"pkg-{i}", [cap])
        registry.register(ref)

    ordered = registry.chain_order("processor.chain")
    names = [c.name for c in ordered]
    assert names == ["c", "a", "b"]


def test_chain_order_with_before_after():
    """验证 before/after 拓扑排序"""
    registry = PluginRegistry()
    caps = [
        _make_cap(group="processor.chain", name="robots", constraints={"before": "fetch"}),
        _make_cap(group="processor.chain", name="fetch", constraints={"after": "robots", "before": "extract"}),
        _make_cap(group="processor.chain", name="extract", constraints={"after": "fetch"}),
        _make_cap(group="processor.chain", name="output", constraints={"after": "extract"}),
    ]
    ref = _make_plugin_ref("chain-pkg", caps)
    registry.register(ref)

    ordered = registry.chain_order("processor.chain")
    names = [c.name for c in ordered]
    # robots before fetch before extract before output
    assert names.index("robots") < names.index("fetch")
    assert names.index("fetch") < names.index("extract")
    assert names.index("extract") < names.index("output")


def test_chain_order_cycle_graceful():
    """循环约束 → WARNING + 按插入顺序返回"""
    registry = PluginRegistry()
    caps = [
        _make_cap(group="processor.chain", name="a", constraints={"after": "b"}),
        _make_cap(group="processor.chain", name="b", constraints={"after": "a"}),
    ]
    ref = _make_plugin_ref("cycle-pkg", caps)
    registry.register(ref)

    ordered = registry.chain_order("processor.chain")
    # 不抛异常，返回所有 2 个
    assert len(ordered) == 2


# ── 查询 ───────────────────────────────────────────────────────────────────────


def test_resolve_by_group_and_name():
    registry = PluginRegistry()
    cap = _make_cap(group="health.check", name="db-check")
    ref = _make_plugin_ref("health-pkg", [cap])
    registry.register(ref)

    found = registry.resolve("health.check", "db-check")
    assert found is not None
    assert found.group == "health.check"
    assert found.name == "db-check"


def test_resolve_missing_returns_none():
    registry = PluginRegistry()
    assert registry.resolve("x", "y") is None


def test_list_group():
    registry = PluginRegistry()
    caps = [
        _make_cap(group="content.extractor", name="html"),
        _make_cap(group="content.extractor", name="jsonld"),
    ]
    ref = _make_plugin_ref("extractor-pkg", caps)
    registry.register(ref)

    group_caps = registry.list_group("content.extractor")
    assert len(group_caps) == 2
    names = {c.name for c in group_caps}
    assert names == {"html", "jsonld"}


def test_list_all_returns_snapshot():
    registry = PluginRegistry()
    cap = _make_cap()
    ref = _make_plugin_ref("test-pkg", [cap])
    registry.register(ref)

    snapshot = registry.list_all()
    assert len(snapshot) == 1
    # snapshot 是副本，修改不影响注册表
    snapshot.clear()
    assert registry.capability_count == 1


def test_unregister_plugin():
    registry = PluginRegistry()
    cap = _make_cap()
    ref = _make_plugin_ref("test-pkg", [cap])
    registry.register(ref)
    assert registry.capability_count == 1

    removed = registry.unregister_plugin("test-pkg")
    assert removed == 1
    assert registry.capability_count == 0


# ── 多插件注册 ─────────────────────────────────────────────────────────────────


def test_multiple_plugins_different_groups():
    registry = PluginRegistry()
    ref1 = _make_plugin_ref("pkg-a", [_make_cap(group="processor.chain", name="proc-a")])
    ref2 = _make_plugin_ref("pkg-b", [_make_cap(group="ai.provider", name="ai-b", implements="ChatProvider")])
    ref3 = _make_plugin_ref("pkg-c", [_make_cap(group="output.exporter", name="exp-c", implements="Exporter")])

    registry.register(ref1)
    registry.register(ref2)
    registry.register(ref3)

    assert registry.capability_count == 3
    assert registry.group_count == 3


def test_same_group_different_names():
    registry = PluginRegistry()
    ref1 = _make_plugin_ref("pkg-a", [_make_cap(group="processor.chain", name="robots")])
    ref2 = _make_plugin_ref("pkg-b", [_make_cap(group="processor.chain", name="fetch")])
    ref3 = _make_plugin_ref("pkg-c", [_make_cap(group="processor.chain", name="extract")])

    registry.register(ref1)
    registry.register(ref2)
    registry.register(ref3)

    assert registry.capability_count == 3
    group_caps = registry.list_group("processor.chain")
    assert len(group_caps) == 3

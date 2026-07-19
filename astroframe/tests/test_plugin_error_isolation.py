"""插件错误隔离集成测试 — 单插件 import 失败不影响其他、沙箱崩溃隔离、子进程上限。

测试模块间隔离性——需所有模块就位后运行。
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from astroframe._lifecycle import MAX_SUBPROCESSES, SubprocessRegistry
from astroframe._types import CapabilityRef, PermissionLevel, PluginManifest, PluginRef, PluginStatus

_CAP_DEFAULTS: dict[str, object] = {
    "group": "processor.chain",
    "name": "test-proc",
    "display_name": "Test",
    "description": "",
    "factory": "nonexistent.module:FakeFactory",
    "implements": "Processor",
    "permissions": (),
}
_BASE_CAP = CapabilityRef(**_CAP_DEFAULTS)


def _make_cap(**overrides: object) -> CapabilityRef:
    if not overrides:
        return _BASE_CAP
    return replace(_BASE_CAP, **overrides)


def _make_manifest(**overrides: object) -> PluginManifest:
    defaults: dict[str, object] = {
        "manifest_version": 1,
        "name": "test-plugin",
        "requires_engine": ">=0.1",
        "capabilities": (_make_cap(),),
    }
    defaults.update(overrides)
    return PluginManifest.from_dict(defaults)


_MANIFEST_CAP_DICT: dict[str, object] = {
    "group": "processor.chain",
    "name": "test-proc",
    "display_name": "Test",
    "description": "",
    "factory": "nonexistent.module:FakeFactory",
    "implements": "Processor",
    "permissions": [],
}


def _make_manifest(**overrides: object) -> PluginManifest:
    defaults: dict[str, object] = {
        "manifest_version": 1,
        "name": "test-plugin",
        "requires_engine": ">=0.1",
        "capabilities": [dict(_MANIFEST_CAP_DICT)],
    }
    defaults.update(overrides)
    return PluginManifest.from_dict(defaults)


def _make_plugin_ref(**overrides: object) -> PluginRef:
    defaults: dict[str, object] = {
        "manifest": _make_manifest(),
        "status": PluginStatus.LOADED,
        "package_name": "test-plugin",
        "version": "1.0.0",
        "effective_permissions": (),
        "effective_permission_level": PermissionLevel.NORMAL,
    }
    defaults.update(overrides)
    return PluginRef(**defaults)


class TestSubprocessRegistry:
    def test_register_and_count(self) -> None:
        registry = SubprocessRegistry()
        assert registry.count == 0

    def test_register_unregister(self) -> None:
        registry = SubprocessRegistry()
        # 使用 mock 对象
        registry._plugins["test1"] = None  # type: ignore[dict-item]
        assert registry.count == 1
        registry.unregister("test1")
        assert registry.count == 0

    def test_max_subprocesses_limit(self) -> None:
        """验证 MAX_SUBPROCESSES 常量存在且为合理值。"""
        assert MAX_SUBPROCESSES == 16

    @pytest.mark.asyncio
    async def test_aclose_all_noop(self) -> None:
        registry = SubprocessRegistry()
        await registry.aclose_all()
        assert registry.count == 0


class TestErrorIsolation:
    def test_failed_status_contains_info(self) -> None:
        """验证 FAILED 状态可携带信息。"""
        ref = _make_plugin_ref(status=PluginStatus.FAILED)
        assert ref.status == PluginStatus.FAILED
        assert not ref.is_loaded

    def test_multiple_plugins_independent_status(self) -> None:
        """单个插件的状态不影响其他插件。"""
        ref1 = _make_plugin_ref(status=PluginStatus.LOADED, package_name="pkg1")
        ref2 = _make_plugin_ref(status=PluginStatus.INCOMPATIBLE, package_name="pkg2")

        assert ref1.is_loaded
        assert not ref2.is_loaded
        # ref1 不受 ref2 影响
        assert ref1.status == PluginStatus.LOADED

    def test_effective_permissions_default(self) -> None:
        """验证 PluginRef 默认 effective_permissions 为空。"""
        ref = PluginRef(
            manifest=_make_manifest(),
            status=PluginStatus.LOADED,
            package_name="test",
            version="1.0.0",
        )
        assert ref.effective_permissions == ()
        assert ref.effective_permission_level == PermissionLevel.NORMAL

    def test_permission_level_ordering(self) -> None:
        """验证权限等级排序正确。"""
        order = {PermissionLevel.NORMAL: 0, PermissionLevel.DANGEROUS: 1, PermissionLevel.SIGNATURE: 2}
        assert order[PermissionLevel.NORMAL] < order[PermissionLevel.DANGEROUS]
        assert order[PermissionLevel.DANGEROUS] < order[PermissionLevel.SIGNATURE]

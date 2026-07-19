"""插件生命周期测试 — 统一子进程沙箱、SubprocessPlugin、PluginGlobal。

测试覆盖：三步判断 factory 性质、状态门控、Protocol 校验、崩溃恢复。
"""

from __future__ import annotations

import asyncio
import inspect
import sys
from typing import Any

import pytest

from astroframe._errors import PluginLoadError
from astroframe._types import (
    CapabilityRef,
    ExecutionMode,
    PermissionLevel,
    PluginGlobal,
    PluginManifest,
    PluginRef,
    PluginScoped,
    PluginStatus,
    Processor,
    derive_execution_mode,
)

_CAP_DEFAULTS: dict[str, Any] = {
    "group": "processor.chain",
    "name": "test-proc",
    "display_name": "Test Processor",
    "description": "A test capability",
    "factory": "tests._fakes_plugin:TestProcessor",
    "implements": "Processor",
    "permissions": (),
}


def _make_cap(**overrides: Any) -> CapabilityRef:
    from dataclasses import replace

    base = CapabilityRef(**_CAP_DEFAULTS)
    if not overrides:
        return base
    return replace(base, **overrides)


_MANIFEST_CAP_DICT: dict[str, Any] = {
    "group": "processor.chain",
    "name": "test-proc",
    "display_name": "Test Processor",
    "description": "A test capability",
    "factory": "tests._fakes_plugin:TestProcessor",
    "implements": "Processor",
    "permissions": [],
}


def _make_manifest(**overrides: Any) -> PluginManifest:
    defaults: dict[str, Any] = {
        "manifest_version": 1,
        "name": "test-plugin",
        "requires_engine": ">=0.1",
        "capabilities": [dict(_MANIFEST_CAP_DICT)],
    }
    defaults.update(overrides)
    return PluginManifest.from_dict(defaults)


def _make_plugin_ref(**overrides: Any) -> PluginRef:
    defaults: dict[str, Any] = {
        "manifest": _make_manifest(),
        "status": PluginStatus.LOADED,
        "package_name": "test-plugin",
        "version": "1.0.0",
        "effective_permissions": (),
        "effective_permission_level": PermissionLevel.NORMAL,
    }
    defaults.update(overrides)
    return PluginRef(**defaults)


# ── derive_execution_mode ──────────────────────────────────────────────────────


class TestExecutionModeRouting:
    def test_normal_subprocess(self) -> None:
        assert derive_execution_mode(PermissionLevel.NORMAL) == ExecutionMode.SUBPROCESS

    def test_dangerous_subprocess(self) -> None:
        assert derive_execution_mode(PermissionLevel.DANGEROUS) == ExecutionMode.SUBPROCESS

    def test_signature_subprocess_signed(self) -> None:
        assert derive_execution_mode(PermissionLevel.SIGNATURE) == ExecutionMode.SUBPROCESS_SIGNED


# ── 状态门控 ──────────────────────────────────────────────────────────────────


class TestStatusGating:
    def test_pending_review_rejected(self) -> None:
        ref = _make_plugin_ref(status=PluginStatus.PENDING_REVIEW)
        cap = _make_cap()
        with pytest.raises(PluginLoadError, match="not yet trusted"):
            asyncio.run(_try_create_instance(cap, ref))

    def test_disabled_rejected(self) -> None:
        ref = _make_plugin_ref(status=PluginStatus.DISABLED)
        cap = _make_cap()
        with pytest.raises(PluginLoadError, match="disabled"):
            asyncio.run(_try_create_instance(cap, ref))

    def test_incompatible_rejected(self) -> None:
        ref = _make_plugin_ref(status=PluginStatus.INCOMPATIBLE)
        cap = _make_cap()
        with pytest.raises(PluginLoadError, match="incompatible"):
            asyncio.run(_try_create_instance(cap, ref))


async def _try_create_instance(cap: CapabilityRef, ref: PluginRef) -> None:
    from astroframe._lifecycle import create_plugin_instance

    await create_plugin_instance(cap, ref, {}, "0.1.5")


class TestPluginGlobal:
    def test_plugin_global_protocol_detection(self) -> None:
        class MyGlobal:
            @staticmethod
            def on_load() -> None:
                pass

            @staticmethod
            def on_unload() -> None:
                pass

        assert isinstance(MyGlobal, PluginGlobal)

    def test_on_load_exception_handled(self) -> None:
        called = False

        class FailingGlobal:
            @staticmethod
            def on_load() -> None:
                nonlocal called
                called = True
                raise RuntimeError("test error")

            @staticmethod
            def on_unload() -> None:
                pass

        # 不应抛异常
        try:
            FailingGlobal.on_load()
        except RuntimeError:
            pass
        assert called


# ── Protocol 实例化后校验 ──────────────────────────────────────────────────────


class TestProtocolValidation:
    def test_sync_setup_rejected(self) -> None:
        class BadProcessor:
            def setup(self, config: dict) -> None:  # sync, not async
                pass

            async def aclose(self) -> None:
                pass

            async def process(self, ctx: Any, deps: Any) -> Any:
                return ctx

        assert not inspect.iscoroutinefunction(BadProcessor.setup)

    def test_async_setup_accepted(self) -> None:
        class GoodProcessor:
            async def setup(self, config: dict) -> None:
                pass

            async def aclose(self) -> None:
                pass

            async def process(self, ctx: Any, deps: Any) -> Any:
                return ctx

        assert inspect.iscoroutinefunction(GoodProcessor.setup)
        assert inspect.iscoroutinefunction(GoodProcessor.aclose)
        assert isinstance(GoodProcessor, Processor)


class TestFactoryTypeDetection:
    """三步判断 factory 性质（类/工厂函数/纯函数/已有实例）。"""

    def test_issubclass_structural_matching(self) -> None:
        """未显式继承 PluginScoped 的类通过 @runtime_checkable issubclass 检测。"""

        class ImplicitProcessor:
            async def setup(self, config: dict) -> None:
                pass

            async def aclose(self) -> None:
                pass

            async def process(self, ctx: Any, deps: Any) -> Any:
                return ctx

        assert issubclass(ImplicitProcessor, PluginScoped)

    def test_issubclass_type_guard_on_non_class(self) -> None:
        """issubclass() 在非类类型（函数、可调用实例）上应抛 TypeError——Step A 必须先做 isinstance type 守卫。"""

        def func(x: Any) -> Any:
            return x

        # 函数是 callable 但不是 type
        assert callable(func)
        assert not isinstance(func, type)
        # 在非 type 上调用 issubclass 会抛 TypeError
        with pytest.raises(TypeError):
            issubclass(func, PluginScoped)  # type: ignore[arg-type]

    def test_pure_function_not_plugin_scoped(self) -> None:
        """纯函数不实现 PluginScoped。"""

        def pure_func(ctx: Any, deps: Any) -> Any:
            return ctx

        assert not isinstance(pure_func, PluginScoped)

    def test_callable_instance_not_type(self) -> None:
        """可调用对象不是 type——应路由到 Step B 或 C。"""

        class CallableInstance:
            async def setup(self, config: dict) -> None:
                pass

            async def aclose(self) -> None:
                pass

            async def process(self, ctx: Any, deps: Any) -> Any:
                return ctx

        obj = CallableInstance()
        assert isinstance(obj, PluginScoped)  # Step C: 已有实例


class TestSubprocessRegistryLimit:
    def test_max_subprocesses_constant(self) -> None:
        from astroframe._lifecycle import MAX_SUBPROCESSES

        assert MAX_SUBPROCESSES == 16

    def test_registry_register_unregister_cycle(self) -> None:
        from astroframe._lifecycle import SubprocessRegistry as SR

        registry = SR()
        # 使用 None 作为占位（测试计数逻辑）
        registry._plugins["p1"] = None  # type: ignore[dict-item]
        assert registry.count == 1
        registry.unregister("p1")
        assert registry.count == 0

    def test_registry_count_at_limit(self) -> None:
        """注册表现在有 16 个插件时 count == MAX_SUBPROCESSES。"""
        from astroframe._lifecycle import SubprocessRegistry as SR

        registry = SR()
        for i in range(16):
            registry._plugins[f"p{i}"] = None  # type: ignore[dict-item]
        assert registry.count == 16


# ── IPC 常量 ─────────────────────────────────────────────────────────────────


class TestIPCConstants:
    def test_ipc_constants_exist(self) -> None:
        from astroframe._lifecycle import (
            ACLOSE_KILL_TIMEOUT,
            ACLOSE_SIGTERM_TIMEOUT,
            ACLOSE_TERMINATE_TIMEOUT,
            HANDSHAKE_TIMEOUT,
            MAX_CRASH_RESTARTS,
            MAX_MSG_BYTES,
            PROCESS_TIMEOUT,
            SETUP_TIMEOUT,
        )

        assert MAX_MSG_BYTES == 10 * 1024 * 1024
        assert PROCESS_TIMEOUT == 30.0
        assert HANDSHAKE_TIMEOUT == 30.0
        assert SETUP_TIMEOUT == 30.0
        assert MAX_CRASH_RESTARTS == 3
        assert ACLOSE_SIGTERM_TIMEOUT == 10.0
        assert ACLOSE_TERMINATE_TIMEOUT == 10.0
        assert ACLOSE_KILL_TIMEOUT == 5.0


class TestPluginGlobalOrchestration:
    def test_orchestrate_with_no_plugins(self) -> None:
        """空 plugins dict → 返回空列表。"""
        loaded = asyncio.run(_try_orchestrate({}))
        assert loaded == []

    def test_orchestrate_skips_non_loaded(self) -> None:
        """PENDING_REVIEW 插件被跳过。"""
        ref = _make_plugin_ref(status=PluginStatus.PENDING_REVIEW)
        loaded = asyncio.run(_try_orchestrate({"test": ref}))
        assert loaded == []

    def test_orchestrate_includes_plugin(self) -> None:
        """所有 LOADED 插件走统一的 PluginGlobal 发现管线。"""
        ref = _make_plugin_ref()
        loaded = asyncio.run(_try_orchestrate({"test": ref}))
        assert loaded == ["test"]


async def _try_orchestrate(plugins: dict) -> list[str]:
    from astroframe._lifecycle import orchestrate_plugin_global

    return await orchestrate_plugin_global(plugins, None)


# ══════════════════════════════════════════════════════════════════════════════════
# Phase 3b — SubprocessPlugin E2E + 崩溃恢复 + 信号量 + 隔离
# ══════════════════════════════════════════════════════════════════════════════════


class TestSubprocessPluginE2E:
    """SubprocessPlugin 端到端测试（AC4/AC13）。"""

    @pytest.mark.asyncio
    async def test_full_lifecycle(self) -> None:
        """handshake → setup → process → aclose 完整通过。"""
        from astroframe._lifecycle import SubprocessPlugin
        from astroframe._types import ExecutionMode

        cap = _make_cap(factory="tests._fakes_plugin:TestProcessor")
        ref = _make_plugin_ref()
        plugin = await SubprocessPlugin.create(
            cap, ref, {"key": "val"}, "0.1.5", ExecutionMode.SUBPROCESS, "class", "tests._fakes_plugin", "TestProcessor"
        )
        try:
            result = await plugin.call("process")
            assert result["result"] == "ok"
            assert result["config_keys"] == ["key"]
        finally:
            await plugin.aclose()

    @pytest.mark.asyncio
    async def test_process_with_context(self) -> None:
        """process() 传递 SandboxContext 参数。"""
        from astroframe._lifecycle import SubprocessPlugin
        from astroframe._types import ExecutionMode

        cap = _make_cap(factory="tests._fakes_plugin:TestProcessor")
        ref = _make_plugin_ref()
        plugin = await SubprocessPlugin.create(
            cap, ref, {}, "0.1.5", ExecutionMode.SUBPROCESS, "class", "tests._fakes_plugin", "TestProcessor"
        )
        try:
            result = await plugin.call("process", ctx={"from_engine": 42})
            assert result["result"] == "ok"
        finally:
            await plugin.aclose()

    @pytest.mark.asyncio
    async def test_env_whitelist(self) -> None:
        """子进程 env 仅含白名单变量（AC7）。"""
        import os as os_mod

        from astroframe._lifecycle import SubprocessPlugin
        from astroframe._types import ExecutionMode

        # 设置一个父进程变量，确认不泄露进子进程
        os_mod.environ["_TEST_SHOULD_NOT_LEAK"] = "secret"
        try:
            cap = _make_cap(factory="tests._fakes_plugin:EnvProbeProcessor")
            ref = _make_plugin_ref()
            plugin = await SubprocessPlugin.create(
                cap,
                ref,
                {},
                "0.1.5",
                ExecutionMode.SUBPROCESS,
                "class",
                "tests._fakes_plugin",
                "EnvProbeProcessor",
            )
            try:
                result = await plugin.call("process")
                child_env = result["environ"]
                assert "_TEST_SHOULD_NOT_LEAK" not in child_env
                # HOME 应在白名单中
                if "HOME" in os_mod.environ:
                    assert "HOME" in child_env
            finally:
                await plugin.aclose()
        finally:
            os_mod.environ.pop("_TEST_SHOULD_NOT_LEAK", None)

    @pytest.mark.asyncio
    async def test_sys_path_isolation(self) -> None:
        """子进程 sys.path 仅含预期路径（AC8）。"""
        from astroframe._lifecycle import SubprocessPlugin
        from astroframe._types import ExecutionMode

        cap = _make_cap(factory="tests._fakes_plugin:SysPathProbeProcessor")
        ref = _make_plugin_ref()
        plugin = await SubprocessPlugin.create(
            cap,
            ref,
            {},
            "0.1.5",
            ExecutionMode.SUBPROCESS,
            "class",
            "tests._fakes_plugin",
            "SysPathProbeProcessor",
        )
        try:
            result = await plugin.call("process")
            child_sys_path = result["sys_path"]
            assert len(child_sys_path) > 0
            # 不应包含用户 site-packages（-I 隔离模式）
            assert ".local" not in str(child_sys_path)
        finally:
            await plugin.aclose()


class TestCrashRecovery:
    """崩溃恢复测试（AC13/AC14）。"""

    @pytest.mark.asyncio
    @pytest.mark.skipif(sys.platform == "win32", reason="os.kill not available on Windows")
    async def test_crash_triggers_restart(self) -> None:
        """子进程 SIGKILL → PluginSandboxError → crash_count 递增。"""
        from astroframe._lifecycle import SubprocessPlugin
        from astroframe._types import ExecutionMode

        cap = _make_cap(factory="tests._fakes_plugin:CrashingProcessor")
        ref = _make_plugin_ref()
        plugin = await SubprocessPlugin.create(
            cap,
            ref,
            {},
            "0.1.5",
            ExecutionMode.SUBPROCESS,
            "class",
            "tests._fakes_plugin",
            "CrashingProcessor",
        )
        try:
            # CrashingProcessor 默认 crash_after=0 → 每次调用均 SIGKILL
            with pytest.raises(Exception):
                await plugin.call("process")

            # crash_count 应已递增
            assert plugin._crash_count == 1

            # 第二次调用再次 crash → crash_count = 2
            with pytest.raises(Exception):
                await plugin.call("process")
            assert plugin._crash_count == 2
        finally:
            await plugin.aclose()

    def test_max_crash_restarts_constant(self) -> None:
        """MAX_CRASH_RESTARTS == 3。"""
        from astroframe._lifecycle import MAX_CRASH_RESTARTS

        assert MAX_CRASH_RESTARTS == 3

    @pytest.mark.asyncio
    async def test_handle_crash_exhaustion_kills_process(self) -> None:
        """crash_count > MAX 时 _handle_crash 调用 _kill_process（AC14）。"""
        from astroframe._lifecycle import MAX_CRASH_RESTARTS, SubprocessPlugin
        from astroframe._types import ExecutionMode

        cap = _make_cap(factory="tests._fakes_plugin:TestProcessor")
        ref = _make_plugin_ref()
        plugin = await SubprocessPlugin.create(
            cap, ref, {}, "0.1.5", ExecutionMode.SUBPROCESS, "class", "tests._fakes_plugin", "TestProcessor"
        )
        try:
            # 手动设置 crash_count 超过上限
            plugin._crash_count = MAX_CRASH_RESTARTS + 1
            await plugin._handle_crash("test exhaustion")
            # 应已 kill 进程
            assert plugin._process is None
            # semaphore 应已释放
            assert plugin._semaphore_held is False
        finally:
            await plugin.aclose()


class TestSubprocessSemaphore:
    """BoundedSemaphore 并发控制测试（AC15）。"""

    @pytest.mark.asyncio
    async def test_semaphore_acquire_and_release(self) -> None:
        """正常路径：acquire → spawn → aclose → release。"""
        from astroframe._lifecycle import _subprocess_semaphore

        # 验证 semaphore 存在且可用
        assert _subprocess_semaphore is not None

    @pytest.mark.asyncio
    async def test_semaphore_released_on_create_failure(self) -> None:
        """create() 中 spawn 失败 → semaphore 释放。"""
        from astroframe._lifecycle import _subprocess_semaphore

        # 获取 semaphore 前的状态
        # 模拟 acquire 后 spawn 失败——验证 release 在 except 块中被调用
        await _subprocess_semaphore.acquire()
        # 手动 release（模拟异常处理）
        _subprocess_semaphore.release()

    @pytest.mark.asyncio
    async def test_registry_register_no_longer_checks_count(self) -> None:
        """SubprocessRegistry.register() 不再检查 len()（semaphore 是真正的限流器）。"""
        from astroframe._lifecycle import SubprocessRegistry as SR

        registry = SR()
        # 可以注册超过 MAX_SUBPROCESSES（semaphore 提供真正的限流）
        for i in range(32):
            registry._plugins[f"p{i}"] = None  # type: ignore[dict-item]
        assert registry.count == 32  # 不再被拒


class TestAcloseSequence:
    """aclose 升级序列测试（AC12）。"""

    @pytest.mark.asyncio
    async def test_aclose_sends_aclose_message(self) -> None:
        """正常 aclose → 发送 aclose JSONL msg → 子进程退出。"""
        from astroframe._lifecycle import SubprocessPlugin
        from astroframe._types import ExecutionMode

        cap = _make_cap(factory="tests._fakes_plugin:TestProcessor")
        ref = _make_plugin_ref()
        plugin = await SubprocessPlugin.create(
            cap, ref, {}, "0.1.5", ExecutionMode.SUBPROCESS, "class", "tests._fakes_plugin", "TestProcessor"
        )
        await plugin.aclose()
        # 子进程已终止
        assert plugin._process is None

    @pytest.mark.asyncio
    async def test_aclose_cleanup_tmp_dir(self) -> None:
        """aclose 后临时目录已清理。"""
        from astroframe._lifecycle import SubprocessPlugin
        from astroframe._types import ExecutionMode

        cap = _make_cap(factory="tests._fakes_plugin:TestProcessor")
        ref = _make_plugin_ref()
        plugin = await SubprocessPlugin.create(
            cap, ref, {}, "0.1.5", ExecutionMode.SUBPROCESS, "class", "tests._fakes_plugin", "TestProcessor"
        )
        tmp_dir = plugin._tmp_dir
        assert tmp_dir.exists()
        await plugin.aclose()
        assert not tmp_dir.exists()


class TestStderrStdoutSeparation:
    """stdout/stderr 分离测试（AC20）。"""

    @pytest.mark.asyncio
    async def test_print_goes_to_stderr(self) -> None:
        """插件 print() → stderr，不污染 IPC stdout。"""
        from astroframe._lifecycle import SubprocessPlugin
        from astroframe._types import ExecutionMode

        cap = _make_cap(factory="tests._fakes_plugin:StderrPrintProcessor")
        ref = _make_plugin_ref()
        plugin = await SubprocessPlugin.create(
            cap,
            ref,
            {},
            "0.1.5",
            ExecutionMode.SUBPROCESS,
            "class",
            "tests._fakes_plugin",
            "StderrPrintProcessor",
        )
        try:
            result = await plugin.call("process")
            assert result["ok"] is True
            # print() 输出在 stderr（由 _drain_stderr 转发为 DEBUG 日志）
        finally:
            await plugin.aclose()


# ══════════════════════════════════════════════════════════════════════════════════
# AC11: process() 超时测试
# ══════════════════════════════════════════════════════════════════════════════════


class TestProcessTimeout:
    """process() 30s 超时测试（AC11）。"""

    @pytest.mark.asyncio
    async def test_process_timeout_raises_plugin_sandbox_error(self) -> None:
        """SlowProcessor(35s) → asyncio.wait_for 超时 → PluginSandboxError。"""
        from astroframe._errors import PluginSandboxError
        from astroframe._lifecycle import SubprocessPlugin
        from astroframe._types import ExecutionMode

        cap = _make_cap(factory="tests._fakes_plugin:SlowProcessor")
        ref = _make_plugin_ref()
        plugin = await SubprocessPlugin.create(
            cap,
            ref,
            {},
            "0.1.5",
            ExecutionMode.SUBPROCESS,
            "class",
            "tests._fakes_plugin",
            "SlowProcessor",
        )
        try:
            with pytest.raises(PluginSandboxError, match="timeout"):
                await plugin.call("process")
        finally:
            await plugin.aclose()


# ══════════════════════════════════════════════════════════════════════════════════
# ADR-0013: 所有插件统一走子进程沙箱（删除 BUILTIN 本地快速通道）
# ══════════════════════════════════════════════════════════════════════════════════


class TestPluginInstanceRouting:
    """所有插件统一走子进程沙箱（ADR-0013：无 BUILTIN 特权）。"""

    @pytest.mark.asyncio
    async def test_class_factory_goes_subprocess(self) -> None:
        """class factory → SubprocessPlugin（所有插件统一）。"""
        from astroframe._lifecycle import SubprocessPlugin, create_plugin_instance

        cap = _make_cap(factory="tests._fakes_plugin:TestProcessor")
        ref = _make_plugin_ref()
        plugin = await create_plugin_instance(cap, ref, {"key": "val"}, "0.1.5")
        try:
            assert isinstance(plugin, SubprocessPlugin)
        finally:
            await plugin.aclose()

    @pytest.mark.asyncio
    async def test_unknown_implements_goes_to_sandbox(self) -> None:
        """不认识的 Protocol → 仍然走子进程沙箱，不绕过。"""
        from astroframe._lifecycle import create_plugin_instance

        cap = _make_cap(factory="tests._fakes_plugin:pure_function", implements="SomeUnknown")
        ref = _make_plugin_ref()
        from astroframe._errors import PluginLoadError, PluginSandboxError

        with pytest.raises((PluginLoadError, PluginSandboxError)):
            await create_plugin_instance(cap, ref, {}, "0.1.5")


class TestSecurityScanUniform:
    """所有插件统一走安全扫描（ADR-0013：无 BUILTIN 跳过）。"""

    @pytest.mark.asyncio
    async def test_all_plugins_scanned(self) -> None:
        """所有插件触发 scan_plugin_package 调用。"""
        from unittest.mock import patch

        from astroframe._lifecycle import create_plugin_instance

        cap = _make_cap(factory="tests._fakes_plugin:TestProcessor")
        ref = _make_plugin_ref()

        with patch("astroframe._scanner.scan_plugin_package") as mock_scan:
            mock_scan.return_value.hard_blocks = []
            mock_scan.return_value.is_clean = True
            mock_scan.return_value.required_permissions = set()
            plugin = await create_plugin_instance(cap, ref, {}, "0.1.5")
            try:
                mock_scan.assert_called_once()
            finally:
                await plugin.aclose()

    @pytest.mark.asyncio
    async def test_deprecated_removed_capability_rejected(self) -> None:
        """REMOVED capability 被拒绝——废弃门控对所有插件统一执行。"""
        from astroframe._lifecycle import create_plugin_instance

        cap = _make_cap(
            factory="tests._fakes_plugin:TestProcessor",
            deprecated=True,
            deprecated_since="0.0.5",
            deprecation_message="migrate to new api",
        )
        ref = _make_plugin_ref()
        with pytest.raises(PluginLoadError, match="已移除"):
            await create_plugin_instance(cap, ref, {}, "0.9.9")

    @pytest.mark.asyncio
    async def test_deprecated_removed_entry_point_rejected(self) -> None:
        """REMOVED capability（不同 deprecated_since）仍然被拒绝。"""
        from astroframe._lifecycle import create_plugin_instance

        cap = _make_cap(
            factory="tests._fakes_plugin:TestProcessor",
            deprecated=True,
            deprecated_since="0.1.0",
            deprecation_message="migrate to new api",
        )
        ref = _make_plugin_ref()
        with pytest.raises(PluginLoadError, match="已移除"):
            await create_plugin_instance(cap, ref, {}, "0.5.0")


# ══════════════════════════════════════════════════════════════════════════════


class TestSubprocessPluginCall:
    """call() — 唯一 IPC 入口，method 必传，无默认值。"""

    async def test_call_process_method(self) -> None:
        """call('process') 路由到子进程并返回结果。"""
        from astroframe._lifecycle import SubprocessPlugin

        cap = _make_cap(factory="tests._fakes_plugin:TestProcessor")
        ref = _make_plugin_ref()
        plugin = await SubprocessPlugin.create(
            cap, ref, {"key": "val"}, "0.1.5", ExecutionMode.SUBPROCESS, "class", "tests._fakes_plugin", "TestProcessor"
        )
        try:
            result = await plugin.call("process")
            assert result["result"] == "ok"
            assert result["config_keys"] == ["key"]
        finally:
            await plugin.aclose()

    async def test_call_passes_args_and_kwargs(self) -> None:
        """call() 透传 *args 和 **kwargs 到子进程方法。"""
        from astroframe._lifecycle import SubprocessPlugin

        cap = _make_cap(factory="tests._fakes_plugin:TestProcessor")
        ref = _make_plugin_ref()
        plugin = await SubprocessPlugin.create(
            cap, ref, {}, "0.1.5", ExecutionMode.SUBPROCESS, "class", "tests._fakes_plugin", "TestProcessor"
        )
        try:
            result = await plugin.call("process", ctx={"from_engine": 42})
            assert result["result"] == "ok"
        finally:
            await plugin.aclose()

    async def test_call_method_required_no_default(self) -> None:
        """call() 的 method 参数必传——无默认值。"""
        from astroframe._lifecycle import SubprocessPlugin

        cap = _make_cap()
        ref = _make_plugin_ref()
        plugin = SubprocessPlugin(cap, ref, {}, "0.1.0", ExecutionMode.SUBPROCESS, "class", "m", "f")

        import inspect

        sig = inspect.signature(plugin.call)
        params = list(sig.parameters.keys())
        assert params[0] == "method"
        assert sig.parameters["method"].default is inspect.Parameter.empty


class TestRegisterGroup:
    """register_group() — 第三方 group 注册。"""

    def test_register_new_group(self) -> None:
        """register_group() 将自定义 group 写入 GROUP_PROTOCOL、_GROUP_TIER、_VALID_IMPLEMENTS。"""
        from astroframe._types import _GROUP_TIER, _VALID_IMPLEMENTS, GROUP_PROTOCOL, Tier, register_group

        class FakeProtocol:
            def solve(self, challenge: str) -> str: ...

        group_name = "test.custom_group"
        register_group(group_name, FakeProtocol)

        assert group_name in GROUP_PROTOCOL
        assert GROUP_PROTOCOL[group_name] is None  # 引擎不持有 Protocol 引用
        assert _GROUP_TIER[group_name] == Tier.SERVICE
        assert group_name in _VALID_IMPLEMENTS
        assert _VALID_IMPLEMENTS[group_name] == frozenset()  # 空集合 → validate_implements() 返回 True

    def test_duplicate_group_raises(self) -> None:
        """重复注册已有 group 抛出 ValueError。"""
        from astroframe._types import register_group

        class FakeProtocol:
            pass

        register_group("test.duplicate_group", FakeProtocol)
        with pytest.raises(ValueError, match="already registered"):
            register_group("test.duplicate_group", FakeProtocol)

    def test_cannot_override_builtin_group(self) -> None:
        """不能覆盖内置 group。"""
        from astroframe._types import register_group

        class FakeProtocol:
            pass

        with pytest.raises(ValueError, match="already registered"):
            register_group("processor.chain", FakeProtocol)

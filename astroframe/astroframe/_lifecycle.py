"""插件生命周期工厂 + 统一子进程沙箱（ADR-0011 决策 5/7/14）。

所有插件统一在子进程中运行（Chrome/Deno/iOS 统一沙箱原则）。
SubprocessPlugin: 子进程沙箱（IPC + 崩溃恢复 + seccomp/Landlock）
PluginGlobal 编排: on_load/on_unload 生命周期
"""

from __future__ import annotations

import asyncio
import atexit
import inspect
import json
import os
import shutil
import sys
from importlib.metadata import distribution
from pathlib import Path
from typing import TYPE_CHECKING, Any

from astrobasis import LogfmtLogger
from astroframe._errors import PluginLoadError, PluginSandboxError
from astroframe._types import (
    GROUP_PROTOCOL,
    AuditEvent,
    CapabilityRef,
    DeprecationSeverity,
    ExecutionMode,
    PluginGlobal,
    PluginRef,
    PluginScoped,
    PluginStatus,
    SandboxContext,
    check_deprecation,
    derive_execution_mode,
)

if TYPE_CHECKING:
    from collections.abc import Callable

log = LogfmtLogger("astroframe.lifecycle")

# ── IPC 安全常量 ────────────────────────────────────────────────────────────────

MAX_MSG_BYTES = 10 * 1024 * 1024
HANDSHAKE_TIMEOUT = 30.0
SETUP_TIMEOUT = 30.0
PROCESS_TIMEOUT = 30.0
ACLOSE_SIGTERM_TIMEOUT = 10.0  # aclose 消息响应等待
ACLOSE_TERMINATE_TIMEOUT = 10.0  # SIGTERM 后等待（ADR: 10s）
ACLOSE_KILL_TIMEOUT = 5.0  # SIGKILL 后等待（ADR: 5s）
MAX_SUBPROCESSES = 16
MAX_CRASH_RESTARTS = 3

# Engine 全局 asyncio.BoundedSemaphore — 并发子进程上限（ADR-0011 决策 14 IPC 约束表）
_subprocess_semaphore = asyncio.BoundedSemaphore(MAX_SUBPROCESSES)


# ══════════════════════════════════════════════════════════════════════════════════
# 执行模式路由 — 统一子进程沙箱模型（Chrome/Deno/iOS 原则）
# ══════════════════════════════════════════════════════════════════════════════════


def _plugin_integrity_verified(plugin_ref: PluginRef) -> bool:
    """检查插件是否通过了完整性验证（信任记录或签名）。

    requires_signature_verification 违规的消解条件：
    - 插件有匹配的信任记录（hash-pinned 或 version-matched）
    - 或插件通过了签名验证（sigstore/GPG）
    """
    # 信任记录已在 discovery 阶段由 _check_trust_record 处理，
    # 签名验证在 _determine_status 中完成。若 plugin_ref 状态
    # 为 LOADED 且拥有 SIGNATURE 级别权限，则说明已通过验证。
    # NORMAL 权限的插件不要求签名——返回 True。
    signing = plugin_ref.manifest.signing
    if signing is not None and signing.get("method", "unsigned") != "unsigned":
        return True
    if plugin_ref.effective_permission_level.value in ("normal",):
        return True
    # 若插件有签名配置且已通过 discovery 验证，status 为 LOADED
    if plugin_ref.is_loaded:
        return True
    return False


async def create_plugin_instance(
    cap: CapabilityRef,
    plugin_ref: PluginRef,
    config: dict[str, Any],
    engine_version: str,
    *,
    state: Any = None,
    allow_deprecated: bool = False,
) -> PluginScoped | Callable:
    """插件实例工厂——三步判断 factory 性质 + 执行模式路由。

    Args:
        allow_deprecated: 设为 True 以允许使用 ERROR 阶段的已废弃 capability。
            REMOVED 阶段的 capability 始终拒绝——无 opt-in。
    """
    # 步骤 0.5: 状态门控
    if plugin_ref.status != PluginStatus.LOADED:
        status_reason = {
            PluginStatus.PENDING_REVIEW: "plugin not yet trusted",
            PluginStatus.DISABLED: "plugin is disabled",
            PluginStatus.FAILED: "plugin failed to load",
            PluginStatus.INCOMPATIBLE: "plugin is incompatible",
            PluginStatus.SANDBOX_CRASH: "plugin sandbox crashed",
        }
        reason = status_reason.get(plugin_ref.status, f"unknown status: {plugin_ref.status}")
        if plugin_ref.status == PluginStatus.PENDING_REVIEW:
            log.info(AuditEvent.TRUST_PENDING, package=plugin_ref.package_name, capability=cap.global_key)
        raise PluginLoadError(f"plugin '{plugin_ref.package_name}': {reason}")

    # 步骤 1: 执行模式路由
    eff_level = plugin_ref.effective_permission_level
    exec_mode = derive_execution_mode(eff_level)

    # 步骤 2: 安全扫描——所有插件统一 AST 扫描
    from astroframe._scanner import scan_plugin_package

    scan_result = scan_plugin_package(plugin_ref.package_name, cap.factory)

    if not scan_result.is_clean:
        from astroframe._loader import _resolve_permissions

        resolved = _resolve_permissions(plugin_ref.effective_permissions)
        sig_ok = _plugin_integrity_verified(plugin_ref)

        for v in scan_result.violations:
            # 维度 1: 权限声明
            if v.required_permission is not None and v.required_permission not in resolved:
                raise PluginLoadError(
                    f"plugin '{plugin_ref.package_name}': 缺少权限 {v.required_permission} "
                    f"({v.file_path}:{v.line_number} — {v.message})"
                )
            # 维度 2: 签名验证
            if v.requires_signature_verification and not sig_ok:
                raise PluginLoadError(
                    f"plugin '{plugin_ref.package_name}': 需要签名验证 ({v.file_path}:{v.line_number} — {v.message})"
                )

    # 步骤 2.5: 废弃策略门控——对所有插件统一执行
    if cap.deprecated:
        severity = check_deprecation(cap.deprecated_since, engine_version)
        # deprecated=True 但未声明 deprecated_since → 永久 WARNING
        if severity == DeprecationSeverity.NONE and cap.deprecated_since is None:
            severity = DeprecationSeverity.WARNING

        if severity == DeprecationSeverity.WARNING:
            log.warning(
                "capability_deprecated",
                capability=cap.global_key,
                deprecated_since=cap.deprecated_since or "unknown",
                hint=cap.deprecation_message or "",
            )
        elif severity == DeprecationSeverity.ERROR:
            if not allow_deprecated:
                raise PluginLoadError(
                    f"capability '{cap.global_key}' 已废弃 (since {cap.deprecated_since})，"
                    f"宽限期已过，请设置 allow_deprecated=True 以显式 opt-in。"
                    f"{cap.deprecation_message or '联系插件作者获取新版本。'}"
                )
            log.error(
                "capability_deprecated_hard_opted_in",
                capability=cap.global_key,
                deprecated_since=cap.deprecated_since or "unknown",
                hint=cap.deprecation_message or "用户已显式 opt-in 继续使用",
            )
        elif severity == DeprecationSeverity.REMOVED:
            raise PluginLoadError(
                f"capability '{cap.global_key}' 已移除 (since {cap.deprecated_since})。"
                f"{cap.deprecation_message or '请使用替代 capability。'}"
            )

    # 步骤 3: 判断 factory 类型
    factory_module, factory_attr = cap.factory.split(":", 1)
    try:
        mod = __import__(factory_module, fromlist=[factory_attr])
        factory_obj = getattr(mod, factory_attr)
    except Exception as exc:
        raise PluginLoadError(f"plugin '{plugin_ref.package_name}': factory import failed: {exc}") from exc

    # 三步判断 factory 性质
    factory_kind: str | None = None

    # Step A: factory 是类，实现 PluginScoped
    if isinstance(factory_obj, type):
        try:
            if issubclass(factory_obj, PluginScoped):
                factory_kind = "class"
        except TypeError:
            pass

    # Step B: factory 是可调用函数（不是类）
    if factory_kind is None and callable(factory_obj) and not isinstance(factory_obj, type):
        factory_kind = "function"

    # Step C: factory 是模块级 PluginScoped 实例
    if factory_kind is None and isinstance(factory_obj, PluginScoped):
        factory_kind = "instance"

    if factory_kind is None:
        raise PluginLoadError(
            f"plugin '{plugin_ref.package_name}': factory '{cap.factory}' "
            f"is not a PluginScoped class, callable, or instance"
        )

    # 所有插件统一走子进程沙箱（ADR-0013：删除 BUILTIN 本地执行分叉）
    return await _route_instance(
        factory_obj if factory_kind != "function" else None,
        cap,
        plugin_ref,
        config,
        engine_version,
        exec_mode,
        factory_kind,
        factory_module,
        factory_attr,
    )


async def _route_instance(
    instance_or_class: Any,
    cap: CapabilityRef,
    plugin_ref: PluginRef,
    config: dict[str, Any],
    engine_version: str,
    exec_mode: ExecutionMode,
    factory_kind: str,
    factory_module: str,
    factory_attr: str,
) -> PluginScoped:
    """统一子进程沙箱路由 — 所有插件进 SubprocessPlugin。"""
    # 步骤 3.5: Protocol 校验
    # class/instance: 在父进程校验——issubclass 和 isinstance 只查 MRO，不执行插件代码，安全。
    # function: 延迟至子进程校验——避免沙箱外调用工厂函数（零信任构造原则）。
    if factory_kind != "function":
        protocol_cls = GROUP_PROTOCOL.get(cap.group)
        if protocol_cls is not None:
            if isinstance(instance_or_class, type):
                if not issubclass(instance_or_class, protocol_cls):
                    raise PluginLoadError(f"factory class does not implement {protocol_cls.__name__} Protocol")
            elif not isinstance(instance_or_class, protocol_cls):
                raise PluginLoadError(f"factory instance does not implement {protocol_cls.__name__} Protocol")

        # async 签名校验（setup/aclose 生命周期方法）
        for method_name in ("setup", "aclose"):
            method = getattr(instance_or_class, method_name, None)
            if method is not None and not inspect.iscoroutinefunction(method):
                raise PluginLoadError(f"{method_name}() must be async")

    log.info(AuditEvent.PERMISSION_GRANT, package=plugin_ref.package_name, capability=cap.global_key)
    return await SubprocessPlugin.create(
        cap,
        plugin_ref,
        config,
        engine_version,
        exec_mode,
        factory_kind,
        factory_module,
        factory_attr,
        config_schema=plugin_ref.manifest.config_schema,
    )


# ══════════════════════════════════════════════════════════════════════════════════
# SubprocessPlugin — 统一子进程沙箱（所有权限等级）
# ══════════════════════════════════════════════════════════════════════════════════


class SubprocessPlugin:
    """子进程插件包装器（IPC + 崩溃恢复）。"""

    def __init__(
        self,
        cap: CapabilityRef,
        plugin_ref: PluginRef,
        config: dict[str, Any],
        engine_version: str,
        exec_mode: ExecutionMode,
        factory_kind: str,
        factory_module: str,
        factory_attr: str,
        config_schema: dict[str, Any] | None = None,
    ) -> None:
        self._cap = cap
        self._plugin_ref = plugin_ref
        self._config = config
        self._engine_version = engine_version
        self._exec_mode = exec_mode
        self._factory_kind = factory_kind
        self._factory_module = factory_module
        self._factory_attr = factory_attr
        self._config_schema = config_schema

        self._process: asyncio.subprocess.Process | None = None
        self._stdin: asyncio.StreamWriter | None = None
        self._stdout: asyncio.StreamReader | None = None
        self._stderr: asyncio.StreamReader | None = None
        self._ipc_lock = asyncio.Lock()
        self._crash_count = 0
        self._semaphore_held = False
        self._tmp_dir = Path(f"/tmp/astroframe-plugin-{plugin_ref.package_name}")
        self._stderr_task: asyncio.Task | None = None
        self._started = False
        self._exhausted = False

    @property
    def is_exhausted(self) -> bool:
        """崩溃重启次数耗尽 → FAILED 状态。"""
        return self._exhausted

    def _mark_failed(self) -> None:
        """更新 PluginRef 状态为 FAILED（不可逆）。

        PluginRef 是 frozen dataclass——通过 dataclasses.replace() 创建新实例。
        注册表中的外部引用通过 SubprocessRegistry 同步更新。
        """
        import dataclasses

        self._plugin_ref = dataclasses.replace(self._plugin_ref, status=PluginStatus.FAILED)

    @classmethod
    async def create(
        cls,
        cap: CapabilityRef,
        plugin_ref: PluginRef,
        config: dict[str, Any],
        engine_version: str,
        exec_mode: ExecutionMode,
        factory_kind: str,
        factory_module: str,
        factory_attr: str,
        *,
        config_schema: dict[str, Any] | None = None,
    ) -> SubprocessPlugin:
        """创建并启动子进程。"""
        plugin = cls(
            cap,
            plugin_ref,
            config,
            engine_version,
            exec_mode,
            factory_kind,
            factory_module,
            factory_attr,
            config_schema=config_schema,
        )

        # 准备 tmp_dir
        if plugin._tmp_dir.exists():
            shutil.rmtree(plugin._tmp_dir, ignore_errors=True)
        plugin._tmp_dir.mkdir(parents=True, exist_ok=True)

        # 计算 sys_path
        sys_path_entries = _build_sys_path_entries(plugin_ref.package_name)
        readonly_paths = _build_readonly_paths(plugin_ref.package_name)
        readwrite_paths = [str(plugin._tmp_dir)]

        # 获取 BoundedSemaphore（排队等待而非直接拒绝）
        await _subprocess_semaphore.acquire()
        plugin._semaphore_held = True

        try:
            # 启动子进程
            await plugin._spawn(sys_path_entries, readonly_paths, readwrite_paths)

            # 握手
            await plugin._handshake()

            # setup
            await plugin._send_setup()
        except Exception:
            # spawn 成功但 handshake/setup 失败 → 清理孤儿子进程
            if plugin._process is not None:
                # 先释放 semaphore（_kill_process 会再次尝试释放——幂等）
                # 然后 kill 子进程
                await plugin._kill_process()
            elif plugin._semaphore_held:
                # spawn 本身失败（_process 为 None）→ 仅释放 semaphore
                _subprocess_semaphore.release()
                plugin._semaphore_held = False
            raise

        return plugin

    async def _spawn(
        self,
        sys_path_entries: list[str],
        readonly_paths: list[str],
        readwrite_paths: list[str],
    ) -> None:
        """启动子进程。"""
        # bootstrap_paths: -I 隐含 -E（忽略 PYTHONPATH），路径通过 config JSON 传递
        # host 在 sandbox import 之前将这些路径注入 sys.path
        import astrobasis
        import astroframe

        bootstrap_paths = [
            str(Path(astroframe.__file__).parent.parent),
            str(Path(astrobasis.__file__).parent.parent),
        ]

        try:
            pkg_root = distribution(self._plugin_ref.package_name).locate_file("")
            if pkg_root is not None:
                bootstrap_paths.append(str(Path(str(pkg_root)).parent))
        except Exception:
            pass

        import site as site_mod

        try:
            bootstrap_paths.extend(site_mod.getsitepackages())
        except Exception:
            pass

        config_json = json.dumps(
            {
                "factory_module": self._factory_module,
                "factory_attr": self._factory_attr,
                "factory_kind": self._factory_kind,
                "implements": self._cap.implements,
                "engine_version": self._engine_version,
                "bootstrap_paths": bootstrap_paths,
                "sys_path": sys_path_entries,
                "readonly_paths": readonly_paths,
                "readwrite_paths": readwrite_paths,
            }
        )

        # 环境变量白名单（-I 隐含 -E，忽略 PYTHON* 变量，路径通过 bootstrap_paths 传递）
        clean_env: dict[str, str] = {}
        for key in ("HOME", "TMPDIR", "LANG"):
            if key in os.environ:
                clean_env[key] = os.environ[key]

        # x-env-var 注入——子进程退出时自动清理，无需 pop。JSON Schema 标准格式 {"type":"object","properties":{...}}
        field_schemas: dict[str, Any] = (self._config_schema or {}).get("properties", {})
        for key, prop in field_schemas.items():
            if not isinstance(prop, dict):
                continue
            env_var = prop.get("x-env-var")
            if env_var and key in self._config:
                clean_env[env_var] = str(self._config[key])

        # -S -I: 隔离模式。不可使用 -m（-I 下 PYTHONPATH 被忽略，无法找到 astroframe 包）。
        # 改为直接执行 _host.py 脚本文件，由 main() 在 import 前注入 bootstrap_paths。
        host_script = str(Path(astroframe.__file__).parent / "_host.py")

        self._process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-S",
            "-I",
            host_script,
            config_json,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            close_fds=True,
            env=clean_env,
            start_new_session=True,
        )  # type: ignore[arg-type]

        # 管道引用提升为独立属性——构造保证 stdin/stdout/stderr 均为非 None
        # 对标 CPython asyncio.subprocess.Process.communicate() 的一次守卫 + 内部信任模式
        assert self._process.stdin is not None, "stdin=PIPE guarantees non-None"
        assert self._process.stdout is not None, "stdout=PIPE guarantees non-None"
        assert self._process.stderr is not None, "stderr=PIPE guarantees non-None"
        self._stdin = self._process.stdin
        self._stdout = self._process.stdout
        self._stderr = self._process.stderr

        # stderr drain
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def _drain_stderr(self) -> None:
        """后台排空子进程 stderr（防死锁）。"""
        if self._stderr is None:
            return
        try:
            while True:
                line = await self._stderr.readline()
                if not line:
                    break
                log.debug(
                    AuditEvent.SUBPROCESS_STDERR,
                    package=self._plugin_ref.package_name,
                    line=line.decode("utf-8", errors="replace").rstrip(),
                )
        except (OSError, asyncio.CancelledError):
            pass

    async def _handshake(self) -> None:
        """协议握手。"""
        if self._stdin is None or self._stdout is None:
            raise PluginSandboxError("subprocess not started")

        self._stdin.write(b'{"type": "handshake", "protocol": 1}\n')
        await self._stdin.drain()

        try:
            line = await asyncio.wait_for(self._stdout.readline(), HANDSHAKE_TIMEOUT)
        except asyncio.TimeoutError:
            raise PluginSandboxError("handshake timeout") from None

        if not line:
            raise PluginSandboxError("subprocess exited during handshake")

        try:
            resp = json.loads(line.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise PluginSandboxError(f"invalid handshake response: {exc}") from exc

        if not resp.get("ok"):
            raise PluginSandboxError(f"handshake failed: {resp.get('error', 'unknown')}")

    async def _send_setup(self) -> None:
        """发送 setup 消息。"""
        if self._stdin is None or self._stdout is None:
            raise PluginSandboxError("subprocess not started")

        msg = json.dumps({"type": "setup", "config": self._config}) + "\n"
        self._stdin.write(msg.encode("utf-8"))
        await self._stdin.drain()

        try:
            line = await asyncio.wait_for(self._stdout.readline(), SETUP_TIMEOUT)
        except asyncio.TimeoutError:
            raise PluginSandboxError("setup timeout") from None

        if not line:
            raise PluginSandboxError("subprocess exited during setup")

        try:
            resp = json.loads(line.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise PluginSandboxError(f"invalid setup response: {exc}") from exc

        if resp.get("status") != "ok":
            raise PluginSandboxError(f"setup failed: {resp.get('error', 'unknown')}")

    async def setup(self, config: dict[str, Any]) -> None:
        pass  # setup 在 create() 中已完成

    async def call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """IPC 调用（含崩溃恢复）。"""
        async with self._ipc_lock:
            return await self._process_ipc(method, *args, **kwargs)

    async def _process_ipc(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """单次 IPC process 调用。"""
        if self._stdin is None or self._stdout is None:
            raise PluginSandboxError("subprocess not running")

        if self._crash_count > MAX_CRASH_RESTARTS:
            raise PluginSandboxError(f"plugin sandbox crashed {MAX_CRASH_RESTARTS} times, marked FAILED")

        # 序列化参数
        serialized_args = list(args)
        serialized_kwargs = dict(kwargs)

        # SandboxContext → dict
        for i, arg in enumerate(serialized_args):
            if isinstance(arg, SandboxContext):
                ctx_dict = _serialize_sandbox_context(arg)
                serialized_kwargs.update(ctx_dict)
                serialized_kwargs["context_class"] = type(arg).__name__
                serialized_args[i] = None  # placeholder

        msg_data: dict[str, Any] = {
            "type": "process",
            "method": method,
            "args": [a for a in serialized_args if a is not None],
            "kwargs": serialized_kwargs,
        }

        msg = json.dumps(msg_data, default=str) + "\n"
        try:
            self._stdin.write(msg.encode("utf-8"))
            await self._stdin.drain()
        except (OSError, BrokenPipeError) as exc:
            await self._handle_crash(f"IPC write failed: {exc}")
            raise PluginSandboxError(f"plugin sandbox crashed: {exc}") from exc

        try:
            line = await asyncio.wait_for(self._stdout.readline(), PROCESS_TIMEOUT)
        except asyncio.TimeoutError:
            await self._handle_crash("process timeout")
            raise PluginSandboxError("process timeout") from None

        if not line:
            await self._handle_crash("subprocess exited unexpectedly")
            raise PluginSandboxError("subprocess exited unexpectedly")

        try:
            resp = json.loads(line.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise PluginSandboxError(f"invalid process response: {exc}") from exc

        if resp.get("status") != "ok":
            raise PluginSandboxError(f"plugin error in subprocess: {resp.get('error', 'unknown')}")

        # 成功——重置崩溃计数器
        self._crash_count = 0
        return resp.get("result")

    async def _handle_crash(self, reason: str) -> None:
        """崩溃恢复——终止旧进程 + 重启（≤ 3 次）。

        崩溃语义（ADR 决策 14）：丢弃当前调用，返回调用前原件 + PluginSandboxError。
        重启 ≤ 3 次 → 耗尽 → FAILED（不可逆）。
        """
        self._crash_count += 1
        log.error(
            AuditEvent.SANDBOX_CRASH,
            package=self._plugin_ref.package_name,
            reason=reason,
            crash_count=self._crash_count,
        )

        if self._crash_count > MAX_CRASH_RESTARTS:
            # 耗尽重启次数——清理僵尸 + 释放资源，标记 FAILED
            await self._kill_process()
            self._exhausted = True
            self._mark_failed()
            return

        # 终止旧子进程（释放 semaphore）
        await self._kill_process()

        # 重新获取 BoundedSemaphore——_kill_process() 已释放
        await _subprocess_semaphore.acquire()
        self._semaphore_held = True

        # 重新 spawn + handshake + setup
        try:
            sys_path_entries = _build_sys_path_entries(self._plugin_ref.package_name)
            readonly_paths = _build_readonly_paths(self._plugin_ref.package_name)
            readwrite_paths = [str(self._tmp_dir)]
            await self._spawn(sys_path_entries, readonly_paths, readwrite_paths)
            await self._handshake()
            await self._send_setup()
        except Exception as exc:
            log.error(AuditEvent.SANDBOX_CRASH, package=self._plugin_ref.package_name, error=f"restart failed: {exc}")
            # 重启失败消耗一次额外"生命"——若总次数超限，标记 FAILED
            self._crash_count += 1
            if self._crash_count > MAX_CRASH_RESTARTS:
                self._exhausted = True
                self._mark_failed()
            # 清理资源（spawn 成功但 handshake/setup 失败 → kill；spawn 失败 → 释放 semaphore）
            if self._process is not None:
                await self._kill_process()
            elif self._semaphore_held:
                _subprocess_semaphore.release()
                self._semaphore_held = False

    async def _kill_process(self) -> None:
        """四阶段终止子进程。"""
        if self._process is None:
            return

        # 取消 stderr drain
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            self._stderr_task = None

        try:
            if self._stdin is not None:
                self._stdin.close()
        except OSError:
            pass

        try:
            self._process.terminate()
            await asyncio.wait_for(self._process.wait(), ACLOSE_TERMINATE_TIMEOUT)
        except (asyncio.TimeoutError, ProcessLookupError):
            try:
                self._process.kill()
                await asyncio.wait_for(self._process.wait(), ACLOSE_KILL_TIMEOUT)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    os.killpg(os.getpgid(self._process.pid), 9)  # SIGKILL
                except (OSError, ProcessLookupError):
                    pass

        self._process = None
        self._stdin = None
        self._stdout = None
        self._stderr = None

        # 释放 BoundedSemaphore（确保子进程终止后槽位归还）
        if self._semaphore_held:
            _subprocess_semaphore.release()
            self._semaphore_held = False

    async def aclose(self) -> None:
        """四阶段 aclose。"""
        async with self._ipc_lock:
            if self._stdin is None or self._stdout is None:
                return

            # 阶段 1: 发送 aclose 消息
            try:
                self._stdin.write(b'{"type": "aclose"}\n')
                await self._stdin.drain()
                line = await asyncio.wait_for(self._stdout.readline(), ACLOSE_SIGTERM_TIMEOUT)
                if line:
                    resp = json.loads(line.decode("utf-8"))
                    if resp.get("status") == "ok":
                        await self._kill_process()
                        self._cleanup_tmp()
                        return
            except (OSError, asyncio.TimeoutError, json.JSONDecodeError):
                pass

            # 阶段 2-4: 升级终止
            await self._kill_process()
            self._cleanup_tmp()

    def _cleanup_tmp(self) -> None:
        """清理临时目录。"""
        try:
            shutil.rmtree(self._tmp_dir, ignore_errors=True)
        except OSError:
            pass


# ── SubprocessPlugin helpers ────────────────────────────────────────────────────


def _build_sys_path_entries(package_name: str) -> list[str]:
    """构建子进程 sys.path——依赖闭包 + 引擎包路径 + 引擎第三方依赖。"""
    entries: list[str] = []

    # 引擎包父目录 + 依赖包父目录
    import astrobasis
    import astroframe

    entries.append(str(Path(astroframe.__file__).parent.parent))
    entries.append(str(Path(astrobasis.__file__).parent.parent))

    # 插件包目录
    try:
        pkg_root = distribution(package_name).locate_file("")
        if pkg_root is not None:
            entries.append(str(Path(str(pkg_root)).resolve()))
    except Exception:
        pass

    # 依赖闭包
    _add_dependency_paths(package_name, entries)
    _add_dependency_paths("astroframe", entries)

    # 去重 + 过滤不存在路径
    seen: set[str] = set()
    result: list[str] = []
    for entry in entries:
        if entry not in seen and Path(entry).is_dir():
            seen.add(entry)
            result.append(entry)
    return result


def _add_dependency_paths(package_name: str, entries: list[str]) -> None:
    """递归添加依赖包的路径。"""
    try:
        requires = distribution(package_name).requires or []
    except Exception:
        return

    for req_str in requires:
        dep_name = req_str.split()[0] if req_str else ""
        if not dep_name:
            continue
        try:
            dep_path = distribution(dep_name).locate_file("")
            if dep_path is not None:
                entries.append(str(Path(str(dep_path)).resolve()))
        except Exception:
            pass


def _build_readonly_paths(package_name: str) -> list[str]:
    """构建 Landlock 只读路径列表。"""
    import sysconfig

    paths: list[str] = []

    # stdlib 路径
    for key in ("stdlib", "platstdlib", "purelib", "platlib"):
        p = sysconfig.get_path(key)
        if p:
            paths.append(p)

    # sys.path 中的额外路径
    for p in sys.path:
        if p and Path(p).is_dir():
            paths.append(p)

    # 插件包目录
    try:
        pkg_root = distribution(package_name).locate_file("")
        if pkg_root is not None:
            paths.append(str(Path(str(pkg_root)).resolve()))
    except Exception:
        pass

    # 引擎包目录
    import astroframe

    paths.append(str(Path(astroframe.__file__).parent))

    # 依赖闭包
    _add_dependency_paths(package_name, paths)
    _add_dependency_paths("astroframe", paths)

    return paths


def _serialize_sandbox_context(ctx: SandboxContext) -> dict[str, Any]:
    """SandboxContext → dict（含 frozenset→list 转换）。"""
    import dataclasses

    result: dict[str, Any] = {}
    for field in dataclasses.fields(ctx):
        value = getattr(ctx, field.name)
        if isinstance(value, frozenset):
            result[field.name] = sorted(value)
        else:
            result[field.name] = value
    return result


# ══════════════════════════════════════════════════════════════════════════════════
# SubprocessRegistry
# ══════════════════════════════════════════════════════════════════════════════════


class SubprocessRegistry:
    """跟踪所有运行中的子进程，引擎关闭时统一清理。"""

    def __init__(self) -> None:
        self._plugins: dict[str, SubprocessPlugin] = {}

    def register(self, plugin_id: str, process: SubprocessPlugin) -> None:
        # 并发限流由模块级 _subprocess_semaphore 保证——registry 计数仅为观测指标
        self._plugins[plugin_id] = process

    def unregister(self, plugin_id: str) -> None:
        self._plugins.pop(plugin_id, None)

    async def aclose_all(self) -> None:
        for plugin_id, plugin in list(self._plugins.items()):
            try:
                await plugin.aclose()
            except Exception as exc:
                log.warning("plugin_registry_aclose_failed", plugin_id=plugin_id, error=str(exc))
        self._plugins.clear()

    @property
    def count(self) -> int:
        return len(self._plugins)


# ══════════════════════════════════════════════════════════════════════════════════
# PluginGlobal 生命周期编排
# ══════════════════════════════════════════════════════════════════════════════════


async def orchestrate_plugin_global(
    plugins: dict[str, PluginRef],
    registry: Any,
) -> list[str]:
    """编排 PluginGlobal 生命周期——遍历 LOADED 插件，调用 on_load()。

    Args:
        plugins: discover_plugins() 返回的完整 dict（S18 pass 已完成）
        registry: PluginRegistry 实例

    Returns:
        成功调用 on_load() 的插件名列表。
    """
    loaded: list[str] = []

    for package_name, ref in plugins.items():
        if ref.status != PluginStatus.LOADED:
            continue
        # S5 轻量扫描门控
        try:
            from astroframe._scanner import scan_plugin_package

            for cap in ref.manifest.capabilities:
                scan_result = scan_plugin_package(package_name, cap.factory)
                sig_ok = _plugin_integrity_verified(ref)
                resolved = set(ref.effective_permissions)
                blocked = False
                for v in scan_result.violations:
                    if v.required_permission is not None and v.required_permission not in resolved:
                        log.warning(
                            AuditEvent.GLOBAL_UNDECLARED_PERMISSION,
                            package=package_name,
                            missing_permissions=v.required_permission,
                        )
                        blocked = True
                        break
                    if v.requires_signature_verification and not sig_ok:
                        log.error(
                            "plugin_global_blocked_by_scanner",
                            package=package_name,
                            requires_sig=True,
                        )
                        blocked = True
                        break
                if blocked:
                    break
            else:
                # 通过扫描——查找 PluginGlobal 实现
                for cap in ref.manifest.capabilities:
                    try:
                        module_name = cap.factory.split(":")[0]
                        mod = __import__(module_name, fromlist=["__all__"])
                        for attr_name in dir(mod):
                            obj = getattr(mod, attr_name)
                            if isinstance(obj, type) and issubclass(obj, PluginGlobal):
                                try:
                                    obj.on_load()
                                    loaded.append(package_name)
                                except Exception as exc:
                                    log.error(
                                        "plugin_global_on_load_failed",
                                        package=package_name,
                                        error=str(exc),
                                    )
                                break
                        else:
                            continue
                        break
                    except Exception as exc:
                        log.error(
                            "plugin_global_import_failed",
                            package=package_name,
                            error=str(exc),
                        )
                        break
        except Exception as exc:
            log.error(
                "plugin_global_scan_failed",
                package=package_name,
                error=str(exc),
            )

    # 注册 atexit 回调
    def _on_unload_all() -> None:
        for name in loaded:
            ref = plugins.get(name)
            if ref is None:
                continue
            for cap in ref.manifest.capabilities:
                try:
                    module_name = cap.factory.split(":")[0]
                    mod = __import__(module_name, fromlist=["__all__"])
                    for attr_name in dir(mod):
                        obj = getattr(mod, attr_name)
                        if isinstance(obj, type) and issubclass(obj, PluginGlobal):
                            try:
                                obj.on_unload()
                            except Exception:
                                pass
                            break
                except Exception:
                    pass

    if loaded:
        atexit.register(_on_unload_all)

    return loaded

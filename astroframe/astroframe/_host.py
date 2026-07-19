"""子进程宿主入口点（ADR-0011 S13-S14）。

由父进程通过 `sys.executable -S -I <path>/_host.py <config_json>` 启动。
在沙箱内运行插件代码，通过 stdin/stdout JSONL IPC 与父进程通信。

8 步不可变启动序列（ADR-0011 S14）：

  1. 解析命令行参数 + 注入 bootstrap_paths
  2. dup2(stderr, stdout) — IPC 通道提前就绪。沙箱初始化错误必须通过 IPC 报告父进程——
     若 IPC 在沙箱之后才建立，初始化失败将静默丢失（fail-closed 原则：错误必须可见）
  3. import astroframe._sandbox（引擎沙箱模块——非插件代码，可信）
  4. composite_sandbox.apply_to_process()
     rlimit → prctl(NO_NEW_PRIVS) → seccomp-bpf → Landlock FS ACL → PEP 578 audit hooks
  5. 构建完整 sys.path（stdlib ∪ 父进程传入路径列表，替换 bootstrap_paths）
  6. PEP 578 审计钩子（步骤 4 中已由 CompositeSandbox 统一安装）
  7. import 插件代码（不可信代码的第一个字节在此执行——此时四层沙箱已完整就绪）
  8. IPC 主循环（handshake-first → setup → process → aclose）
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any


def _load_config(argv: list[str]) -> dict[str, Any]:
    """从命令行参数解析 config_json。"""
    if len(argv) < 2:
        print('{"status": "error", "error": "missing config_json argument"}', flush=True)  # noqa: T201
        sys.exit(1)
    try:
        return json.loads(argv[1])  # type: ignore[no-any-return]
    except json.JSONDecodeError as exc:
        print(f'{{"status": "error", "error": "invalid config_json: {exc}"}}', flush=True)  # noqa: T201
        sys.exit(1)


# ── IPC 读取限长 ───────────────────────────────────────────────────────────────

_MAX_LINE_BYTES = 10 * 1024 * 1024 + 1024  # 10 MB + 1 KB 余量


def _read_line_guarded() -> bytes | None:
    """逐 chunk 读取直到 \\n 或超 max_bytes。

    使用 os.read(0, ...) 直接从 stdin fd 读取——线程安全，绕过 Python 缓冲层。
    """
    chunks: list[bytes] = []
    total = 0
    while total < _MAX_LINE_BYTES:
        try:
            chunk = os.read(0, 65536)
        except OSError:
            return b"".join(chunks) if chunks else b""
        if not chunk:
            return b"".join(chunks) if chunks else b""
        newline_pos = chunk.find(b"\n")
        if newline_pos >= 0:
            chunks.append(chunk[: newline_pos + 1])
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
    return None


async def main(argv: list[str] | None = None) -> None:
    """子进程主机入口点——在单个 asyncio.run() 内执行全生命周期。"""
    if argv is None:
        argv = sys.argv

    # ── 步骤 1: 解析命令行参数 + 注入 bootstrap_paths ──────────────────────
    config = _load_config(argv)
    factory_module = config["factory_module"]
    factory_attr = config["factory_attr"]
    factory_kind = config.get("factory_kind", "class")
    implements = config.get("implements", "")
    sys_path_entries: list[str] = config.get("sys_path", [])
    readonly_paths: list[str] = config.get("readonly_paths", [])
    readwrite_paths: list[str] = config.get("readwrite_paths", [])

    # -I 隐含 -E（忽略 PYTHONPATH），路径通过 config JSON 的 bootstrap_paths 传递
    # 必须在 sandbox import 之前注入，确保 host 能找到 astroframe._sandbox
    bootstrap_paths: list[str] = config.get("bootstrap_paths", [])
    for entry in reversed(bootstrap_paths):
        if entry and entry not in sys.path:
            sys.path.insert(0, entry)

    # ── 步骤 2: stdout 重定向（提前——确保 sandbox 导入失败时能通过 IPC 报告）─
    saved_stdout_fd = os.dup(1)
    os.dup2(2, 1)  # fd 1 → stderr (插件 print() 不污染 IPC)
    ipc_out = os.fdopen(saved_stdout_fd, "w", buffering=1)

    composite_sandbox = None  # fail-closed: finally 块引用前保证已绑定
    # ── 步骤 3-4: 沙箱初始化（fail-closed）────────────────────────────────
    try:
        from astroframe._sandbox import (
            AuditHookProvider,
            CompositeSandbox,
            LandlockSandbox,
            ResourceLimitSandbox,
            SeccompBpfSandbox,
        )

        seccomp = SeccompBpfSandbox()
        landlock = LandlockSandbox(readonly_paths, readwrite_paths)
        rlimit = ResourceLimitSandbox()
        audit = AuditHookProvider()

        composite_sandbox = CompositeSandbox([rlimit, seccomp, landlock, audit])
        composite_sandbox.setup()
        # apply: rlimit → prctl(NO_NEW_PRIVS) → seccomp-bpf → Landlock FS ACL → PEP 578
        composite_sandbox.apply_to_process()
    except ImportError as exc:
        _send_error(ipc_out, f"sandbox module import failed: {exc}")
        ipc_out.close()
        sys.exit(1)
    except Exception as exc:
        _send_error(ipc_out, f"sandbox setup failed: {exc}")
        ipc_out.close()
        sys.exit(1)

    # ── 步骤 5: 构建完整 sys.path（stdlib ∪ 父进程传入的路径列表）────────
    initial_stdlib = [p for p in sys.path if p and Path(p).is_dir()]
    new_path: list[str] = list(initial_stdlib)
    for entry in sys_path_entries:
        if entry and entry not in new_path:
            new_path.append(entry)
    sys.path = new_path

    # ── 步骤 6: PEP 578 审计钩子（由 CompositeSandbox 统一管理，见步骤 4）──

    # ── 步骤 7: import 插件代码 ────────────────────────────────────────────
    plugin_instance: Any = None
    setup_done = False
    handshake_done = False

    try:
        module = __import__(factory_module, fromlist=[factory_attr])
        factory_obj = getattr(module, factory_attr)
    except Exception as exc:
        _send_error(ipc_out, f"factory import failed: {exc}")
        ipc_out.close()
        sys.exit(1)

    # ── 步骤 8: IPC 主循环 ─────────────────────────────────────────────────
    loop = asyncio.get_running_loop()

    try:
        while True:
            # 限长 chunked read（防 OOM）
            line_bytes = await loop.run_in_executor(None, _read_line_guarded)
            if line_bytes is None:
                # 消息超 10MB 限制 → 关闭连接
                _send_error(ipc_out, "message exceeds 10MB limit")
                ipc_out.close()
                sys.exit(1)
            if not line_bytes:
                break  # stdin closed

            line = line_bytes.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                _send_error(ipc_out, "invalid JSON")
                continue

            msg_type = msg.get("type", "")

            # handshake-first 强制排序
            if not handshake_done and msg_type != "handshake":
                _send_error(ipc_out, "protocol handshake required first")
                continue

            if msg_type == "setup":
                if setup_done:
                    _send_error(ipc_out, "plugin already set up")
                    continue

                config_data = msg.get("config", {})
                try:
                    plugin_instance = _create_instance(factory_obj, factory_kind, config_data)
                    # 零信任构造验证：function kind 在子进程校验（父进程未预执行工厂函数）
                    _validate_plugin_instance(plugin_instance, implements)
                    await plugin_instance.setup(config_data)
                    setup_done = True
                    _send_ok(ipc_out)
                except Exception as exc:
                    _send_error(ipc_out, f"setup failed: {exc}")
                    continue

            elif msg_type == "process":
                if not setup_done:
                    _send_error(ipc_out, "plugin not set up — send setup first")
                    continue

                method_name = msg.get("method", "process")
                args = msg.get("args", [])
                kwargs = msg.get("kwargs", {})

                # SandboxContext 反序列化
                context_class_name = kwargs.pop("context_class", None)
                if context_class_name is not None:
                    try:
                        context_cls = _resolve_context_class(context_class_name)
                        kwargs = _deserialize_context_kwargs(context_cls, kwargs)
                    except Exception as exc:
                        _send_error(ipc_out, f"context deserialization failed: {exc}")
                        continue

                try:
                    method = getattr(plugin_instance, method_name, None)
                    if method is None:
                        _send_error(ipc_out, f"unknown method: {method_name}")
                        continue

                    if asyncio.iscoroutinefunction(method):
                        result = await method(*args, **kwargs)
                    else:
                        result = method(*args, **kwargs)

                    _send_result(ipc_out, result)
                except Exception as exc:
                    _send_error(ipc_out, f"process failed: {exc}")

            elif msg_type == "aclose":
                if plugin_instance is not None:
                    try:
                        await plugin_instance.aclose()
                    except Exception as exc:
                        _send_error(ipc_out, f"aclose failed: {exc}")
                        break  # aclose 失败仍退出——父进程以 kill 作为兜底
                    setup_done = False
                    plugin_instance = None
                _send_ok(ipc_out)
                break  # aclose 后退出

            elif msg_type == "handshake":
                protocol = msg.get("protocol", 0)
                if protocol == 1:
                    _send_json(ipc_out, {"protocol": 1, "ok": True})
                    handshake_done = True
                else:
                    _send_json(ipc_out, {"protocol": 1, "ok": False, "error": f"unsupported protocol: {protocol}"})
                    ipc_out.close()
                    sys.exit(1)

            else:
                _send_error(ipc_out, f"unknown message type: {msg_type}")

    except (BrokenPipeError, OSError):
        pass
    finally:
        if composite_sandbox is not None:
            try:
                composite_sandbox.teardown()
            except Exception:
                pass
        try:
            ipc_out.close()
        except OSError:
            pass
        try:
            os.close(saved_stdout_fd)
        except OSError:
            pass


def _validate_plugin_instance(instance: Any, implements: str) -> None:
    """子进程内 PluginScoped 契约校验——零信任构造的最后一步。

    function kind 的实例在父进程未构造——此处是首次验证（Chrome/Deno/Flatpak 原则）。
    class/instance kind 在父进程已做 issubclass/isinstance 校验，此处二次验证可捕获
    序列化/反序列化不一致。

    Raises:
        TypeError: 实例不满足 PluginScoped 最小契约。
    """
    from astroframe._types import PluginScoped

    if not isinstance(instance, PluginScoped):
        raise TypeError(
            f"factory returned non-PluginScoped: {type(instance).__name__}. "
            f"Plugins declared with implements='{implements}' must implement PluginScoped (setup + aclose)."
        )

    import inspect as _inspect

    for method_name in ("setup", "aclose"):
        method = getattr(instance, method_name, None)
        if method is not None and not _inspect.iscoroutinefunction(method):
            raise TypeError(f"{method_name}() must be an async method")


def _create_instance(
    factory_obj: Any,
    factory_kind: str,
    config: dict[str, Any],
) -> Any:
    """按 factory_kind 创建插件实例。

    - class: 无参构造 + setup(config) 由调用者执行
    - function: func(config) 返回实例 + setup(config) 由调用者执行
    - instance: 已有实例 + setup(config) 由调用者执行
    """
    if factory_kind == "class":
        try:
            return factory_obj()
        except TypeError as exc:
            raise TypeError(
                f"factory class requires no-arg constructor; use factory_kind='function' instead: {exc}"
            ) from exc
    elif factory_kind == "function":
        return factory_obj(config)
    elif factory_kind == "instance":
        return factory_obj
    raise ValueError(f"unknown factory_kind: {factory_kind}")


def _resolve_context_class(name: str) -> type:
    """解析 SandboxContext 子类——仅从注册表查找。

    子系统必须通过 register_sandbox_context() 注册自己的 SandboxContext 子类。
    底座不持有也不搜索子系统类型——这是子系统消费底座，不是底座依赖子系统。
    """
    from astroframe._types import _SANDBOX_CONTEXT_REGISTRY

    if name not in _SANDBOX_CONTEXT_REGISTRY:
        raise ValueError(
            f"SandboxContext 子类 '{name}' 未注册。请调用 register_sandbox_context('{name}', {name}) 注册后再使用。"
        )
    return _SANDBOX_CONTEXT_REGISTRY[name]


def _deserialize_context_kwargs(cls: type, kwargs: dict[str, Any]) -> dict[str, Any]:
    """泛型 frozenset 反序列化——list → frozenset 递归转换。"""
    import dataclasses
    from typing import get_origin

    if not dataclasses.is_dataclass(cls):
        return kwargs

    result = dict(kwargs)
    for field in dataclasses.fields(cls):
        field_name = field.name
        if field_name not in result:
            continue
        value = result[field_name]
        # 检测 frozenset 类型——兼容 __future__ annotations（string annotations）
        field_type = field.type
        if isinstance(field_type, str):
            # 简单字符串检测——frozenset 在 annotation 中以 'frozenset' 出现
            # get_type_hints 会触发未导入模块的延迟求值，手工字符串匹配更保守
            is_frozenset = "frozenset" in field_type
        else:
            is_frozenset = field_type is frozenset or get_origin(field_type) is frozenset
        if is_frozenset and isinstance(value, list):
            result[field_name] = frozenset(value)

    return result


# ── IPC helpers ────────────────────────────────────────────────────────────────


def _send_json(fp: Any, data: dict[str, Any]) -> None:
    """发送 JSONL 消息——一行 JSON + \\n + flush。"""
    msg = json.dumps(data, default=str) + "\n"
    fp.write(msg)
    fp.flush()


def _send_ok(fp: Any) -> None:
    _send_json(fp, {"status": "ok"})


def _send_error(fp: Any, error: str) -> None:
    _send_json(fp, {"status": "error", "error": error})


def _send_result(fp: Any, result: Any) -> None:
    try:
        _send_json(fp, {"status": "ok", "result": result})
    except (TypeError, ValueError):
        _send_json(fp, {"status": "ok", "result": str(result)})


if __name__ == "__main__":
    asyncio.run(main())

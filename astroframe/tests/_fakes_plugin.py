"""测试用假插件实现（ADR-0011 Phase 2）。

提供各 Protocol 的标准测试实现，供 test_plugin_*.py 使用。
所有类均通过 @runtime_checkable Protocol 的 isinstance/issubclass 检测。
"""

from __future__ import annotations

import asyncio
import os
import signal
from typing import Any


class TestProcessor:
    """标准 Processor 实现——async setup/process/aclose。"""

    def __init__(self) -> None:
        self._config: dict[str, Any] = {}
        self._setup_called = False
        self._aclose_called = False

    async def setup(self, config: dict[str, Any]) -> None:
        self._config = config
        self._setup_called = True

    async def process(self, ctx: Any = None, **kwargs: Any) -> Any:
        return {"result": "ok", "config_keys": list(self._config.keys()), "setup_called": self._setup_called}

    async def aclose(self) -> None:
        self._aclose_called = True


class TestProcessorSyncSetup:
    """sync setup（非 async）——用于 Protocol 校验拒绝测试。"""

    def setup(self, config: dict[str, Any]) -> None:
        pass

    async def aclose(self) -> None:
        pass

    async def process(self, ctx: Any = None, **kwargs: Any) -> Any:
        return ctx


class TestProcessorNoAclose:
    """缺少 aclose 方法——用于 Protocol 不匹配测试。"""

    async def setup(self, config: dict[str, Any]) -> None:
        pass

    async def process(self, ctx: Any = None, **kwargs: Any) -> Any:
        return ctx


class SlowProcessor:
    """可配置延迟的 Processor——用于超时测试。"""

    def __init__(self, delay: float = 35.0) -> None:
        self._delay = delay

    async def setup(self, config: dict[str, Any]) -> None:
        pass

    async def process(self, ctx: Any = None, **kwargs: Any) -> Any:
        await asyncio.sleep(self._delay)
        return {"result": "slow_done"}

    async def aclose(self) -> None:
        pass


class CrashingProcessor:
    """process() 中自毁——用于崩溃恢复测试。"""

    def __init__(self, crash_after: int = 0) -> None:
        self._crash_after = crash_after
        self._call_count = 0

    async def setup(self, config: dict[str, Any]) -> None:
        pass

    async def process(self, ctx: Any = None, **kwargs: Any) -> Any:
        self._call_count += 1
        if self._call_count > self._crash_after:
            os.kill(os.getpid(), signal.SIGKILL)
            await asyncio.sleep(999)  # unreachable
        return {"result": "ok", "call": self._call_count}

    async def aclose(self) -> None:
        pass


class EnvProbeProcessor:
    """返回 os.environ 内容——用于 env whitelist 测试。"""

    async def setup(self, config: dict[str, Any]) -> None:
        pass

    async def process(self, ctx: Any = None, **kwargs: Any) -> Any:
        return {"environ": dict(os.environ)}

    async def aclose(self) -> None:
        pass


class SysPathProbeProcessor:
    """返回 sys.path 内容——用于 sys.path 隔离测试。"""

    async def setup(self, config: dict[str, Any]) -> None:
        pass

    async def process(self, ctx: Any = None, **kwargs: Any) -> Any:
        import sys

        return {"sys_path": list(sys.path)}

    async def aclose(self) -> None:
        pass


class StderrPrintProcessor:
    """process() 中 print()——用于 stdout/stderr 分离测试。"""

    async def setup(self, config: dict[str, Any]) -> None:
        pass

    async def process(self, ctx: Any = None, **kwargs: Any) -> Any:
        print("to_stderr_from_plugin")  # noqa: T201
        return {"ok": True}

    async def aclose(self) -> None:
        pass


class TestExporter:
    """标准 Exporter 实现。"""

    def __init__(self) -> None:
        self._items: list[dict[str, Any]] = []

    async def setup(self, config: dict[str, Any]) -> None:
        pass

    async def write(self, items: list[dict[str, Any]]) -> None:
        self._items.extend(items)

    async def aclose(self) -> None:
        pass


class TestGlobalPlugin:
    """PluginGlobal 实现——on_load/on_unload 静态方法。"""

    load_called = False
    unload_called = False

    @staticmethod
    def on_load() -> None:
        TestGlobalPlugin.load_called = True

    @staticmethod
    def on_unload() -> None:
        TestGlobalPlugin.unload_called = True


def pure_function(value: Any) -> Any:
    """纯函数（非 PluginScoped）——用于 factory 类型检测测试。"""
    return value


class TestLifecycleHook:
    """LifecycleHook 实现。"""

    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.errors: list[Exception] = []

    async def setup(self, config: dict[str, Any]) -> None:
        pass

    async def on_start(self, context: dict[str, Any]) -> None:
        self.started = True

    async def on_stop(self, context: dict[str, Any]) -> None:
        self.stopped = True

    async def on_error(self, error: Exception, context: dict[str, Any]) -> None:
        self.errors.append(error)

    async def aclose(self) -> None:
        pass

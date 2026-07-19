"""爬虫运行时诊断 — 信号 + HTTP + 自动触发三层机制。

对标：
- Go runtime SIGQUIT: 信号驱动堆栈 dump
- Spring Boot Actuator: HTTP /health 健康报告
- JVM HeapDumpOnOutOfMemoryError: 致命条件自动 dump
- Erlang SASL: 崩溃时进程状态报告

使用方式:
    diag = CrawlDiagnostics(loop)
    diag.register("component_name", component)
    diag.install_signal_handler()      # SIGUSR1 → stderr
    await diag.start_http()            # GET /health, GET /health/full
    await diag.on_fatal(reason)        # 自动 dump + 健康报告
"""

from __future__ import annotations

import asyncio
import io
import json
import signal
import sys
import time
from typing import TYPE_CHECKING, Optional

from astrobasis import LogfmtLogger
from astrocrawl._constants import HTTP_READ_LINE_TIMEOUT
from astrocrawl.health import Health, health_to_report
from astrocrawl.health_monitor import HealthMonitor

if TYPE_CHECKING:
    from astrocrawl.health import HealthChecked

_log = LogfmtLogger("astrocrawl.diagnostics")


# ── Task Stack Dumper ───────────────────────────────────────────


class TaskDumper:
    """asyncio Task 堆栈收集器。"""

    @staticmethod
    def dump_all(loop: asyncio.AbstractEventLoop) -> str:
        """收集所有 asyncio Task 的堆栈，返回格式化字符串。"""
        out = io.StringIO()
        out.write("\n" + "=" * 80 + "\n")
        out.write("ASYNCIO TASK DUMP\n")
        out.write(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        out.write("=" * 80 + "\n")

        tasks = asyncio.all_tasks(loop)
        out.write(f"Total tasks: {len(tasks)}\n")
        pending = 0

        for i, task in enumerate(sorted(tasks, key=lambda t: t.get_name())):
            name = task.get_name()
            done = task.done()
            if not done:
                pending += 1

            out.write(f"\n--- Task #{i + 1}: {name} {'[DONE]' if done else '[PENDING]'} ---\n")
            if done:
                if not task.cancelled():
                    exc = task.exception()
                    if exc:
                        out.write(f"  Exception: {type(exc).__name__}: {exc}\n")
                continue

            try:
                stack = task.get_stack()
            except Exception:
                stack = None

            if stack:
                out.write("  Stack (most recent call last):\n")
                for frame in reversed(stack[-20:]):
                    out.write(f"    {frame.f_code.co_filename}:{frame.f_lineno} in {frame.f_code.co_name}\n")
            else:
                try:
                    coro = task.get_coro()
                    if coro is not None and hasattr(coro, "cr_frame"):
                        f = coro.cr_frame
                        frames = []
                        while f is not None:
                            frames.append(f)
                            f = f.f_back
                        out.write("  Stack (from cr_frame):\n")
                        for frame in reversed(frames[-20:]):
                            out.write(f"    {frame.f_code.co_filename}:{frame.f_lineno} in {frame.f_code.co_name}\n")
                except Exception:
                    out.write("  (no stack available)\n")

        out.write(f"\n--- Summary: {pending}/{len(tasks)} tasks pending ---\n")
        out.write("=" * 80 + "\n")
        return out.getvalue()


# ── HTTP 服务器 ─────────────────────────────────────────────────


class HealthHttpServer:
    """最小化 HTTP 服务器——仅两个端点。对标 Spring Actuator。

    端点:
        GET /health      → JSON 健康摘要
        GET /health/full → JSON 健康报告 + asyncio Task 堆栈
    """

    def __init__(self, health_monitor: HealthMonitor, loop: asyncio.AbstractEventLoop, port: int = 9090) -> None:
        self._health_monitor = health_monitor
        self._loop = loop
        self._port = port
        self._server: Optional[asyncio.AbstractServer] = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle,
            "127.0.0.1",
            self._port,
        )
        _log.info("health_endpoint_start", port=self._port)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=HTTP_READ_LINE_TIMEOUT)
            request = line.decode(errors="replace").strip()
            parts = request.split()
            path = parts[1] if len(parts) >= 2 else "/"

            if path == "/health":
                health = await asyncio.wait_for(
                    self._health_monitor.get_health(),
                    timeout=5.0,
                )
                body = json.dumps(health_to_report(health), ensure_ascii=False)
                await self._respond(writer, 200, body)
            elif path == "/health/full":
                health = await asyncio.wait_for(
                    self._health_monitor.get_health(),
                    timeout=5.0,
                )
                body = json.dumps(
                    {
                        "health": health_to_report(health),
                        "task_dump": TaskDumper.dump_all(self._loop),
                    },
                    ensure_ascii=False,
                )
                await self._respond(writer, 200, body)
            else:
                await self._respond(writer, 404, json.dumps({"error": "not found"}))
        except Exception:
            _log.warning("http_handler_error", exc_info=True)
        finally:
            try:
                writer.close()
            except Exception:
                pass

    @staticmethod
    async def _respond(writer: asyncio.StreamWriter, status: int, body: str) -> None:
        data = (
            f"HTTP/1.1 {status} {'OK' if status == 200 else 'Not Found'}\r\n"
            f"Content-Type: application/json; charset=utf-8\r\n"
            f"Content-Length: {len(body.encode())}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
            f"{body}"
        ).encode()
        writer.write(data)
        await writer.drain()


# ── 主入口 ──────────────────────────────────────────────────────


class CrawlDiagnostics:
    """爬虫运行时诊断。三层机制。

    - 信号: SIGUSR1 → stderr 输出完整堆栈
    - HTTP: GET /health → JSON 健康摘要
    - 自动: on_fatal(reason) → 致命条件时 dump + 健康报告
    """

    def __init__(
        self, loop: asyncio.AbstractEventLoop, port: int = 9090, health_monitor: Optional[HealthMonitor] = None
    ) -> None:
        self._loop = loop
        self._port = port
        self._health_monitor = health_monitor if health_monitor is not None else HealthMonitor()
        self._http: Optional[HealthHttpServer] = None

    # ── 组件注册 ──────────────────────────────────────────────

    def register(self, name: str, target: HealthChecked) -> None:
        self._health_monitor.register_passive(name, target)

    # ── 信号 ──────────────────────────────────────────────────

    def install_signal_handler(self) -> None:
        def _handler():
            dump = TaskDumper.dump_all(self._loop)
            sys.stderr.write(dump)
            sys.stderr.flush()

        try:
            self._loop.add_signal_handler(signal.SIGUSR1, _handler)
            _log.debug("sigusr1_installed")
        except (NotImplementedError, ValueError, AttributeError, RuntimeError):
            _log.debug("sigusr1_unavailable")

    def uninstall_signal_handler(self) -> None:
        try:
            self._loop.remove_signal_handler(signal.SIGUSR1)
        except (NotImplementedError, RuntimeError, ValueError):
            pass

    # ── HTTP ──────────────────────────────────────────────────

    async def start_http(self) -> None:
        self._http = HealthHttpServer(self._health_monitor, self._loop, self._port)
        await self._http.start()

    async def stop_http(self) -> None:
        if self._http:
            await self._http.stop()

    # ── 自动触发 ──────────────────────────────────────────────

    async def on_fatal(self, reason: str) -> str:
        """致命条件触发：dump 堆栈 + 健康报告。返回完整文本。"""
        report = self._health_monitor.get_health_report()
        dump = TaskDumper.dump_all(self._loop)
        report_text = (
            f"\n{'#' * 80}\n"
            f"# FATAL: {reason}\n"
            f"# Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"# Health: {report['status']} (uptime: {report['uptime_seconds']:.0f}s)\n"
            f"{'#' * 80}\n" + json.dumps(report, ensure_ascii=False, indent=2) + "\n" + dump
        )
        sys.stderr.write(report_text)
        sys.stderr.flush()
        _log.critical("fatal_diagnostics_dump", reason=reason)
        return report_text

    # ── 健康查询 ──────────────────────────────────────────────

    async def get_health(self) -> Health:
        return await self._health_monitor.get_health()

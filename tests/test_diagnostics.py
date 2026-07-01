"""爬虫运行时诊断测试。

三层机制：
- TaskDumper: asyncio Task 堆栈收集
- HealthHttpServer: 最小化 HTTP 端点 /health + /health/full
- CrawlDiagnostics: 信号 + HTTP + 自动触发集成
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest

from astrocrawl.diagnostics import CrawlDiagnostics, HealthHttpServer, TaskDumper
from astrocrawl.health import Health
from astrocrawl.health_monitor import HealthMonitor

# ═══════════════════════════════════════════════════════════════════════
# 辅助：monkeypatch all_tasks 使已完成 task 可被 TaskDumper 观察到
# ═══════════════════════════════════════════════════════════════════════


def _patch_all_tasks(tasks: set[asyncio.Task]) -> patch:
    """monkeypatch asyncio.all_tasks 返回受控 task 集合。"""
    return patch("asyncio.all_tasks", return_value=tasks)


# ═══════════════════════════════════════════════════════════════════════
# TaskDumper
# ═══════════════════════════════════════════════════════════════════════


class TestTaskDumper:
    async def test_no_tasks(self):
        loop = asyncio.get_running_loop()
        with _patch_all_tasks(set()):
            output = TaskDumper.dump_all(loop)
        assert "Total tasks: 0" in output
        assert "0/0 tasks pending" in output

    async def test_pending_task_reported(self):
        async def _sleeper():
            await asyncio.sleep(10)

        loop = asyncio.get_running_loop()
        t = asyncio.create_task(_sleeper(), name="sleeper-1")
        try:
            output = TaskDumper.dump_all(loop)
            assert "sleeper-1" in output
            assert "[PENDING]" in output
        finally:
            t.cancel()
            with pytest.raises(asyncio.CancelledError):
                await t

    async def test_done_task_reported(self):
        async def _quick():
            return 42

        loop = asyncio.get_running_loop()
        t = loop.create_task(_quick(), name="quick-task")
        await t
        assert t.done()
        with _patch_all_tasks({t}):
            output = TaskDumper.dump_all(loop)
        assert "quick-task" in output
        assert "[DONE]" in output

    async def test_done_task_with_exception(self):
        async def _failer():
            raise ValueError("test error")

        loop = asyncio.get_running_loop()
        t = loop.create_task(_failer(), name="fail-task")
        with pytest.raises(ValueError):
            await t
        with _patch_all_tasks({t}):
            output = TaskDumper.dump_all(loop)
        assert "fail-task" in output
        assert "[DONE]" in output
        assert "ValueError" in output
        assert "test error" in output

    async def test_cancelled_task_no_exception(self):
        async def _cancelled():
            await asyncio.sleep(10)

        loop = asyncio.get_running_loop()
        t = loop.create_task(_cancelled(), name="cancel-task")
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
        with _patch_all_tasks({t}):
            output = TaskDumper.dump_all(loop)
        assert "cancel-task" in output
        # 已取消的 task 不输出 Exception 信息
        assert "Exception:" not in output

    async def test_mixed_task_states(self):
        async def _sleep():
            await asyncio.sleep(10)

        async def _fail():
            raise RuntimeError("mixed failure")

        loop = asyncio.get_running_loop()
        sleep_t = asyncio.create_task(_sleep(), name="sleep-t")
        fail_t = loop.create_task(_fail(), name="fail-t")
        with pytest.raises(RuntimeError):
            await fail_t
        try:
            with _patch_all_tasks({sleep_t, fail_t}):
                output = TaskDumper.dump_all(loop)
            assert "sleep-t" in output
            assert "fail-t" in output
            assert "RuntimeError" in output
            assert "mixed failure" in output
        finally:
            sleep_t.cancel()
            with pytest.raises(asyncio.CancelledError):
                await sleep_t

    async def test_pending_task_with_nested_stack(self):
        """嵌套协程的 pending task，dump 包含堆栈信息（get_stack 或 cr_frame 回退）。"""

        async def _nested():
            await asyncio.sleep(0)

        async def _wrapper():
            await _nested()

        loop = asyncio.get_running_loop()
        t = asyncio.create_task(_wrapper(), name="stack-task")
        await asyncio.sleep(0)
        try:
            output = TaskDumper.dump_all(loop)
            assert "stack-task" in output
            assert "[PENDING]" in output
        finally:
            t.cancel()
            with pytest.raises(asyncio.CancelledError):
                await t


# ═══════════════════════════════════════════════════════════════════════
# HealthHttpServer
# ═══════════════════════════════════════════════════════════════════════


class TestHealthHttpServer:
    @staticmethod
    async def _start_server(monitor: HealthMonitor, port: int = 0):
        loop = asyncio.get_running_loop()
        server = HealthHttpServer(monitor, loop, port=port)
        await server.start()
        return server

    @staticmethod
    async def _get_port(server: HealthHttpServer) -> int:
        return server._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]

    @staticmethod
    async def _http_get(port: int, path: str) -> tuple[int, dict]:
        """发送 GET 请求，返回 (status_code, json_body)。"""
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        request = f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n"
        writer.write(request.encode())
        await writer.drain()
        try:
            raw = await asyncio.wait_for(reader.read(65536), timeout=3.0)
        except asyncio.TimeoutError:
            raw = b""
        writer.close()
        await writer.wait_closed()
        text = raw.decode(errors="replace")
        parts = text.split("\r\n\r\n", 1)
        status_line = parts[0].split("\r\n")[0] if parts else ""
        status_code = int(status_line.split()[1]) if len(status_line.split()) >= 2 else 0
        body_str = parts[1] if len(parts) > 1 else ""
        try:
            body = json.loads(body_str)
        except (json.JSONDecodeError, ValueError):
            body = {}
        return status_code, body

    async def test_start_stop_lifecycle(self):
        monitor = HealthMonitor()
        server = await self._start_server(monitor, port=0)
        await server.stop()

    async def test_health_endpoint_returns_200_json(self):
        monitor = HealthMonitor()
        server = await self._start_server(monitor, port=0)
        try:
            port = await self._get_port(server)
            status, body = await self._http_get(port, "/health")
            assert status == 200
            assert body["status"] == "UP"
        finally:
            await server.stop()

    async def test_health_full_endpoint_returns_json_with_task_dump(self):
        monitor = HealthMonitor()
        server = await self._start_server(monitor, port=0)
        try:
            port = await self._get_port(server)
            status, body = await self._http_get(port, "/health/full")
            assert status == 200
            assert "health" in body
            assert "task_dump" in body
        finally:
            await server.stop()

    async def test_unknown_path_returns_404(self):
        monitor = HealthMonitor()
        server = await self._start_server(monitor, port=0)
        try:
            port = await self._get_port(server)
            status, body = await self._http_get(port, "/nonexistent")
            assert status == 404
            assert "error" in body
        finally:
            await server.stop()

    async def test_content_type_is_json_utf8(self):
        monitor = HealthMonitor()
        server = await self._start_server(monitor, port=0)
        try:
            port = await self._get_port(server)
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
            await writer.drain()
            raw = await asyncio.wait_for(reader.read(4096), timeout=3.0)
            writer.close()
            await writer.wait_closed()
            text = raw.decode(errors="replace")
            assert "Content-Type: application/json" in text
            assert "charset=utf-8" in text
        finally:
            await server.stop()

    async def test_content_length_matches_body(self):
        monitor = HealthMonitor()
        server = await self._start_server(monitor, port=0)
        try:
            port = await self._get_port(server)
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
            await writer.drain()
            raw = await asyncio.wait_for(reader.read(4096), timeout=3.0)
            writer.close()
            await writer.wait_closed()
            text = raw.decode(errors="replace")
            assert "Content-Length:" in text
            cl = 0
            for line in text.split("\r\n"):
                if line.lower().startswith("content-length:"):
                    cl = int(line.split(":", 1)[1].strip())
                    break
            else:
                pytest.fail("Content-Length header not found")
            parts = text.split("\r\n\r\n", 1)
            body = parts[1] if len(parts) > 1 else ""
            assert len(body.encode()) == cl
        finally:
            await server.stop()

    async def test_connection_close_header(self):
        monitor = HealthMonitor()
        server = await self._start_server(monitor, port=0)
        try:
            port = await self._get_port(server)
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
            await writer.drain()
            raw = await asyncio.wait_for(reader.read(4096), timeout=3.0)
            writer.close()
            await writer.wait_closed()
            text = raw.decode(errors="replace")
            assert "Connection: close" in text
        finally:
            await server.stop()

    async def test_malformed_request_does_not_crash(self):
        monitor = HealthMonitor()
        server = await self._start_server(monitor, port=0)
        try:
            port = await self._get_port(server)
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GARBAGE\r\n\r\n")
            await writer.drain()
            await asyncio.sleep(0.1)
            writer.close()
            await writer.wait_closed()
            # 正常请求确认服务器仍在运行
            status, body = await self._http_get(port, "/health")
            assert status == 200
            assert "status" in body
        finally:
            await server.stop()

    async def test_concurrent_requests(self):
        monitor = HealthMonitor()
        server = await self._start_server(monitor, port=0)
        try:
            port = await self._get_port(server)

            async def _req():
                return await self._http_get(port, "/health")

            results = await asyncio.gather(_req(), _req())
            for status, body in results:
                assert status == 200
                assert "status" in body
        finally:
            await server.stop()

    async def test_server_listens_on_specified_port(self):
        monitor = HealthMonitor()
        server = await self._start_server(monitor, port=0)
        try:
            port = await self._get_port(server)
            assert port > 0
            sock = server._server.sockets[0]  # type: ignore[union-attr]
            assert sock.getsockname()[0] == "127.0.0.1"
        finally:
            await server.stop()


# ═══════════════════════════════════════════════════════════════════════
# CrawlDiagnostics
# ═══════════════════════════════════════════════════════════════════════


class TestCrawlDiagnostics:
    def test_init_default_health_monitor(self):
        loop = asyncio.new_event_loop()
        try:
            diag = CrawlDiagnostics(loop)
            assert isinstance(diag._health_monitor, HealthMonitor)
        finally:
            loop.close()

    def test_init_injected_health_monitor(self):
        loop = asyncio.new_event_loop()
        try:
            monitor = HealthMonitor()
            diag = CrawlDiagnostics(loop, health_monitor=monitor)
            assert diag._health_monitor is monitor
        finally:
            loop.close()

    def test_register_delegates_to_health_monitor(self):
        loop = asyncio.new_event_loop()
        try:
            monitor = HealthMonitor()
            diag = CrawlDiagnostics(loop, health_monitor=monitor)

            class _Comp:
                def get_health(self) -> Health:
                    return Health("UP")

            comp = _Comp()
            diag.register("my_component", comp)
            assert "my_component" in monitor._passive_indicators
            assert monitor._passive_indicators["my_component"] is comp
        finally:
            loop.close()

    async def test_get_health_delegates(self):
        loop = asyncio.get_running_loop()
        diag = CrawlDiagnostics(loop)
        result = await diag.get_health()
        assert result.status == "UP"

    async def test_on_fatal_formats_report(self):
        loop = asyncio.get_running_loop()
        diag = CrawlDiagnostics(loop)
        report = await diag.on_fatal("test crash")
        assert "# FATAL: test crash" in report
        assert "Health:" in report
        assert "ASYNCIO TASK DUMP" in report

    def test_on_fatal_writes_to_stderr(self, capsys):
        async def _run():
            loop = asyncio.get_running_loop()
            diag = CrawlDiagnostics(loop)
            return await diag.on_fatal("test reason")

        report = asyncio.run(_run())
        captured = capsys.readouterr()
        assert "test reason" in captured.err
        assert "# FATAL:" in captured.err
        # 返回值内容也正确
        assert "test reason" in report

    def test_on_fatal_returns_report_string(self):
        async def _run():
            loop = asyncio.get_running_loop()
            diag = CrawlDiagnostics(loop)
            return await diag.on_fatal("verify return")

        report = asyncio.run(_run())
        assert "verify return" in report

    async def test_on_fatal_before_any_registration(self):
        loop = asyncio.get_running_loop()
        diag = CrawlDiagnostics(loop)
        report = await diag.on_fatal("early crash")
        assert "# FATAL: early crash" in report

    def test_signal_install_uninstall_noop_on_unsupported(self):
        loop = asyncio.new_event_loop()
        try:
            diag = CrawlDiagnostics(loop)

            def _raise(*args, **kwargs):
                raise NotImplementedError("no signals on this platform")

            loop.add_signal_handler = _raise  # type: ignore[method-assign]
            # 不应抛出异常
            diag.install_signal_handler()
            # uninstall 也不应抛出
            diag.uninstall_signal_handler()
        finally:
            loop.close()

    async def test_double_start_http_raises(self):
        """P3: 无双重启动保护，同一端口第二次 start_http 抛 OSError。"""
        # 找一个空闲端口用于测试
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            fixed_port = s.getsockname()[1]

        loop = asyncio.get_running_loop()
        diag = CrawlDiagnostics(loop, port=fixed_port)
        await diag.start_http()
        try:
            with pytest.raises(OSError):
                await diag.start_http()
        finally:
            await diag.stop_http()

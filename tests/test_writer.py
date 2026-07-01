"""AsyncJsonlWriter 测试。

JSONL 输出写入器——缓冲 + 定时刷新 + GZip 压缩 + 并发安全。
"""

from __future__ import annotations

import asyncio
import gzip
import json
import os
from pathlib import Path

from astrocrawl._json_compat import _json_dumps
from astrocrawl.storage.writer import AsyncJsonlWriter

# ═══════════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════════

_SAMPLE_RECORD = {"url": "https://example.com", "text": "hello"}


def _record_bytes(record: dict) -> int:
    """返回 _json_dumps(record) 的字节长度。"""
    return len(_json_dumps(record))


def _read_lines(path: Path, *, writer: AsyncJsonlWriter | None = None) -> list[bytes]:
    """读取 JSONL 输出文件，返回非空行列表。

    start() 写入 schema header 后不调用 file_handle.flush()——数据可能在内核缓冲中。
    传入 writer 时先 flush 再读取。
    """
    if writer is not None and writer._file_handle is not None:
        writer._file_handle.flush()
    content = path.read_bytes()
    return [line for line in content.split(b"\n") if line]


async def _yield_to_task() -> None:
    """让出事件循环，确保后台任务有机会进入 sleep。"""
    await asyncio.sleep(0)


# ═══════════════════════════════════════════════════════════════════════
# __init__ + 初始状态
# ═══════════════════════════════════════════════════════════════════════


class TestAsyncJsonlWriterInit:
    def test_default_construction(self):
        writer = AsyncJsonlWriter(Path("/tmp/out.jsonl"), False, 4096, 5.0)
        assert writer._path == Path("/tmp/out.jsonl")
        assert writer._gzip is False
        assert writer._buf_size == 4096
        assert writer._flush_interval == 5.0

    def test_initial_state(self):
        writer = AsyncJsonlWriter(Path("/tmp/out.jsonl"), False, 4096, 5.0)
        assert writer._closed is False
        assert writer._started is False
        assert writer._file_handle is None
        assert writer._flush_task is None

    def test_buffer_is_empty_bytesio(self):
        writer = AsyncJsonlWriter(Path("/tmp/out.jsonl"), False, 4096, 5.0)
        assert writer._buffer.tell() == 0

    def test_lock_is_asyncio_lock(self):
        writer = AsyncJsonlWriter(Path("/tmp/out.jsonl"), False, 4096, 5.0)
        assert isinstance(writer._lock, asyncio.Lock)


# ═══════════════════════════════════════════════════════════════════════
# start() — fresh + resume
# ═══════════════════════════════════════════════════════════════════════


class TestStart:
    async def test_fresh_start_creates_parent_dir(self, tmp_path):
        path = tmp_path / "sub" / "out.jsonl"
        writer = AsyncJsonlWriter(path, False, 4096, 999.0)
        await writer.start()
        assert path.exists()
        await writer.aclose()

    async def test_fresh_start_writes_schema_header(self, tmp_path):
        path = tmp_path / "out.jsonl"
        writer = AsyncJsonlWriter(path, False, 4096, 999.0)
        await writer.start()
        await writer.aclose()

        content = path.read_bytes()
        assert content.startswith(b'{"_schema":"astrocrawl_output/1"}\n')

    async def test_fresh_start_sets_file_permissions(self, tmp_path, monkeypatch):
        path = tmp_path / "out.jsonl"
        called_with_mode = None

        def _fake_chmod(p, mode):
            nonlocal called_with_mode
            called_with_mode = mode

        monkeypatch.setattr(os, "chmod", _fake_chmod)
        writer = AsyncJsonlWriter(path, False, 4096, 999.0)
        await writer.start()
        assert called_with_mode == 0o600
        await writer.aclose()

    async def test_chmod_failure_is_silent(self, tmp_path, monkeypatch):
        path = tmp_path / "out.jsonl"

        def _failing_chmod(p, mode):
            raise OSError("permission denied")

        monkeypatch.setattr(os, "chmod", _failing_chmod)
        writer = AsyncJsonlWriter(path, False, 4096, 999.0)
        await writer.start()  # 不抛异常
        assert path.exists()
        await writer.aclose()

    async def test_resume_skips_schema_header(self, tmp_path):
        path = tmp_path / "out.jsonl"
        path.write_bytes(b"existing line\n")

        writer = AsyncJsonlWriter(path, False, 4096, 999.0)
        await writer.start(resume=True)
        await writer.aclose()

        assert path.read_bytes() == b"existing line\n"

    async def test_start_creates_flush_task(self, tmp_path):
        path = tmp_path / "out.jsonl"
        writer = AsyncJsonlWriter(path, False, 4096, 999.0)
        await writer.start()
        assert writer._flush_task is not None
        assert isinstance(writer._flush_task, asyncio.Task)
        await writer.aclose()


# ═══════════════════════════════════════════════════════════════════════
# write_record() — 缓冲 + 阈值触发
# ═══════════════════════════════════════════════════════════════════════


class TestWriteRecord:
    async def test_single_record_buffered_not_flushed(self, tmp_path):
        path = tmp_path / "out.jsonl"
        writer = AsyncJsonlWriter(path, False, buffer_size=100000, flush_interval=999.0)
        await writer.start()
        await writer.write_record(_SAMPLE_RECORD)

        assert writer._buffer.tell() > 0
        # 手动 flush 后文件有记录
        await writer._do_flush()
        lines = _read_lines(path, writer=writer)
        assert len(lines) >= 2  # header + 1 record
        await writer.aclose()

    async def test_buffer_at_threshold_triggers_flush(self, tmp_path):
        path = tmp_path / "out.jsonl"
        rec_size = _record_bytes(_SAMPLE_RECORD)
        writer = AsyncJsonlWriter(path, False, buffer_size=rec_size, flush_interval=999.0)
        await writer.start()
        await writer.write_record(_SAMPLE_RECORD)

        assert writer._buffer.tell() == 0
        lines = _read_lines(path, writer=writer)
        assert len(lines) == 2  # header + 1 record
        await writer.aclose()

    async def test_buffer_one_byte_below_no_flush(self, tmp_path):
        path = tmp_path / "out.jsonl"
        rec_size = _record_bytes(_SAMPLE_RECORD)
        writer = AsyncJsonlWriter(path, False, buffer_size=rec_size + 1, flush_interval=999.0)
        await writer.start()
        await writer.write_record(_SAMPLE_RECORD)

        assert writer._buffer.tell() == rec_size
        lines = _read_lines(path, writer=writer)
        assert len(lines) == 1  # 只有 header
        await writer.aclose()

    async def test_multiple_records_accumulate(self, tmp_path):
        path = tmp_path / "out.jsonl"
        rec_size = _record_bytes({"u": "a"})
        writer = AsyncJsonlWriter(path, False, buffer_size=rec_size * 2 + 1, flush_interval=999.0)
        await writer.start()

        await writer.write_record({"u": "a"})
        assert writer._buffer.tell() == rec_size
        await writer.write_record({"u": "b"})
        assert writer._buffer.tell() == rec_size * 2
        await writer.write_record({"u": "c"})
        assert writer._buffer.tell() == 0
        await writer.aclose()

    async def test_write_record_after_close_silent_noop(self, tmp_path):
        path = tmp_path / "out.jsonl"
        writer = AsyncJsonlWriter(path, False, buffer_size=100000, flush_interval=999.0)
        await writer.start()
        writer._closed = True
        buffer_before = writer._buffer.tell()
        await writer.write_record(_SAMPLE_RECORD)
        assert writer._buffer.tell() == buffer_before
        writer._file_handle.close()

    async def test_record_written_is_valid_jsonl(self, tmp_path):
        path = tmp_path / "out.jsonl"
        writer = AsyncJsonlWriter(path, False, buffer_size=1, flush_interval=999.0)
        await writer.start()
        await writer.write_record({"key": "value"})
        await writer.aclose()

        lines = _read_lines(path)
        parsed = json.loads(lines[1].decode("utf-8"))
        assert parsed == {"key": "value"}


# ═══════════════════════════════════════════════════════════════════════
# aclose() — 幂等关闭 + 残留缓冲 + fsync + 上下文管理器
# ═══════════════════════════════════════════════════════════════════════


class TestAclose:
    async def test_aclose_sets_closed(self, tmp_path):
        path = tmp_path / "out.jsonl"
        writer = AsyncJsonlWriter(path, False, 4096, 999.0)
        await writer.start()
        await writer.aclose()
        assert writer._closed is True

    async def test_aclose_flushes_remaining_buffer(self, tmp_path):
        path = tmp_path / "out.jsonl"
        writer = AsyncJsonlWriter(path, False, buffer_size=100000, flush_interval=999.0)
        await writer.start()
        await writer.write_record({"u": "residual"})
        assert writer._buffer.tell() > 0
        await writer.aclose()

        lines = _read_lines(path)
        assert len(lines) == 2
        assert b'"u":"residual"' in lines[1]

    async def test_aclose_closes_file_handle(self, tmp_path):
        path = tmp_path / "out.jsonl"
        writer = AsyncJsonlWriter(path, False, 4096, 999.0)
        await writer.start()
        fh = writer._file_handle
        await writer.aclose()
        assert writer._file_handle is None
        assert fh.closed is True

    async def test_aclose_idempotent(self, tmp_path):
        path = tmp_path / "out.jsonl"
        writer = AsyncJsonlWriter(path, False, 4096, 999.0)
        await writer.start()
        await writer.aclose()
        await writer.aclose()
        assert writer._closed is True

    async def test_aclose_empty_buffer_no_extra_content(self, tmp_path):
        path = tmp_path / "out.jsonl"
        writer = AsyncJsonlWriter(path, False, buffer_size=1, flush_interval=999.0)
        await writer.start()
        await writer.write_record({"u": "data"})
        await writer.aclose()

        lines = _read_lines(path)
        assert len(lines) == 2

    async def test_aclose_sets_started_false(self, tmp_path):
        path = tmp_path / "out.jsonl"
        writer = AsyncJsonlWriter(path, False, 4096, 999.0)
        await writer.start()
        assert writer._started is True
        await writer.aclose()
        assert writer._started is False

    async def test_context_manager_auto_cleanup(self, tmp_path):
        path = tmp_path / "out.jsonl"
        async with AsyncJsonlWriter(path, False, 4096, 999.0) as writer:
            await writer.start()
            await writer.write_record({"url": "https://example.com"})
        assert writer._closed is True
        assert writer._file_handle is None
        assert writer._started is False


# ═══════════════════════════════════════════════════════════════════════
# _do_flush() — 空缓冲 no-op + 写入 + 重置
# ═══════════════════════════════════════════════════════════════════════


class TestDoFlush:
    async def test_flush_empty_buffer_noop(self, tmp_path):
        path = tmp_path / "out.jsonl"
        writer = AsyncJsonlWriter(path, False, 4096, 999.0)
        await writer.start()
        await writer._do_flush()  # 空缓冲，立即返回
        lines = _read_lines(path, writer=writer)
        assert len(lines) == 1  # 只有 header
        await writer.aclose()

    async def test_flush_writes_and_resets_buffer(self, tmp_path):
        path = tmp_path / "out.jsonl"
        writer = AsyncJsonlWriter(path, False, buffer_size=100000, flush_interval=999.0)
        await writer.start()
        await writer.write_record({"u": "a"})
        old_buffer = writer._buffer
        assert old_buffer.tell() > 0

        await writer._do_flush()
        assert writer._buffer is not old_buffer
        assert writer._buffer.tell() == 0
        lines = _read_lines(path, writer=writer)
        assert len(lines) == 2
        await writer.aclose()

    async def test_consecutive_flushes_append_correctly(self, tmp_path):
        path = tmp_path / "out.jsonl"
        writer = AsyncJsonlWriter(path, False, buffer_size=100000, flush_interval=999.0)
        await writer.start()
        await writer.write_record({"u": "first"})
        await writer._do_flush()
        await writer.write_record({"u": "second"})
        await writer._do_flush()

        lines = _read_lines(path, writer=writer)
        assert len(lines) == 3
        assert b'"first"' in lines[1]
        assert b'"second"' in lines[2]
        await writer.aclose()


# ═══════════════════════════════════════════════════════════════════════
# _periodic_flush() — 定时刷新 + CancelledError 兜底
# ═══════════════════════════════════════════════════════════════════════


class TestPeriodicFlush:
    async def test_periodic_flush_writes_below_threshold(self, tmp_path):
        path = tmp_path / "out.jsonl"
        writer = AsyncJsonlWriter(path, False, buffer_size=100000, flush_interval=0.01)
        await writer.start()
        await writer.write_record({"u": "periodic"})

        # 5× interval 确保后台任务至少执行一次
        await asyncio.sleep(0.05)
        writer._closed = True
        writer._flush_task.cancel()
        try:
            await writer._flush_task
        except asyncio.CancelledError:
            pass

        lines = _read_lines(path, writer=writer)
        assert len(lines) >= 2
        writer._file_handle.close()

    async def test_cancelled_error_triggers_last_flush(self, tmp_path):
        """任务在 asyncio.sleep 中被取消 → CancelledError 处理器执行兜底 flush。"""
        path = tmp_path / "out.jsonl"
        writer = AsyncJsonlWriter(path, False, buffer_size=100000, flush_interval=999.0)
        await writer.start()
        await _yield_to_task()  # 确保 _periodic_flush 进入 sleep
        await writer.write_record({"u": "last_ditch"})
        assert writer._buffer.tell() > 0

        writer._flush_task.cancel()
        try:
            await writer._flush_task
        except asyncio.CancelledError:
            pass

        lines = _read_lines(path, writer=writer)
        record_lines = [entry for entry in lines if b'"u":"last_ditch"' in entry]
        assert len(record_lines) == 1
        writer._file_handle.close()

    async def test_periodic_flush_exits_when_closed(self, tmp_path):
        path = tmp_path / "out.jsonl"
        writer = AsyncJsonlWriter(path, False, buffer_size=100000, flush_interval=0.01)
        await writer.start()
        writer._closed = True
        await asyncio.sleep(0.05)
        assert writer._flush_task.done()
        writer._file_handle.close()


# ═══════════════════════════════════════════════════════════════════════
# GZip 模式
# ═══════════════════════════════════════════════════════════════════════


class TestGzipMode:
    async def test_gzip_output_is_valid_archive(self, tmp_path):
        path = tmp_path / "out.jsonl.gz"
        writer = AsyncJsonlWriter(path, True, buffer_size=1, flush_interval=999.0)
        await writer.start()
        await writer.write_record({"u": "gzipped"})
        await writer.aclose()

        with gzip.open(path, "rb") as f:
            content = f.read()
        assert b'"u":"gzipped"' in content

    async def test_gzip_roundtrip_matches_plain(self, tmp_path):
        records = [{"u": "a"}, {"u": "b"}, {"u": "c"}]

        gz_path = tmp_path / "out.jsonl.gz"
        gz_writer = AsyncJsonlWriter(gz_path, True, buffer_size=1, flush_interval=999.0)
        await gz_writer.start()
        for r in records:
            await gz_writer.write_record(r)
        await gz_writer.aclose()

        plain_path = tmp_path / "out.jsonl"
        plain_writer = AsyncJsonlWriter(plain_path, False, buffer_size=1, flush_interval=999.0)
        await plain_writer.start()
        for r in records:
            await plain_writer.write_record(r)
        await plain_writer.aclose()

        with gzip.open(gz_path, "rb") as f:
            gz_content = f.read()
        plain_content = plain_path.read_bytes()
        assert gz_content == plain_content

    async def test_gzip_aclose_fsync_handled(self, tmp_path):
        path = tmp_path / "out.jsonl.gz"
        writer = AsyncJsonlWriter(path, True, buffer_size=1, flush_interval=999.0)
        await writer.start()
        await writer.write_record({"u": "test"})
        await writer.aclose()
        assert path.exists()

    async def test_gzip_schema_header_written(self, tmp_path):
        path = tmp_path / "out.jsonl.gz"
        writer = AsyncJsonlWriter(path, True, buffer_size=100000, flush_interval=999.0)
        await writer.start()
        await writer.aclose()

        with gzip.open(path, "rb") as f:
            content = f.read()
        assert content.startswith(b'{"_schema":"astrocrawl_output/1"}\n')

    async def test_gzip_resume_preserves_existing_content(self, tmp_path):
        path = tmp_path / "out.jsonl.gz"

        w1 = AsyncJsonlWriter(path, True, buffer_size=1, flush_interval=999.0)
        await w1.start(resume=False)
        await w1.write_record({"seq": 1})
        await w1.write_record({"seq": 2})
        await w1.aclose()

        w2 = AsyncJsonlWriter(path, True, buffer_size=1, flush_interval=999.0)
        await w2.start(resume=True)
        await w2.write_record({"seq": 3})
        await w2.aclose()

        with gzip.open(path, "rb") as f:
            content = f.read()
        lines = [line for line in content.split(b"\n") if line]
        assert len(lines) == 4
        assert b'"seq":1' in lines[1]
        assert b'"seq":2' in lines[2]
        assert b'"seq":3' in lines[3]


# ═══════════════════════════════════════════════════════════════════════
# 并发写入 + 端到端
# ═══════════════════════════════════════════════════════════════════════


class TestConcurrentAndEndToEnd:
    async def test_concurrent_writes_no_data_loss(self, tmp_path):
        path = tmp_path / "out.jsonl"
        N = 50
        writer = AsyncJsonlWriter(path, False, buffer_size=100000, flush_interval=999.0)
        await writer.start()

        async def _write(i: int):
            await writer.write_record({"idx": i})

        await asyncio.gather(*(_write(i) for i in range(N)))
        await writer.aclose()

        lines = _read_lines(path)
        record_lines = lines[1:]
        assert len(record_lines) == N

    async def test_concurrent_writes_no_corruption(self, tmp_path):
        path = tmp_path / "out.jsonl"
        N = 30
        writer = AsyncJsonlWriter(path, False, buffer_size=100000, flush_interval=999.0)
        await writer.start()

        async def _write(i: int):
            await writer.write_record({"idx": i, "data": f"item_{i}"})

        await asyncio.gather(*(_write(i) for i in range(N)))
        await writer.aclose()

        lines = _read_lines(path)
        for i, line in enumerate(lines):
            parsed = json.loads(line.decode("utf-8"))
            if i == 0:
                assert parsed == {"_schema": "astrocrawl_output/1"}
            else:
                assert "idx" in parsed
                assert "data" in parsed

    async def test_full_lifecycle_fresh(self, tmp_path):
        path = tmp_path / "out.jsonl"
        records = [
            {"url": "https://a.com", "text": "page A"},
            {"url": "https://b.com", "text": "page B"},
            {"url": "https://c.com", "text": "page C"},
        ]
        writer = AsyncJsonlWriter(path, False, buffer_size=1, flush_interval=999.0)
        await writer.start()
        for r in records:
            await writer.write_record(r)
        await writer.aclose()

        lines = _read_lines(path)
        assert len(lines) == 4
        assert json.loads(lines[0]) == {"_schema": "astrocrawl_output/1"}
        assert b"page A" in lines[1]
        assert b"page B" in lines[2]
        assert b"page C" in lines[3]

    async def test_full_lifecycle_resume(self, tmp_path):
        path = tmp_path / "out.jsonl"

        w1 = AsyncJsonlWriter(path, False, buffer_size=1, flush_interval=999.0)
        await w1.start(resume=False)
        await w1.write_record({"batch": 1, "n": 1})
        await w1.aclose()

        w2 = AsyncJsonlWriter(path, False, buffer_size=1, flush_interval=999.0)
        await w2.start(resume=True)
        await w2.write_record({"batch": 2, "n": 1})
        await w2.aclose()

        lines = _read_lines(path)
        assert len(lines) == 3
        assert b'"batch":1' in lines[1]
        assert b'"batch":2' in lines[2]

    async def test_resume_preserves_existing_content(self, tmp_path):
        path = tmp_path / "out.jsonl"

        w1 = AsyncJsonlWriter(path, False, buffer_size=1, flush_interval=999.0)
        await w1.start(resume=False)
        await w1.write_record({"seq": 1})
        await w1.write_record({"seq": 2})
        await w1.aclose()

        w2 = AsyncJsonlWriter(path, False, buffer_size=1, flush_interval=999.0)
        await w2.start(resume=True)
        await w2.write_record({"seq": 3})
        await w2.aclose()

        lines = _read_lines(path)
        assert len(lines) == 4
        assert b'"seq":1' in lines[1]
        assert b'"seq":2' in lines[2]
        assert b'"seq":3' in lines[3]

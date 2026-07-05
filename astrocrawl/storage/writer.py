from __future__ import annotations

import asyncio
import gzip
import io
import logging
import os
from typing import IO, TYPE_CHECKING, Optional

from astrocrawl._json_compat import _json_dumps

if TYPE_CHECKING:
    from pathlib import Path


class AsyncJsonlWriter:
    def __init__(self, output_path: Path, gzip_output: bool, buffer_size: int, flush_interval: float):
        self._path = output_path
        self._gzip = gzip_output
        self._buf_size = buffer_size
        self._flush_interval = flush_interval
        # 使用 BytesIO 以支持 orjson 字节输出，避免 str→bytes 编解码开销
        self._buffer = io.BytesIO()
        self._file_handle: Optional[IO[bytes]] = None
        self._lock = asyncio.Lock()
        self._flush_task: Optional[asyncio.Task] = None
        self._closed = False
        self._started = False
        self._log = logging.getLogger("astrocrawl.writer")

    _SCHEMA_HEADER = b'{"_schema":"astrocrawl_output/1"}\n'

    async def start(self, resume: bool = False) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        mode = "ab" if resume else "wb"
        if self._gzip:
            self._file_handle = gzip.open(self._path, mode)  # type: ignore[assignment]
        else:
            self._file_handle = open(self._path, mode)  # type: ignore[assignment]
        try:
            os.chmod(self._path, 0o600)
        except Exception:
            pass
        if not resume:
            # 写入 schema 版本声明作为 JSONL 首行
            assert self._file_handle is not None
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._file_handle.write, self._SCHEMA_HEADER)
        self._started = True
        self._flush_task = asyncio.create_task(self._periodic_flush())

    async def write_record(self, record: dict) -> None:
        async with self._lock:
            if self._closed:
                return
            # _json_dumps 返回 bytes（含换行），使用 orjson 时避免中间 str 创建
            self._buffer.write(_json_dumps(record))
            if self._buffer.tell() >= self._buf_size:
                await self._do_flush()

    async def _do_flush(self) -> None:
        """调用方必须持有 self._lock——asyncio.Lock 不可重入，此方法不自行获取。"""
        assert self._file_handle is not None, "start() must be called before flushing"
        if self._buffer.tell() == 0:
            return
        content = self._buffer.getvalue()
        self._buffer = io.BytesIO()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._file_handle.write, content)
        await loop.run_in_executor(None, self._file_handle.flush)

    async def _periodic_flush(self) -> None:
        while not self._closed:
            try:
                await asyncio.sleep(self._flush_interval)
            except asyncio.CancelledError:
                # 被取消前尝试最后一次刷新，减少数据丢失
                try:
                    async with self._lock:
                        if not self._closed:
                            await self._do_flush()
                except Exception:
                    pass
                break
            async with self._lock:
                if not self._closed:
                    await self._do_flush()

    async def aclose(self) -> None:
        """AsyncCloseable 协议：取消后台 task，flush 残留缓冲，fsync，关闭文件。幂等。"""
        # 锁外取消 flush_task — 避免与 _periodic_flush 的 CancelledError handler 死锁
        # （handler 中 async with self._lock 需要同一把锁，asyncio.Lock 不可重入）
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass

        async with self._lock:
            if self._closed:
                return
            self._closed = True
            self._started = False
            await self._do_flush()
            if self._file_handle:
                try:
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(None, os.fsync, self._file_handle.fileno())
                except (OSError, AttributeError):
                    pass
                await loop.run_in_executor(None, self._file_handle.close)
                self._file_handle = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.aclose()

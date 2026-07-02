"""日志系统测试 — setup_root_logger + Qt 日志桥接。

使用 caplog 验证日志输出, qapp fixture 提供 Qt Signal。
"""

from __future__ import annotations

import logging
import sys

from astrocrawl.gui._log_bridge import _QtLogHandler, attach_qt_handler, detach_qt_handler
from astrocrawl.utils.logging import setup_root_logger

# ═══════════════════════════════════════════════════════════════════════
# setup_root_logger
# ═══════════════════════════════════════════════════════════════════════


class TestSetupRootLogger:
    """根日志配置。"""

    def test_default_level_is_info(self):
        """默认级别为 INFO, 且添加一个带 _astrocrawl_handler 标记的 handler。"""
        root = logging.getLogger()
        prev_level = root.level
        prev_handlers = list(root.handlers)
        try:
            setup_root_logger()
            assert root.level == logging.INFO
            marked = [h for h in root.handlers if getattr(h, "_astrocrawl_handler", False)]
            assert len(marked) == 1
        finally:
            root.setLevel(prev_level)
            root.handlers[:] = prev_handlers

    def test_custom_level(self):
        """自定义级别生效。"""
        root = logging.getLogger()
        prev_level = root.level
        prev_handlers = list(root.handlers)
        try:
            setup_root_logger(level=logging.DEBUG)
            assert root.level == logging.DEBUG
            marked = [h for h in root.handlers if getattr(h, "_astrocrawl_handler", False)]
            assert len(marked) == 1
        finally:
            root.setLevel(prev_level)
            root.handlers[:] = prev_handlers

    def test_idempotent_no_duplicate_handlers(self):
        """二次调用不重复 handler——带 _astrocrawl_handler 标记的旧 handler 被移除。"""
        root = logging.getLogger()
        prev_level = root.level
        prev_handlers = list(root.handlers)
        try:
            setup_root_logger()
            count1 = sum(1 for h in root.handlers if getattr(h, "_astrocrawl_handler", False))
            setup_root_logger()
            count2 = sum(1 for h in root.handlers if getattr(h, "_astrocrawl_handler", False))
            assert count1 == 1
            assert count2 == 1
        finally:
            root.setLevel(prev_level)
            root.handlers[:] = prev_handlers

    def test_preserves_non_astrocrawl_handlers(self):
        """非 AstroCrawl handler 在 setup_root_logger 后保留。"""
        root = logging.getLogger()
        prev_level = root.level
        prev_handlers = list(root.handlers)
        try:
            external = logging.StreamHandler(sys.stderr)
            root.addHandler(external)
            setup_root_logger()
            non_dc = [h for h in root.handlers if not getattr(h, "_astrocrawl_handler", False)]
            assert external in non_dc
        finally:
            root.setLevel(prev_level)
            root.handlers[:] = prev_handlers

    def test_both_console_and_file_handlers(self, tmp_path):
        """log_file 提供时，同时添加控制台和文件 handler。"""
        log_path = tmp_path / "test.log"
        root = logging.getLogger()
        prev_level = root.level
        prev_handlers = list(root.handlers)
        try:
            setup_root_logger(log_file=str(log_path))
            marked = [h for h in root.handlers if getattr(h, "_astrocrawl_handler", False)]
            assert len(marked) == 2
            from logging.handlers import RotatingFileHandler

            types = {type(h) for h in marked}
            assert logging.StreamHandler in types or any(issubclass(t, logging.StreamHandler) for t in types)
            assert RotatingFileHandler in types or any(issubclass(t, RotatingFileHandler) for t in types)
        finally:
            for h in root.handlers:
                if getattr(h, "_astrocrawl_handler", False):
                    h.close()
            root.setLevel(prev_level)
            root.handlers[:] = prev_handlers

    def test_file_handler_writes_to_path(self, tmp_path):
        """文件 handler 写入指定路径。"""
        log_path = tmp_path / "test.log"
        root = logging.getLogger()
        prev_level = root.level
        prev_handlers = list(root.handlers)
        try:
            setup_root_logger(log_file=str(log_path))
            test_logger = logging.getLogger("astrocrawl.test_file")
            test_logger.info("test message")
            for h in root.handlers:
                h.flush()
            assert log_path.exists()
            content = log_path.read_text("utf-8")
            assert "test message" in content
        finally:
            for h in root.handlers:
                if getattr(h, "_astrocrawl_handler", False):
                    h.close()
            root.setLevel(prev_level)
            root.handlers[:] = prev_handlers


# ═══════════════════════════════════════════════════════════════════════
# Qt 日志桥接
# ═══════════════════════════════════════════════════════════════════════


class TestQtLogHandler:
    """attach_qt_handler / detach_qt_handler。"""

    def test_attach_adds_handler(self, qapp):
        """attach 添加 _QtLogHandler 到 logger, 含 formatter。"""
        logger = logging.getLogger("astrocrawl.test_qt_attach")
        prev_handlers = list(logger.handlers)
        try:
            from PySide6.QtCore import QObject, Signal

            class _Obj(QObject):
                sig = Signal(str)

            obj = _Obj()
            attach_qt_handler(logger, obj.sig)
            qt_handlers = [h for h in logger.handlers if isinstance(h, _QtLogHandler)]
            assert len(qt_handlers) == 1
            assert qt_handlers[0].formatter is not None
        finally:
            logger.handlers[:] = prev_handlers

    def test_attach_replaces_existing(self, qapp):
        """重复 attach 替换旧 Qt handler, 不累积。"""
        logger = logging.getLogger("astrocrawl.test_qt_replace")
        prev_handlers = list(logger.handlers)
        try:
            from PySide6.QtCore import QObject, Signal

            class _Obj(QObject):
                sig = Signal(str)

            obj = _Obj()
            attach_qt_handler(logger, obj.sig)
            attach_qt_handler(logger, obj.sig)
            qt_handlers = [h for h in logger.handlers if isinstance(h, _QtLogHandler)]
            assert len(qt_handlers) == 1
        finally:
            logger.handlers[:] = prev_handlers

    def test_detach_removes_handler(self, qapp):
        """detach 移除所有 _QtLogHandler。"""
        logger = logging.getLogger("astrocrawl.test_qt_detach")
        prev_handlers = list(logger.handlers)
        try:
            from PySide6.QtCore import QObject, Signal

            class _Obj(QObject):
                sig = Signal(str)

            obj = _Obj()
            attach_qt_handler(logger, obj.sig)
            assert any(isinstance(h, _QtLogHandler) for h in logger.handlers)
            detach_qt_handler(logger)
            qt_handlers = [h for h in logger.handlers if isinstance(h, _QtLogHandler)]
            assert len(qt_handlers) == 0
        finally:
            logger.handlers[:] = prev_handlers

    def test_detach_no_handler_no_error(self):
        """无 Qt handler 时 detach 不抛异常, handler 数不变。"""
        logger = logging.getLogger("astrocrawl.test_qt_none")
        prev_count = len(logger.handlers)
        detach_qt_handler(logger)
        assert len(logger.handlers) == prev_count

    def test_emit_sends_to_signal(self, qapp):
        """emit 格式化日志并通过 signal 发送。"""
        logger = logging.getLogger("astrocrawl.test_qt_emit")
        prev_handlers = list(logger.handlers)
        emitted = []
        try:
            from PySide6.QtCore import QObject, Signal

            class _Obj(QObject):
                sig = Signal(str)

            obj = _Obj()
            obj.sig.connect(lambda msg: emitted.append(msg))
            attach_qt_handler(logger, obj.sig)
            logger.warning("test emit message")
            assert len(emitted) == 1
            assert "test emit message" in emitted[0]
        finally:
            logger.handlers[:] = prev_handlers

    def test_emit_self_cleanup_on_signal_deleted(self):
        """Signal source 被删除时 handler 自动从 logger 移除自身。"""
        from unittest.mock import MagicMock

        logger = logging.getLogger("astrocrawl.test_signal_deleted")
        prev_handlers = list(logger.handlers)
        try:
            mock_signal = MagicMock()
            mock_signal.emit.side_effect = RuntimeError("Signal source has been deleted")
            handler = _QtLogHandler(mock_signal, logger)
            logger.addHandler(handler)
            logger.warning("test cleanup message")
            assert handler not in logger.handlers
            mock_signal.emit.assert_called_once()
        finally:
            logger.handlers[:] = prev_handlers

    def test_emit_handle_error_on_unexpected_exception(self):
        """非 signal-deleted 异常时调用 handleError，不崩溃。"""
        from unittest.mock import MagicMock, patch

        logger = logging.getLogger("astrocrawl.test_handle_error")
        mock_signal = MagicMock()
        mock_signal.emit.side_effect = ValueError("unexpected")
        handler = _QtLogHandler(mock_signal, logger)
        record = logging.LogRecord(
            name="test", level=logging.WARNING, pathname="", lineno=0, msg="test", args=(), exc_info=None
        )
        with patch.object(handler, "handleError") as mock_handle_error:
            handler.emit(record)
            mock_handle_error.assert_called_once_with(record)

    def test_emit_runtime_error_non_signal_deleted(self):
        """RuntimeError 但非 'Signal source has been deleted' → handleError 被调用。"""
        from unittest.mock import MagicMock, patch

        logger = logging.getLogger("astrocrawl.test_runtime_error_other")
        mock_signal = MagicMock()
        mock_signal.emit.side_effect = RuntimeError("something else went wrong")
        handler = _QtLogHandler(mock_signal, logger)
        record = logging.LogRecord(
            name="test", level=logging.WARNING, pathname="", lineno=0, msg="test", args=(), exc_info=None
        )
        with patch.object(handler, "handleError") as mock_handle_error:
            handler.emit(record)
            mock_handle_error.assert_called_once_with(record)

    def test_attach_remove_handler_exception(self, monkeypatch):
        """attach 时旧 handler removeHandler 抛异常 → 静默忽略。"""
        logger = logging.getLogger("astrocrawl.test_attach_exc")
        prev_handlers = list(logger.handlers)
        try:
            from unittest.mock import MagicMock

            mock_signal = MagicMock()
            # 先添加一个 Qt handler
            attach_qt_handler(logger, mock_signal)
            # mock removeHandler 使其对 _QtLogHandler 实例抛异常
            original_remove = logger.removeHandler

            def _raising_remove(h):
                if isinstance(h, _QtLogHandler):
                    raise RuntimeError("remove failed")
                original_remove(h)

            monkeypatch.setattr(logger, "removeHandler", _raising_remove)
            # 再次 attach — 旧 handler 移除失败被静默处理，新 handler 仍被添加
            attach_qt_handler(logger, mock_signal)
            qt_handlers = [h for h in logger.handlers if isinstance(h, _QtLogHandler)]
            # 旧 handler 移除失败(仍在) + 新 handler 被添加 = 2
            assert len(qt_handlers) == 2
        finally:
            logger.handlers[:] = prev_handlers

    def test_detach_remove_handler_exception(self, monkeypatch):
        """detach 时 removeHandler 抛异常 → 静默忽略。"""
        logger = logging.getLogger("astrocrawl.test_detach_exc")
        prev_handlers = list(logger.handlers)
        try:
            from unittest.mock import MagicMock

            mock_signal = MagicMock()
            attach_qt_handler(logger, mock_signal)
            initial_count = len([h for h in logger.handlers if isinstance(h, _QtLogHandler)])
            assert initial_count == 1
            # mock removeHandler 以抛异常
            original_remove = logger.removeHandler

            def _raising_remove(h):
                if isinstance(h, _QtLogHandler):
                    raise RuntimeError("remove failed")
                original_remove(h)

            monkeypatch.setattr(logger, "removeHandler", _raising_remove)
            # detach — 异常应被静默处理，不传播
            detach_qt_handler(logger)
            # handler 数量不变（remove 失败但无异常传播）
            qt_handlers = [h for h in logger.handlers if isinstance(h, _QtLogHandler)]
            assert len(qt_handlers) == 1
        finally:
            logger.handlers[:] = prev_handlers


# ═══════════════════════════════════════════════════════════════════════
# setup_root_logger — 文件 handler 构造失败
# ═══════════════════════════════════════════════════════════════════════


class TestSetupRootLoggerFileHandlerFailure:
    """setup_root_logger 文件 handler 创建失败时静默降级。"""

    def test_file_handler_creation_failure_does_not_crash(self, monkeypatch):
        """RotatingFileHandler 构造失败 → print 错误信息，不抛异常。"""
        import logging.handlers

        def _failing_init(*args, **kwargs):
            raise OSError("Permission denied")

        monkeypatch.setattr(logging.handlers, "RotatingFileHandler", _failing_init)
        root = logging.getLogger()
        prev_level = root.level
        prev_handlers = list(root.handlers)
        try:
            # 不应抛异常
            setup_root_logger(log_file="/fake/path/test.log")
            # 控制台 handler 仍正常添加（文件 handler 部分失败）
            marked = [h for h in root.handlers if getattr(h, "_astrocrawl_handler", False)]
            assert len(marked) == 1  # 只有控制台 handler
        finally:
            root.setLevel(prev_level)
            root.handlers[:] = prev_handlers

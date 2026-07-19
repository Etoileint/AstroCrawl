"""Qt log bridge integration tests — attach_qt_handler / detach_qt_handler.

setup_root_logger unit tests moved to astrobasis/tests/test_logging.py.
"""

from __future__ import annotations

import logging

from astrocrawl.gui._log_bridge import _QtLogHandler, attach_qt_handler, detach_qt_handler


class TestQtLogHandler:
    def test_attach_adds_handler(self, qapp):
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
        logger = logging.getLogger("astrocrawl.test_qt_none")
        prev_count = len(logger.handlers)
        detach_qt_handler(logger)
        assert len(logger.handlers) == prev_count

    def test_emit_sends_to_signal(self, qapp):
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
        logger = logging.getLogger("astrocrawl.test_attach_exc")
        prev_handlers = list(logger.handlers)
        try:
            from unittest.mock import MagicMock

            mock_signal = MagicMock()
            attach_qt_handler(logger, mock_signal)
            original_remove = logger.removeHandler

            def _raising_remove(h):
                if isinstance(h, _QtLogHandler):
                    raise RuntimeError("remove failed")
                original_remove(h)

            monkeypatch.setattr(logger, "removeHandler", _raising_remove)
            attach_qt_handler(logger, mock_signal)
            qt_handlers = [h for h in logger.handlers if isinstance(h, _QtLogHandler)]
            assert len(qt_handlers) == 2
        finally:
            logger.handlers[:] = prev_handlers

    def test_detach_remove_handler_exception(self, monkeypatch):
        logger = logging.getLogger("astrocrawl.test_detach_exc")
        prev_handlers = list(logger.handlers)
        try:
            from unittest.mock import MagicMock

            mock_signal = MagicMock()
            attach_qt_handler(logger, mock_signal)
            initial_count = len([h for h in logger.handlers if isinstance(h, _QtLogHandler)])
            assert initial_count == 1
            original_remove = logger.removeHandler

            def _raising_remove(h):
                if isinstance(h, _QtLogHandler):
                    raise RuntimeError("remove failed")
                original_remove(h)

            monkeypatch.setattr(logger, "removeHandler", _raising_remove)
            detach_qt_handler(logger)
            qt_handlers = [h for h in logger.handlers if isinstance(h, _QtLogHandler)]
            assert len(qt_handlers) == 1
        finally:
            logger.handlers[:] = prev_handlers

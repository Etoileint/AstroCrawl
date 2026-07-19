"""setup_root_logger tests — handler dedup, file output, failure fallback."""

from __future__ import annotations

import logging
import sys

from astrobasis import setup_root_logger


class TestSetupRootLogger:
    def test_default_level_is_info(self):
        root = logging.getLogger()
        prev_level = root.level
        prev_handlers = list(root.handlers)
        try:
            setup_root_logger()
            assert root.level == logging.INFO
            marked = [h for h in root.handlers if getattr(h, "_astrobasis_handler", False)]
            assert len(marked) == 1
        finally:
            root.setLevel(prev_level)
            root.handlers[:] = prev_handlers

    def test_custom_level(self):
        root = logging.getLogger()
        prev_level = root.level
        prev_handlers = list(root.handlers)
        try:
            setup_root_logger(level=logging.DEBUG)
            assert root.level == logging.DEBUG
            marked = [h for h in root.handlers if getattr(h, "_astrobasis_handler", False)]
            assert len(marked) == 1
        finally:
            root.setLevel(prev_level)
            root.handlers[:] = prev_handlers

    def test_idempotent_no_duplicate_handlers(self):
        root = logging.getLogger()
        prev_level = root.level
        prev_handlers = list(root.handlers)
        try:
            setup_root_logger()
            count1 = sum(1 for h in root.handlers if getattr(h, "_astrobasis_handler", False))
            setup_root_logger()
            count2 = sum(1 for h in root.handlers if getattr(h, "_astrobasis_handler", False))
            assert count1 == 1
            assert count2 == 1
        finally:
            root.setLevel(prev_level)
            root.handlers[:] = prev_handlers

    def test_preserves_non_astrobasis_handlers(self):
        root = logging.getLogger()
        prev_level = root.level
        prev_handlers = list(root.handlers)
        try:
            external = logging.StreamHandler(sys.stderr)
            root.addHandler(external)
            setup_root_logger()
            non_base = [h for h in root.handlers if not getattr(h, "_astrobasis_handler", False)]
            assert external in non_base
        finally:
            root.setLevel(prev_level)
            root.handlers[:] = prev_handlers

    def test_both_console_and_file_handlers(self, tmp_path):
        log_path = tmp_path / "test.log"
        root = logging.getLogger()
        prev_level = root.level
        prev_handlers = list(root.handlers)
        try:
            setup_root_logger(log_file=str(log_path))
            marked = [h for h in root.handlers if getattr(h, "_astrobasis_handler", False)]
            assert len(marked) == 2
            from logging.handlers import RotatingFileHandler

            types = {type(h) for h in marked}
            assert logging.StreamHandler in types or any(issubclass(t, logging.StreamHandler) for t in types)
            assert RotatingFileHandler in types or any(issubclass(t, RotatingFileHandler) for t in types)
        finally:
            for h in root.handlers:
                if getattr(h, "_astrobasis_handler", False):
                    h.close()
            root.setLevel(prev_level)
            root.handlers[:] = prev_handlers

    def test_file_handler_writes_to_path(self, tmp_path):
        log_path = tmp_path / "test.log"
        root = logging.getLogger()
        prev_level = root.level
        prev_handlers = list(root.handlers)
        try:
            setup_root_logger(log_file=str(log_path))
            test_logger = logging.getLogger("astrobasis.test_file")
            test_logger.info("test message")
            for h in root.handlers:
                h.flush()
            assert log_path.exists()
            content = log_path.read_text("utf-8")
            assert "test message" in content
        finally:
            for h in root.handlers:
                if getattr(h, "_astrobasis_handler", False):
                    h.close()
            root.setLevel(prev_level)
            root.handlers[:] = prev_handlers


class TestSetupRootLoggerFileHandlerFailure:
    def test_file_handler_creation_failure_does_not_crash(self, monkeypatch):
        import logging.handlers

        def _failing_init(*args, **kwargs):
            raise OSError("Permission denied")

        monkeypatch.setattr(logging.handlers, "RotatingFileHandler", _failing_init)
        root = logging.getLogger()
        prev_level = root.level
        prev_handlers = list(root.handlers)
        try:
            setup_root_logger(log_file="/fake/path/test.log")
            marked = [h for h in root.handlers if getattr(h, "_astrobasis_handler", False)]
            assert len(marked) == 1
        finally:
            root.setLevel(prev_level)
            root.handlers[:] = prev_handlers

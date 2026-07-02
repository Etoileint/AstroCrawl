from __future__ import annotations

import logging
import weakref

_HAS_PYSIDE6: bool | None = None


def _is_qt_available() -> bool:
    global _HAS_PYSIDE6
    if _HAS_PYSIDE6 is None:
        try:
            from PySide6.QtCore import Signal  # noqa: F401

            _HAS_PYSIDE6 = True
        except ImportError:
            _HAS_PYSIDE6 = False
    return _HAS_PYSIDE6


class _QtLogHandler(logging.Handler):
    def __init__(self, signal, logger: logging.Logger):
        super().__init__()
        self._signal = signal
        self._logger = weakref.ref(logger)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self._signal.emit(msg)
        except RuntimeError as e:
            if "Signal source has been deleted" in str(e):
                logger = self._logger()
                if logger:
                    logger.removeHandler(self)
            else:
                self.handleError(record)
        except Exception:
            self.handleError(record)


def attach_qt_handler(logger: logging.Logger, signal) -> None:
    if not _is_qt_available():
        return
    for handler in logger.handlers[:]:
        if isinstance(handler, _QtLogHandler):
            try:
                logger.removeHandler(handler)
            except Exception:
                pass
    qh = _QtLogHandler(signal, logger)
    qh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(qh)


def detach_qt_handler(logger: logging.Logger) -> None:
    if not _HAS_PYSIDE6:
        return
    for handler in logger.handlers[:]:
        if isinstance(handler, _QtLogHandler):
            try:
                logger.removeHandler(handler)
            except Exception:
                pass

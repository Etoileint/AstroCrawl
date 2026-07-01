from __future__ import annotations

import logging
import sys
import weakref

from PySide6.QtCore import Signal

from astrocrawl._constants import FILE_LOG_BACKUP_COUNT, FILE_LOG_MAX_BYTES

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_LOG_DATE_FORMAT = "%H:%M:%S"
_ASTROCRAWL_HANDLER = True  # handler marker tag — 用于 setup_root_logger 去重


class _QtLogHandler(logging.Handler):
    def __init__(self, signal: Signal, logger: logging.Logger):
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


def setup_root_logger(level: int = logging.INFO, log_file: str = "") -> None:
    """配置根日志。可多次调用（Stop→Start 重新爬取时幂等）。

    对标 Django dictConfig 替换模式：每次调用先移除上次添加的 handler，
    再添加新的。使用 _astrocrawl_handler 标记区分 AstroCrawl 创建的 handler。
    """
    root = logging.getLogger()
    root.setLevel(level)
    # 先移除所有上次添加的 handler（marker-tag 去重）
    for h in root.handlers[:]:
        if getattr(h, "_astrocrawl_handler", False):
            root.removeHandler(h)
    # 控制台 handler
    ch = logging.StreamHandler(sys.stdout)
    ch._astrocrawl_handler = _ASTROCRAWL_HANDLER  # type: ignore[attr-defined]
    ch.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATE_FORMAT))
    root.addHandler(ch)
    # 文件 handler
    if log_file:
        try:
            from logging.handlers import RotatingFileHandler

            fh = RotatingFileHandler(
                log_file, maxBytes=FILE_LOG_MAX_BYTES, backupCount=FILE_LOG_BACKUP_COUNT, encoding="utf-8"
            )
            fh._astrocrawl_handler = _ASTROCRAWL_HANDLER  # type: ignore[attr-defined]
            fh.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATE_FORMAT))
            root.addHandler(fh)
        except Exception as e:
            print(f"Failed to setup file logging: {e}")


def attach_qt_handler(logger: logging.Logger, signal: Signal) -> None:
    for handler in logger.handlers[:]:
        if isinstance(handler, _QtLogHandler):
            try:
                logger.removeHandler(handler)
            except Exception:
                pass
    qh = _QtLogHandler(signal, logger)
    qh.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATE_FORMAT))
    logger.addHandler(qh)


def detach_qt_handler(logger: logging.Logger) -> None:
    for handler in logger.handlers[:]:
        if isinstance(handler, _QtLogHandler):
            try:
                logger.removeHandler(handler)
            except Exception:
                pass

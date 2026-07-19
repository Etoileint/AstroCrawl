"""LogfmtLogger — 纯 logfmt 格式日志（event 强制位置参数 + **kwargs 键值对渲染）。"""

from __future__ import annotations

import json
import logging
import re
import sys
from typing import Literal

_FILE_LOG_MAX_BYTES = 10 * 1024 * 1024
_FILE_LOG_BACKUP_COUNT = 3

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_LOG_DATE_FORMAT = "%H:%M:%S"
_ASTROBASIS_HANDLER = True  # handler marker tag — used for setup_root_logger dedup

_LOGFRMT_NEEDS_QUOTE = re.compile(r'[\s"]')


def _logfmt_escape(value: object) -> str:
    """Escape a value per Brandur Leach logfmt spec, single-line guaranteed.

    Control characters (\n, \r, \t) are replaced with spaces.
    Values containing spaces or double-quotes are quoted and internal special chars escaped.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    s = str(value).replace("\n", " ").replace("\r", " ").replace("\t", " ")
    if not s:
        return '""'
    if _LOGFRMT_NEEDS_QUOTE.search(s):
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return s


def _format_exc_oneline(exc_info: tuple | None) -> str:
    """Single-line exception summary: ``TypeError: msg at /path/file.py:42``.

    For console logfmt output — full traceback is preserved in file logs.
    """
    if not exc_info or exc_info[0] is None:
        return ""
    exc_type, exc_value, exc_tb = exc_info
    msg = str(exc_value) if exc_value else ""
    if exc_tb:
        while exc_tb.tb_next:
            exc_tb = exc_tb.tb_next
        return f"{exc_type.__name__}: {msg} at {exc_tb.tb_frame.f_code.co_filename}:{exc_tb.tb_lineno}"
    return f"{exc_type.__name__}: {msg}"


def _format_logfmt_message(event: str, fields: dict[str, object]) -> str:
    """Build logfmt message body fragment: ``event=xxx key=value ...``

    Called by LogfmtLogger._log() to pre-build the msg string passed to stdlib logger.
    The Formatter obtains this fragment via record.getMessage().
    """
    parts = [f"event={_logfmt_escape(event)}"]
    for k, v in fields.items():
        parts.append(f"{_logfmt_escape(k)}={_logfmt_escape(v)}")
    return " ".join(parts)


class LogfmtFormatter(logging.Formatter):
    """Console + GUI log panel use. One line of pure logfmt per record.

    All records get their message body via record.getMessage() —
    LogfmtLogger pre-builds "event=xxx key=value" as msg in _log();
    legacy records have the original "event=xxx ..." format string after %-formatting.
    The Formatter only wraps metadata (ts/level/logger/thread) and exception summary.
    """

    def format(self, record: logging.LogRecord) -> str:
        record.asctime = self.formatTime(record, self.datefmt)
        parts = [
            f"ts={record.asctime}",
            f"level={record.levelname.lower()}",
            f"logger={record.name}",
        ]
        if record.threadName != "MainThread":
            parts.append(f"thread={_logfmt_escape(record.threadName)}")

        parts.append(record.getMessage())

        if record.exc_info and record.exc_info[0] is not None:
            parts.append(f"error={_logfmt_escape(_format_exc_oneline(record.exc_info))}")
        return " ".join(parts)


class JsonLogFormatter(logging.Formatter):
    """JSON Lines format (file output, for log aggregation systems).

    Not enabled by default — explicitly enable via setup_root_logger(json_file=True).
    Structured records read _logfmt_event / _logfmt_fields from extra.
    Legacy records fall back to msg field.
    Exceptions preserve full multi-line traceback (files are for post-mortem analysis).

    Note: exception handling before field loop — ensures data["error"] is written first,
    so subsequent user fields named "error" trigger collision detection → field_error.
    """

    def format(self, record: logging.LogRecord) -> str:
        record.asctime = self.formatTime(record, self.datefmt)
        data: dict[str, object] = {
            "ts": record.asctime,
            "level": record.levelname.lower(),
            "logger": record.name,
        }
        if record.threadName != "MainThread":
            data["thread"] = record.threadName

        event = getattr(record, "_logfmt_event", None)
        if event is not None:
            data["event"] = event
        else:
            data["msg"] = record.getMessage()

        if record.exc_info and record.exc_info[0] is not None:
            data["error"] = self.formatException(record.exc_info)

        if event is not None:
            fields = getattr(record, "_logfmt_fields", None)
            if fields:
                for k, v in fields.items():
                    data[f"field_{k}" if k in data else k] = v

        return json.dumps(data, ensure_ascii=False, default=str)


class LogfmtLogger:
    """logfmt structured logging wrapper. event is a mandatory positional argument.

    Usage:
        log = LogfmtLogger("astrobasis.mymodule")
        log.info("crawl_start", depth=3, concurrency=4)
        log.warning("slot_exhausted", idx=5, attempts=3, exc_info=True)
        log.exception("crawl_error", url=url)  # auto-attach traceback

        # Pre-bound context
        ctx_log = log.bind(crawl_id="abc123")
        ctx_log.info("worker_start", idx=0)  # auto-carries crawl_id
    """

    __slots__ = ("_logger", "_bound_fields")

    def __init__(self, name: str, /, **bound: object) -> None:
        self._logger = logging.getLogger(name)
        self._bound_fields = bound

    @property
    def name(self) -> str:
        return self._logger.name

    def bind(self, **kwargs: object) -> LogfmtLogger:
        """Return a new instance with additional bound context. Original unchanged."""
        merged = type(self).__new__(type(self))
        merged._logger = self._logger
        merged._bound_fields = {**self._bound_fields, **kwargs}
        return merged

    def isEnabledFor(self, level: int) -> bool:
        return self._logger.isEnabledFor(level)

    def setLevel(self, level: int) -> None:
        self._logger.setLevel(level)

    def getEffectiveLevel(self) -> int:
        return self._logger.getEffectiveLevel()

    def debug(self, event: str, /, *, exc_info: bool = False, **fields: object) -> None:
        if self._logger.isEnabledFor(logging.DEBUG):
            self._log(logging.DEBUG, event, exc_info, fields)

    def info(self, event: str, /, *, exc_info: bool = False, **fields: object) -> None:
        if self._logger.isEnabledFor(logging.INFO):
            self._log(logging.INFO, event, exc_info, fields)

    def warning(self, event: str, /, *, exc_info: bool = False, **fields: object) -> None:
        if self._logger.isEnabledFor(logging.WARNING):
            self._log(logging.WARNING, event, exc_info, fields)

    def error(self, event: str, /, *, exc_info: bool = False, **fields: object) -> None:
        if self._logger.isEnabledFor(logging.ERROR):
            self._log(logging.ERROR, event, exc_info, fields)

    def critical(self, event: str, /, *, exc_info: bool = False, **fields: object) -> None:
        if self._logger.isEnabledFor(logging.CRITICAL):
            self._log(logging.CRITICAL, event, exc_info, fields)

    def exception(self, event: str, /, **fields: object) -> None:
        """Log at ERROR level with exception traceback. Must be called inside an except block."""
        if self._logger.isEnabledFor(logging.ERROR):
            self._log(logging.ERROR, event, True, fields)

    def _log(self, level: int, event: str, exc_info: bool, fields: dict[str, object]) -> None:
        """SSOT for all formatting logic.

        Pre-builds the logfmt message body as msg passed to stdlib logger —
        Formatter obtains it via record.getMessage(), no need to distinguish
        structured vs legacy records.
        _logfmt_event and _logfmt_fields are only injected via extra=, for JsonLogFormatter use.
        """
        merged = {**self._bound_fields, **fields}
        msg = _format_logfmt_message(event, merged)
        extra: dict[str, object] = {
            "_logfmt_event": event,
            "_logfmt_fields": merged,
        }
        self._logger.log(level, msg, exc_info=exc_info, extra=extra, stacklevel=2)


def setup_root_logger(
    level: int = logging.INFO,
    log_file: str = "",
    *,
    format_style: Literal["logfmt", "classic"] = "logfmt",
    json_file: bool = False,
) -> None:
    """Configure root logger. format_style="classic" for one-click legacy rollback.

    Console always uses LogfmtFormatter (or classic fallback). json_file only affects the file handler.
    """
    root = logging.getLogger()
    root.setLevel(level)
    for h in root.handlers[:]:
        if getattr(h, "_astrobasis_handler", False):
            root.removeHandler(h)

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch._astrobasis_handler = _ASTROBASIS_HANDLER  # type: ignore[attr-defined]
    if format_style == "classic":
        ch.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATE_FORMAT))
    else:
        ch.setFormatter(LogfmtFormatter(datefmt=_LOG_DATE_FORMAT))
    root.addHandler(ch)

    # File handler
    if log_file:
        try:
            from logging.handlers import RotatingFileHandler

            fh = RotatingFileHandler(
                log_file, maxBytes=_FILE_LOG_MAX_BYTES, backupCount=_FILE_LOG_BACKUP_COUNT, encoding="utf-8"
            )
            fh._astrobasis_handler = _ASTROBASIS_HANDLER  # type: ignore[attr-defined]
            if json_file:
                fh.setFormatter(JsonLogFormatter(datefmt=_LOG_DATE_FORMAT))
            elif format_style == "classic":
                fh.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATE_FORMAT))
            else:
                fh.setFormatter(LogfmtFormatter(datefmt=_LOG_DATE_FORMAT))
            root.addHandler(fh)
        except Exception as e:
            print(f"Failed to setup file logging: {e}")

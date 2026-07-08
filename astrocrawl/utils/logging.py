from __future__ import annotations

import logging
import re
import sys
from typing import Literal

from astrocrawl._constants import FILE_LOG_BACKUP_COUNT, FILE_LOG_MAX_BYTES

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_LOG_DATE_FORMAT = "%H:%M:%S"
_ASTROCRAWL_HANDLER = True  # handler marker tag — 用于 setup_root_logger 去重

_LOGFRMT_NEEDS_QUOTE = re.compile(r'[\s"]')


def _logfmt_escape(value: object) -> str:
    """按 Brandur Leach logfmt 规范转义值，保证单行输出。

    控制字符（\\n, \\r, \\t）统一替换为空格，确保一行一条记录。
    含空格或双引号的值用双引号包裹并转义内部特殊字符。
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
    """单行异常摘要: ``TypeError: msg at /path/file.py:42``。

    用于控制台 logfmt 输出——完整堆栈保留在文件日志中。
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
    """构造 logfmt 消息体片段: ``event=xxx key=value ...``

    由 LogfmtLogger._log() 调用，将 event + fields 预构造为 msg 字符串传入 stdlib logger。
    Formatter 通过 record.getMessage() 获取此片段，无需区分结构化/遗留记录。
    """
    parts = [f"event={_logfmt_escape(event)}"]
    for k, v in fields.items():
        parts.append(f"{_logfmt_escape(k)}={_logfmt_escape(v)}")
    return " ".join(parts)


class LogfmtFormatter(logging.Formatter):
    """控制台 + GUI 日志面板用。一行一条纯 logfmt 记录。

    所有记录统一通过 record.getMessage() 获取消息体片段——
    LogfmtLogger 在 _log() 中预构造好 "event=xxx key=value" 作为 msg 传入；
    遗留记录则是原始的 "event=xxx ..." 格式字符串经 % 格式化后的结果。
    Formatter 只负责包裹元信息（ts/level/logger/thread）和异常摘要。
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
    """JSON Lines 格式（文件输出，对接日志聚合系统）。

    暂不启用——通过 setup_root_logger(json_file=True) 显式开启。
    结构化记录从 extra 中读取 _logfmt_event / _logfmt_fields 展开到 JSON 顶层；
    遗留记录退化为 msg 字段。
    异常保留完整多行堆栈（文件用于事后分析，无需压缩为单行）。

    注意：异常处理在字段循环之前——确保 data["error"] 先写入，
    后续用户字段名为 error 时碰撞检测自动写为 field_error，两者都不丢失。
    """

    def format(self, record: logging.LogRecord) -> str:
        import json

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

        # 异常处理先于字段循环——data["error"] 先占位，后续字段循环的碰撞检测生效
        if record.exc_info and record.exc_info[0] is not None:
            data["error"] = self.formatException(record.exc_info)

        # 字段循环在后——已存在的标准键若与用户字段同名，自动加 field_ 前缀保护
        if event is not None:
            fields = getattr(record, "_logfmt_fields", None)
            if fields:
                for k, v in fields.items():
                    data[f"field_{k}" if k in data else k] = v

        return json.dumps(data, ensure_ascii=False, default=str)


class LogfmtLogger:
    """logfmt 结构化日志封装。event 是强制位置参数，无法遗漏。

    用法:
        log = LogfmtLogger("astrocrawl.crawler")
        log.info("crawl_start", depth=3, concurrency=4)
        log.warning("slot_exhausted", idx=5, attempts=3, exc_info=True)
        log.exception("crawl_error", url=url)  # 自动附带堆栈

        # 预绑定上下文
        ctx_log = log.bind(crawl_id="abc123")
        ctx_log.info("worker_start", idx=0)  # 自动携带 crawl_id
    """

    __slots__ = ("_logger", "_bound_fields")

    def __init__(self, name: str, /, **bound: object) -> None:
        self._logger = logging.getLogger(name)
        self._bound_fields = bound

    @property
    def name(self) -> str:
        return self._logger.name

    def bind(self, **kwargs: object) -> LogfmtLogger:
        """返回绑定额外上下文的新实例。原实例不变。"""
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
        """记录 ERROR 级别日志并附带异常堆栈。必须在 except 块内调用。"""
        if self._logger.isEnabledFor(logging.ERROR):
            self._log(logging.ERROR, event, True, fields)

    def _log(self, level: int, event: str, exc_info: bool, fields: dict[str, object]) -> None:
        """所有格式化逻辑的 SSOT。

        预构造 logfmt 消息体作为 msg 传入 stdlib logger——
        Formatter 通过 record.getMessage() 获取，无需区分结构化/遗留记录。
        _logfmt_event 和 _logfmt_fields 仅通过 extra= 注入，供 JsonLogFormatter 使用。
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
    """配置根日志。format_style="classic" 一键回退旧格式；json_file=True 启用 JSON 文件输出。

    控制台始终用 LogfmtFormatter（或 classic 回退），json_file 仅影响文件 handler。
    """
    root = logging.getLogger()
    root.setLevel(level)
    for h in root.handlers[:]:
        if getattr(h, "_astrocrawl_handler", False):
            root.removeHandler(h)

    # 控制台 handler
    ch = logging.StreamHandler(sys.stdout)
    ch._astrocrawl_handler = _ASTROCRAWL_HANDLER  # type: ignore[attr-defined]
    if format_style == "classic":
        ch.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATE_FORMAT))
    else:
        ch.setFormatter(LogfmtFormatter(datefmt=_LOG_DATE_FORMAT))
    root.addHandler(ch)

    # 文件 handler
    if log_file:
        try:
            from logging.handlers import RotatingFileHandler

            fh = RotatingFileHandler(
                log_file, maxBytes=FILE_LOG_MAX_BYTES, backupCount=FILE_LOG_BACKUP_COUNT, encoding="utf-8"
            )
            fh._astrocrawl_handler = _ASTROCRAWL_HANDLER  # type: ignore[attr-defined]
            if json_file:
                fh.setFormatter(JsonLogFormatter(datefmt=_LOG_DATE_FORMAT))
            elif format_style == "classic":
                fh.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATE_FORMAT))
            else:
                fh.setFormatter(LogfmtFormatter(datefmt=_LOG_DATE_FORMAT))
            root.addHandler(fh)
        except Exception as e:
            print(f"Failed to setup file logging: {e}")

"""LogfmtLogger / LogfmtFormatter / JsonLogFormatter / setup_root_logger tests."""

from __future__ import annotations

import logging

from astrobasis import JsonLogFormatter, LogfmtFormatter, LogfmtLogger, setup_root_logger
from astrobasis._logging import _format_exc_oneline, _format_logfmt_message, _logfmt_escape


class TestLogfmtEscape:
    def test_none(self):
        assert _logfmt_escape(None) == "null"

    def test_true(self):
        assert _logfmt_escape(True) == "true"

    def test_false(self):
        assert _logfmt_escape(False) == "false"

    def test_int(self):
        assert _logfmt_escape(42) == "42"

    def test_float(self):
        assert _logfmt_escape(3.14) == "3.14"

    def test_plain_string(self):
        assert _logfmt_escape("hello") == "hello"

    def test_empty_string(self):
        assert _logfmt_escape("") == '""'

    def test_string_with_space(self):
        assert _logfmt_escape("hello world") == '"hello world"'

    def test_string_with_quote(self):
        result = _logfmt_escape('say "hi"')
        assert result == '"say \\"hi\\""'

    def test_newline_replaced(self):
        result = _logfmt_escape("line1\nline2")
        assert "\n" not in result
        assert "line1 line2" in result

    def test_tab_replaced(self):
        result = _logfmt_escape("col1\tcol2")
        assert "\t" not in result
        assert "col1 col2" in result

    def test_carriage_return_replaced(self):
        result = _logfmt_escape("line1\rline2")
        assert "\r" not in result

    def test_backslash(self):
        assert _logfmt_escape("a\\b") == "a\\b"

    def test_dots_in_event_name(self):
        assert _logfmt_escape("gen_ai.chat.request") == "gen_ai.chat.request"

    def test_unicode(self):
        assert _logfmt_escape("中文") == "中文"

    def test_double_space_preserved(self):
        assert _logfmt_escape("hello  world") == '"hello  world"'


class TestFormatExcOneline:
    def test_none(self):
        assert _format_exc_oneline(None) == ""

    def test_none_tuple(self):
        assert _format_exc_oneline((None, None, None)) == ""

    def test_simple_exception(self):
        try:
            raise ValueError("test error")
        except ValueError:
            result = _format_exc_oneline(__import__("sys").exc_info())
        assert result.startswith("ValueError: test error at ")

    def test_exception_no_traceback(self):
        assert _format_exc_oneline((ValueError, ValueError("msg"), None)) == "ValueError: msg"


class TestFormatLogfmtMessage:
    def test_empty_fields(self):
        assert _format_logfmt_message("crawl_start", {}) == "event=crawl_start"

    def test_single_field(self):
        assert _format_logfmt_message("ev", {"key": "val"}) == "event=ev key=val"

    def test_multiple_fields(self):
        result = _format_logfmt_message("crawl_start", {"depth": 3, "url": "http://x.com"})
        assert "event=crawl_start" in result
        assert "depth=3" in result
        assert "url=http://x.com" in result

    def test_field_value_with_space(self):
        result = _format_logfmt_message("ev", {"name": "hello world"})
        assert 'name="hello world"' in result

    def test_bool_and_float_values(self):
        result = _format_logfmt_message("ev", {"active": True, "ratio": 0.75})
        assert "active=true" in result
        assert "ratio=0.75" in result


class TestLogfmtFormatter:
    def _make_record(self, **extra):
        record = logging.LogRecord(
            name="astrobasis.test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="event=hello key=val",
            args=(),
            exc_info=None,
        )
        for k, v in extra.items():
            setattr(record, k, v)
        return record

    def test_basic_output(self):
        fmt = LogfmtFormatter(datefmt="%H:%M:%S")
        record = self._make_record()
        output = fmt.format(record)
        assert "ts=" in output
        assert "level=info" in output
        assert "logger=astrobasis.test" in output
        assert "event=hello key=val" in output

    def test_no_event_field_for_structured(self):
        fmt = LogfmtFormatter(datefmt="%H:%M:%S")
        record = self._make_record(_logfmt_event="real_event")
        output = fmt.format(record)
        assert "event=hello key=val" in output
        assert "event=real_event" not in output

    def test_legacy_record_message(self):
        fmt = LogfmtFormatter(datefmt="%H:%M:%S")
        record = logging.LogRecord(
            name="test",
            level=logging.WARNING,
            pathname="",
            lineno=0,
            msg="plain message without event",
            args=(),
            exc_info=None,
        )
        output = fmt.format(record)
        assert "plain message without event" in output

    def test_with_exception(self):
        fmt = LogfmtFormatter(datefmt="%H:%M:%S")
        try:
            raise ValueError("bad")
        except ValueError:
            record = logging.LogRecord(
                name="test",
                level=logging.ERROR,
                pathname="",
                lineno=0,
                msg="event=err",
                args=(),
                exc_info=__import__("sys").exc_info(),
            )
        output = fmt.format(record)
        assert "error=" in output
        assert "ValueError" in output
        assert "bad" in output

    def test_no_exception_no_error_field(self):
        fmt = LogfmtFormatter(datefmt="%H:%M:%S")
        record = self._make_record()
        output = fmt.format(record)
        assert "error=" not in output

    def test_level_lowercase(self):
        fmt = LogfmtFormatter(datefmt="%H:%M:%S")
        record = self._make_record()
        record.levelname = "WARNING"
        output = fmt.format(record)
        assert "level=warning" in output

    def test_non_main_thread(self):
        fmt = LogfmtFormatter(datefmt="%H:%M:%S")
        record = self._make_record()
        record.threadName = "Worker-1"
        output = fmt.format(record)
        assert "thread=Worker-1" in output


class TestJsonLogFormatter:
    def _make_record(self, **extra):
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="event=hello key=val",
            args=(),
            exc_info=None,
        )
        for k, v in extra.items():
            setattr(record, k, v)
        return record

    def test_structured_record(self):
        fmt = JsonLogFormatter(datefmt="%H:%M:%S")
        record = self._make_record(_logfmt_event="hello", _logfmt_fields={"key": "val"})
        output = fmt.format(record)
        import json

        data = json.loads(output)
        assert data["event"] == "hello"
        assert data["key"] == "val"

    def test_legacy_record(self):
        fmt = JsonLogFormatter(datefmt="%H:%M:%S")
        record = self._make_record()
        output = fmt.format(record)
        import json

        data = json.loads(output)
        assert "msg" in data
        assert "event" not in data

    def test_collision_detection(self):
        fmt = JsonLogFormatter(datefmt="%H:%M:%S")
        record = self._make_record(_logfmt_event="ev", _logfmt_fields={"ts": "user_ts"})
        output = fmt.format(record)
        import json

        data = json.loads(output)
        assert data["field_ts"] == "user_ts"
        assert data["ts"] != "user_ts"

    def test_error_collision_with_exception(self):
        fmt = JsonLogFormatter(datefmt="%H:%M:%S")
        try:
            raise ValueError("bad")
        except ValueError:
            record = logging.LogRecord(
                name="test",
                level=logging.ERROR,
                pathname="",
                lineno=0,
                msg="event=ev error=user_val",
                args=(),
                exc_info=__import__("sys").exc_info(),
            )
            record._logfmt_event = "ev"
            record._logfmt_fields = {"error": "user_val"}
        output = fmt.format(record)
        import json

        data = json.loads(output)
        assert "error" in data
        assert "field_error" in data
        assert data["field_error"] == "user_val"


class TestLogfmtLogger:
    def _capture(self, logger):
        records: list[logging.LogRecord] = []

        class _Captor(logging.Handler):
            def emit(self, r):
                records.append(r)

        captor = _Captor()
        captor.setLevel(logging.DEBUG)
        logger._logger.setLevel(logging.DEBUG)
        logger._logger.addHandler(captor)
        return records, captor

    def _cleanup(self, log, captor):
        log._logger.removeHandler(captor)
        log._logger.setLevel(logging.NOTSET)

    def test_info_captures(self):
        log = LogfmtLogger("astrobasis.test_info_captures")
        records, captor = self._capture(log)
        log.info("test_ev", key="val")
        assert len(records) == 1
        r = records[0]
        assert r._logfmt_event == "test_ev"  # type: ignore[attr-defined]
        assert r._logfmt_fields == {"key": "val"}  # type: ignore[attr-defined]
        self._cleanup(log, captor)

    def test_debug_disabled(self):
        log = LogfmtLogger("astrobasis.test_debug_disabled")
        records, captor = self._capture(log)
        log._logger.setLevel(logging.WARNING)
        log.debug("should_not_appear")
        assert len(records) == 0
        self._cleanup(log, captor)

    def test_info_disabled(self):
        log = LogfmtLogger("astrobasis.test_info_disabled")
        records, captor = self._capture(log)
        log._logger.setLevel(logging.WARNING)
        log.info("should_not_appear")
        assert len(records) == 0
        self._cleanup(log, captor)

    def test_bind_independent(self):
        log = LogfmtLogger("astrobasis.test_bind_independent")
        a = log.bind(worker=1)
        b = log.bind(worker=2)
        assert a._bound_fields == {"worker": 1}
        assert b._bound_fields == {"worker": 2}
        assert log._bound_fields == {}

    def test_bind_chained(self):
        log = LogfmtLogger("astrobasis.test_bind_chained")
        ctx = log.bind(a=1).bind(b=2)
        assert ctx._bound_fields == {"a": 1, "b": 2}

    def test_bind_does_not_modify_original(self):
        log = LogfmtLogger("astrobasis.test_bind_immutable", base="x")
        log.bind(extra="y")
        assert log._bound_fields == {"base": "x"}

    def test_fields_override_bound(self):
        log = LogfmtLogger("astrobasis.test_fields_override", k="bound_val")
        records, captor = self._capture(log)
        log.info("ev", k="call_val")
        assert records[0]._logfmt_fields == {"k": "call_val"}  # type: ignore[attr-defined]
        self._cleanup(log, captor)

    def test_exception_method(self):
        log = LogfmtLogger("astrobasis.test_exception")
        records, captor = self._capture(log)
        try:
            raise ValueError("bad")
        except ValueError:
            log.exception("exc_ev")
        assert len(records) == 1
        assert records[0]._logfmt_event == "exc_ev"  # type: ignore[attr-defined]
        assert records[0].exc_info is not None
        self._cleanup(log, captor)

    def test_exc_info_keyword(self):
        log = LogfmtLogger("astrobasis.test_exc_info")
        records, captor = self._capture(log)
        log.error("err_ev", exc_info=True)
        assert records[0].exc_info is not None
        self._cleanup(log, captor)

    def test_name_property(self):
        log = LogfmtLogger("astrobasis.test_name")
        assert log.name == "astrobasis.test_name"

    def test_isEnabledFor(self):
        log = LogfmtLogger("astrobasis.test_isenabled")
        log._logger.setLevel(logging.WARNING)
        assert not log.isEnabledFor(logging.DEBUG)
        assert not log.isEnabledFor(logging.INFO)
        assert log.isEnabledFor(logging.WARNING)
        assert log.isEnabledFor(logging.ERROR)
        log._logger.setLevel(logging.NOTSET)

    def test_all_six_methods(self):
        log = LogfmtLogger("astrobasis.test_all_six")
        log._logger.setLevel(logging.DEBUG)
        records, captor = self._capture(log)
        log.debug("d")
        log.info("i")
        log.warning("w")
        log.error("e")
        log.critical("c")
        try:
            raise ValueError("x")
        except ValueError:
            log.exception("exc")
        assert len(records) == 6
        events = [r._logfmt_event for r in records]  # type: ignore[attr-defined]
        assert events == ["d", "i", "w", "e", "c", "exc"]
        assert records[5].exc_info is not None
        self._cleanup(log, captor)

    def test_msg_includes_event_and_fields(self):
        log = LogfmtLogger("astrobasis.test_msg_fields")
        records, captor = self._capture(log)
        log.info("test_ev", url="http://example.com", count=3)
        msg = records[0].getMessage()
        assert msg.startswith("event=test_ev")
        assert "url=http://example.com" in msg
        assert "count=3" in msg
        self._cleanup(log, captor)


class TestSetupRootLogger:
    def test_default_formatter_is_logfmt(self):
        root = logging.getLogger()
        prev_level, prev_handlers = root.level, list(root.handlers)
        try:
            setup_root_logger()
            for h in root.handlers:
                if getattr(h, "_astrobasis_handler", False):
                    assert isinstance(h.formatter, LogfmtFormatter)
                    break
        finally:
            root.setLevel(prev_level)
            root.handlers[:] = prev_handlers

    def test_classic_format_style(self):
        root = logging.getLogger()
        prev_level, prev_handlers = root.level, list(root.handlers)
        try:
            setup_root_logger(format_style="classic")
            for h in root.handlers:
                if getattr(h, "_astrobasis_handler", False):
                    assert isinstance(h.formatter, logging.Formatter)
                    assert not isinstance(h.formatter, LogfmtFormatter)
                    break
        finally:
            root.setLevel(prev_level)
            root.handlers[:] = prev_handlers

    def test_backward_compatible_call(self):
        root = logging.getLogger()
        prev_level, prev_handlers = root.level, list(root.handlers)
        try:
            setup_root_logger(logging.DEBUG, "/tmp/test_astrobasis.log")
            for h in root.handlers:
                if getattr(h, "_astrobasis_handler", False):
                    if hasattr(h, "baseFilename"):
                        h.close()
        finally:
            root.setLevel(prev_level)
            root.handlers[:] = prev_handlers

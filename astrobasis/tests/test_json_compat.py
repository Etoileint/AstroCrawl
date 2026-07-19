"""JSON compat layer tests — orjson fast path and stdlib fallback path."""

from __future__ import annotations

import importlib
import json
import logging
import sys
from unittest.mock import MagicMock

import pytest

from astrobasis import _json_compat

_ORIGINAL_JSON_MOD = sys.modules.get("orjson")


def _restore():
    if _ORIGINAL_JSON_MOD is not None:
        sys.modules["orjson"] = _ORIGINAL_JSON_MOD
    else:
        sys.modules.pop("orjson", None)
    importlib.reload(_json_compat)


@pytest.fixture(autouse=True)
def _isolate_modules():
    yield
    _restore()


def _reload_with_orjson(orjson_module):
    sys.modules["orjson"] = orjson_module
    importlib.reload(_json_compat)


def _reload_without_orjson():
    sys.modules["orjson"] = None
    importlib.reload(_json_compat)


class TestJsonCompatWithoutOrjson:
    def test_dumps_returns_utf8_bytes_newline_terminated(self):
        _reload_without_orjson()
        result = _json_compat.json_dumps({"a": 1})
        assert isinstance(result, bytes)
        assert result.endswith(b"\n")
        assert b": " in result, "stdlib path not taken — _reload_without_orjson() did not block orjson import"

    def test_dumps_ensure_ascii_false_preserves_unicode(self):
        _reload_without_orjson()
        result = _json_compat.json_dumps({"key": "中文"})
        decoded = result.decode("utf-8")
        assert "中文" in decoded

    def test_loads_returns_python_objects(self):
        _reload_without_orjson()
        data = _json_compat.json_dumps({"x": [1, 2, 3]})
        result = json.loads(data.decode("utf-8"))
        assert result == {"x": [1, 2, 3]}

    def test_roundtrip_mixed_types(self):
        _reload_without_orjson()
        original = {
            "str": "hello",
            "int": 42,
            "float": 3.14,
            "none": None,
            "list": [1, "two", None],
            "nested": {"a": True, "b": False},
        }
        dumped = _json_compat.json_dumps(original)
        loaded = json.loads(dumped.decode("utf-8"))
        assert loaded == original

    def test_dumps_accepts_list(self):
        _reload_without_orjson()
        result = _json_compat.json_dumps([1, 2, 3])
        loaded = json.loads(result.decode("utf-8"))
        assert loaded == [1, 2, 3]

    def test_stdlib_fallback_emits_debug_log(self, caplog):
        caplog.set_level(logging.DEBUG, logger="astrobasis.json_compat")
        _reload_without_orjson()
        assert "orjson_unavailable" in caplog.text


class TestJsonCompatWithOrjson:
    def test_dumps_delegates_to_orjson_with_option(self):
        mock_orjson = MagicMock()
        mock_orjson.OPT_APPEND_NEWLINE = 4
        mock_orjson.dumps.return_value = b'{"a":1}'
        _reload_with_orjson(mock_orjson)

        result = _json_compat.json_dumps({"a": 1})
        mock_orjson.dumps.assert_called_once_with({"a": 1}, option=4)
        assert result == b'{"a":1}'

    def test_dumps_returns_bytes(self):
        mock_orjson = MagicMock()
        mock_orjson.OPT_APPEND_NEWLINE = 4
        mock_orjson.dumps.return_value = b'{"c":3}'
        _reload_with_orjson(mock_orjson)

        result = _json_compat.json_dumps({"c": 3})
        assert isinstance(result, bytes)

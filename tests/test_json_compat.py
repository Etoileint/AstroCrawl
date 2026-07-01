"""JSON 兼容层测试 — orjson 加速路径与 stdlib 回退路径。

使用 importlib.reload 控制模块初始化时的 import 条件，
分别验证 orjson 路径、stdlib 回退路径、降级日志。

每个测试仅操纵 orjson 和 _json_compat 两个模块，
测试后恢复原始状态，避免全局 sys.modules 污染。
"""

from __future__ import annotations

import importlib
import json
import logging
import sys
from unittest.mock import MagicMock

import pytest

from astrocrawl import _json_compat

# 保存原始模块引用（仅一次）
_ORIGINAL_JSON_MOD = sys.modules.get("orjson")


def _restore():
    """恢复 _json_compat 和 orjson 模块到原始状态。"""
    if _ORIGINAL_JSON_MOD is not None:
        sys.modules["orjson"] = _ORIGINAL_JSON_MOD
    else:
        sys.modules.pop("orjson", None)
    importlib.reload(_json_compat)


@pytest.fixture(autouse=True)
def _isolate_modules():
    """每个测试后恢复 orjson + _json_compat 到原始状态。"""
    yield
    _restore()


def _reload_with_orjson(orjson_module):
    """用指定的 orjson 模块重新加载 _json_compat。"""
    sys.modules["orjson"] = orjson_module
    importlib.reload(_json_compat)


def _reload_without_orjson():
    """移除 orjson 后重新加载 _json_compat（触发 stdlib 回退）。"""
    sys.modules["orjson"] = None
    importlib.reload(_json_compat)


class TestJsonCompatWithoutOrjson:
    """stdlib json 回退路径 — orjson 不可导入。"""

    def test_dumps_returns_utf8_bytes_newline_terminated(self):
        _reload_without_orjson()
        result = _json_compat._json_dumps({"a": 1})
        assert isinstance(result, bytes)
        assert result.endswith(b"\n")
        # stdlib json.dumps 输出 ": " (冒号后空格)，orjson 紧凑无空格
        assert b": " in result, "stdlib path not taken — _reload_without_orjson() did not block orjson import"

    def test_dumps_ensure_ascii_false_preserves_unicode(self):
        _reload_without_orjson()
        result = _json_compat._json_dumps({"key": "中文"})
        decoded = result.decode("utf-8")
        assert "中文" in decoded

    def test_loads_returns_python_objects(self):
        _reload_without_orjson()
        data = _json_compat._json_dumps({"x": [1, 2, 3]})
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
        dumped = _json_compat._json_dumps(original)
        loaded = json.loads(dumped.decode("utf-8"))
        assert loaded == original

    def test_dumps_accepts_list(self):
        _reload_without_orjson()
        result = _json_compat._json_dumps([1, 2, 3])
        loaded = json.loads(result.decode("utf-8"))
        assert loaded == [1, 2, 3]

    def test_stdlib_fallback_emits_debug_log(self, caplog):
        """orjson 不可导入时，模块加载期发出 DEBUG 降级日志。"""
        caplog.set_level(logging.DEBUG, logger="astrocrawl.json_compat")
        _reload_without_orjson()
        assert "orjson_unavailable" in caplog.text


class TestJsonCompatWithOrjson:
    """orjson 加速路径。"""

    def test_dumps_delegates_to_orjson_with_option(self):
        mock_orjson = MagicMock()
        mock_orjson.OPT_APPEND_NEWLINE = 4
        mock_orjson.dumps.return_value = b'{"a":1}'
        _reload_with_orjson(mock_orjson)

        result = _json_compat._json_dumps({"a": 1})
        mock_orjson.dumps.assert_called_once_with({"a": 1}, option=4)
        assert result == b'{"a":1}'

    def test_dumps_returns_bytes(self):
        mock_orjson = MagicMock()
        mock_orjson.OPT_APPEND_NEWLINE = 4
        mock_orjson.dumps.return_value = b'{"c":3}'
        _reload_with_orjson(mock_orjson)

        result = _json_compat._json_dumps({"c": 3})
        assert isinstance(result, bytes)

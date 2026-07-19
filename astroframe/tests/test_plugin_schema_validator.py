"""插件 JSON Schema 校验器测试 — ADR-0011 决策 11。

覆盖 validate_config（jsonschema 硬依赖）、x-env-var 注入、default 补全、custom extension 剥离。
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from astroframe._errors import SchemaValidationError
from astroframe._schema_validator import (
    _apply_defaults,
    _coerce_env_value,
    _resolve_env_vars,
    _strip_custom_extensions,
    validate_config,
)


def test_validate_config_works():
    """jsonschema 路径正常校验 + default 补全。"""
    schema = {
        "type": "object",
        "properties": {"key": {"type": "string", "default": "val"}},
    }
    result = validate_config({}, schema)
    assert result["key"] == "val"


def test_validate_config_required_fails():
    """缺少必填字段时抛 SchemaValidationError。"""
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    }
    with pytest.raises(SchemaValidationError):
        validate_config({}, schema)


def test_validate_config_empty_schema():
    result = validate_config({"any": "value"}, {})
    assert result["any"] == "value"


# ── x-env-var 环境变量注入 ─────────────────────────────────────────────────────


def test_resolve_env_vars_injects_from_env():
    """x-env-var 声明的字段从环境变量注入。"""
    schema = {
        "type": "object",
        "properties": {"api_key": {"type": "string", "x-env-var": "TEST_API_KEY"}},
    }
    with patch.dict(os.environ, {"TEST_API_KEY": "sk-secret"}, clear=True):
        result = _resolve_env_vars({}, schema)
    assert result["api_key"] == "sk-secret"


def test_resolve_env_vars_no_override_existing():
    """config 中已有值时不覆盖。"""
    schema = {
        "type": "object",
        "properties": {"api_key": {"type": "string", "x-env-var": "TEST_API_KEY"}},
    }
    with patch.dict(os.environ, {"TEST_API_KEY": "from-env"}, clear=True):
        result = _resolve_env_vars({"api_key": "from-config"}, schema)
    assert result["api_key"] == "from-config"


def test_resolve_env_vars_env_var_missing():
    """环境变量不存在时不注入，保留 config 原值。"""
    schema = {
        "type": "object",
        "properties": {"api_key": {"type": "string", "x-env-var": "MISSING_VAR"}},
    }
    result = _resolve_env_vars({}, schema)
    assert "api_key" not in result


def test_resolve_env_vars_non_dict_prop_skipped():
    schema = {"type": "object", "properties": {"key": "not-a-dict"}}
    result = _resolve_env_vars({}, schema)
    assert isinstance(result, dict)


# ── _coerce_env_value — 类型转换 ──────────────────────────────────────────────


def test_coerce_env_value_integer():
    assert _coerce_env_value("42", "integer") == 42


def test_coerce_env_value_integer_invalid():
    assert _coerce_env_value("abc", "integer") == "abc"


def test_coerce_env_value_number():
    assert _coerce_env_value("3.14", "number") == 3.14


def test_coerce_env_value_boolean_true():
    assert _coerce_env_value("true", "boolean") is True
    assert _coerce_env_value("1", "boolean") is True


def test_coerce_env_value_boolean_false():
    assert _coerce_env_value("false", "boolean") is False
    assert _coerce_env_value("0", "boolean") is False


def test_coerce_env_value_string():
    assert _coerce_env_value("hello", "string") == "hello"


# ── _apply_defaults — 默认值补全 ────────────────────────────────────────────────


def test_apply_defaults_adds_missing():
    schema = {
        "type": "object",
        "properties": {
            "timeout": {"type": "integer", "default": 30},
            "name": {"type": "string", "default": "default-name"},
        },
    }
    result = _apply_defaults({"name": "custom"}, schema)
    assert result["timeout"] == 30
    assert result["name"] == "custom"


def test_apply_defaults_no_overwrite():
    schema = {
        "type": "object",
        "properties": {"key": {"type": "string", "default": "default"}},
    }
    result = _apply_defaults({"key": "existing"}, schema)
    assert result["key"] == "existing"


def test_apply_defaults_empty_schema():
    result = _apply_defaults({}, {})
    assert result == {}


def test_apply_defaults_non_dict_property_skipped():
    schema = {"type": "object", "properties": {"invalid": "not-a-dict"}}
    _apply_defaults({}, schema)


# ── _strip_custom_extensions — x- 前缀剥离 ──────────────────────────────────────


def test_strip_custom_extensions_removes_x_prefix():
    schema = {
        "type": "object",
        "x-custom": "value",
        "properties": {
            "key": {
                "type": "string",
                "x-env-var": "MY_VAR",
            }
        },
    }
    cleaned = _strip_custom_extensions(schema)
    assert "x-custom" not in cleaned
    assert cleaned["type"] == "object"
    assert cleaned["properties"]["key"]["type"] == "string"
    assert "x-env-var" not in cleaned["properties"]["key"]


def test_strip_custom_extensions_non_dict_properties():
    schema = {"type": "object", "properties": {"key": "not-an-object"}}
    cleaned = _strip_custom_extensions(schema)
    assert cleaned["properties"]["key"] == "not-an-object"

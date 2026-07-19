"""JSON Schema 校验器 — 11 类型 + x-env-var 扩展（ADR-0011 决策 11）。

一份 schema 四层复用：GUI 渲染、CLI --set 校验、环境变量注入、plugin-state.json 写入前校验。
jsonschema 为硬依赖。
"""

from __future__ import annotations

import os
from typing import Any

import jsonschema

from astroframe._errors import SchemaValidationError


def validate_config(config: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """根据 config_schema 校验并补全插件 config。

    返回补全 default 后的 config 副本。校验失败抛 SchemaValidationError。
    """
    return _validate_with_jsonschema(config, schema)


def _resolve_env_vars(config: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """将 schema 中 x-env-var 标注的字段从环境变量注入 config。"""
    properties = schema.get("properties", {})
    result = dict(config)
    for key, prop_schema in properties.items():
        if not isinstance(prop_schema, dict):
            continue
        env_var = prop_schema.get("x-env-var")
        if env_var and key not in result:
            env_val = os.environ.get(env_var)
            if env_val is not None:
                # 根据 schema type 做基本类型转换
                prop_type = prop_schema.get("type", "string")
                result[key] = _coerce_env_value(env_val, prop_type)
    return result


def _coerce_env_value(value: str, schema_type: str) -> Any:
    """将环境变量字符串值转换为 schema 声明的类型。"""
    if schema_type == "integer":
        try:
            return int(value)
        except ValueError:
            return value
    if schema_type == "number":
        try:
            return float(value)
        except ValueError:
            return value
    if schema_type == "boolean":
        return value.lower() in ("true", "1", "yes")
    return value


def _validate_with_jsonschema(config: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """使用 jsonschema 库进行完整校验。"""
    config = _resolve_env_vars(config, schema)
    config = _apply_defaults(config, schema)

    # 构建 jsonschema 可接受的 schema 副本（移除 x-env-var 扩展）
    clean_schema = _strip_custom_extensions(schema)
    try:
        jsonschema.validate(instance=config, schema=clean_schema)
    except jsonschema.ValidationError as exc:
        raise SchemaValidationError(str(exc)) from exc

    return config


def _apply_defaults(config: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """将 schema 中声明的 default 值补全到 config。"""
    properties = schema.get("properties", {})
    result = dict(config)
    for key, prop in properties.items():
        if not isinstance(prop, dict):
            continue
        if key not in result and "default" in prop:
            result[key] = prop["default"]
    return result


def _strip_custom_extensions(schema: dict[str, Any]) -> dict[str, Any]:
    """移除 schema 中的自定义扩展字段（x-env-var 等），生成 jsonschema 兼容副本。"""
    result: dict[str, Any] = {}
    for k, v in schema.items():
        if k.startswith("x-"):
            continue
        if k == "properties" and isinstance(v, dict):
            result[k] = {
                pk: {sk: sv for sk, sv in pv.items() if not sk.startswith("x-")} if isinstance(pv, dict) else pv
                for pk, pv in v.items()
            }
        else:
            result[k] = v
    return result

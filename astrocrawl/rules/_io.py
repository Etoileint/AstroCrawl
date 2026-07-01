"""规则文件安全 I/O — 原子写入、重复 key 检测、导入导出、临时文件清理。

所有写入通过 atomic_write_json 保证崩溃安全 (S12)。
所有读取检测 JSON 重复 key (S25)。
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from astrocrawl._constants import MAX_RULE_FILE_SIZE, MAX_RULES_CACHE_SIZE, RULES_TMP_MAX_AGE_HOURS
from astrocrawl.rules._markdown import clean_markdown_wrapper
from astrocrawl.rules._schema import RuleSchema, validate_rule
from astrocrawl.utils._atomic import atomic_write_json

logger = logging.getLogger("astrocrawl.rules.io")


# ═══════════════════════════════════════════════════════════════════
# 安全读写
# ═══════════════════════════════════════════════════════════════════


def safe_read_rule_file(path: Path) -> dict[str, Any]:
    """安全读取规则 JSON 文件，含重复 key 检测 (S25)。

    Returns: parsed rule dict
    Raises: ValueError on duplicate keys or invalid JSON
    """
    raw = path.read_text(encoding="utf-8")
    if raw.startswith("﻿"):
        raw = raw[1:]

    decoder = json.JSONDecoder(object_pairs_hook=_check_duplicate_keys)
    try:
        data = decoder.decode(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON 解析失败: {e}") from e

    if not isinstance(data, dict):
        raise ValueError("规则文件必须是 JSON 对象")

    return data


def safe_write_rule_file(path: Path, data: dict[str, Any]) -> None:
    """原子写入规则文件 (S12 + S13 + C4 L1 持久化门)。

    L1 验证门不可绕过——任何代码写规则文件必须通过 validate_rule。
    对标 Django Model.save() → full_clean()。
    """
    validate_rule(data)
    atomic_write_json(path, data, max_bytes=MAX_RULE_FILE_SIZE, chmod_mask=0o600)


# ═══════════════════════════════════════════════════════════════════
# 序列化
# ═══════════════════════════════════════════════════════════════════


def rule_to_dict(rule: RuleSchema) -> dict[str, Any]:
    """将 RuleSchema 转为 dict，用于 I/O 序列化（CLI export、GUI edit、文件写入）。

    Pydantic model_dump(mode="json") 自动递归序列化全部嵌套模型，
    包含 MatchConfig、FieldRule 的 fallback 链、transform 等。"""
    return rule.model_dump(mode="json")


# ═══════════════════════════════════════════════════════════════════
# 导入 / 导出
# ═══════════════════════════════════════════════════════════════════


def export_rule(rule: RuleSchema) -> dict[str, Any]:
    """导出单条规则：剥离元数据 + 清空 test_urls + 隐私提示 (N29, N30)。

    返回一个"干净"的规则 dict，仅包含 Schema 定义的字段。
    """
    d = rule_to_dict(rule)
    clean = {
        "name": d.get("name", ""),
        "schema_version": d.get("schema_version", 1),
        "version": d.get("version", 1),
        "display_name": d.get("display_name", ""),
        "description": d.get("description", ""),
        "author": "",  # N29: 不导出 author
        "tags": d.get("tags", []),
        "enabled": d.get("enabled", True),
        "match": d.get("match", {}),
        "fields": d.get("fields", {}),
        "options": d.get("options", {}),
        # N30: test_urls 清空并附带提示
        "test_urls": [],
        "_export_note": "test_urls 已被移除——导出文件不含测试 URL",
    }
    return clean


def export_rule_to_file(rule: RuleSchema, output_path: Path) -> None:
    """导出单条规则到文件。"""
    clean = export_rule(rule)
    safe_write_rule_file(output_path, clean)


def export_all_rules(rules: list[RuleSchema], output_dir: Path) -> int:
    """批量导出全部规则 (N102)。返回成功导出数量。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for rule in rules:
        name = rule.name
        if not name:
            continue
        output_path = output_dir / f"{name}.json"
        try:
            export_rule_to_file(rule, output_path)
            count += 1
        except Exception as exc:
            logger.warning("event=rule_export_error rule=%s error=%s", name, exc)
    return count


def import_rule_preview(file_path: Path) -> dict[str, Any]:
    """预览导入规则——读取清洗后返回预览信息。不写入磁盘。

    L2 UX 早反馈：JSON 解析后调用 validate_rule，用户在确认导入前看到验证错误。
    Returns: {"name": ..., "fields_count": ..., "domains": ..., "raw": parsed_dict}
    """
    raw_text = file_path.read_text(encoding="utf-8")
    cleaned = clean_markdown_wrapper(raw_text)

    try:
        decoder = json.JSONDecoder(object_pairs_hook=_check_duplicate_keys)
        data = decoder.decode(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"无法解析为 JSON: {e}")

    if not isinstance(data, dict) or "name" not in data:
        raise ValueError("文件不含有效的规则定义（缺少 name 字段）")

    # L2: 早验证，避免用户确认后写入磁盘时才报错
    validate_rule(data)

    fields = data.get("fields", {})
    match = data.get("match", {})
    return {
        "name": data.get("name", ""),
        "display_name": data.get("display_name", ""),
        "fields_count": len(fields) if isinstance(fields, dict) else 0,
        "domains": match.get("domains", []) if isinstance(match, dict) else [],
        "raw": data,
    }


def import_rule(file_path: Path, output_dir: Path, overwrite: bool = False) -> Path:
    """导入规则文件到输出目录 (N42)。

    自动清洗 markdown 包装。重复检查。
    Returns: 写入的 Path。
    """
    preview = import_rule_preview(file_path)
    data = preview["raw"]

    # 重复检查
    name = data.get("name", "")
    target = output_dir / f"{name}.json"
    if target.exists() and not overwrite:
        raise FileExistsError(f"规则 '{name}' 已存在，使用 --overwrite 覆盖")

    # N42: 自动清洗已由 import_rule_preview 完成，直接写入
    safe_write_rule_file(target, data)
    return target


# ═══════════════════════════════════════════════════════════════════
# 临时文件清理
# ═══════════════════════════════════════════════════════════════════


def cleanup_tmp_files(directory: Path) -> int:
    """清理过期 .tmp 文件 (>RULES_TMP_MAX_AGE_HOURS) (S15)。

    Returns: 清理数量。
    """
    if not directory.is_dir():
        return 0
    threshold = time.time() - RULES_TMP_MAX_AGE_HOURS * 3600
    count = 0
    for entry in directory.rglob("*.tmp"):
        try:
            if entry.stat().st_mtime < threshold:
                entry.unlink()
                count += 1
                logger.debug("event=tmp_cleanup path=%s", entry)
        except OSError:
            pass
    if count:
        logger.info("event=tmp_cleanup_done count=%d dir=%s", count, directory)
    return count


def check_cache_size(directory: Path, max_bytes: int = MAX_RULES_CACHE_SIZE) -> int:
    """检查目录下 .json 文件总大小是否超限 (S16)。

    Returns: 当前总字节数。
    """
    if not directory.is_dir():
        return 0
    total = 0
    for entry in directory.rglob("*.json"):
        try:
            total += entry.stat().st_size
        except OSError:
            pass
    if total > max_bytes:
        logger.warning(
            "event=rules_cache_size_exceeded total=%d max=%d",
            total,
            max_bytes,
        )
    return total


# ═══════════════════════════════════════════════════════════════════
# 辅助
# ═══════════════════════════════════════════════════════════════════


def _check_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """object_pairs_hook: 检测 JSON 重复 key (S25)。"""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON 包含重复 key: {key}")
        result[key] = value
    return result

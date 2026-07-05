"""规则 Schema 定义与校验 — RuleSchema Pydantic BaseModel + validate_rule()。

所有规则加载路径（本地文件、远程下载、AI 生成、导入）均通过 validate_rule()
统一校验后进入 RuleSnapshot。
"""

from __future__ import annotations

import logging
import re
from enum import Enum
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

from astrocrawl._constants import (
    ATTR_NAME_PATTERN,
    MAX_FALLBACK_DEPTH,
    MAX_FIELD_NAME_LENGTH,
    MAX_FIELDS_PER_RULE,
    MULTIPLE_MAX_ITEMS,
    RULE_NAME_MAX_LENGTH,
    RULE_NAME_PATTERN,
)
from astrocrawl._types import DEFAULT_EXTRACTION_TYPE

logger = logging.getLogger("astrocrawl.rules.schema")

# 顶层保留字段名 — 规则 fields key 不得使用 (N34)
_RESERVED_FIELD_NAMES = frozenset(
    {
        "url",
        "depth",
        "timestamp",
        "extraction_type",
        "fields",
        "schema_org",
    }
)

_RULE_NAME_RE = re.compile(RULE_NAME_PATTERN)
_ATTR_NAME_RE = re.compile(ATTR_NAME_PATTERN)
_DOMAIN_RE = re.compile(r"^([a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$")

VALID_EXTRACT_TYPES = frozenset({"text", "attr", "html"})

# C5: Unicode 控制字符集 — 对标 Unicode TR36 + Git ident 校验
_BIDI = set(range(0x202A, 0x202E + 1)) | {0x061C, 0x200E, 0x200F}
_ISOLATE = set(range(0x2066, 0x2069 + 1))
_C0 = (set(range(0x00, 0x20)) - {0x09, 0x0A}) | {0x7F}
_INTERLINEAR = {0xFFF9, 0xFFFA, 0xFFFB}
_DANGEROUS_CODES = _BIDI | _ISOLATE | _C0 | _INTERLINEAR

MAX_TEST_URLS = 10


class MatchScope(Enum):
    """规则匹配范围 — 对标 uBlock Origin filter syntax 的显式意图声明。"""

    DOMAIN_PATTERN = "domain_pattern"
    DOMAIN_ALL = "domain_all"
    GLOBAL_PATTERN = "global_pattern"
    ANY = "any"


class FieldRule(BaseModel):
    """单字段提取规则。"""

    model_config = ConfigDict(frozen=True, extra="ignore")

    selector: str = Field("", description="CSS 选择器。对应元素不存在时设为 null")
    description: str = Field("", description="字段描述")
    extract: str = Field("text", json_schema_extra={"enum": ["text", "attr", "html"]})
    attr: str = Field(
        "", description="HTML 属性名。仅 extract='attr' 时填写", json_schema_extra={"pattern": ATTR_NAME_PATTERN}
    )
    multiple: bool = Field(False, description=f"是否提取多个元素。最多 {MULTIPLE_MAX_ITEMS} 项")
    fallback: list[FieldRule] = Field(
        default_factory=list,
        description=f"备选 CSS 选择器链。主选择器失败时依次尝试，最多 {MAX_FALLBACK_DEPTH - 1} 层，不允许嵌套 fallback",
    )
    transform: dict[str, Any] = Field(
        default_factory=dict,
        json_schema_extra={
            "properties": {
                "strip": {"type": "boolean", "description": "去除首尾空白"},
                "strip_currency": {"type": "boolean", "description": "去除货币符号"},
                "regex": {"type": "string", "description": "re2 正则提取（第一个捕获组或整匹配）"},
                "replace": {"type": "object", "description": '字符串替换，格式: {"from": "旧", "to": "新"}'},
                "join": {"type": "string", "description": "将 multiple 数组用指定分隔符拼接为单字符串"},
            },
        },
    )


class MatchConfig(BaseModel):
    """规则匹配配置。"""

    model_config = ConfigDict(frozen=True, extra="ignore")

    scope: MatchScope = Field(
        MatchScope.DOMAIN_PATTERN, description="匹配范围：domain_pattern / domain_all / global_pattern / any"
    )
    domains: list[str] = Field(default_factory=list, description="目标域名列表（不含协议和路径）")
    url_pattern: str = Field("", description="URL 路径正则模式（re2 兼容语法）")


class RuleOptions(BaseModel):
    """规则行为选项。"""

    model_config = ConfigDict(frozen=True, extra="ignore")

    keep_body_text: bool = Field(False, description="是否保留页面正文文本")
    follow_links: bool = Field(True, description="是否提取并跟踪页面中的链接")
    generation_mode: Literal["type", "position"] | None = Field(None, description="AI 规则生成模式")


class RuleSchema(BaseModel):
    """单条规则的完整定义。

    通过 validate_rule() 从 dict 构造，保证所有自定义校验和向后兼容逻辑执行。
    也可直接构造（测试路径），但不会执行 scope 推断等向后兼容逻辑。
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    name: str = Field(
        "",
        min_length=1,
        max_length=RULE_NAME_MAX_LENGTH,
        pattern=RULE_NAME_PATTERN,
        description="规则标识名（仅限 a-z 0-9 _ -）",
    )
    schema_version: int = Field(1, description="Schema 版本号")
    version: int = Field(1, description="规则版本号")
    display_name: str = Field("", description="显示名称")
    description: str = Field("", description="规则描述")
    author: str = Field("", description="规则作者")
    tags: list[str] = Field(default_factory=list, description="标签列表")
    enabled: bool = Field(True, description="是否启用")
    match: MatchConfig = Field(default_factory=MatchConfig, description="规则匹配配置")  # type: ignore[arg-type]
    test_urls: list[dict[str, str]] = Field(default_factory=list, description="测试 URL 列表")
    fields: dict[str, FieldRule] = Field(
        default_factory=dict,
        max_length=MAX_FIELDS_PER_RULE,
        description=f"提取字段定义。key 不可使用保留字: {', '.join(sorted(_RESERVED_FIELD_NAMES))}",
    )
    options: RuleOptions = Field(default_factory=RuleOptions, description="规则行为选项")  # type: ignore[arg-type]

    @property
    def is_generic(self) -> bool:
        return self.match.scope in (MatchScope.GLOBAL_PATTERN, MatchScope.ANY)


# 解析递归引用：FieldRule.fallback 自引用
FieldRule.model_rebuild()
MatchConfig.model_rebuild()
RuleSchema.model_rebuild()


def sanitize_display_text(text: str) -> str:
    """C5: 清洗 Unicode 控制字符。静默清洗，对标 Git clean/smudge。"""
    if not text:
        return text
    cleaned: list[str] = []
    for ch in text:
        if ord(ch) in _DANGEROUS_CODES:
            continue
        cleaned.append(ch)
    return "".join(cleaned)


def validate_rule(data: dict[str, Any]) -> RuleSchema:
    """校验并解析规则 JSON。失败时抛出 ValueError，调用方负责 WARNING + skip。"""

    if not isinstance(data, dict):
        raise ValueError("规则必须是 JSON 对象")

    # ── schema_version 演进 (N82) ──
    sv = data.get("schema_version", 1)
    if not isinstance(sv, int) or sv < 1:
        raise ValueError(f"schema_version 无效: {sv}")
    if sv > 2:
        raise ValueError(f"不支持的 schema_version {sv} (当前支持 1-2)")

    # ── name (S11) ──
    name = _str_field(data, "name")
    if not name:
        raise ValueError("name 不能为空")
    if name == DEFAULT_EXTRACTION_TYPE:
        raise ValueError(f"'{DEFAULT_EXTRACTION_TYPE}' 是保留名，不可用于自定义规则")
    if len(name) > RULE_NAME_MAX_LENGTH:
        raise ValueError(f"name 长度超过 {RULE_NAME_MAX_LENGTH}: {name}")
    if not _RULE_NAME_RE.match(name):
        raise ValueError(f"name 包含非法字符 (仅允许 a-z 0-9 _ -): {name}")

    # ── enabled ──
    enabled = data.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError("enabled 必须是 boolean")

    # ── version ──
    version = data.get("version", 1)
    if not isinstance(version, int) or version < 1:
        raise ValueError(f"version 无效: {version}")

    # ── display text with C5 sanitization ──
    display_name = sanitize_display_text(_str_field(data, "display_name", ""))
    description = sanitize_display_text(_str_field(data, "description", ""))
    author = sanitize_display_text(_str_field(data, "author", ""))

    # ── match ──
    match_data = data.get("match", {})
    if not isinstance(match_data, dict):
        raise ValueError("match 必须是对象")

    domains = _normalize_domains(match_data.get("domains", []))
    url_pattern = _resolve_url_pattern(match_data.get("url_pattern", ""))
    scope = _resolve_scope(match_data.get("scope", ""), domains, url_pattern)

    # 向后兼容推断日志 — name 在此作用域天然可用
    if not match_data.get("scope") and scope in (MatchScope.DOMAIN_ALL, MatchScope.ANY):
        logger.warning("event=rule_scope_inferred rule=%s scope=%s domains=%s", name, scope.value, domains)

    _validate_scope_consistency(scope, domains, url_pattern)
    match = MatchConfig(scope=scope, domains=domains, url_pattern=url_pattern)

    # ── fields ──
    fields_data = data.get("fields", {})
    if not isinstance(fields_data, dict):
        raise ValueError("fields 必须是对象")
    if len(fields_data) > MAX_FIELDS_PER_RULE:
        raise ValueError(f"fields 数量 {len(fields_data)} 超过上限 {MAX_FIELDS_PER_RULE}")
    fields: dict[str, FieldRule] = {}
    for key, val in fields_data.items():
        if not isinstance(key, str) or not key:
            raise ValueError("字段名不能为空")
        if len(key) > MAX_FIELD_NAME_LENGTH:
            raise ValueError(f"字段名长度超过 {MAX_FIELD_NAME_LENGTH}: {key}")
        if key in _RESERVED_FIELD_NAMES:
            raise ValueError(f"字段名 '{key}' 与顶层保留字段冲突 (N34)")
        if not isinstance(val, dict):
            raise ValueError(f"字段 '{key}' 定义必须是对象")
        field_rule = _validate_field_rule(key, val)
        if field_rule.multiple and field_rule.transform:
            if "regex" in field_rule.transform or "replace" in field_rule.transform:
                raise ValueError(f"字段 '{key}' multiple:true 不支持 transform.regex/replace")
        fields[key] = field_rule

    # ── options ──
    options_data = data.get("options", {})
    if not isinstance(options_data, dict):
        raise ValueError("options 必须是对象")
    gm_raw = options_data.get("generation_mode")
    generation_mode = gm_raw if gm_raw in ("type", "position") else None
    options = RuleOptions(
        keep_body_text=bool(options_data.get("keep_body_text", False)),
        follow_links=bool(options_data.get("follow_links", True)),
        generation_mode=generation_mode,
    )

    # ── tags (m6: 非字符串项 WARNING) ──
    tags_raw = data.get("tags", [])
    tags: list[str] = []
    if isinstance(tags_raw, list):
        for t in tags_raw:
            if isinstance(t, str):
                tags.append(t)
            else:
                logger.warning("event=rule_tag_invalid_type rule=%s tag=%r", name, t)
    else:
        tags = []

    # ── test_urls (m5: HTTPS 强制 + urlparse + 去重 + 上限) ──
    test_urls = _validate_test_urls(data.get("test_urls", []))

    return RuleSchema(
        name=name,
        schema_version=sv,
        version=version,
        display_name=display_name,
        description=description,
        author=author,
        tags=tags,
        enabled=enabled,
        match=match,
        test_urls=test_urls,
        fields=fields,
        options=options,
    )


def _normalize_domains(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    normalized = [str(d).lower().strip() for d in raw if isinstance(d, str) and d.strip()]
    result: list[str] = []
    for d in normalized:
        d = d.rstrip(".")
        if not d or not _DOMAIN_RE.match(d):
            raise ValueError(f"match.domains 含非法域名: {d}")
        result.append(d)
    return result


def _resolve_url_pattern(raw: object) -> str:
    if not raw or not isinstance(raw, str):
        return ""
    try:
        import re2

        re2.compile(raw)
    except Exception as e:
        raise ValueError(f"url_pattern re2 编译失败: {e}") from e
    return raw


def _resolve_scope(scope_raw: str, domains: list[str], url_pattern: str) -> MatchScope:
    if scope_raw:
        try:
            return MatchScope(scope_raw)
        except ValueError:
            raise ValueError(f"match.scope 无效: {scope_raw} (可选: {[s.value for s in MatchScope]})") from None
    if domains and url_pattern:
        return MatchScope.DOMAIN_PATTERN
    if domains:
        return MatchScope.DOMAIN_ALL
    if url_pattern:
        return MatchScope.GLOBAL_PATTERN
    return MatchScope.ANY


def _validate_scope_consistency(scope: MatchScope, domains: list[str], url_pattern: str) -> None:
    if scope == MatchScope.DOMAIN_PATTERN:
        if not domains:
            raise ValueError("domain_pattern scope 要求 domains 非空")
        if not url_pattern:
            raise ValueError("domain_pattern scope 要求 url_pattern 非空")
    elif scope == MatchScope.DOMAIN_ALL:
        if not domains:
            raise ValueError("domain_all scope 要求 domains 非空")
    elif scope == MatchScope.GLOBAL_PATTERN:
        if domains:
            raise ValueError("global_pattern scope 要求 domains 为空")
        if not url_pattern:
            raise ValueError("global_pattern scope 要求 url_pattern 非空")
    elif scope == MatchScope.ANY:
        if domains:
            raise ValueError("any scope 要求 domains 为空")


def _validate_test_urls(raw: Any) -> list[dict[str, str]]:
    """m5: test_urls 三层过滤 — HTTPS 强制 + urlparse 含 scheme+netloc + 去重 + 上限 10。"""
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    result: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url", "")).strip() if item.get("url") else ""
        if not url:
            continue
        parsed = urlparse(url)
        if parsed.scheme != "https":
            logger.warning("event=test_url_not_https url=%s", url)
            continue
        if not parsed.netloc:
            logger.warning("event=test_url_no_netloc url=%s", url)
            continue
        if url in seen:
            continue
        seen.add(url)
        if len(result) >= MAX_TEST_URLS:
            break
        result.append({"url": url})
    return result


def _validate_field_rule(field_name: str, data: dict[str, Any]) -> FieldRule:
    selector = _str_field(data, "selector")
    if selector == "":
        raise ValueError(f"字段 '{field_name}' selector 不能为空")

    extract = _str_field(data, "extract", "text")
    if extract not in VALID_EXTRACT_TYPES:
        raise ValueError(f"字段 '{field_name}' extract 无效: {extract}")

    attr = ""
    if extract == "attr":
        attr = _str_field(data, "attr")
        if not attr:
            raise ValueError(f"字段 '{field_name}' extract=attr 时必须提供 attr")
        if not _ATTR_NAME_RE.match(attr):
            raise ValueError(f"字段 '{field_name}' attr 名包含非法字符: {attr} (S29)")

    multiple_raw = data.get("multiple", False)
    if not isinstance(multiple_raw, bool):
        raise ValueError(f"字段 '{field_name}' multiple 必须是 boolean，当前值: {multiple_raw!r}")
    multiple = multiple_raw

    fallback: list[FieldRule] = []
    fb_data = data.get("fallback")
    if fb_data and isinstance(fb_data, list):
        if len(fb_data) > MAX_FALLBACK_DEPTH - 1:
            raise ValueError(f"字段 '{field_name}' fallback 深度超过 {MAX_FALLBACK_DEPTH - 1}")
        for i, fb_item in enumerate(fb_data):
            if isinstance(fb_item, dict):
                fallback.append(_validate_fallback_field(f"{field_name}.fallback[{i}]", fb_item))
            elif isinstance(fb_item, str):
                fallback.append(FieldRule(selector=fb_item, extract="text"))  # type: ignore[call-arg]
            else:
                raise ValueError(f"字段 '{field_name}' fallback[{i}] 必须是对象或字符串简写")

    description = _str_field(data, "description", "")
    transform = _validate_transform(data.get("transform", {}))

    return FieldRule(
        selector=selector,
        description=description,
        extract=extract,
        attr=attr,
        multiple=multiple,
        fallback=fallback,
        transform=transform,
    )


def _validate_fallback_field(name: str, data: dict[str, Any]) -> FieldRule:
    if data.get("fallback"):
        raise ValueError(f"{name} fallback 不允许嵌套 (MAX_FALLBACK_DEPTH={MAX_FALLBACK_DEPTH})")
    return _validate_field_rule(name, data)


_VALID_TRANSFORMS = frozenset({"strip", "strip_currency", "regex", "replace", "join"})


def _validate_transform(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    result: dict[str, Any] = {}
    for key, val in data.items():
        if key not in _VALID_TRANSFORMS:
            logger.warning("未知 transform 类型 '%s'，已忽略", key)
            continue
        if key == "strip":
            result[key] = bool(val)
        elif key == "strip_currency":
            result[key] = bool(val)
        elif key == "regex":
            if isinstance(val, str) and val:
                try:
                    import re2

                    re2.compile(val)
                except Exception as e:
                    raise ValueError(f"transform.regex re2 编译失败: {e}") from e
                result[key] = val
        elif key == "replace":
            if isinstance(val, dict) and "from" in val and "to" in val:
                result[key] = {"from": str(val["from"]), "to": str(val["to"])}
        elif key == "join":
            if isinstance(val, str):
                result[key] = val
    return result


def _str_field(data: dict[str, Any], key: str, default: str = "") -> str:
    val = data.get(key, default)
    return str(val).strip() if isinstance(val, str) else default

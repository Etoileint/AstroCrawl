"""插件发现与 manifest 加载器（ADR-0011 决策 2/6/7/8）。

entry_points 扫描（零 import） → manifest JSON 解析 → S6 全量输入清洗（10 项）
→ S7 身份防伪造 → 6 种状态判定（含依赖级联 + PEP 440 版本约束）
→ S20 组合权限约束 → PluginRef 构建。

对标 Airflow provider.yaml + entry_points 单入口 + DAG 按需加载。
"""

from __future__ import annotations

import json
import re
import unicodedata
from importlib.metadata import distribution, entry_points
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, Any

from astrobasis import LogfmtLogger
from astroframe._errors import ManifestValidationError
from astroframe._types import (
    _ALL_KNOWN_PERMISSIONS,
    _DEFAULT_GRANTED_PERMISSIONS,
    _PERMISSION_CRAWL_CTX_READ,
    _PERMISSION_CRAWL_CTX_WRITE,
    _PERMISSION_NETWORK_DOMAINS,
    _PERMISSION_NETWORK_OUTBOUND,
    _WRITE_IMPLIES_READ,
    CapabilityRef,
    DeprecationSeverity,
    PermissionLevel,
    PluginManifest,
    PluginRef,
    PluginStatus,
    check_deprecation,
    derive_permission_level,
    get_valid_implements,
    permission_level_higher_than,
)

if TYPE_CHECKING:
    from astroframe._state import PluginState

log = LogfmtLogger("astroframe.loader")

MANIFEST_FILENAME = "astroframe-plugin.json"
MANIFEST_MAX_BYTES = 64 * 1024
MAX_JSON_DEPTH = 20
MAX_CONFIG_SCHEMA_FIELDS = 50
MAX_DISPLAY_NAME_LEN = 128
MAX_DESCRIPTION_LEN = 1024
MAX_DEPRECATION_MSG_LEN = 256
MAX_NAME_LEN = 128
CURRENT_MANIFEST_VERSION = 1
# name/group: must start and end with [a-z0-9], internal chars [a-z0-9._-]
# Single alphanumeric char also valid. Prevents leading/trailing dots/dashes/underscores.
NAME_WHITELIST = re.compile(r"^[a-z0-9]([a-z0-9._-]*[a-z0-9])?$")
FACTORY_FORMAT = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)*:[a-zA-Z_][a-zA-Z0-9_]*$")

# PEP 440 版本约束语法的宽松格式校验
_PEP440_CONSTRAINT = re.compile(
    r"^\s*[<>!=]=?\s*[0-9]+(\.[0-9]+)*(\.[0-9]+)?"
    r"(\s*,\s*[<>!=]=?\s*[0-9]+(\.[0-9]+)*(\.[0-9]+)?)*\s*$"
)

# 单版本号解析
_VERSION_RE = re.compile(r"^([0-9]+)(?:\.([0-9]+))?(?:\.([0-9]+))?")


def _is_control_char(c: str) -> bool:
    """判断字符是否为 Unicode 控制字符（TR36 清洗目标）。

    覆盖 C0 (U+0000-U+001F)、C1 (U+0080-U+009F)、
    bidi 覆盖 (U+200E-U+200F, U+202A-U+202E)、行/段分隔符 (U+2028-U+2029)、
    不可见格式字符 (U+2060-U+206F)、Tags block (U+E0000-U+E007F)。

    U+0000 (NUL) 最危险——在 C 字符串和某些 Python 版本中作为终止符。
    """
    cp = ord(c)
    # C0 controls (including NUL at 0x00)
    if cp <= 0x1F:
        return True
    # C1 controls (0x80-0x9F)
    if 0x7F <= cp <= 0x9F:
        return True
    # Bidi format chars + line/paragraph separators + invisible operators
    if cp in (
        0x200E,
        0x200F,  # LRM, RLM
        0x2028,
        0x2029,  # LINE SEPARATOR, PARAGRAPH SEPARATOR
        0x202A,
        0x202B,
        0x202C,
        0x202D,
        0x202E,  # bidi embedding overrides
    ):
        return True
    # Word joiner / invisible operators / zero-width chars
    if 0x2060 <= cp <= 0x206F:
        return True
    # Tags block (U+E0000-U+E007F)
    if 0xE0000 <= cp <= 0xE007F:
        return True
    # General category check: Cc, Cf, Cs, Co, Cn
    cat = unicodedata.category(c)
    if cat.startswith("C"):
        return True
    return False


def _tr36_sanitize(text: str) -> str:
    """移除 Unicode 控制字符，对标 ADR-0005 S40。"""
    if not text:
        return text
    chars: list[str] = []
    modified = False
    for ch in text:
        if _is_control_char(ch):
            modified = True
            continue
        chars.append(ch)
    return "".join(chars) if modified else text


def _tr36_sanitize_with_len(text: str, max_len: int, field_name: str) -> str:
    """TR36 清洗 + 长度截断 + 日志告警。"""
    cleaned = _tr36_sanitize(text)
    if cleaned != text:
        log.debug("plugin_s6_tr36_strip", field=field_name, original_len=len(text))
    if len(cleaned) > max_len:
        log.warning("plugin_s6_field_truncated", field=field_name, original_len=len(cleaned), max_len=max_len)
        cleaned = cleaned[:max_len]
    return cleaned


def _check_json_depth(obj: Any, max_depth: int = MAX_JSON_DEPTH) -> int:
    """递归检查 JSON 嵌套深度。返回实际最大深度，超过 max_depth 时抛异常。"""
    if isinstance(obj, dict):
        if max_depth <= 0:
            raise ManifestValidationError(f"JSON 嵌套深度超过 {MAX_JSON_DEPTH}")
        max_child = 0
        for v in obj.values():
            max_child = max(max_child, _check_json_depth(v, max_depth - 1))
        return 1 + max_child
    if isinstance(obj, list):
        if max_depth <= 0:
            raise ManifestValidationError(f"JSON 嵌套深度超过 {MAX_JSON_DEPTH}")
        max_child = 0
        for item in obj:
            max_child = max(max_child, _check_json_depth(item, max_depth - 1))
        return 1 + max_child
    return 0


def _parse_version(version_str: str) -> tuple[int, ...]:
    """解析 PEP 440 版本字符串为元组，便于比较。"""
    m = _VERSION_RE.match(version_str.strip())
    if not m:
        raise ManifestValidationError(f"无效的版本号: {version_str!r}")
    parts = tuple(int(g) for g in m.groups() if g is not None)
    return parts


def _check_version_constraint(constraint: str, actual_version: str) -> bool:
    """检查单个 PEP 440 版本约束是否满足。支持 >=, <=, >, <, ==, !=。"""
    constraint = constraint.strip()
    if not constraint:
        return True

    actual = _parse_version(actual_version)

    for clause in constraint.split(","):
        clause = clause.strip()
        if not clause:
            continue
        op_match = re.match(r"^\s*([<>!=]=?)\s*([0-9.]+)\s*$", clause)
        if not op_match:
            raise ManifestValidationError(f"无效的版本约束语法: {clause!r}")
        op = op_match.group(1)
        target = _parse_version(op_match.group(2))

        if op == ">=" and not (actual >= target):
            return False
        if op == "<=" and not (actual <= target):
            return False
        if op == ">" and not (actual > target):
            return False
        if op == "<" and not (actual < target):
            return False
        if op == "==" and not (actual == target):
            return False
        if op == "!=" and not (actual != target):
            return False

    return True


def _validate_pep440_constraint(constraint: str) -> bool:
    """校验 PEP 440 版本约束字符串的格式是否合法。"""
    if not constraint or not isinstance(constraint, str):
        return False
    return bool(_PEP440_CONSTRAINT.match(constraint))


def _validate_name_whitelist(value: str, field_name: str) -> None:
    """检查 name/group 字段是否符合字符白名单 [a-z0-9._-] + 长度上限。

    白名单仅为 ASCII → 西里尔 'а' (U+0430) 等 Unicode confusable 自动拒绝。
    这是显式的 NFKC 同形异义防护——不依赖 NFKC 归一化，纯 ASCII 白名单 = 硬阻断。
    """
    if len(value) > MAX_NAME_LEN:
        raise ManifestValidationError(f"{field_name} 长度 {len(value)} 超过上限 {MAX_NAME_LEN}")
    if not NAME_WHITELIST.match(value):
        non_ascii = [f"U+{ord(c):04X}" for c in value if ord(c) > 127]
        detail = f" 含非 ASCII 字符: {non_ascii}" if non_ascii else ""
        raise ManifestValidationError(f"{field_name} '{value}' 不匹配白名单 [a-z0-9._-]{detail}")


def _validate_factory_format(factory: str) -> None:
    """校验 factory 字段的 module:attr 格式。"""
    if not FACTORY_FORMAT.match(factory):
        raise ManifestValidationError(f"factory '{factory}' 格式不匹配 module:attr")


def _validate_permissions_known(permissions: list[str]) -> None:
    """校验 permissions 中每个权限名在权限目录中定义。"""
    for perm in permissions:
        if perm not in _ALL_KNOWN_PERMISSIONS:
            raise ManifestValidationError(f"未知权限名 '{perm}'——不在权限目录中定义")


def _validate_config_schema(schema: dict[str, Any]) -> None:
    """校验 config_schema 字段数 ≤ 50（ADR-0011 S6）。"""
    properties = schema.get("properties", {})
    if isinstance(properties, dict) and len(properties) > MAX_CONFIG_SCHEMA_FIELDS:
        raise ManifestValidationError(f"config_schema 字段数 {len(properties)} 超过上限 {MAX_CONFIG_SCHEMA_FIELDS}")


def _resolve_permissions(perms: tuple[str, ...]) -> frozenset[str]:
    """解析权限集合——应用隐含规则和自动授予。

    1. filesystem.state → 自动授予所有插件（不参与 PermissionLevel 升级）
    2. crawl.ctx.write → 自动隐含 crawl.ctx.read
    """
    resolved: set[str] = set(perms)
    # 自动授予
    resolved.update(_DEFAULT_GRANTED_PERMISSIONS)
    # 隐含规则
    for perm, implied in _WRITE_IMPLIES_READ.items():
        if perm in resolved:
            resolved.add(implied)
    return frozenset(resolved)


def _check_s20_combo(capabilities: list[CapabilityRef]) -> None:
    """S20 组合约束：(crawl.ctx.read|crawl.ctx.write) + network.outbound → network.domains 强制非空且不含通配符。"""
    for cap in capabilities:
        perms = set(cap.permissions)
        has_data = _PERMISSION_CRAWL_CTX_READ in perms or _PERMISSION_CRAWL_CTX_WRITE in perms
        has_outbound = _PERMISSION_NETWORK_OUTBOUND in perms
        if has_data and has_outbound:
            if _PERMISSION_NETWORK_DOMAINS not in perms:
                raise ManifestValidationError(
                    f"capability '{cap.group}/{cap.name}': (crawl.ctx.read|crawl.ctx.write) + network.outbound "
                    f"同时声明 → network.domains 必须非空（S20 Layer A 技术阻断）"
                )
            # S20 通配符检测——域名值不在 manifest 的 permissions 列表中，
            # 而在 trust record 的 egress_domains 字段（用户信任仪式时填写）。
            # 通配符校验在 PluginState.set_trusted() 写入路径执行（ADR S20:1121 Layer A→B 分工）。
            # 对标 Deno: --allow-net=<domain> 是运行时显式授权，不在 deno.json 中声明。


def _apply_deprecation_policy(
    manifest: PluginManifest,
    package_name: str,
    engine_version: str,
) -> PluginManifest:
    """根据废弃窗口过滤 manifest 中的 capability（ADR-0011 S3 废弃策略）。

    REMOVED（3+ minor version 之后）→ capability 从 manifest 中移除，记 ERROR 日志。
    ERROR（2 个 minor version 之后）→ 保留但记 ERROR 日志，引用时需显式 opt-in。
    WARNING（宽限期内）→ 保留，记 WARNING 日志。

    对标 Kubernetes deprecation policy:
      废弃公告后 2 个 minor version → WARNING → 1 个 → ERROR → 之后 → REMOVED
    """
    kept: list[CapabilityRef] = []
    for cap in manifest.capabilities:
        if not cap.deprecated:
            kept.append(cap)
            continue

        severity = check_deprecation(cap.deprecated_since, engine_version)
        # deprecated=True 但未声明 deprecated_since → 永久 WARNING（不追溯，不自动升级）
        # 仅当作者未提供版本号时才回退——若提供了版本号但引擎早于该版本，则保留 NONE
        if severity == DeprecationSeverity.NONE and cap.deprecated_since is None:
            severity = DeprecationSeverity.WARNING

        if severity == DeprecationSeverity.REMOVED:
            log.error(
                "capability_removed",
                capability=cap.global_key,
                package=package_name,
                deprecated_since=cap.deprecated_since or "unknown",
                engine_version=engine_version,
                hint=cap.deprecation_message or "",
            )
            continue  # 不注册

        if severity == DeprecationSeverity.ERROR:
            log.error(
                "capability_deprecated_hard",
                capability=cap.global_key,
                package=package_name,
                deprecated_since=cap.deprecated_since or "unknown",
                engine_version=engine_version,
                hint=cap.deprecation_message or "需要显式 opt-in 才可使用",
            )
        elif severity == DeprecationSeverity.WARNING:
            log.warning(
                "capability_deprecated",
                capability=cap.global_key,
                package=package_name,
                deprecated_since=cap.deprecated_since or "unknown",
                hint=cap.deprecation_message or "",
            )
        kept.append(cap)

    if len(kept) != len(manifest.capabilities):
        return manifest.with_capabilities(tuple(kept))
    return manifest


def _validate_manifest_s6(data: dict[str, Any], package_name: str) -> dict[str, Any]:
    """S6 全量输入清洗——10 项检查，按序执行。

    返回清洗后的 data 副本。任何失败抛 ManifestValidationError。
    """
    # 1. manifest_version 必填 + 前向兼容检测（S1）
    if "manifest_version" not in data:
        raise ManifestValidationError(f"插件 '{package_name}': manifest_version 为必填字段")
    mv = data["manifest_version"]
    if not isinstance(mv, int) or mv < 1:
        raise ManifestValidationError(f"插件 '{package_name}': manifest_version 必须为正整数，实际为 {mv!r}")
    if mv > CURRENT_MANIFEST_VERSION:
        log.warning(
            "plugin_manifest_version_future",
            package=package_name,
            manifest_version=mv,
            engine_max_version=CURRENT_MANIFEST_VERSION,
        )

    # 2. JSON 嵌套深度
    _check_json_depth(data)

    # 3. name whitelist
    name = data.get("name", "")
    if not name:
        raise ManifestValidationError(f"插件 '{package_name}': name 为必填字段")
    _validate_name_whitelist(name, "name")

    # 4. requires_engine PEP 440 校验
    requires_engine = data.get("requires_engine", ">=0.1")
    if not _validate_pep440_constraint(requires_engine):
        raise ManifestValidationError(f"插件 '{name}': requires_engine '{requires_engine}' 不是有效的 PEP 440 约束")

    # 5. config_schema 字段数
    config_schema = data.get("config_schema", {})
    if isinstance(config_schema, dict):
        _validate_config_schema(config_schema)

    # 6-9. capabilities 数组各项清洗
    capabilities_raw = data.get("capabilities", [])
    if not isinstance(capabilities_raw, list):
        raise ManifestValidationError(
            f"插件 '{name}': capabilities 必须是数组，实际为 {type(capabilities_raw).__name__}"
        )
    capabilities: list[dict[str, Any]] = capabilities_raw
    for i, cap in enumerate(capabilities):
        idx_label = f"插件 '{name}' capabilities[{i}]"

        if not isinstance(cap, dict):
            raise ManifestValidationError(f"{idx_label}: 必须是对象，实际为 {type(cap).__name__}")

        # 6. group whitelist
        group = cap.get("group", "")
        _validate_name_whitelist(group, f"{idx_label} group")

        # 7. name whitelist
        cap_name = cap.get("name", "")
        _validate_name_whitelist(cap_name, f"{idx_label} name")

        # 8. factory 格式
        factory = cap.get("factory", "")
        _validate_factory_format(factory)

        # 9. permissions 已知名称
        permissions = cap.get("permissions", [])
        if isinstance(permissions, list):
            _validate_permissions_known(permissions)

        # display_name / description TR36 清洗 + 截断
        raw_display = cap.get("display_name") or cap_name
        raw_desc = cap.get("description") or ""
        cap["display_name"] = _tr36_sanitize_with_len(
            str(raw_display), MAX_DISPLAY_NAME_LEN, f"{idx_label} display_name"
        )
        cap["description"] = _tr36_sanitize_with_len(str(raw_desc), MAX_DESCRIPTION_LEN, f"{idx_label} description")

        # deprecation_message TR36 清洗 + 截断（S3 废弃策略——对标 display_name 处理）
        raw_dep_msg = cap.get("deprecation_message")
        if raw_dep_msg is not None and isinstance(raw_dep_msg, str):
            cap["deprecation_message"] = _tr36_sanitize_with_len(raw_dep_msg, 256, f"{idx_label} deprecation_message")

        # deprecated_since PEP 440 格式校验（S3 废弃策略——格式不合法则清除，永久 WARNING）
        raw_dep_since = cap.get("deprecated_since")
        if raw_dep_since is not None and isinstance(raw_dep_since, str):
            if not _VERSION_RE.match(raw_dep_since.strip()):
                log.warning(
                    "plugin_s6_deprecated_since_invalid",
                    field=f"{idx_label} deprecated_since",
                    value=raw_dep_since,
                    msg="格式不合法，已清除——废弃窗口退化为永久 WARNING",
                )
                cap.pop("deprecated_since", None)

    # 10. signing 字段格式校验（S17）
    signing = data.get("signing")
    if signing is not None:
        from astroframe._signature import validate_signing_field

        validate_signing_field(signing)

    return data


def _find_manifest_path(package_name: str) -> Path | None:
    """通过 importlib.resources 定位包目录，查找 astroframe-plugin.json。

    importlib.resources.files()（Python 3.9+，PEP 365）是定位包内文件的
    标准 API——正确解析 editable / non-editable / zip 等所有安装模式的包路径。
    替代已弃用的 pkg_resources.resource_filename，无需 import 目标包。
    """
    try:
        pkg_files = files(package_name)
    except Exception:
        log.debug("plugin_distribution_not_found", package=package_name)
        return None

    manifest = pkg_files / MANIFEST_FILENAME
    try:
        if manifest.is_file():
            return Path(str(manifest))
    except Exception:
        pass

    log.debug("plugin_manifest_not_found", package=package_name)
    return None


def _read_manifest_file(path: Path) -> dict[str, Any]:
    """读取并解析 manifest JSON 文件。零 import。"""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ManifestValidationError(f"无法读取 manifest 文件 {path}: {exc}") from exc

    if len(raw) > MANIFEST_MAX_BYTES:
        raise ManifestValidationError(f"manifest 文件 {path} 大小 {len(raw)} 字节超过上限 {MANIFEST_MAX_BYTES}")

    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ManifestValidationError(f"manifest {path} JSON 解析失败: {exc}") from exc

    if not isinstance(data, dict):
        raise ManifestValidationError(f"manifest {path} 顶层必须是对象，实际为 {type(data).__name__}")

    return data


def _get_package_version(package_name: str) -> str:
    """通过 importlib.metadata 获取已安装包的版本。零 import。"""
    try:
        return distribution(package_name).version
    except Exception:
        return "0.0.0"


def _check_engine_version(requires_engine: str, engine_version: str) -> bool:
    """检查引擎版本是否满足 requires_engine 约束。"""
    try:
        return _check_version_constraint(requires_engine, engine_version)
    except ManifestValidationError:
        return False


def _detect_dependency_cycles(
    plugins: dict[str, PluginRef],
) -> list[str]:
    """Kahn 拓扑排序检测循环依赖。

    返回循环依赖中的包名列表（空列表 = 无循环）。
    """
    in_degree: dict[str, int] = dict.fromkeys(plugins, 0)
    adj: dict[str, list[str]] = {name: [] for name in plugins}

    for name, ref in plugins.items():
        for dep_name in ref.manifest.requires_plugins:
            if dep_name in plugins:
                adj[dep_name].append(name)
                in_degree[name] = in_degree.get(name, 0) + 1

    queue = [name for name, deg in in_degree.items() if deg == 0]
    result: list[str] = []

    while queue:
        node = queue.pop(0)
        result.append(node)
        for neighbor in adj.get(node, []):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if len(result) != len(plugins):
        cycle_members = set(plugins) - set(result)
        log.error("plugin_dependency_cycle", members=",".join(sorted(cycle_members)))
        return list(cycle_members)

    return []


def discover_plugins(
    engine_version: str,
    state: PluginState,
) -> dict[str, PluginRef]:
    """发现并加载所有可用插件的 manifest（零 import）。

    扫描 entry_points → 读 manifest → S6 清洗 → S7 防伪造 → 状态判定 → 依赖检查。

    ADR-0012: ASTROCRAWL_PLUGINS=0 环境变量禁用全部插件加载（测试隔离）。
    设为 "1" 显式启用，"0" 返回空注册表。

    Returns:
        dict[package_name, PluginRef] — 所有已发现插件的运行时引用。
        状态可能为 LOADED / PENDING_REVIEW / DISABLED / INCOMPATIBLE。
    """
    import os as _os

    _plugins_env = _os.environ.get("ASTROCRAWL_PLUGINS", "1")
    if _plugins_env == "0":
        log.info("plugin_discovery_disabled", reason="ASTROCRAWL_PLUGINS=0")
        return {}

    plugin_state = state.load()
    disabled_list: list[str] = plugin_state.get("disabled", [])
    require_approval: str = plugin_state.get("require_approval", "all")
    trusted: dict[str, Any] = plugin_state.get("trusted_capabilities", {})

    discovered: dict[str, PluginRef] = {}

    # entry_points 扫描
    eps_scan_ok = True
    try:
        eps = list(entry_points(group="astroframe.plugins"))
    except Exception as exc:
        log.warning("plugin_entry_points_scan_failed", error=str(exc))
        eps = []
        eps_scan_ok = False

    for ep in eps:
        package_name = ep.value.strip()
        if not package_name or package_name in discovered:
            continue
        plugin_ref = _load_plugin_ref(package_name, engine_version, disabled_list, require_approval, trusted)
        discovered[package_name] = plugin_ref

    # ── 依赖级联（ADR-0011 决策 7）───────────────────────────────────────────
    # Fixpoint loop: a dependency becoming INCOMPATIBLE may cascade to its
    # dependents. Single pass is insufficient when the iteration order doesn't
    # match topological dependency order. Re-scan until no more status changes.
    changed = True
    while changed:
        changed = False
        for name, ref in list(discovered.items()):
            if ref.status in (PluginStatus.INCOMPATIBLE, PluginStatus.DISABLED):
                continue
            for dep_name, dep_constraint in ref.manifest.requires_plugins.items():
                dep_ref = discovered.get(dep_name)
                if dep_ref is None or dep_ref.status != PluginStatus.LOADED:
                    log.warning(
                        "plugin_dependency_not_loaded",
                        plugin=name,
                        dependency=dep_name,
                        dep_status=dep_ref.status.value if dep_ref else "not_found",
                    )
                    discovered[name] = PluginRef(
                        manifest=ref.manifest,
                        status=PluginStatus.INCOMPATIBLE,
                        package_name=ref.package_name,
                        version=ref.version,
                    )
                    changed = True
                    break
                if dep_constraint and dep_ref.version != "0.0.0":
                    try:
                        if not _check_version_constraint(dep_constraint, dep_ref.version):
                            log.warning(
                                "plugin_dependency_version_mismatch",
                                plugin=name,
                                dependency=dep_name,
                                required=dep_constraint,
                                actual=dep_ref.version,
                            )
                            discovered[name] = PluginRef(
                                manifest=ref.manifest,
                                status=PluginStatus.INCOMPATIBLE,
                                package_name=ref.package_name,
                                version=ref.version,
                            )
                            changed = True
                            break
                    except ManifestValidationError:
                        discovered[name] = PluginRef(
                            manifest=ref.manifest,
                            status=PluginStatus.INCOMPATIBLE,
                            package_name=ref.package_name,
                            version=ref.version,
                        )
                        changed = True
                        break

    # ── 循环依赖检测 ──────────────────────────────────────────────────────────
    cycle_members = _detect_dependency_cycles(discovered)
    if cycle_members:
        for name in cycle_members:
            cycle_ref: PluginRef | None = discovered.get(name)
            if cycle_ref is not None and cycle_ref.status == PluginStatus.LOADED:
                discovered[name] = PluginRef(
                    manifest=cycle_ref.manifest,
                    status=PluginStatus.INCOMPATIBLE,
                    package_name=cycle_ref.package_name,
                    version=cycle_ref.version,
                )

    # ── S18 有效权限传递闭包计算 ───────────────────────────────────────────────
    discovered = _compute_effective_permissions(discovered, trusted, require_approval, disabled_list, engine_version)

    # ── S19 僵尸条目清理 ───────────────────────────────────────────────────────
    # 仅当 entry_points 扫描成功时执行——扫描异常时跳过清理（fail-safe：宁可残留也不可误删）
    if eps_scan_ok:
        known_packages = set(discovered.keys())
        try:
            state.clean_zombie_disabled(known_packages)
        except Exception as exc:
            log.warning("plugin_zombie_disabled_clean_failed", error=str(exc))
        try:
            state.clean_zombie_configs(known_packages)
        except Exception as exc:
            log.warning("plugin_zombie_configs_clean_failed", error=str(exc))
        try:
            state.clean_zombie_trusted(known_packages)
        except Exception as exc:
            log.warning("plugin_zombie_trusted_clean_failed", error=str(exc))
    else:
        log.warning("plugin_zombie_cleanup_skipped", reason="entry_points scan failed")

    return discovered


def _compute_effective_permissions(
    discovered: dict[str, PluginRef],
    trusted: dict[str, Any],
    require_approval: str,
    disabled_list: list[str],
    engine_version: str = "",
) -> dict[str, PluginRef]:
    """S18 有效权限传递闭包计算（后处理阶段）。

    对所有非 INCOMPATIBLE、非 DISABLED 的插件按拓扑序计算：
    1. effective_permissions = 自身 manifest 权限 ∪ 所有依赖的 effective_permissions 并集
    2. effective_permission_level = derive_permission_level(effective_permissions)
    3. 被依赖提升等级的插件重新调用 _determine_status(effective_level=...)
    4. 依赖级联重跑
    """
    # 收集可参与计算的插件
    active = {
        name: ref
        for name, ref in discovered.items()
        if ref.status not in (PluginStatus.INCOMPATIBLE, PluginStatus.DISABLED)
    }
    if not active:
        return discovered

    # 拓扑排序
    in_degree: dict[str, int] = dict.fromkeys(active, 0)
    adj: dict[str, list[str]] = {name: [] for name in active}
    for name, ref in active.items():
        for dep_name in ref.manifest.requires_plugins:
            if dep_name in active:
                adj[dep_name].append(name)
                in_degree[name] = in_degree.get(name, 0) + 1

    queue = [name for name, deg in in_degree.items() if deg == 0]
    topo_order: list[str] = []
    while queue:
        node = queue.pop(0)
        topo_order.append(node)
        for neighbor in adj.get(node, []):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    # 按拓扑序计算传递闭包
    effective_perms: dict[str, set[str]] = {}
    for name in topo_order:
        ref = active[name]
        perms: set[str] = set()
        # 自身 manifest 权限（含 auto-grant + write→read 隐含）
        for cap in ref.manifest.capabilities:
            perms.update(_resolve_permissions(cap.permissions))
        # 合并所有依赖的 effective_permissions
        for dep_name in ref.manifest.requires_plugins:
            if dep_name in effective_perms:
                perms.update(effective_perms[dep_name])
        effective_perms[name] = perms

    # 写入 PluginRef + 状态重评估
    result = dict(discovered)
    for name in topo_order:
        ref = active[name]
        perms = effective_perms[name]
        eff_level = derive_permission_level(list(perms))
        self_level = _effective_max_permission_level(ref.manifest)

        result[name] = PluginRef(
            manifest=ref.manifest,
            status=ref.status,
            package_name=ref.package_name,
            version=ref.version,
            effective_permissions=tuple(sorted(perms)),
            effective_permission_level=eff_level,
        )

        # 被依赖提升等级 → 重新评估状态
        if permission_level_higher_than(eff_level, self_level):
            new_status = _determine_status(
                ref.manifest,
                ref.package_name,
                ref.version,
                engine_version,
                disabled_list,
                require_approval,
                trusted,
                effective_level=eff_level,
            )
            if new_status != ref.status:
                result[name] = PluginRef(
                    manifest=ref.manifest,
                    status=new_status,
                    package_name=ref.package_name,
                    version=ref.version,
                    effective_permissions=tuple(sorted(perms)),
                    effective_permission_level=eff_level,
                )

    # ── 依赖级联重跑 ─────────────────────────────────────────────────────────
    # 状态降级（LOADED→PENDING_REVIEW/INCOMPATIBLE）可能级联影响依赖者
    changed = True
    while changed:
        changed = False
        for name, ref in list(result.items()):
            if ref.status in (PluginStatus.INCOMPATIBLE, PluginStatus.DISABLED):
                continue
            for dep_name, _dep_constraint in ref.manifest.requires_plugins.items():
                dep_ref = result.get(dep_name)
                if dep_ref is None or dep_ref.status != PluginStatus.LOADED:
                    log.warning(
                        "plugin_dependency_not_loaded",
                        plugin=name,
                        dependency=dep_name,
                        dep_status=dep_ref.status.value if dep_ref else "not_found",
                    )
                    result[name] = PluginRef(
                        manifest=ref.manifest,
                        status=PluginStatus.INCOMPATIBLE,
                        package_name=ref.package_name,
                        version=ref.version,
                        effective_permissions=ref.effective_permissions,
                        effective_permission_level=ref.effective_permission_level,
                    )
                    changed = True
                    break

    return result


def _load_plugin_ref(
    package_name: str,
    engine_version: str,
    disabled_list: list[str],
    require_approval: str,
    trusted: dict[str, Any],
) -> PluginRef:
    """加载单个插件的 PluginRef——统一入口。

    验证层（S6/S7/implements/S20）对所有插件同等执行，fail-closed。
    """
    manifest_path = _find_manifest_path(package_name)
    if manifest_path is None:
        return PluginRef(
            manifest=PluginManifest(manifest_version=1, name=package_name, requires_engine=">=0.1"),
            status=PluginStatus.INCOMPATIBLE,
            package_name=package_name,
            version="0.0.0",
        )

    try:
        data = _read_manifest_file(manifest_path)
        data = _validate_manifest_s6(data, package_name)
    except ManifestValidationError as exc:
        log.error("plugin_s6_rejected", package=package_name, error=str(exc))
        return PluginRef(
            manifest=PluginManifest(manifest_version=1, name=package_name, requires_engine=">=0.1"),
            status=PluginStatus.INCOMPATIBLE,
            package_name=package_name,
            version="0.0.0",
        )

    manifest = PluginManifest.from_dict(data)

    # S7 身份防伪造
    if manifest.name != package_name:
        log.error(
            "plugin_s7_identity_mismatch",
            entry_point_package=package_name,
            manifest_name=manifest.name,
        )
        return PluginRef(
            manifest=manifest,
            status=PluginStatus.INCOMPATIBLE,
            package_name=package_name,
            version="0.0.0",
        )

    # 两段式 implements 校验
    for cap in manifest.capabilities:
        if not cap.validate_implements():
            valid = get_valid_implements(cap.group)
            log.error(
                "plugin_implements_invalid",
                package=package_name,
                group=cap.group,
                implements=cap.implements,
                valid=",".join(valid) if valid else "(third-party group)",
            )
            return PluginRef(
                manifest=manifest,
                status=PluginStatus.INCOMPATIBLE,
                package_name=package_name,
                version="0.0.0",
            )

    # S20 组合约束
    try:
        _check_s20_combo(list(manifest.capabilities))
    except ManifestValidationError as exc:
        log.error("plugin_s20_rejected", package=package_name, error=str(exc))
        return PluginRef(
            manifest=manifest,
            status=PluginStatus.INCOMPATIBLE,
            package_name=package_name,
            version="0.0.0",
        )

    version = _get_package_version(package_name)

    # 废弃策略：REMOVED 过滤 + WARNING/ERROR 日志
    manifest = _apply_deprecation_policy(manifest, package_name, engine_version)

    status = _determine_status(
        manifest=manifest,
        package_name=package_name,
        version=version,
        engine_version=engine_version,
        disabled_list=disabled_list,
        require_approval=require_approval,
        trusted=trusted,
    )

    return PluginRef(
        manifest=manifest,
        status=status,
        package_name=package_name,
        version=version,
    )


def _check_trust_record(
    manifest: PluginManifest,
    package_name: str,
    version: str,
    trusted: dict[str, Any],
) -> PluginStatus | None:
    """检查信任记录是否匹配当前包。返回 None 表示无匹配记录，继续后续判定。

    ADR-0011 S17 哈希 pin 优先于版本匹配：
      - trusted_hash 存在且匹配当前包 → LOADED（内容未变，即使版本已升级）
      - trusted_hash 存在但不匹配 → 尝试重新签名验证 → PENDING_REVIEW / FAILED（G1 修正）
      - trusted_hash 不存在但 trusted_version 匹配 → LOADED（旧信任记录兼容）
      - trusted_version 不匹配 → PENDING_REVIEW
    """
    from astroframe._signature import compute_package_hash, verify_plugin

    for cap in manifest.capabilities:
        cap_key = f"{package_name}/{cap.name}"
        trust_record = trusted.get(cap_key)
        if not trust_record:
            continue

        trusted_version = trust_record.get("granted_version")
        trusted_hash = trust_record.get("granted_hash")

        # 哈希 pin —— 优先于版本匹配（S17 最终锚点）
        if trusted_hash and trusted_hash.strip():
            current_hash = compute_package_hash(package_name)
            if current_hash is None:
                if trusted_version == version:
                    return PluginStatus.LOADED
                log.info("plugin_trust_version_changed_no_hash", package=package_name, cap=cap_key)
                return PluginStatus.PENDING_REVIEW

            if trusted_hash == current_hash:
                if trusted_version != version:
                    log.info(
                        "plugin_trust_version_changed_hash_unchanged",
                        package=package_name,
                        cap=cap_key,
                        old_version=trusted_version,
                        new_version=version,
                    )
                return PluginStatus.LOADED

            # G1 修正：哈希不匹配 → 内容已变更 → 尝试重新签名验证
            log.info(
                "plugin_trust_hash_changed_reverify",
                package=package_name,
                cap=cap_key,
                trusted=trusted_hash[:16],
                current=current_hash[:16],
            )

            signing = manifest.signing
            if signing is not None and signing.get("method", "unsigned") != "unsigned":
                try:
                    sig_result = verify_plugin(package_name, manifest, trusted_hash=None)
                except Exception as exc:
                    log.error("plugin_signature_reverify_error", package=package_name, error=str(exc))
                    return PluginStatus.FAILED
                if sig_result.verified:
                    log.info(
                        "plugin_signature_reverify_passed",
                        package=package_name,
                        cap=cap_key,
                        method=signing.get("method"),
                    )
                else:
                    log.warning(
                        "plugin_signature_reverify_failed",
                        package=package_name,
                        cap=cap_key,
                        method=signing.get("method"),
                        error=sig_result.error,
                    )

            return PluginStatus.PENDING_REVIEW

        # 旧信任记录（无 hash attribute）→ 版本匹配兼容
        if trusted_version == version:
            return PluginStatus.LOADED

        log.info("plugin_trust_version_changed", package=package_name, cap=cap_key)
        return PluginStatus.PENDING_REVIEW

    return None  # 无匹配的信任记录


def _determine_status(
    manifest: PluginManifest,
    package_name: str,
    version: str,
    engine_version: str,
    disabled_list: list[str],
    require_approval: str,
    trusted: dict[str, Any],
    *,
    effective_level: PermissionLevel | None = None,
) -> PluginStatus:
    """两个检查窗口之窗口 1（扫描时）——确定插件状态（ADR-0011 决策 7 + S18）。

    所有插件走统一管线，无特权身份。零 source 感知。

    effective_level: S18 传递闭包计算后的有效权限等级。非 None 且高于自身
    max_permission_level 时生效——跳过 Tier 检查，用 effective_level 做信任决策。
    None 时行为不变（向后兼容逐插件初始调用）。
    """

    if package_name in disabled_list:
        return PluginStatus.DISABLED

    if not _check_engine_version(manifest.requires_engine, engine_version):
        log.info(
            "plugin_engine_incompatible",
            package=package_name,
            requires=manifest.requires_engine,
            engine=engine_version,
        )
        return PluginStatus.INCOMPATIBLE

    trust_result = _check_trust_record(manifest, package_name, version, trusted)
    if trust_result is not None:
        return trust_result

    # 零 capability 的插件 — 无害，直接 LOADED
    if not manifest.capabilities:
        return PluginStatus.LOADED

    # S17: 签名验证——有 signing 声明且非 unsigned 的插件在首次发现时验证
    signing = manifest.signing
    if signing is not None and signing.get("method", "unsigned") != "unsigned":
        try:
            from astroframe._signature import verify_plugin

            sig_result = verify_plugin(package_name, manifest, trusted_hash=None)
        except Exception as exc:
            log.error("plugin_signature_infra_error", package=package_name, error=str(exc))
            return PluginStatus.FAILED
        if not sig_result.verified:
            log.warning(
                "plugin_signature_unverified",
                package=package_name,
                method=signing.get("method"),
                error=sig_result.error,
            )
        return PluginStatus.PENDING_REVIEW

    self_level = _effective_max_permission_level(manifest)

    # S18: effective_level 仅在高于自身等级时生效
    if effective_level is not None and permission_level_higher_than(effective_level, self_level):
        # 等级被依赖提升——跳过 Tier 检查，直接走信任决策
        # S18 传递信任语义：若 effective_level 完全来自依赖（非自身 manifest），
        # 用户需信任的是依赖项的 SIGNATURE 功能——跳过本插件信任记录检查，
        # 返回 PENDING_REVIEW 让用户显式确认 SIGNATURE 依赖链
        return _check_sig_or_pending(manifest, trusted, package_name, version, effective_level=effective_level)

    # effective_level 未生效时：使用自身 manifest 等级走原逻辑
    decision_level = effective_level if effective_level is not None else self_level

    if require_approval == "none":
        return _check_signature_level(manifest, trusted, package_name, version, effective_level=decision_level)

    if require_approval == "dangerous":
        max_level = decision_level
        if max_level == PermissionLevel.NORMAL:
            return PluginStatus.LOADED
        if max_level == PermissionLevel.SIGNATURE:
            return _check_sig_or_pending(manifest, trusted, package_name, version, effective_level=max_level)
        # DANGEROUS → PENDING_REVIEW
        return PluginStatus.PENDING_REVIEW

    # require_approval == "all" (default): non-Pure → PENDING_REVIEW
    # 但若 effective_level 生效（被依赖提升），已在上面返回
    return PluginStatus.PENDING_REVIEW


def _effective_max_permission_level(manifest: PluginManifest) -> PermissionLevel:
    """计算插件的最高权限等级（所有 capability 的最高等级）。"""
    levels = {PermissionLevel.NORMAL: 0, PermissionLevel.DANGEROUS: 1, PermissionLevel.SIGNATURE: 2}
    max_level = PermissionLevel.NORMAL
    for cap in manifest.capabilities:
        level = derive_permission_level(list(cap.permissions))
        if levels.get(level, 0) > levels.get(max_level, 0):
            max_level = level
    return max_level


def _check_signature_level(
    manifest: PluginManifest,
    trusted: dict[str, Any],
    package_name: str,
    version: str,
    *,
    effective_level: PermissionLevel | None = None,
) -> PluginStatus:
    level = effective_level if effective_level is not None else _effective_max_permission_level(manifest)
    if level == PermissionLevel.SIGNATURE:
        return _check_sig_or_pending(manifest, trusted, package_name, version, effective_level=level)
    return PluginStatus.LOADED


def _check_sig_or_pending(
    manifest: PluginManifest,
    trusted: dict[str, Any],
    package_name: str,
    version: str,
    *,
    effective_level: PermissionLevel | None = None,
) -> PluginStatus:
    # S18 传递信任语义：若 effective_level 高于自身等级（来自依赖），
    # 本插件自身的信任记录不覆盖依赖的 SIGNATURE——返回 PENDING_REVIEW
    self_level = _effective_max_permission_level(manifest)
    if effective_level is not None and permission_level_higher_than(effective_level, self_level):
        return PluginStatus.PENDING_REVIEW

    # SIGNATURE 权限 + 未签名/无签名 → 硬拒绝（INCOMPATIBLE）
    decision_level = effective_level if effective_level is not None else self_level
    if decision_level == PermissionLevel.SIGNATURE:
        signing = manifest.signing
        if signing is None or signing.get("method", "unsigned") == "unsigned":
            log.error(
                "plugin_signature_required",
                package=package_name,
                level=decision_level.value,
            )
            return PluginStatus.INCOMPATIBLE

    for cap in manifest.capabilities:
        cap_key = f"{package_name}/{cap.name}"
        if cap_key in trusted:
            return PluginStatus.LOADED
    return PluginStatus.PENDING_REVIEW

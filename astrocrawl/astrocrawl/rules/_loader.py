"""规则加载器 — 从目录递归加载 JSON 规则文件，构造 RuleSnapshot。

加载顺序：pip 预置 → 远程缓存 → 用户自定义 → default。
同名规则按源优先级 + version 去重 (S35)。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Sequence, Tuple

from astrobasis import LogfmtLogger
from astrocrawl._constants import MAX_JSON_DEPTH, MAX_RULE_FILE_SIZE, MAX_RULES_TOTAL, SOURCE_PRIORITY
from astrocrawl._types import DEFAULT_EXTRACTION_TYPE, RuleSnapshot
from astrocrawl.config import ConfigValidationError, CrawlerConfig
from astrocrawl.rules._io import safe_read_rule_file
from astrocrawl.rules._schema import MatchScope, RuleSchema, validate_rule
from astrocrawl.rules._state import get_disabled_rules

if TYPE_CHECKING:
    import threading

logger = LogfmtLogger("astrocrawl.rules.loader")


class RuleConflictError(ConfigValidationError):
    """规则配置歧义：多个规则对同一 URL 空间具有相同指定性。

    conflicts: [[name1, name2], ...] — 每组为互斥的规则名列表。
    """

    def __init__(self, conflicts: Sequence[Sequence[str]]) -> None:
        self.conflicts = tuple(tuple(g) for g in conflicts)
        groups = [", ".join(g) for g in self.conflicts]
        super().__init__(f"规则配置歧义 — {len(self.conflicts)} 组冲突规则: {'; '.join(groups)}")


def build_rule_snapshot(
    cfg: CrawlerConfig,
    *,
    state_file: Path | None = None,
    extra_rules_dirs: list | None = None,
    rules_dirs_enabled: bool = True,
) -> RuleSnapshot:
    """从所有规则目录加载并构造 RuleSnapshot。

    加载顺序保持确定性：pip → remote → user → default。
    同名规则按 (源优先级, version DESC) 去重。
    """
    # 构建搜索路径
    _base = Path(__file__).resolve().parent  # astrocrawl/rules/
    dirs: List[Tuple[str, Path]] = []

    # pip 预置
    pip_dir = _base
    if pip_dir.is_dir():
        dirs.append(("pip", pip_dir))

    # 远程缓存
    remote_dir = Path.home() / ".astrocrawl" / "rules_cache"
    if remote_dir.is_dir():
        dirs.append(("remote", remote_dir))

    # 用户自定义
    user_dir = Path.home() / ".astrocrawl" / "rules"
    if user_dir.is_dir():
        dirs.append(("user", user_dir))

    # 用户额外目录 (GlobalSettings.rules_dirs, 受 rules_dirs_enabled 保护)
    if rules_dirs_enabled:
        for d in extra_rules_dirs or []:
            p = Path(d).expanduser().resolve()
            if p.is_dir():
                dirs.append(("user", p))
            elif p.exists():
                logger.warning("rules_dir_not_directory", path=p)
            else:
                logger.debug("rules_dir_not_found", path=p)

    all_rules: List[Tuple[RuleSchema, Path, str]] = []  # (rule, path, source)

    for source, directory in dirs:
        try:
            loaded = _load_from_dir(directory, source)
            all_rules.extend(loaded)
        except Exception as exc:
            logger.warning("rule_load_dir_failed", dir=directory, source=source, error=exc)

    # 去重：同名规则按源优先级 > version 决出唯一
    deduped = _deduplicate_rules(all_rules)
    if not deduped:
        return RuleSnapshot.default_only()

    # 提取路径和来源索引（去重获胜者的 path/source），之后只用 RuleSchema 列表
    _path_map: Dict[str, str] = {}
    _source_map: Dict[str, str] = {}
    deduped_rules: List[RuleSchema] = []
    for rule, path, source in deduped:
        _path_map[rule.name] = str(path)
        _source_map[rule.name] = source
        deduped_rules.append(rule)

    # 按匹配优先级排序 (N14)：scope 精度 > url_pattern 长度 > version
    deduped_rules.sort(key=_rule_sort_key)

    # 数量限制
    if len(deduped_rules) > MAX_RULES_TOTAL:
        logger.warning(
            "rule_limit_exceeded",
            total=len(deduped_rules),
            max=MAX_RULES_TOTAL,
        )
        deduped_rules = deduped_rules[:MAX_RULES_TOTAL]

    generic_count = sum(1 for r in deduped_rules if r.is_generic)
    if generic_count > cfg.rules_max_generic:
        logger.warning(
            "generic_rule_limit_exceeded",
            total=generic_count,
            max=cfg.rules_max_generic,
        )
        kept = 0
        filtered: List[RuleSchema] = []
        for r in deduped_rules:
            if r.is_generic:
                if kept < cfg.rules_max_generic:
                    filtered.append(r)
                    kept += 1
            else:
                filtered.append(r)
        deduped_rules = filtered

    # ── 加载启用/禁用状态覆盖 ──
    disabled_names, state_exists = get_disabled_rules(state_file)

    # ── 构建索引 ──
    by_name: Dict[str, RuleSchema] = {}
    by_domain: Dict[str, List[str]] = {}

    for rule in deduped_rules:
        r = rule
        # 状态文件存在时以它为准；不存在时保留文件级 enabled（向后兼容）
        if state_exists:
            r = rule.model_copy(update={"enabled": rule.name not in disabled_names})

        by_name[rule.name] = r
        if r.enabled:
            for domain in rule.match.domains:
                if domain not in by_domain:
                    by_domain[domain] = []
                by_domain[domain].append(rule.name)

    # 确保 default 始终在 by_name 中（匹配算法回退需要）
    if DEFAULT_EXTRACTION_TYPE not in by_name:
        by_name[DEFAULT_EXTRACTION_TYPE] = RuleSchema(name=DEFAULT_EXTRACTION_TYPE, enabled=True)  # type: ignore[call-arg]

    # 检测歧义规则（state file 覆盖后，基于最终 enabled 状态）
    final_rules = [r for name, r in by_name.items() if name != DEFAULT_EXTRACTION_TYPE]
    conflicts = _detect_ambiguous_rules(final_rules)
    if conflicts:
        for group in conflicts:
            logger.warning("rule_conflict", rules=", ".join(group))

    # 过滤路径/来源索引：仅保留通过截断进入 by_name 的规则，保持与 by_name 一致
    _path_map = {name: p for name, p in _path_map.items() if name in by_name}
    _source_map = {name: s for name, s in _source_map.items() if name in by_name}

    # rules 元组：仅已启用规则（匹配器/引擎消费），保持排序顺序
    rules_tuple = tuple(r for name, r in by_name.items() if r.enabled and name != DEFAULT_EXTRACTION_TYPE)

    frozen_by_domain = {k: tuple(v) for k, v in by_domain.items()}

    # 构建 _generic_rules：无 domain 的泛型规则独立索引
    generic_names = [r.name for r in deduped_rules if r.is_generic and r.name in by_name and by_name[r.name].enabled]
    generic_names.sort()

    return RuleSnapshot(
        rules=rules_tuple,
        by_name=by_name,
        by_domain=frozen_by_domain,
        _generic_rules=tuple(generic_names),
        _path_map=_path_map,
        _source_map=_source_map,
        _conflicts=tuple(tuple(g) for g in conflicts) if conflicts else (),
    )


def _load_from_dir(directory: Path, source: str) -> List[Tuple[RuleSchema, Path, str]]:
    """递归加载目录中所有 .json 规则文件，跳过无效文件。返回 (rule, path, source)。"""
    rules: List[Tuple[RuleSchema, Path, str]] = []

    def _on_walk_error(err: OSError) -> None:
        logger.warning("rule_dir_unreadable", path=err.filename, error=err)

    try:
        json_files: List[Path] = []
        for root, _dirs, files in os.walk(str(directory), onerror=_on_walk_error):
            for f in files:
                if f.endswith(".json"):
                    json_files.append(Path(root) / f)
        for entry in sorted(json_files):
            rule = load_rule_file(entry, source)
            if rule is not None:
                rules.append((rule, entry, source))
    except PermissionError:
        logger.warning("rule_dir_permission_denied", dir=directory)
    return rules


def load_rule_file(path: Path, source: str) -> RuleSchema | None:
    """加载并校验单个规则文件。失败返回 None + WARNING。"""
    try:
        # S14: 文件大小门控
        size = path.stat().st_size
        if size > MAX_RULE_FILE_SIZE:
            logger.warning("rule_file_too_large", path=path, size=size, max=MAX_RULE_FILE_SIZE)
            return None
        if size == 0:
            return None

        # S17/S18/S25: UTF-8 + BOM + 重复 key 检测
        data = safe_read_rule_file(path)

        # S24: JSON 深度限制
        if not _check_json_depth(data, MAX_JSON_DEPTH):
            logger.warning("rule_json_depth_exceeded", path=path, max=MAX_JSON_DEPTH)
            return None

        # 跳过 _prompt_template.txt 等非规则文件 (可能在递归中被误匹配)
        if "fields" not in data:
            return None

        rule = validate_rule(data)
        return rule
    except ValueError as e:
        # JSON 解析错误 (含 S25 重复 key) + Schema 校验错误
        logger.warning("rule_load_invalid", path=path, error=e)
        return None
    except Exception as e:
        logger.warning("rule_load_error", path=path, error=e)
        return None


def _check_json_depth(obj: Any, max_depth: int, _current: int = 0) -> bool:
    """递归检查 JSON 对象嵌套深度 ≤ max_depth (S24)。"""
    if _current > max_depth:
        return False
    if isinstance(obj, dict):
        return all(_check_json_depth(v, max_depth, _current + 1) for v in obj.values())
    if isinstance(obj, list):
        return all(_check_json_depth(v, max_depth, _current + 1) for v in obj)
    return True


def _deduplicate_rules(rules: List[Tuple[RuleSchema, Path, str]]) -> List[Tuple[RuleSchema, Path, str]]:
    """同名规则去重 (S35)：源优先级 > version DESC。保留获胜者的 path 和 source。"""
    best: Dict[str, Tuple[RuleSchema, Path, str]] = {}
    for rule, path, source in rules:
        pri = SOURCE_PRIORITY.get(source, 99)
        if rule.name not in best:
            best[rule.name] = (rule, path, source)
        else:
            existing, _, exist_src = best[rule.name]
            exist_pri = SOURCE_PRIORITY.get(exist_src, 99)
            if pri < exist_pri or (pri == exist_pri and rule.version > existing.version):
                best[rule.name] = (rule, path, source)
    return list(best.values())


_SCOPE_SORT: Dict["MatchScope", int] = {
    MatchScope.DOMAIN_PATTERN: 0,
    MatchScope.DOMAIN_ALL: 1,
    MatchScope.GLOBAL_PATTERN: 2,
    MatchScope.ANY: 3,
}


def _rule_sort_key(rule: RuleSchema) -> Tuple[int, int, int, int]:
    """排序键：数值越小优先级越高。

    Scope priority (DOMAIN_PATTERN < DOMAIN_ALL < GLOBAL_PATTERN < ANY),
    -url_pattern length (longer first, only for pattern scopes),
    -version (higher first).
    """
    scope_pri = _SCOPE_SORT.get(rule.match.scope, 3)

    if rule.match.scope in (MatchScope.DOMAIN_PATTERN, MatchScope.GLOBAL_PATTERN):
        pattern_len = -(len(rule.match.url_pattern))
    else:
        pattern_len = 0

    neg_version = -(rule.version)
    return (scope_pri, pattern_len, neg_version, 0)  # 源优先级在 _deduplicate_rules 中已处理


def _detect_ambiguous_rules(deduped_rules: list[RuleSchema]) -> list[list[str]]:
    """检测指定性完全相同的启用规则组（每 URL 匹配歧义）。"""
    from collections import defaultdict

    by_scope: dict[MatchScope, list[RuleSchema]] = defaultdict(list)
    for r in deduped_rules:
        if r.enabled:
            by_scope[r.match.scope].append(r)

    conflicts: list[list[str]] = []
    for scope, rules in by_scope.items():
        groups: dict[tuple[Any, ...], list[str]] = defaultdict(list)
        for r in rules:
            key: tuple[Any, ...]
            if scope == MatchScope.DOMAIN_PATTERN:
                key = (tuple(sorted(r.match.domains)), r.match.url_pattern, r.version)
            elif scope == MatchScope.DOMAIN_ALL:
                key = (tuple(sorted(r.match.domains)), r.version)
            elif scope == MatchScope.GLOBAL_PATTERN:
                key = (r.match.url_pattern, r.version)
            elif scope == MatchScope.ANY:
                key = (r.version,)
            else:
                continue
            groups[key].append(r.name)
        for names in groups.values():
            if len(names) > 1:
                conflicts.append(names)

    return conflicts


def ensure_no_rule_conflicts(snapshot: RuleSnapshot) -> None:
    """如果快照包含规则歧义则 raise RuleConflictError。"""
    if snapshot._conflicts:
        raise RuleConflictError(snapshot._conflicts)


def validate_rule_files(
    cfg: CrawlerConfig,
    *,
    extra_rules_dirs: list | None = None,
    cancel_event: threading.Event | None = None,
) -> list:
    """扫描所有规则目录，对每个 .json 文件执行完整校验。

    返回 [{status, path, source, name?, schema_version?, fields_count?, error?}, ...]
    status: "pass" | "fail" | "skip"

    cancel_event 不为 None 时，每处理完一个文件检查 cancel_event.is_set()，
    若已设置则立即返回部分结果。用于 QThread 协作式取消。
    """
    _base = Path(__file__).resolve().parent  # astrocrawl/rules/
    dirs: list[tuple[str, Path]] = []

    pip_dir = _base
    if pip_dir.is_dir():
        dirs.append(("pip", pip_dir))

    remote_dir = Path.home() / ".astrocrawl" / "rules_cache"
    if remote_dir.is_dir():
        dirs.append(("remote", remote_dir))

    user_dir = Path.home() / ".astrocrawl" / "rules"
    if user_dir.is_dir():
        dirs.append(("user", user_dir))

    for d in extra_rules_dirs or []:
        p = Path(d).expanduser().resolve()
        if p.is_dir():
            dirs.append(("user", p))

    results: list = []
    for source, directory in dirs:
        try:
            json_files = []
            for root, _, files in os.walk(str(directory)):
                for f in files:
                    if f.endswith(".json"):
                        json_files.append(Path(root) / f)
            for entry in sorted(json_files):
                result: dict = {"path": str(entry), "source": source}
                try:
                    rule = load_rule_file(entry, source)
                    if rule is not None:
                        result.update(
                            status="pass",
                            name=rule.name,
                            schema_version=rule.schema_version,
                            fields_count=len(rule.fields),
                        )
                    else:
                        result["status"] = "skip"
                        result["error"] = "文件加载失败（格式错误或校验不通过）"
                except Exception as e:
                    result["status"] = "fail"
                    result["error"] = str(e)
                results.append(result)
                if cancel_event and cancel_event.is_set():
                    return results
        except PermissionError:
            pass
        if cancel_event and cancel_event.is_set():
            return results

    return results

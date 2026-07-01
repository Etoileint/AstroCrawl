"""规则匹配算法 — 四级优先级 + by_domain 索引 + LRU 缓存。

对标 Nginx server_name 三哈希表 + uBlock 域名编译模型。
"运行时不做搜索，搜索发生在构建时。"

每个 URL 匹配唯一规则：域名精确度 > url_pattern 长度 > version > 源优先级 > 名称。
匹配结果按域名缓存，缓存绑定到 RuleSnapshot 生命周期——新快照自带空缓存。
"""

from __future__ import annotations

import logging
from typing import List, Tuple
from urllib.parse import urlparse

from astrocrawl._constants import SOURCE_PRIORITY
from astrocrawl._types import DEFAULT_EXTRACTION_TYPE, RuleSnapshot
from astrocrawl.rules._schema import MatchScope, RuleSchema

logger = logging.getLogger("astrocrawl.rules.matcher")


def match_url(url: str, snapshot: RuleSnapshot) -> str:
    """对 URL 执行 by_domain 索引匹配算法，返回规则名。

    若所有规则均不匹配，返回 DEFAULT_EXTRACTION_TYPE ("default")。
    """
    result = _do_match(url, snapshot)
    return result[0]


def match_url_with_candidates(url: str, snapshot: RuleSnapshot) -> Tuple[str, List[str]]:
    """对 URL 执行 by_domain 索引匹配，返回 (规则名, 候选规则名列表)。(S8 N38 trace)

    候选列表按匹配优先级排序（最优在前），用于诊断。
    """
    return _do_match(url, snapshot)


def _do_match(url: str, snapshot: RuleSnapshot) -> Tuple[str, List[str]]:
    """by_domain 索引驱动匹配：O(域名深度 + 泛型数) ≈ O(25)，从 O(2000) 降低。"""
    parsed = urlparse(url)
    domain = parsed.hostname or ""

    if not domain:
        logger.debug("event=match_no_hostname url=%s", url)

    # 缓存命中 — 直接返回
    cached = snapshot._match_cache.get(domain)
    if cached is not None:
        return cached, [cached]

    path_query = parsed.path
    if parsed.query:
        path_query += "?" + parsed.query

    # 收集候选规则名
    candidate_names_set: set[str] = set()

    # 1. by_domain 精确匹配 → O(1)
    if domain and domain in snapshot.by_domain:
        candidate_names_set.update(snapshot.by_domain[domain])

    # 2. 逐级父域后缀匹配 → O(子域级数)
    if domain:
        parts = domain.lower().rstrip(".").split(".")
        for i in range(1, len(parts)):
            parent = ".".join(parts[i:])
            if parent in snapshot.by_domain:
                candidate_names_set.update(snapshot.by_domain[parent])

    # 3. _generic_rules 泛型扫描 → O(泛型数) ≤ 20
    candidate_names_set.update(snapshot._generic_rules)

    # 4. 解析为 RuleSchema + 按 scope 匹配 url_pattern
    candidates: list[Tuple[RuleSchema, int, int]] = []
    for name in candidate_names_set:
        rule = snapshot.by_name.get(name)
        if rule is None or not rule.enabled:
            continue  # 防御：快照已过滤禁用规则，仅防御手动构造的非标准快照

        domain_score = _match_domain(domain, rule.match.domains)
        if domain_score < 0:
            continue  # 快照约束保证不可达（by_domain 仅含匹配域名的已启用规则）

        scope = rule.match.scope
        if scope in (MatchScope.DOMAIN_PATTERN, MatchScope.GLOBAL_PATTERN):
            if _match_url_pattern(path_query, rule.match.url_pattern):
                pattern_len = len(rule.match.url_pattern)
            else:
                continue
        else:
            pattern_len = 0  # DOMAIN_ALL / ANY — 不限制路径

        candidates.append((rule, domain_score, pattern_len))

    if not candidates:
        snapshot._match_cache.set(domain, DEFAULT_EXTRACTION_TYPE)
        return DEFAULT_EXTRACTION_TYPE, []

    # ── 4. 排序：域名精确度 > url_pattern 长度 > version > 源优先级 > 名称
    candidates.sort(
        key=lambda x: (
            x[1],
            -x[2],
            -x[0].version,
            SOURCE_PRIORITY.get(snapshot._source_map.get(x[0].name, ""), 99),
            x[0].name,
        )
    )

    best = candidates[0][0]
    rule_name = best.name
    candidate_names = [c[0].name for c in candidates]

    if len(candidates) > 1:
        logger.debug("event=rule_conflict url_domain=%s rules=%s winner=%s", domain, candidate_names, rule_name)

    # 仅缓存无路径限制的 scope (DOMAIN_ALL / ANY) — 有 pattern 的同域不同路径可能匹配不同规则
    if best.match.scope in (MatchScope.DOMAIN_ALL, MatchScope.ANY):
        snapshot._match_cache.set(domain, rule_name)
    return rule_name, candidate_names


def _match_domain(hostname: str, rule_domains: list[str]) -> int:
    """域名匹配：0=精确, 1=后缀, 2=泛型, -1=不匹配。

    遍历全部 domain 取最佳分数——后缀匹配后继续扫描，防止父域排在子域前时
    子域精确匹配被父域后缀匹配提前截断。
    """
    if not rule_domains:
        return 2  # 泛型规则——最低优先级

    if not hostname:
        return -1

    hostname = hostname.lower().rstrip(".")
    best = -1

    for rd in rule_domains:
        rd = rd.lower().rstrip(".")
        if hostname == rd:
            return 0  # 精确匹配——最高优先级，不可改进
        if hostname.endswith("." + rd):
            best = 1  # 后缀匹配，继续扫描后续 domain 是否有精确匹配

    return best


def _match_url_pattern(path_query: str, pattern: str) -> bool:
    import re2

    try:
        return bool(re2.search(pattern, path_query))
    except Exception:
        logger.warning("event=url_pattern_match_error pattern=%s", pattern)
        return False

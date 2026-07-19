"""CSS 选择器提取引擎 — text/attr/html 三种提取 + fallback 链 + multiple。

整规则一次 to_thread 调度，内部同步做字段级异常隔离 (N19)。
规则级 asyncio 超时 5s (N18)。
BS4 select()/get_text() 是纯 CPU 操作，无 I/O/锁，不会挂起——字段级超时无物理可能。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from typing import TYPE_CHECKING, Any, Dict, Optional

import cssselect
from bs4 import BeautifulSoup
from cssselect.parser import Pseudo

from astrocrawl._constants import MAX_FALLBACK_DEPTH, MULTIPLE_MAX_ITEMS, SELECTOR_TIMEOUT_PER_RULE

if TYPE_CHECKING:
    from astrocrawl.rules._schema import FieldRule

from astrobasis import LogfmtLogger

logger = LogfmtLogger("astrocrawl.rules.extractor")

# H6: 独立线程池隔离提取超时/僵尸线程故障域
_EXTRACTION_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="rule-extract")
_EXTRACTION_POOL_PRESSURE_THRESHOLD = 0.75

# BeautifulSoup 不支持的 CSS 伪类/伪元素 (N93)
# 伪类：soupsieve 静默返回 None，无异常
_UNSUPPORTED_PSEUDO_CLASSES = frozenset({"hover", "focus", "visited", "active"})
# 伪元素：soupsieve 抛 NotImplementedError
_UNSUPPORTED_PSEUDO_ELEMENTS = frozenset({"after", "before", "selection"})


async def extract_fields(
    html: str,
    rule_name: str,
    fields_config: Dict[str, FieldRule],
    max_text_length: int = 500000,
) -> Dict[str, Any]:
    """对一条规则执行全字段提取（从 HTML 字符串）。整规则 5s 超时 (N18)。"""
    if not html or not html.strip():
        return {}  # N89: HTML 为空优雅跳过

    soup = BeautifulSoup(html, "lxml")
    return await extract_fields_from_soup(soup, rule_name, fields_config, max_text_length)


async def extract_fields_from_soup(
    soup: BeautifulSoup,
    rule_name: str,
    fields_config: Dict[str, FieldRule],
    max_text_length: int = 500000,
) -> Dict[str, Any]:
    """一次专用线程池调度完成全规则提取，内部同步字段隔离 (N19)。"""
    if not fields_config:
        return {}

    # H6: 池压力告警
    pool = _EXTRACTION_EXECUTOR
    active = getattr(pool, "_work_queue", None)
    if active is not None and hasattr(active, "qsize"):
        queued = active.qsize()
        if queued > 0:
            ratio = queued / (queued + 4)
            if ratio > _EXTRACTION_POOL_PRESSURE_THRESHOLD:
                logger.warning("extraction_pool_pressure", queued=queued, ratio=ratio)

    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(pool, _extract_all_fields, soup, fields_config, max_text_length),
            timeout=SELECTOR_TIMEOUT_PER_RULE,
        )
    except asyncio.TimeoutError:
        logger.warning("rule_extract_timeout", rule=rule_name)
        return {}


def _extract_all_fields(
    soup: BeautifulSoup,
    fields_config: Dict[str, FieldRule],
    max_text_length: int = 500000,
) -> Dict[str, Any]:
    """同步提取全规则字段，字段级异常隔离 (N19)。"""
    result: Dict[str, Any] = {}
    for field_name, field_cfg in fields_config.items():
        try:
            result[field_name] = _extract_single_field(soup, field_name, field_cfg, max_text_length)
        except Exception as exc:
            logger.warning("field_extract_error", field=field_name, error=exc)
            result[field_name] = None
    return result


def _extract_single_field(
    soup: BeautifulSoup,
    field_name: str,
    field_cfg: FieldRule,
    max_text_length: int = 500000,
) -> Any:
    """同步执行单字段提取（在专用线程池中运行）。"""
    selector = field_cfg.selector
    extract_type = field_cfg.extract
    attr_name = field_cfg.attr
    multiple = field_cfg.multiple
    fallback = field_cfg.fallback

    # S28: 空 selector 跳过
    if not selector:
        return None

    # N93: 不支持 CSS 伪类警告
    _warn_unsupported_pseudo(selector, field_name)

    # 尝试主 selector
    result = _try_select(soup, selector, extract_type, attr_name, multiple, max_text_length)
    if _is_non_empty(result):
        return result

    # fallback 链
    for fb in fallback[: MAX_FALLBACK_DEPTH - 1]:
        if not fb.selector:
            continue
        _warn_unsupported_pseudo(fb.selector, field_name)
        result = _try_select(soup, fb.selector, fb.extract, fb.attr, multiple, max_text_length)
        if _is_non_empty(result):
            return result

    return result  # 可能为空


def _warn_unsupported_pseudo(selector: str, field_name: str) -> None:
    """检测并警告不支持的 CSS 伪类/伪元素 (N93)。

    使用 cssselect 解析选择器 AST，对伪类/伪元素节点做 frozenset 成员检查。
    废弃字符串匹配方案——CSS 语法中 ':hover' 可出现在引号内属性值或更长
    伪类名 (:hovering) 中，只有结构化 AST 遍历能可靠区分。
    """
    try:
        parsed = cssselect.parse(selector)
    except Exception:
        return  # 无效选择器由 BS4 在运行时报告

    for sel in parsed:
        # 伪元素 (::after / ::before / ::selection)
        pe = getattr(sel, "pseudo_element", None)
        if pe and pe in _UNSUPPORTED_PSEUDO_ELEMENTS:
            logger.warning(
                "unsupported_css_pseudo",
                selector=selector,
                field=field_name,
                pseudo=f"::{pe}",
            )
            return

        # 伪类 (:hover / :focus / :visited / :active)
        for node in _walk_pseudo_nodes(sel.parsed_tree):
            if node.ident in _UNSUPPORTED_PSEUDO_CLASSES:
                logger.warning(
                    "unsupported_css_pseudo",
                    selector=selector,
                    field=field_name,
                    pseudo=f":{node.ident}",
                )
                return


def _walk_pseudo_nodes(node):
    """递归遍历 cssselect AST，yield 所有 Pseudo 节点。

    cssselect AST 节点类型及可遍历属性:
      Selector      → .parsed_tree  (根 AST 节点)
      Pseudo        → .selector     (内部选择器)
      Negation      → .selector, .subselector
      CombinedSel   → .selector, .subselector
      Function      → .selector     (如 :nth-child(2))
      Element       → 无 (叶子节点，.element 是标签名字符串)
      Class/Attrib  → 无 (叶子节点)
    """
    if isinstance(node, Pseudo):
        yield node
    for attr in ("parsed_tree", "selector", "subselector"):
        child = getattr(node, attr, None)
        if child is not None and hasattr(child, "__dict__"):
            yield from _walk_pseudo_nodes(child)


def _try_select(
    soup: BeautifulSoup,
    selector: str,
    extract_type: str,
    attr_name: str,
    multiple: bool,
    max_text_length: int = 500000,
) -> Any:
    """执行单次 CSS 选择并提取。"""
    try:
        if multiple:
            elements = soup.select(selector)
            if not elements:
                return [] if multiple else None
            results = []
            for el in elements[:MULTIPLE_MAX_ITEMS]:
                val = _extract_value(el, extract_type, attr_name, max_text_length)
                if val:
                    results.append(val)
            return results if results else ([] if multiple else None)
        else:
            el = soup.select_one(selector)  # type: ignore[assignment]
            if el is None:
                return None
            return _extract_value(el, extract_type, attr_name, max_text_length)
    except Exception:
        logger.warning("selector_error", selector=selector)
        return None


def _extract_value(
    element,
    extract_type: str,
    attr_name: str,
    max_text_length: int = 500000,
) -> Optional[str]:
    """从 BeautifulSoup 元素提取值。L10: 提取层截断超长文本。"""
    if extract_type == "attr":
        val = element.get(attr_name)
        if val is not None:
            if isinstance(val, str):
                stripped = val.strip()
                result = stripped if stripped else ""
            else:
                # L6: 布尔属性存在但无值 → "" (存在)，非 None (不存在)
                result = ""
            return _truncate_if_needed(result, max_text_length)
        return None
    elif extract_type == "html":
        val = element.decode_contents()
        stripped = val.strip() if val else ""
        if not stripped:
            # M15: void 元素 html 提取静默为空
            logger.debug("extract_html_void", tag=element.name)
            return None
        return _truncate_if_needed(stripped, max_text_length)
    else:  # "text"
        raw = element.get_text()
        val = " ".join(raw.split()) if raw else ""
        if val:
            return _truncate_if_needed(val, max_text_length)
        return None


def _truncate_if_needed(val: str, max_text_length: int) -> str:
    """L10: 提取层截断超长文本，防御 50MB <div> 等内存放大。

    按字节截断——UTF-8 边界感知，避免 CJK 字符被截断后仍超限。
    """
    if isinstance(val, str):
        raw = val.encode("utf-8", errors="ignore")
        if len(raw) > max_text_length:
            logger.warning("field_text_truncated", length=len(val), max=max_text_length)
            return raw[:max_text_length].decode("utf-8", errors="ignore")
    return val


def _is_non_empty(val: Any) -> bool:
    """检查提取结果是否为非空。"""
    if val is None:
        return False
    if isinstance(val, list):
        return len(val) > 0
    if isinstance(val, str):
        return bool(val.strip())
    return True

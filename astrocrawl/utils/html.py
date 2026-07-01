from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from hashlib import md5
from typing import Any, Dict, List, Optional, Set, Tuple, cast
from typing import Protocol as _Protocol
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from astrocrawl._constants import DOWNLOAD_EXTENSIONS
from astrocrawl.config import DEFAULT_CONFIG
from astrocrawl.utils.url import is_valid_http_url, normalize_url, parse_domain


@dataclass
class ParseResult:
    """HTML 解析结果数据类 — 由 check_meta_robots + extract_title + extract_text_from_soup + extract_links_from_soup 组合填充。"""

    text: str
    links: List[str]
    allow_index: bool
    allow_follow: bool
    title: str = ""
    parse_error: bool = False
    text_truncated: bool = False
    original_text_len: int = 0
    nofollow_skipped: int = 0
    cross_domain_skipped: int = 0
    invalid_url_skipped: int = 0
    download_candidate_skipped: int = 0
    same_page_dupes: int = 0


class ContentConfig(_Protocol):
    """extract_text_from_soup / compute_robust_hash 需要的配置字段（ISP 窄接口）。"""

    respect_meta_robots: bool
    follow_nofollow: bool
    max_text_length: int
    content_hash_sample_size: int


_CONTENT_FREE_TAGS: frozenset[str] = frozenset({"script", "style", "noscript", "iframe", "embed"})
_SEMANTIC_CONTAINER_TAGS: frozenset[str] = frozenset({"nav", "footer", "header", "aside"})
_SKIP_HREF_PREFIXES = ("#", "javascript:", "mailto:", "tel:", "data:")


def check_meta_robots(soup: BeautifulSoup, respect_meta_robots: bool) -> Tuple[bool, bool]:
    """检查 meta robots 标签，返回 (allow_index, allow_follow)。"""
    if not respect_meta_robots:
        return True, True
    meta = soup.find("meta", attrs={"name": lambda x: isinstance(x, str) and x.lower() == "robots"})
    if meta and meta.get("content"):
        content = cast("str", meta["content"]).lower()
        return "noindex" not in content, "nofollow" not in content
    return True, True


def remove_noise_tags(soup: BeautifulSoup) -> None:
    """就地删除纯噪声标签（script/style/noscript/iframe/embed），保留语义容器（nav/footer/header/aside）。"""
    for tag in soup(_CONTENT_FREE_TAGS):
        tag.decompose()


def remove_non_content_tags(soup: BeautifulSoup) -> None:
    """从 soup 中就地删除非内容标签。保留作为向后兼容工具函数。"""
    for tag in soup(_CONTENT_FREE_TAGS | _SEMANTIC_CONTAINER_TAGS):
        tag.decompose()


def _remove_blank_elements(root) -> None:
    """移除文本内容全由不可见字符组成的元素。零信息损失。

    覆盖 Unicode 两类不可见字符：
    - 空白类 (Zs 等): str.isspace() → &nbsp;、全角空格、空格族
    - 格式类 (Cf): ZWSP、ZWNJ、ZWJ、BOM、软连字符等
    """
    for el in root.find_all():
        text = el.get_text()
        if not text:
            continue
        if all(ch.isspace() or unicodedata.category(ch) == "Cf" for ch in text):
            el.decompose()


def _is_download_candidate(url: str) -> bool:
    """按 URL 路径扩展名判断是否为非 HTML 下载资源。"""
    path = urlparse(url).path
    if "." in path:
        ext = path.rsplit(".", 1)[-1].lower()
        return ext in DOWNLOAD_EXTENSIONS
    return False


def extract_text_from_soup(
    soup: BeautifulSoup,
    cfg: ContentConfig = DEFAULT_CONFIG,  # type: ignore[assignment]
) -> Tuple[str, bool, int]:
    """从 soup 提取正文文本。（N16 解耦）

    Returns: (text, text_truncated, original_text_len)
    """
    main_content = soup.find("main") or soup.find("article") or soup
    _remove_blank_elements(main_content)
    raw_text = main_content.get_text("\n")
    clean_lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    text = "\n".join(clean_lines)
    text_truncated = len(text) > cfg.max_text_length
    original_text_len = len(text)
    if text_truncated:
        text = text[: cfg.max_text_length] + "\n…[已截断]"

    return text, text_truncated, original_text_len


def extract_links_from_soup(
    soup: BeautifulSoup,
    base_url: str,
    allowed_domains: Optional[Set[str]],
    same_domain_only: bool,
    allow_follow: bool,
    cfg: ContentConfig = DEFAULT_CONFIG,  # type: ignore[assignment]
) -> Tuple[List[str], Dict[str, int]]:
    """从 soup 提取链接。（N16 解耦）

    Returns: (unique_links, stats_dict)
    """
    stats = {
        "nofollow_skipped": 0,
        "cross_domain_skipped": 0,
        "invalid_url_skipped": 0,
        "download_candidate_skipped": 0,
        "same_page_dupes": 0,
    }
    links: List[str] = []

    if not allow_follow:
        return [], stats

    for a in soup.find_all("a", href=True):
        href = cast("str", a["href"]).strip()
        if any(href.startswith(pfx) for pfx in _SKIP_HREF_PREFIXES):
            stats["invalid_url_skipped"] += 1
            continue
        if any(p.name in _SEMANTIC_CONTAINER_TAGS for p in a.parents):
            continue
        rel_attr: "str | list[str] | list[Any]" = a.get("rel") or []
        rel = " ".join(rel_attr).lower() if isinstance(rel_attr, list) else str(rel_attr).lower()
        if not cfg.follow_nofollow and "nofollow" in rel:
            stats["nofollow_skipped"] += 1
            continue
        full = urljoin(base_url, href)
        if not is_valid_http_url(full):
            stats["invalid_url_skipped"] += 1
            continue
        if _is_download_candidate(full):
            stats["download_candidate_skipped"] += 1
            continue
        if same_domain_only:
            domain = parse_domain(full)
            if allowed_domains and domain not in allowed_domains:
                stats["cross_domain_skipped"] += 1
                continue
        links.append(normalize_url(full, cfg))  # type: ignore[arg-type]

    unique_links = list(dict.fromkeys(links))
    stats["same_page_dupes"] = len(links) - len(unique_links)
    return unique_links, stats


def extract_title(soup: BeautifulSoup) -> str:
    """从页面提取标题：<title> → <h1> → og:title fallback。"""
    tag = soup.find("title")
    if tag:
        t = tag.get_text(strip=True)
        if t:
            return t
    h1 = soup.find("h1")
    if h1:
        t = h1.get_text(strip=True)
        if t:
            return t
    og = soup.find("meta", attrs={"property": "og:title"})
    if og:
        content = cast("str", og.get("content", "")).strip()
        if content:
            return content
    return ""


_JSON_LD_RE = re.compile(
    r'<script[^>]*type\s*=\s*["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


def extract_schema_org(html: str) -> Optional[Dict[str, Any]]:
    """从 HTML 中提取第一段 JSON-LD 结构化数据。"""
    m = _JSON_LD_RE.search(html)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


def compute_robust_hash(text: str, cfg: ContentConfig) -> str:
    if not text:
        return md5(b"").hexdigest()
    sample_size = cfg.content_hash_sample_size
    if len(text) <= sample_size:
        normalized = re.sub(r"\s+", " ", text).strip()
        return md5(normalized.encode("utf-8", errors="ignore")).hexdigest()
    part_size = max(sample_size // 3, 1)
    head = text[:part_size]
    mid_start = len(text) // 2
    mid = text[mid_start : mid_start + part_size]
    tail = text[-part_size:]
    combined = head + mid + tail
    normalized = re.sub(r"\s+", " ", combined).strip()
    return md5(normalized.encode("utf-8", errors="ignore")).hexdigest()

from __future__ import annotations

import re
from typing import Protocol as _Protocol
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import publicsuffixlist

from astrocrawl.config import DEFAULT_CONFIG


class UrlFilterConfig(_Protocol):
    """normalize_url 需要的配置字段（ISP 窄接口）。"""

    tracking_params: frozenset


_REDACT_PROXY_RE = re.compile(r"(://[^:@/]+:)([^@/]+)(@)", re.ASCII)
# 改进的敏感参数正则，覆盖常见密钥名称
_SENSITIVE_PARAM_RE = re.compile(
    r"([?&])(password|passwd|token|auth_token|api_key|apikey|key|authorization|"
    r"secret|client_secret|private_key|signature|sig|csrf_token|sessionid|sid|"
    r"access_token|refresh_token|jwt|bearer)=[^&]*",
    re.IGNORECASE,
)


def redact_proxy_url(url: str) -> str:
    return _REDACT_PROXY_RE.sub(r"\1***\3", url)


def redact_sensitive_params(url: str) -> str:
    return _SENSITIVE_PARAM_RE.sub(r"\1\2=***", url)


def safe_log_url(url: str) -> str:
    return redact_sensitive_params(redact_proxy_url(url))


def normalize_url(url: str, cfg: UrlFilterConfig = DEFAULT_CONFIG) -> str:  # type: ignore[assignment]
    """规范化URL：小写化 scheme/host、剥离默认端口、IDN 编码、移除追踪参数、剥离 fragment/params、路径去尾斜杠"""
    p = urlparse(url)
    scheme = p.scheme.lower()

    host = p.hostname or ""
    try:
        host = host.encode("idna").decode("ascii")
    except (UnicodeError, LookupError):
        pass
    if ":" in host:
        host = f"[{host}]"

    if p.username:
        userinfo = p.username.lower()
        if p.password:
            userinfo += f":{p.password.lower()}"
        userinfo += "@"
    else:
        userinfo = ""

    if p.port is not None and not ((scheme == "http" and p.port == 80) or (scheme == "https" and p.port == 443)):
        netloc = f"{userinfo}{host}:{p.port}"
    else:
        netloc = f"{userinfo}{host}"

    if p.query:
        qd = parse_qs(p.query, keep_blank_values=False)
        filtered = {k: v for k, v in qd.items() if k.lower() not in cfg.tracking_params}
        query = urlencode(filtered, doseq=True)
    else:
        query = ""
    path = p.path.rstrip("/") or "/"
    return urlunparse((scheme, netloc, path, "", query, ""))


_psl = publicsuffixlist.PublicSuffixList()


def _get_registrable_domain(hostname: str) -> str:
    """PSL-based registrable domain extraction. Falls back to hostname on error."""
    try:
        return _psl.privatesuffix(hostname) or hostname
    except Exception:
        return hostname


def strip_www(domain: str) -> str:
    """移除 www 前缀（仅当 www 不是注册域名的一部分时）。"""
    if not domain.startswith("www."):
        return domain
    if _get_registrable_domain(domain).startswith("www."):
        return domain
    return domain[4:]


def parse_domain(url: str) -> str:
    netloc = urlparse(url).netloc
    if ":" in netloc and not netloc.startswith("["):
        netloc = netloc.split(":", 1)[0]
    return strip_www(netloc)


# RFC 3986 §2.4: 不在 unreserved / reserved / pct-encoded 集合中的字符
# 在 HTML href 解析上下文中出现这些字符意味着 parser 错误或页面垃圾
_RFC3986_ILLEGAL_RE = re.compile(r'[<>"{}|\\^`\x00-\x1f\x7f]')


def is_valid_http_url(url: str) -> bool:
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.netloc:
        return False
    if _RFC3986_ILLEGAL_RE.search(p.path):
        return False
    if p.query and _RFC3986_ILLEGAL_RE.search(p.query):
        return False
    if " " in p.path or (p.query and " " in p.query):
        return False
    return True

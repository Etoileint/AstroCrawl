"""补充测试: utils/html.py + utils/url.py — 首轮覆盖中遗漏的函数。

html.py: extract_schema_org, extract_title, check_meta_robots (direct)
url.py: redact_proxy_url, redact_sensitive_params, strip_www
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from astrocrawl.utils.html import check_meta_robots, extract_links_from_soup, extract_schema_org, extract_title
from astrocrawl.utils.url import redact_proxy_url, redact_sensitive_params, strip_www

# ═══════════════════════════════════════════════════════════════════════
# extract_title
# ═══════════════════════════════════════════════════════════════════════


class TestExtractTitle:
    def test_title_tag(self):
        soup = BeautifulSoup("<html><head><title>Page Title</title></head></html>", "lxml-xml")
        assert extract_title(soup) == "Page Title"

    def test_title_tag_empty_falls_back_to_h1(self):
        soup = BeautifulSoup(
            "<html><head><title>  </title></head><body><h1>Main Heading</h1></body></html>", "html.parser"
        )
        assert extract_title(soup) == "Main Heading"

    def test_no_title_uses_h1(self):
        soup = BeautifulSoup("<html><body><h1>Only H1</h1></body></html>", "html.parser")
        assert extract_title(soup) == "Only H1"

    def test_no_title_or_h1_uses_og_title(self):
        soup = BeautifulSoup(
            '<html><head><meta property="og:title" content="OG Title"/></head><body></body></html>',
            "html.parser",
        )
        assert extract_title(soup) == "OG Title"

    def test_no_tags_returns_empty(self):
        soup = BeautifulSoup("<html><body><p>text</p></body></html>", "html.parser")
        assert extract_title(soup) == ""

    def test_h1_empty_falls_back_to_og(self):
        soup = BeautifulSoup(
            '<html><body><h1>  </h1></body><head><meta property="og:title" content="Fallback"/></head></html>',
            "html.parser",
        )
        assert extract_title(soup) == "Fallback"


# ═══════════════════════════════════════════════════════════════════════
# extract_schema_org
# ═══════════════════════════════════════════════════════════════════════


class TestExtractSchemaOrg:
    def test_extracts_json_ld(self):
        html = '<script type="application/ld+json">{"@type": "Product"}</script>'
        result = extract_schema_org(html)
        assert result == {"@type": "Product"}

    def test_extracts_with_extra_attrs(self):
        html = '<script type="application/ld+json" id="schema">{"@type": "Article"}</script>'
        result = extract_schema_org(html)
        assert result == {"@type": "Article"}

    def test_no_json_ld_returns_none(self):
        assert extract_schema_org("<html><body>hi</body></html>") is None

    def test_invalid_json_returns_none(self):
        html = '<script type="application/ld+json">{invalid}</script>'
        assert extract_schema_org(html) is None

    def test_array_json_ld_returns_none(self):
        html = '<script type="application/ld+json">[{"@type": "Item"}]</script>'
        assert extract_schema_org(html) is None

    def test_first_json_ld_only(self):
        html = (
            '<script type="application/ld+json">{"first": true}</script>'
            '<script type="application/ld+json">{"second": true}</script>'
        )
        result = extract_schema_org(html)
        assert result == {"first": True}

    def test_single_quotes_in_type(self):
        html = '<script type=\'application/ld+json\'>{"key": "val"}</script>'
        result = extract_schema_org(html)
        assert result == {"key": "val"}

    def test_empty_html_returns_none(self):
        assert extract_schema_org("") is None

    def test_nested_schema_org(self):
        html = '<script type="application/ld+json">{"@type": "WebPage", "author": {"@type": "Person"}}</script>'
        result = extract_schema_org(html)
        assert result["@type"] == "WebPage"
        assert result["author"]["@type"] == "Person"


# ═══════════════════════════════════════════════════════════════════════
# check_meta_robots (direct)
# ═══════════════════════════════════════════════════════════════════════


class TestCheckMetaRobots:
    """check_meta_robots 返回 (allow_index, allow_follow)。"""

    def test_no_meta_tags_defaults_allow_all(self):
        soup = BeautifulSoup("<html><body>hi</body></html>", "html.parser")
        allow_index, allow_follow = check_meta_robots(soup, respect_meta_robots=True)
        assert allow_index
        assert allow_follow

    def test_respect_false_always_allow_all(self):
        soup = BeautifulSoup('<html><meta name="robots" content="noindex, nofollow"/></html>', "html.parser")
        allow_index, allow_follow = check_meta_robots(soup, respect_meta_robots=False)
        assert allow_index
        assert allow_follow

    def test_noindex_disallows_index_but_allows_follow(self):
        soup = BeautifulSoup('<html><meta name="robots" content="noindex"/></html>', "html.parser")
        allow_index, allow_follow = check_meta_robots(soup, respect_meta_robots=True)
        assert not allow_index
        assert allow_follow

    def test_nofollow_allows_index_but_disallows_follow(self):
        soup = BeautifulSoup('<html><meta name="robots" content="nofollow"/></html>', "html.parser")
        allow_index, allow_follow = check_meta_robots(soup, respect_meta_robots=True)
        assert allow_index
        assert not allow_follow

    def test_both_disallowed(self):
        soup = BeautifulSoup('<html><meta name="robots" content="noindex, nofollow"/></html>', "html.parser")
        allow_index, allow_follow = check_meta_robots(soup, respect_meta_robots=True)
        assert not allow_index
        assert not allow_follow

    def test_case_insensitive(self):
        soup = BeautifulSoup('<html><meta name="robots" content="NOINDEX, NOFOLLOW"/></html>', "html.parser")
        allow_index, allow_follow = check_meta_robots(soup, respect_meta_robots=True)
        assert not allow_index
        assert not allow_follow


# ═══════════════════════════════════════════════════════════════════════
# redact_proxy_url
# ═══════════════════════════════════════════════════════════════════════


class TestRedactProxyUrl:
    def test_redacts_password(self):
        result = redact_proxy_url("http://user:secret123@proxy.com:8080")
        assert "secret123" not in result
        assert "***" in result

    def test_no_auth_passthrough(self):
        result = redact_proxy_url("http://proxy.com:8080")
        assert result == "http://proxy.com:8080"

    def test_socks5_redacted(self):
        result = redact_proxy_url("socks5://admin:mypass@proxy:1080")
        assert "mypass" not in result
        assert "***" in result


# ═══════════════════════════════════════════════════════════════════════
# redact_sensitive_params
# ═══════════════════════════════════════════════════════════════════════


class TestRedactSensitiveParams:
    def test_redacts_api_key(self):
        result = redact_sensitive_params("https://api.com/endpoint?api_key=abc123&other=val")
        assert "abc123" not in result

    def test_redacts_token(self):
        result = redact_sensitive_params("https://api.com?token=secret&user=john")
        assert "secret" not in result

    def test_redacts_password(self):
        result = redact_sensitive_params("https://a.com?password=hunter2")
        assert "hunter2" not in result

    def test_no_sensitive_params_passthrough(self):
        result = redact_sensitive_params("https://a.com?q=search&page=1")
        assert result == "https://a.com?q=search&page=1"

    def test_multiple_params_first_sensitive(self):
        result = redact_sensitive_params("https://a.com?api_key=abc&page=1&token=xyz")
        assert "abc" not in result
        assert "xyz" not in result
        assert "page=1" in result


# ═══════════════════════════════════════════════════════════════════════
# strip_www
# ═══════════════════════════════════════════════════════════════════════


class TestStripWww:
    def test_strips_www_prefix(self):
        assert strip_www("www.example.com") == "example.com"

    def test_no_www_passthrough(self):
        assert strip_www("example.com") == "example.com"

    def test_subdomain_preserved(self):
        assert strip_www("blog.example.com") == "blog.example.com"

    def test_www_in_middle_preserved(self):
        assert strip_www("mywww.example.com") == "mywww.example.com"


# ═══════════════════════════════════════════════════════════════════════
# normalize_url — IDN 编码失败路径
# ═══════════════════════════════════════════════════════════════════════


class TestStripWwwPSLException:
    """strip_www 在 PSL 查询异常时回退到原始 hostname。"""

    def test_psl_exception_falls_back_to_hostname(self, monkeypatch):
        """_psl.privatesuffix 抛异常 → _get_registrable_domain 返回 hostname 本身。"""
        import astrocrawl.utils.url as _url_mod

        def _failing_privatesuffix(hostname: str) -> str:
            raise RuntimeError("PSL database error")

        monkeypatch.setattr(_url_mod._psl, "privatesuffix", _failing_privatesuffix)
        # www.example.com — PSL 抛异常后，_get_registrable_domain 返回 "www.example.com"
        # 其 registrable domain 不是 "www.example.com"，所以 www 应被剥离
        # 但 _get_registrable_domain 的异常回退返回 hostname 本身 → 不会以 www. 开头
        # 实际上异常回退返回 "www.example.com" 原始值，不经过 PSL 处理
        # strip_www: domain="www.example.com" → _get_registrable_domain 返回 "www.example.com"
        # → "www.example.com".startswith("www.") 为 True → 不剥离，算是安全的保守行为
        result = strip_www("www.example.com")
        # 异常时 PSL 返回 hostname，不剥离 (保守，安全)
        assert result == "www.example.com"


# ═══════════════════════════════════════════════════════════════════════
# extract_links_from_soup — is_valid_http_url 拒绝路径
# ═══════════════════════════════════════════════════════════════════════


class TestExtractLinksNonHttpScheme:
    """extract_links_from_soup 中通过前缀检查但被 is_valid_http_url 拒绝的路径。"""

    def test_ftp_scheme_rejected(self):
        """ftp:// 通过前缀检查（非 javascript/mailto/tel/data），但 is_valid_http_url 拒绝。"""
        from bs4 import BeautifulSoup

        from astrocrawl.config import DEFAULT_CONFIG

        html = '<a href="ftp://example.com/file">FTP</a><a href="/page">HTML</a>'
        soup = BeautifulSoup(html, "html.parser")
        links, stats = extract_links_from_soup(soup, "https://example.com", None, False, True, DEFAULT_CONFIG)
        assert stats["invalid_url_skipped"] == 1
        assert len(links) == 1
        assert "ftp" not in links[0]

    def test_rfc3986_illegal_char_after_urljoin_rejected(self):
        """urljoin 后路径含 RFC 3986 非法字符 → is_valid_http_url 拒绝。"""
        from bs4 import BeautifulSoup

        from astrocrawl.config import DEFAULT_CONFIG

        # href 本身不含非法字符，urljoin 后路径含 <script> 标签残片
        html = '<a href="path<script>alert(1)</script>">Bad</a><a href="/ok">OK</a>'
        soup = BeautifulSoup(html, "html.parser")
        links, stats = extract_links_from_soup(soup, "https://example.com", None, False, True, DEFAULT_CONFIG)
        assert stats["invalid_url_skipped"] == 1
        assert len(links) == 1
        assert links[0] == "https://example.com/ok"


class TestExtractLinksAllowFollowFalse:
    """extract_links_from_soup — allow_follow=False 快速返回路径。"""

    def test_allow_follow_false_returns_empty(self):
        """allow_follow=False → 不提取任何链接，立即返回空列表和零统计。"""
        from bs4 import BeautifulSoup

        from astrocrawl.config import DEFAULT_CONFIG

        html = '<a href="/page1">P1</a><a href="/page2">P2</a>'
        soup = BeautifulSoup(html, "html.parser")
        links, stats = extract_links_from_soup(soup, "https://example.com", None, False, False, DEFAULT_CONFIG)
        assert links == []
        assert stats["nofollow_skipped"] == 0
        assert stats["same_page_dupes"] == 0


class TestExtractLinksSameDomainOnly:
    """extract_links_from_soup — same_domain_only + allowed_domains 交互。"""

    def test_same_domain_without_allowed_domains_allows_all(self):
        """same_domain_only=True 但 allowed_domains=None → 所有链接通过（无白名单）。"""
        from bs4 import BeautifulSoup

        from astrocrawl.config import DEFAULT_CONFIG

        html = '<a href="https://other.com/page">Other</a><a href="/local">Local</a>'
        soup = BeautifulSoup(html, "html.parser")
        links, stats = extract_links_from_soup(soup, "https://example.com", None, True, True, DEFAULT_CONFIG)
        assert len(links) == 2
        assert stats["cross_domain_skipped"] == 0


# ═══════════════════════════════════════════════════════════════════════
# check_meta_robots — 多个 meta robots 标签
# ═══════════════════════════════════════════════════════════════════════


class TestCheckMetaRobotsMultiple:
    """check_meta_robots — 多个 <meta name=\"robots\"> 标签的语义。"""

    def test_first_meta_wins_on_multiple_tags(self):
        """BeautifulSoup.find() 返回第一个匹配 → 只有第一个 meta robots 生效。"""
        from bs4 import BeautifulSoup

        html = '<meta name="robots" content="noindex"><meta name="robots" content="nofollow">'
        soup = BeautifulSoup(html, "html.parser")
        ai, af = check_meta_robots(soup, respect_meta_robots=True)
        # 第一个标签含 noindex → index 禁止
        assert ai is False
        # 第二个标签的 nofollow 被忽略 → follow 允许
        assert af is True

    def test_multiple_first_allows_second_forbids(self):
        """第一个标签 allow all，第二个 forbid → first wins → allow all。"""
        from bs4 import BeautifulSoup

        html = '<meta name="robots" content="all"><meta name="robots" content="noindex, nofollow">'
        soup = BeautifulSoup(html, "html.parser")
        ai, af = check_meta_robots(soup, respect_meta_robots=True)
        assert ai is True
        assert af is True

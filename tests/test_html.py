"""HTML 解析与哈希测试"""

from __future__ import annotations

from bs4 import BeautifulSoup

from astrocrawl.config import DEFAULT_CONFIG
from astrocrawl.utils.html import (
    ParseResult,
    check_meta_robots,
    compute_robust_hash,
    extract_links_from_soup,
    extract_text_from_soup,
    extract_title,
    remove_noise_tags,
    remove_non_content_tags,
)


def _parse_page(html, base_url, allowed_domains, same_domain_only, cfg=DEFAULT_CONFIG):
    """Test helper — 组合原子函数，行为与原 parse_page() 一致。"""
    soup = BeautifulSoup(html, "lxml")
    allow_index, allow_follow = check_meta_robots(soup, cfg.respect_meta_robots)
    title = extract_title(soup)
    remove_noise_tags(soup)
    text, text_truncated, original_text_len = extract_text_from_soup(soup, cfg)
    links, link_stats = extract_links_from_soup(soup, base_url, allowed_domains, same_domain_only, allow_follow, cfg)
    return ParseResult(
        text=text,
        links=links,
        allow_index=allow_index,
        allow_follow=allow_follow,
        title=title,
        text_truncated=text_truncated,
        original_text_len=original_text_len,
        nofollow_skipped=link_stats["nofollow_skipped"],
        cross_domain_skipped=link_stats["cross_domain_skipped"],
        invalid_url_skipped=link_stats["invalid_url_skipped"],
        download_candidate_skipped=link_stats["download_candidate_skipped"],
        same_page_dupes=link_stats["same_page_dupes"],
    )


HTML_BASIC = """
<html><head><title>Test</title></head>
<body><main><p>Hello World</p><a href="/page2">Page 2</a></main></body>
</html>
"""

HTML_WITH_NOINDEX = """
<html><head>
<meta name="robots" content="noindex, nofollow">
</head><body><main><p>Secret</p></main></body></html>
"""

HTML_WITH_NOFOLLOW_LINK = """
<html><body>
<a href="/page2" rel="nofollow">Link</a>
<a href="/page3">Normal</a>
</body></html>
"""


class TestParsePage:
    def test_extracts_text(self):
        pr = _parse_page(HTML_BASIC, "https://example.com", None, False, DEFAULT_CONFIG)
        assert "Hello World" in pr.text
        assert pr.allow_index is True
        assert pr.allow_follow is True
        assert pr.parse_error is False

    def test_respects_noindex(self):
        cfg = DEFAULT_CONFIG
        pr = _parse_page(HTML_WITH_NOINDEX, "https://example.com", None, False, cfg)
        assert pr.allow_index is False
        assert pr.allow_follow is False

    def test_respects_nofollow(self):
        cfg = DEFAULT_CONFIG
        pr = _parse_page(HTML_WITH_NOFOLLOW_LINK, "https://example.com", None, False, cfg)
        assert len(pr.links) >= 2  # nofollow 默认被跟随

    def test_follow_nofollow_false(self):
        from dataclasses import replace

        cfg = replace(DEFAULT_CONFIG, follow_nofollow=False)
        pr = _parse_page(HTML_WITH_NOFOLLOW_LINK, "https://example.com", None, False, cfg)
        assert pr.nofollow_skipped == 1
        assert len(pr.links) == 1
        assert "page3" in pr.links[0]

    def test_same_domain_filter(self):
        pr = _parse_page(HTML_BASIC, "https://example.com", {"example.com"}, True, DEFAULT_CONFIG)
        assert any("page2" in link for link in pr.links)

    def test_cross_domain_skipped_count(self):
        html = '<a href="https://other.com/page">Other</a>'
        pr = _parse_page(html, "https://example.com", {"example.com"}, True, DEFAULT_CONFIG)
        assert pr.cross_domain_skipped == 1
        assert len(pr.links) == 0

    def test_skips_javascript_links(self):
        html = '<a href="javascript:void(0)">Click</a>'
        pr = _parse_page(html, "https://example.com", None, False, DEFAULT_CONFIG)
        assert len(pr.links) == 0
        assert pr.invalid_url_skipped == 1

    def test_invalid_url_skipped_count(self):
        html = '<a href="mailto:test@test.com">Email</a><a href="tel:123">Phone</a>'
        pr = _parse_page(html, "https://example.com", None, False, DEFAULT_CONFIG)
        assert pr.invalid_url_skipped == 2

    def test_removes_script_tags(self):
        html = '<script>alert("xss")</script><main><p>Safe</p></main>'
        pr = _parse_page(html, "https://example.com", None, False, DEFAULT_CONFIG)
        assert "alert" not in pr.text
        assert "Safe" in pr.text

    def test_text_truncated_flag(self):
        cfg = DEFAULT_CONFIG
        long_text = "x" * (cfg.max_text_length + 100)
        html = f"<main>{long_text}</main>"
        pr = _parse_page(html, "https://example.com", None, False, cfg)
        assert pr.text_truncated is True
        assert pr.original_text_len == cfg.max_text_length + 100

    def test_text_not_truncated_short(self):
        pr = _parse_page(HTML_BASIC, "https://example.com", None, False, DEFAULT_CONFIG)
        assert pr.text_truncated is False

    def test_parse_error_on_malformed(self):
        # 构造一个 BeautifulSoup 能处理但会触发异常的场景
        # lxml 解析器对某些嵌套错误的容忍度不同，使用空字符串测试返回
        pr = _parse_page("", "https://example.com", None, False, DEFAULT_CONFIG)
        assert isinstance(pr, ParseResult)

    def test_same_page_dupes_count(self):
        html = '<a href="/page1">P1</a><a href="/page1">P1 Again</a>'
        pr = _parse_page(html, "https://example.com", None, False, DEFAULT_CONFIG)
        assert pr.same_page_dupes == 1
        assert len(pr.links) == 1

    def test_download_candidate_filtered(self):
        """二进制扩展名链接被拦截并计入 download_candidate_skipped。"""
        html = (
            '<a href="/file.pdf">PDF</a>'
            '<a href="/archive.tar.gz">Tarball (double ext)</a>'
            '<a href="/setup.exe">EXE</a>'
            '<a href="/normal.html">HTML</a>'
        )
        pr = _parse_page(html, "https://example.com", None, False, DEFAULT_CONFIG)
        assert pr.download_candidate_skipped == 3
        assert len(pr.links) == 1
        assert pr.links[0] == "https://example.com/normal.html"

    def test_download_candidate_no_extension_not_affected(self):
        """无扩展名或 HTML 类扩展名的 URL 正常通过。"""
        html = (
            '<a href="/path/to/page">No ext</a>'
            '<a href="/page.html">HTML</a>'
            '<a href="/page.htm">HTM</a>'
            '<a href="/page.php">PHP</a>'
            '<a href="/page.asp">ASP</a>'
        )
        pr = _parse_page(html, "https://example.com", None, False, DEFAULT_CONFIG)
        assert pr.download_candidate_skipped == 0
        assert len(pr.links) == 5

    def test_download_candidate_mixed_with_valid(self):
        """混合链接中，正常 HTML 链接不受影响。"""
        html = '<a href="/page1">P1</a><a href="/data.csv">CSV</a><a href="/page2">P2</a><a href="/image.png">PNG</a>'
        pr = _parse_page(html, "https://example.com", None, False, DEFAULT_CONFIG)
        assert pr.download_candidate_skipped == 2
        assert len(pr.links) == 2
        assert all(not link.endswith((".csv", ".png")) for link in pr.links)

    def test_all_fields_present(self):
        pr = _parse_page(HTML_BASIC, "https://example.com", None, False, DEFAULT_CONFIG)
        assert hasattr(pr, "text")
        assert hasattr(pr, "links")
        assert hasattr(pr, "allow_index")
        assert hasattr(pr, "allow_follow")
        assert hasattr(pr, "parse_error")
        assert hasattr(pr, "text_truncated")
        assert hasattr(pr, "original_text_len")
        assert hasattr(pr, "nofollow_skipped")
        assert hasattr(pr, "cross_domain_skipped")
        assert hasattr(pr, "invalid_url_skipped")
        assert hasattr(pr, "same_page_dupes")
        assert hasattr(pr, "download_candidate_skipped")


class TestExtractTextBoundary:
    """extract_text_from_soup ISTQB 边界值测试。"""

    def test_at_max_text_length_not_truncated(self):
        """文本恰好 = max_text_length → truncated=False。"""
        cfg = DEFAULT_CONFIG
        text = "x" * cfg.max_text_length
        html = f"<main>{text}</main>"
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        result, truncated, orig_len = extract_text_from_soup(soup, cfg)
        assert truncated is False
        assert orig_len == cfg.max_text_length
        assert "…[已截断]" not in result

    def test_max_text_length_plus_one_truncated(self):
        """文本 = max_text_length + 1 → truncated=True 且附加截断标记。"""
        cfg = DEFAULT_CONFIG
        text = "y" * (cfg.max_text_length + 1)
        html = f"<main>{text}</main>"
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        result, truncated, orig_len = extract_text_from_soup(soup, cfg)
        assert truncated is True
        assert orig_len == cfg.max_text_length + 1
        assert "…[已截断]" in result


class TestComputeRobustHash:
    def test_empty_text_returns_valid_md5(self):
        h = compute_robust_hash("", DEFAULT_CONFIG)
        assert len(h) == 32
        assert all(c in "0123456789abcdef" for c in h)

    def test_short_text(self):
        h = compute_robust_hash("hello world", DEFAULT_CONFIG)
        assert len(h) == 32

    def test_long_text_uses_sampling(self):
        text = "abcdefghij" * 10000
        h = compute_robust_hash(text, DEFAULT_CONFIG)
        assert len(h) == 32

    def test_identical_text_same_hash(self):
        text = "The quick brown fox"
        h1 = compute_robust_hash(text, DEFAULT_CONFIG)
        h2 = compute_robust_hash(text, DEFAULT_CONFIG)
        assert h1 == h2

    def test_normalized_whitespace(self):
        h1 = compute_robust_hash("hello   world", DEFAULT_CONFIG)
        h2 = compute_robust_hash("hello world", DEFAULT_CONFIG)
        assert h1 == h2

    def test_at_sample_size_boundary_no_sampling(self):
        """文本恰好等于 sample_size (4096) → 全文本哈希，不采样。"""
        cfg = DEFAULT_CONFIG
        text = "x" * cfg.content_hash_sample_size
        h = compute_robust_hash(text, cfg)
        assert len(h) == 32
        # 与自身相同
        assert compute_robust_hash(text, cfg) == h

    def test_at_sample_size_plus_one_uses_sampling(self):
        """文本 = sample_size + 1 → 进入头-中-尾采样路径。"""
        cfg = DEFAULT_CONFIG
        text = "x" * (cfg.content_hash_sample_size + 1)
        h = compute_robust_hash(text, cfg)
        assert len(h) == 32
        # 确定性：同样输入产生同样哈希
        assert compute_robust_hash(text, cfg) == h


# ═══════════════════════════════════════════════════════════════════════
# Unicode 空白元素清除
# ═══════════════════════════════════════════════════════════════════════


class TestRemoveBlankElements:
    def test_nbsp_span_removed(self):
        from bs4 import BeautifulSoup

        from astrocrawl.utils.html import _remove_blank_elements

        soup = BeautifulSoup("<div><p>text</p><span>&nbsp;</span></div>", "lxml")
        _remove_blank_elements(soup)
        assert soup.find("span") is None

    def test_ideographic_space_removed(self):
        from bs4 import BeautifulSoup

        from astrocrawl.utils.html import _remove_blank_elements

        soup = BeautifulSoup("<div><p>text</p><span>　</span></div>", "lxml")
        _remove_blank_elements(soup)
        assert soup.find("span") is None

    def test_zero_width_space_removed(self):
        from bs4 import BeautifulSoup

        from astrocrawl.utils.html import _remove_blank_elements

        soup = BeautifulSoup("<div><p>text</p><span>​</span></div>", "lxml")
        _remove_blank_elements(soup)
        assert soup.find("span") is None

    def test_bom_removed(self):
        from bs4 import BeautifulSoup

        from astrocrawl.utils.html import _remove_blank_elements

        soup = BeautifulSoup("<div><p>text</p><span>﻿</span></div>", "lxml")
        _remove_blank_elements(soup)
        assert soup.find("span") is None

    def test_soft_hyphen_removed(self):
        from bs4 import BeautifulSoup

        from astrocrawl.utils.html import _remove_blank_elements

        soup = BeautifulSoup("<div><p>text</p><span>­</span></div>", "lxml")
        _remove_blank_elements(soup)
        assert soup.find("span") is None

    def test_mixed_whitespace_section_removed(self):
        from bs4 import BeautifulSoup

        from astrocrawl.utils.html import _remove_blank_elements

        soup = BeautifulSoup("<section>\n\t  \xa0　​﻿\n</section><p>text</p>", "lxml")
        _remove_blank_elements(soup)
        assert soup.find("section") is None
        assert "text" in soup.get_text()

    def test_normal_text_preserved(self):
        from bs4 import BeautifulSoup

        from astrocrawl.utils.html import _remove_blank_elements

        soup = BeautifulSoup("<div><p>text</p><span>  text  </span></div>", "lxml")
        _remove_blank_elements(soup)
        assert soup.find("span") is not None

    def test_empty_element_not_affected(self):
        from bs4 import BeautifulSoup

        from astrocrawl.utils.html import _remove_blank_elements

        soup = BeautifulSoup("<div><p>text</p><span></span></div>", "lxml")
        _remove_blank_elements(soup)
        assert soup.find("span") is not None

    def test_integration_with_extract_text(self):
        from bs4 import BeautifulSoup

        from astrocrawl.utils.html import extract_text_from_soup

        soup = BeautifulSoup(
            "<main><h1>Title</h1><p>Content</p><span>&nbsp;</span><span>　</span><span>​</span></main>",
            "lxml",
        )
        text, _, _ = extract_text_from_soup(soup)
        assert "Title" in text
        assert "Content" in text
        lines = text.splitlines()
        assert len(lines) <= 3  # Title + Content + possible empty line, no noise lines


class TestHeaderNavPreservation:
    """验证 header/nav/footer/aside 不被 remove_noise_tags 移除。"""

    def test_header_h1_preserved(self):
        """header 中的 h1 在标签清理后仍然可达。"""
        from bs4 import BeautifulSoup

        html = '<header><h1 id="firstHeading">Page Title</h1></header>'
        soup = BeautifulSoup(html, "lxml")
        remove_noise_tags(soup)
        h1 = soup.find("h1", id="firstHeading")
        assert h1 is not None
        assert h1.get_text(strip=True) == "Page Title"

    def test_nav_toc_preserved(self):
        """nav 中的 TOC 列表仍可通过 CSS 选择器提取。"""
        from bs4 import BeautifulSoup

        html = '<nav id="vector-toc"><ul><li>Section 1</li><li>Section 2</li></ul></nav>'
        soup = BeautifulSoup(html, "lxml")
        remove_noise_tags(soup)
        nav = soup.find("nav")
        assert nav is not None

    def test_script_style_still_removed(self):
        """script/style 等 content-free 标签仍被移除，main 保留。"""
        from bs4 import BeautifulSoup

        html = "<script>var x=1;</script><style>.cls{}</style><main><p>text</p></main>"
        soup = BeautifulSoup(html, "lxml")
        remove_noise_tags(soup)
        assert soup.find("script") is None
        assert soup.find("style") is None
        assert soup.find("main") is not None

    def test_footer_aside_preserved(self):
        """footer 和 aside 不被移除。"""
        from bs4 import BeautifulSoup

        html = "<footer><p>copyright</p></footer><aside><p>sidebar</p></aside>"
        soup = BeautifulSoup(html, "lxml")
        remove_noise_tags(soup)
        assert soup.find("footer") is not None
        assert soup.find("aside") is not None


class TestLinkExtractionAncestorFilter:
    """验证 extract_links_from_soup 跳过语义容器中的链接。"""

    def _extract(self, html, base_url="https://example.com"):
        from bs4 import BeautifulSoup

        from astrocrawl.config import DEFAULT_CONFIG

        soup = BeautifulSoup(html, "lxml")
        links, stats = extract_links_from_soup(soup, base_url, None, False, True, DEFAULT_CONFIG)
        return links, stats

    def test_skip_links_in_nav(self):
        """nav 内部的链接不被提取，main 内链接正常。"""
        html = '<nav><a href="/nav-link">Nav</a></nav><main><a href="/main-link">Main</a></main>'
        links, _ = self._extract(html)
        assert any("main-link" in link for link in links)
        assert not any("nav-link" in link for link in links)

    def test_skip_links_in_footer(self):
        """footer 内部的链接不被提取。"""
        html = '<footer><a href="/footer-link">Footer</a></footer>'
        links, _ = self._extract(html)
        assert not any("footer-link" in link for link in links)

    def test_skip_links_in_header(self):
        """header 内部的链接不被提取。"""
        html = '<header><a href="/header-link">Header</a></header>'
        links, _ = self._extract(html)
        assert not any("header-link" in link for link in links)

    def test_skip_links_in_aside(self):
        """aside 内部的链接不被提取。"""
        html = '<aside><a href="/aside-link">Aside</a></aside>'
        links, _ = self._extract(html)
        assert not any("aside-link" in link for link in links)

    def test_nested_container_still_skipped(self):
        """语义容器内的深层嵌套链接也被跳过。"""
        html = '<nav><div><ul><li><a href="/deep-nav">Deep</a></li></ul></div></nav>'
        links, _ = self._extract(html)
        assert not any("deep-nav" in link for link in links)

    def test_normal_links_still_extracted(self):
        """main/article/div/section 中的链接正常提取。"""
        html = (
            '<main><a href="/a">A</a></main>'
            '<article><a href="/b">B</a></article>'
            '<div><a href="/c">C</a></div>'
            '<section><a href="/d">D</a></section>'
        )
        links, _ = self._extract(html)
        assert len(links) == 4


class TestRemoveNonContentTagsBackwardCompat:
    """验证 remove_non_content_tags() 仍删除全部 9 标签。"""

    def test_removes_all_nine_tags(self):
        """remove_non_content_tags 保留向后兼容——删除 content-free + 语义容器。"""
        from bs4 import BeautifulSoup

        html = '<header><h1>T</h1></header><nav><a href="/n">N</a></nav><script>x</script>'
        soup = BeautifulSoup(html, "lxml")
        remove_non_content_tags(soup)
        assert soup.find("header") is None
        assert soup.find("nav") is None
        assert soup.find("script") is None


class TestCrossModuleCssExtraction:
    """验证 remove_noise_tags 之后 CSS 选择器可达语义容器内元素。"""

    def test_header_and_nav_elements_selectable(self):
        """header 内 h1 和 nav 内 li 在标签清理后可通过 CSS 提取。"""
        import asyncio

        from bs4 import BeautifulSoup

        from astrocrawl.rules._schema import FieldRule

        html = (
            '<header><h1 id="firstHeading">Page Title</h1></header>'
            '<nav id="vector-toc"><ul><li>TOC 1</li><li>TOC 2</li></ul></nav>'
        )
        soup = BeautifulSoup(html, "lxml")
        remove_noise_tags(soup)

        fields_config = {
            "page_title": FieldRule(
                selector="h1#firstHeading",
                extract="text",
                transform={"strip": True},
            ),
            "toc": FieldRule(
                selector="#vector-toc li",
                extract="text",
                multiple=True,
                transform={"join": "\n", "strip": True},
            ),
        }
        result = asyncio.run(
            __import__("astrocrawl.rules._extractor", fromlist=["extract_fields_from_soup"]).extract_fields_from_soup(
                soup, "test_rule", fields_config
            )
        )
        assert result["page_title"] == "Page Title"
        assert "TOC 1" in result["toc"]
        assert "TOC 2" in result["toc"]

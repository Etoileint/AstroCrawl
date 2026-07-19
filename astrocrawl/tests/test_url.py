"""URL 工具函数测试"""

from __future__ import annotations

from astrocrawl.config import DEFAULT_CONFIG
from astrocrawl.utils.url import (
    is_valid_http_url,
    normalize_url,
    parse_domain,
    redact_proxy_url,
    redact_sensitive_params,
    safe_log_url,
    strip_www,
)


class TestNormalizeURL:
    def test_strips_tracking_params(self):
        url = "https://example.com/page?utm_source=google&id=123"
        cfg = DEFAULT_CONFIG
        result = normalize_url(url, cfg)
        assert "utm_source" not in result
        assert "id=123" in result

    def test_strips_default_ports(self):
        assert normalize_url("http://example.com:80/path") == "http://example.com/path"
        assert normalize_url("https://example.com:443/path") == "https://example.com/path"

    def test_lowercase_scheme_host(self):
        result = normalize_url("HTTP://EXAMPLE.COM/Path")
        assert result == "http://example.com/Path"

    def test_trailing_slash_normalization(self):
        result = normalize_url("https://example.com/path/")
        assert result == "https://example.com/path"

    def test_root_path_preserved(self):
        result = normalize_url("https://example.com/")
        assert result == "https://example.com/"

    def test_idn_encoding(self):
        result = normalize_url("http://münchen.example.com/path")
        assert result == "http://xn--mnchen-3ya.example.com/path"

    def test_idn_with_custom_port(self):
        result = normalize_url("http://münchen.example.com:8080/path")
        assert result == "http://xn--mnchen-3ya.example.com:8080/path"

    def test_ipv6_preserves_brackets(self):
        result = normalize_url("http://[::1]:8080/path")
        assert result == "http://[::1]:8080/path"

    def test_ipv6_default_port_stripped(self):
        result = normalize_url("http://[::1]:80/path")
        assert result == "http://[::1]/path"

    def test_userinfo_preserved_lowercased(self):
        result = normalize_url("http://USER:PASS@example.com/path")
        assert result == "http://user:pass@example.com/path"

    def test_userinfo_with_idn(self):
        result = normalize_url("http://user:pass@münchen.example.com/path")
        assert result == "http://user:pass@xn--mnchen-3ya.example.com/path"

    def test_custom_tracking_params_isp(self):
        """ISP UrlFilterConfig: 自定义 tracking_params 只过滤指定参数。"""

        class CustomConfig:
            tracking_params: frozenset = frozenset({"custom_track", "session_id"})

        result = normalize_url(
            "https://example.com/page?custom_track=remove&keep=me&utm_source=keep_too", CustomConfig()
        )
        # custom_track 被自定义配置过滤
        assert "custom_track" not in result
        # utm_source 不在自定义配置中 → 保留
        assert "utm_source=keep_too" in result
        # 非 tracking param 保留
        assert "keep=me" in result

    def test_all_transformations_compose(self):
        """所有规范化步骤组合：大小写→端口剥离→IDN→tracking→fragment→尾斜杠。"""
        cfg = DEFAULT_CONFIG
        result = normalize_url("HTTP://USER:PASS@MÜNCHEN.DE:80/PATH/?utm_source=ads&keep=val#section", cfg)
        # scheme+host 小写，IDN 编码，默认端口剥离，tracking 移除，fragment 移除，尾斜杠剥离
        assert result == "http://user:pass@xn--mnchen-3ya.de/PATH?keep=val"


class TestParseDomain:
    def test_strips_www(self):
        assert parse_domain("https://www.example.com/page") == "example.com"

    def test_strips_port(self):
        assert parse_domain("https://example.com:8080/page") == "example.com"

    def test_preserves_subdomain(self):
        assert parse_domain("https://blog.example.com/page") == "blog.example.com"

    def test_ipv6_with_port(self):
        assert parse_domain("http://[::1]:8080/path") == "[::1]:8080"

    def test_www_com_preserved(self):
        """www.com 是注册域名本身，不应剥离 www。"""
        assert parse_domain("https://www.com/page") == "www.com"

    def test_www_co_uk_preserved(self):
        """www.co.uk 是注册域名本身，不应剥离 www。"""
        assert parse_domain("https://www.co.uk/page") == "www.co.uk"

    def test_www_blog_example_strips_www(self):
        """www.blog.example.com — www 是子域名前缀，应剥离。"""
        assert parse_domain("https://www.blog.example.com/page") == "blog.example.com"


class TestStripWww:
    def test_strips_standard_www(self):
        assert strip_www("www.example.com") == "example.com"

    def test_preserves_registrable_www(self):
        assert strip_www("www.com") == "www.com"

    def test_preserves_no_www_prefix(self):
        assert strip_www("example.com") == "example.com"

    def test_strips_www_from_subdomain(self):
        assert strip_www("www.blog.example.com") == "blog.example.com"

    def test_preserves_www_co_uk(self):
        assert strip_www("www.co.uk") == "www.co.uk"

    def test_preserves_www_github_io(self):
        assert strip_www("www.github.io") == "www.github.io"


class TestIsValidHTTPURL:
    def test_valid_urls(self):
        assert is_valid_http_url("https://example.com")
        assert is_valid_http_url("http://example.com/path?q=1")

    def test_invalid_urls(self):
        assert not is_valid_http_url("ftp://example.com")
        assert not is_valid_http_url("not-a-url")
        assert not is_valid_http_url("")

    def test_truncated_url(self):
        """空域名产生 URL — sitemap 垃圾条目或截断链接。"""
        assert not is_valid_http_url("ht")
        assert not is_valid_http_url("http://")
        assert not is_valid_http_url("https://")

    def test_rfc3986_illegal_chars_rejected(self):
        """RFC 3986 非法字符 (< > \" { } | \\ ^ ` 空格等) 被拒绝。"""
        # HTML 片段混入 URL
        assert not is_valid_http_url("https://example.com/path/'>text</a>")
        assert not is_valid_http_url("https://example.com/path/<script>")
        # 裸空格
        assert not is_valid_http_url("https://example.com/path with spaces")
        # 其他非法字符
        assert not is_valid_http_url("https://example.com/path|pipe")
        assert not is_valid_http_url("https://example.com/path^caret")
        assert not is_valid_http_url("https://example.com/path`backtick")
        # query 中也拒绝
        assert not is_valid_http_url("https://example.com/path?q=<script>")

    def test_rfc3986_valid_chars_accepted(self):
        """RFC 3986 合法字符正常通过。"""
        assert is_valid_http_url("https://example.com/path/to/page")
        assert is_valid_http_url("https://example.com/path?q=hello&lang=en")
        # reserved 字符
        assert is_valid_http_url("https://example.com/path!$&'()*+,;=")
        # pct-encoded
        assert is_valid_http_url("https://example.com/path%20with%20spaces")
        # unicode path (IDN 不在 is_valid_http_url 层面检查)
        assert is_valid_http_url("https://example.com/路径")


class TestRedactProxyUrl:
    def test_redacts_password(self):
        assert (
            redact_proxy_url("http://user:pass123@proxy.example.com:8080") == "http://user:***@proxy.example.com:8080"
        )

    def test_no_auth_unchanged(self):
        assert redact_proxy_url("http://proxy.example.com:8080") == "http://proxy.example.com:8080"

    def test_user_only_no_password(self):
        assert redact_proxy_url("http://user@proxy.example.com:8080") == "http://user@proxy.example.com:8080"

    def test_empty_password_unchanged(self):
        assert redact_proxy_url("http://user:@proxy.example.com:8080") == "http://user:@proxy.example.com:8080"

    def test_ipv6_proxy(self):
        assert redact_proxy_url("http://user:secret@[::1]:8080") == "http://user:***@[::1]:8080"

    def test_encoded_at_in_password(self):
        assert redact_proxy_url("http://user:p%40ss@proxy.example.com:8080") == "http://user:***@proxy.example.com:8080"


class TestRedactSensitiveParams:
    def test_redacts_token(self):
        result = redact_sensitive_params("https://api.example.com?token=secret123&page=1")
        assert "secret123" not in result
        assert "***" in result
        assert "page=1" in result

    def test_redacts_case_insensitive(self):
        result = redact_sensitive_params("https://api.example.com?API_KEY=abc123")
        assert "abc123" not in result

    def test_no_sensitive_params_unchanged(self):
        assert (
            redact_sensitive_params("https://api.example.com?page=1&lang=en")
            == "https://api.example.com?page=1&lang=en"
        )

    def test_multiple_sensitive_params(self):
        result = redact_sensitive_params("https://api.example.com?token=a&key=b&page=1")
        assert "token=***" in result
        assert "key=***" in result
        assert "page=1" in result

    def test_substring_not_matched(self):
        assert redact_sensitive_params("https://api.example.com?passkey=abc") == "https://api.example.com?passkey=abc"

    def test_empty_value_also_redacted(self):
        result = redact_sensitive_params("https://api.example.com?token=&page=1")
        assert "***" in result
        assert "page=1" in result


class TestSafeLogURL:
    def test_redacts_sensitive_params(self):
        result = safe_log_url("https://api.example.com?token=secret123&page=1")
        assert "secret123" not in result
        assert "***" in result
        assert "page=1" in result

    def test_redacts_proxy_password(self):
        result = safe_log_url("http://user:pass123@proxy.example.com:8080")
        assert "pass123" not in result
        assert "***" in result


# ═══════════════════════════════════════════════════════════════════════
# 属性测试 — 幂等性 + 不变量
# ═══════════════════════════════════════════════════════════════════════


class TestNormalizeUrlIdempotency:
    """normalize_url 幂等：两次调用结果相同。"""

    URLS = [
        "https://EXAMPLE.COM:443/PATH?utm_source=ads&keep=val#frag",
        "http://user:pass@münchen.de:80/path/",
        "https://example.com",
        "https://example.com/path?q=1&token=secret",
        "http://[::1]:80/path",
        "https://example.com//double//slash",
        "HTTP://WWW.EXAMPLE.COM:80/?ref=old#top",
        "http://MÜNCHEN.DE:8080/PATH/",
    ]

    def test_idempotent(self):
        """任意 URL 经 normalize_url 两次得到相同结果。"""
        cfg = DEFAULT_CONFIG
        for url in self.URLS:
            once = normalize_url(url, cfg)
            twice = normalize_url(once, cfg)
            assert once == twice, f"not idempotent: {url!r} → {once!r} → {twice!r}"


class TestNormalizeUrlOutputInvariant:
    """normalize_url 输出始终通过 is_valid_http_url。"""

    URLS = TestNormalizeUrlIdempotency.URLS

    def test_output_is_valid_http_url(self):
        """规范化后的 URL 必须通过 is_valid_http_url 验证。"""
        cfg = DEFAULT_CONFIG
        for url in self.URLS:
            result = normalize_url(url, cfg)
            assert is_valid_http_url(result), f"output not valid: {url!r} → {result!r}"


class TestStripWwwIdempotency:
    """strip_www 幂等：两次调用结果相同。"""

    DOMAINS = [
        "www.example.com",
        "example.com",
        "www.blog.example.com",
        "www.com",
        "www.co.uk",
        "www.github.io",
    ]

    def test_idempotent(self):
        for d in self.DOMAINS:
            once = strip_www(d)
            twice = strip_www(once)
            assert once == twice, f"not idempotent: {d!r} → {once!r} → {twice!r}"


class TestRedactIdempotency:
    """redact_* 幂等：两次脱敏结果相同。"""

    def test_proxy_url_idempotent(self):
        once = redact_proxy_url("http://user:pass123@proxy.example.com:8080")
        twice = redact_proxy_url(once)
        assert once == twice

    def test_sensitive_params_idempotent(self):
        once = redact_sensitive_params("https://api.com?token=secret&api_key=abc123&page=1")
        twice = redact_sensitive_params(once)
        assert once == twice

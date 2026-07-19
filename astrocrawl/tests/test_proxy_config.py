"""Proxy 配置类型测试 — ProxyEndpointSpec / ParsedProxy / ProxyAuth / ProxyProfile / ProxyConfig / ProxyType。

对标 tests/test_ai_profile.py 模式。覆盖：构造/不可变/序列化/反序列化/类型强制/边界。
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from astrocrawl.proxy._config import (
    ParsedProxy,
    ProxyAuth,
    ProxyConfig,
    ProxyEndpointSpec,
    ProxyProfile,
    ProxyType,
    endpoint_display,
    endpoint_key,
    find_duplicate_endpoint,
)


class TestProxyType:
    """ProxyType IntEnum 值验证。"""

    def test_values(self):
        assert ProxyType.HTTP == 1
        assert ProxyType.HTTPS == 2
        assert ProxyType.SOCKS5 == 3

    def test_is_int_enum(self):
        assert isinstance(ProxyType.HTTP, int)
        assert int(ProxyType.HTTP) == 1


class TestProxyEndpointSpec:
    """ProxyEndpointSpec frozen dataclass — 默认值 / 不可变 / from_url。"""

    def test_defaults(self):
        spec = ProxyEndpointSpec()
        assert spec.label == ""
        assert spec.type == ProxyType.HTTP
        assert spec.host == ""
        assert spec.port == 8080
        assert spec.username == ""
        assert spec.password == ""
        assert spec.weight == 1

    def test_frozen(self):
        spec = ProxyEndpointSpec(label="test")
        with pytest.raises(FrozenInstanceError):
            spec.label = "other"  # type: ignore[misc]

    def test_replace(self):
        spec = ProxyEndpointSpec(label="old")
        new_spec = replace(spec, label="new")
        assert new_spec.label == "new"
        assert spec.label == "old"

    def test_password_repr_not_in_repr(self):
        spec = ProxyEndpointSpec(password="secret123")
        r = repr(spec)
        assert "secret123" not in r

    def test_from_url_http(self):
        spec = ProxyEndpointSpec.from_url("http://1.2.3.4:8080")
        assert spec.host == "1.2.3.4"
        assert spec.port == 8080
        assert spec.type == ProxyType.HTTP
        assert spec.label == "1.2.3.4:8080"

    def test_from_url_https(self):
        spec = ProxyEndpointSpec.from_url("https://proxy.example.com:8443")
        assert spec.type == ProxyType.HTTPS
        assert spec.host == "proxy.example.com"
        assert spec.port == 8443

    def test_from_url_socks5(self):
        spec = ProxyEndpointSpec.from_url("socks5://host:1080")
        assert spec.type == ProxyType.SOCKS5
        assert spec.host == "host"
        assert spec.port == 1080

    def test_from_url_with_auth(self):
        spec = ProxyEndpointSpec.from_url("http://user:pass@1.2.3.4:8080")
        assert spec.username == "user"
        assert spec.password == "pass"
        assert spec.host == "1.2.3.4"
        assert spec.port == 8080

    def test_from_url_ipv6(self):
        spec = ProxyEndpointSpec.from_url("http://[::1]:8080")
        assert spec.host == "::1"
        assert spec.port == 8080
        assert spec.label == "::1:8080"

    def test_from_url_ipv6_full(self):
        spec = ProxyEndpointSpec.from_url("http://[2001:db8::1]:8080")
        assert spec.host == "2001:db8::1"
        assert spec.port == 8080

    def test_from_url_no_scheme(self):
        """旧格式无 scheme URL 自动补 http://。"""
        spec = ProxyEndpointSpec.from_url("1.2.3.4:8080")
        assert spec.host == "1.2.3.4"
        assert spec.port == 8080
        assert spec.type == ProxyType.HTTP

    def test_from_url_no_scheme_with_auth(self):
        spec = ProxyEndpointSpec.from_url("user:pass@1.2.3.4:8080")
        assert spec.host == "1.2.3.4"
        assert spec.port == 8080
        assert spec.username == "user"
        assert spec.password == "pass"

    def test_from_url_no_scheme_ipv6(self):
        spec = ProxyEndpointSpec.from_url("[::1]:8080")
        assert spec.host == "::1"
        assert spec.port == 8080

    def test_from_url_invalid_bare_hostname(self):
        """无 scheme 且无端口号 → ValueError。"""
        with pytest.raises(ValueError, match="无效的代理端点 URL"):
            ProxyEndpointSpec.from_url("not-a-url")

    def test_from_url_weight_default(self):
        spec = ProxyEndpointSpec.from_url("http://1.2.3.4:8080")
        assert spec.weight == 1

    def test_from_url_socks_default_port(self):
        spec = ProxyEndpointSpec.from_url("socks5://host")
        assert spec.port == 1080

    def test_from_url_http_default_port(self):
        """HTTP URL 无端口 → 默认 8080。"""
        spec = ProxyEndpointSpec.from_url("http://host")
        assert spec.host == "host"
        assert spec.port == 8080

    def test_from_url_no_hostname(self):
        """无主机名 → ValueError。"""
        with pytest.raises(ValueError, match="无法从 URL 解析主机名"):
            ProxyEndpointSpec.from_url("http://:8080")


class TestProxyAuth:
    """ProxyAuth — 默认值 / repr 安全 / frozen。"""

    def test_defaults(self):
        auth = ProxyAuth()
        assert auth.username == ""
        assert auth.password == ""

    def test_password_not_in_repr(self):
        auth = ProxyAuth(username="u", password="s3cret")
        r = repr(auth)
        assert "u" in r
        assert "s3cret" not in r

    def test_frozen(self):
        auth = ProxyAuth(username="u")
        with pytest.raises(FrozenInstanceError):
            auth.username = "x"  # type: ignore[misc]


class TestParsedProxy:
    """ParsedProxy — IPv4/IPv6 URL 生成 / Playwright proxy 转换 / frozen。"""

    def test_to_url_with_auth_ipv4(self):
        parsed = ParsedProxy(type=ProxyType.HTTP, host="1.2.3.4", port=8080, auth=ProxyAuth())
        url = parsed.to_url_with_auth()
        assert url == "http://1.2.3.4:8080"

    def test_to_url_with_auth_ipv6(self):
        parsed = ParsedProxy(type=ProxyType.HTTP, host="::1", port=8080, auth=ProxyAuth())
        url = parsed.to_url_with_auth()
        assert url == "http://[::1]:8080"

    def test_to_url_with_auth_ipv6_with_auth(self):
        parsed = ParsedProxy(
            type=ProxyType.HTTP, host="2001:db8::1", port=8080, auth=ProxyAuth(username="u", password="p")
        )
        url = parsed.to_url_with_auth()
        assert "[2001:db8::1]" in url
        assert "u:p@" in url

    def test_to_url_with_auth_special_chars_encoded(self):
        parsed = ParsedProxy(
            type=ProxyType.HTTP, host="1.2.3.4", port=8080, auth=ProxyAuth(username="user", password="p@ss:word")
        )
        url = parsed.to_url_with_auth()
        assert "@" in url
        assert "p%40ss%3Aword" in url

    def test_to_url_with_auth_https(self):
        parsed = ParsedProxy(type=ProxyType.HTTPS, host="proxy.example.com", port=8443, auth=ProxyAuth())
        url = parsed.to_url_with_auth()
        assert url == "https://proxy.example.com:8443"

    def test_to_url_with_auth_socks5(self):
        parsed = ParsedProxy(type=ProxyType.SOCKS5, host="host", port=1080, auth=ProxyAuth())
        url = parsed.to_url_with_auth()
        assert url == "socks5://host:1080"

    def test_to_url_with_auth_no_auth(self):
        parsed = ParsedProxy(type=ProxyType.HTTP, host="1.2.3.4", port=3128, auth=ProxyAuth())
        url = parsed.to_url_with_auth()
        assert "@" not in url

    def test_to_playwright_proxy(self):
        parsed = ParsedProxy(type=ProxyType.HTTP, host="1.2.3.4", port=8080, auth=ProxyAuth())
        pw = parsed.to_playwright_proxy()
        assert "server" in pw
        assert pw["server"] == "http://1.2.3.4:8080"

    def test_frozen(self):
        parsed = ParsedProxy(type=ProxyType.HTTP, host="1.2.3.4", port=8080, auth=ProxyAuth())
        with pytest.raises(FrozenInstanceError):
            parsed.port = 3128  # type: ignore[misc]


class TestProxyProfile:
    """ProxyProfile — to_dict / from_dict 往返 / 类型强制转换 / 缺失键防御。"""

    def test_to_dict_empty(self):
        profile = ProxyProfile()
        d = profile.to_dict()
        assert d["name"] == ""
        assert d["proxies"] == []
        assert d["bypass_domains"] == []

    def test_to_dict_with_proxies(self):
        spec = ProxyEndpointSpec(label="JP", type=ProxyType.HTTP, host="1.2.3.4", port=8080)
        profile = ProxyProfile(name="test", proxies=(spec,), bypass_domains=(".internal",))
        d = profile.to_dict()
        assert d["name"] == "test"
        assert len(d["proxies"]) == 1
        assert d["proxies"][0]["label"] == "JP"
        assert d["proxies"][0]["type"] == 1
        assert d["proxies"][0]["host"] == "1.2.3.4"
        assert d["proxies"][0]["port"] == 8080
        assert d["proxies"][0]["weight"] == 1
        assert d["bypass_domains"] == [".internal"]

    def test_to_dict_with_password(self):
        spec = ProxyEndpointSpec(password="secret")
        profile = ProxyProfile(proxies=(spec,))
        d = profile.to_dict()
        assert d["proxies"][0]["password"] == "secret"

    def test_roundtrip(self):
        spec = ProxyEndpointSpec(
            label="JP", type=ProxyType.SOCKS5, host="2.3.4.5", port=1080, username="u", password="p", weight=3
        )
        profile = ProxyProfile(name="roundtrip", proxies=(spec,), bypass_domains=("*.example.com", "192.168.*"))
        d = profile.to_dict()
        restored = ProxyProfile.from_dict(d)
        assert restored.name == profile.name
        assert len(restored.proxies) == 1
        rp = restored.proxies[0]
        assert rp.label == "JP"
        assert rp.type == ProxyType.SOCKS5
        assert rp.host == "2.3.4.5"
        assert rp.port == 1080
        assert rp.username == "u"
        assert rp.password == "p"
        assert rp.weight == 3
        assert restored.bypass_domains == ("*.example.com", "192.168.*")

    def test_from_dict_type_coercion(self):
        """Port 和 type 必须是整数，from_dict 使用 int() 强制转换。"""
        d = {"name": "test", "proxies": [{"type": 3, "host": "h", "port": 1080}]}
        profile = ProxyProfile.from_dict(d)
        assert profile.proxies[0].type == ProxyType.SOCKS5
        assert profile.proxies[0].port == 1080

    def test_from_dict_missing_keys(self):
        """缺失键回退默认值——对标 AIProfile.from_dict() 防御模式。"""
        d: dict = {}
        profile = ProxyProfile.from_dict(d)
        assert profile.name == ""
        assert profile.proxies == ()
        assert profile.bypass_domains == ()

    def test_from_dict_partial_proxy_fields(self):
        d = {"name": "test", "proxies": [{"host": "1.2.3.4"}]}
        profile = ProxyProfile.from_dict(d)
        assert profile.proxies[0].host == "1.2.3.4"
        assert profile.proxies[0].port == 8080  # default
        assert profile.proxies[0].type == ProxyType.HTTP  # default
        assert profile.proxies[0].label == ""

    def test_frozen(self):
        profile = ProxyProfile()
        with pytest.raises(FrozenInstanceError):
            profile.name = "x"  # type: ignore[misc]


class TestProxyConfig:
    """ProxyConfig — from_profile 工厂 / frozen 不可变 / bypass_domains 传递。"""

    def test_from_profile_basic(self):
        profile = ProxyProfile(
            name="test",
            proxies=(ProxyEndpointSpec(label="JP", type=ProxyType.HTTP, host="1.2.3.4", port=8080),),
            bypass_domains=(".internal",),
        )
        config = ProxyConfig.from_profile(profile)
        assert len(config.proxies) == 1
        parsed = config.proxies[0]
        assert parsed.type == ProxyType.HTTP
        assert parsed.host == "1.2.3.4"
        assert parsed.port == 8080
        assert parsed.weight == 1
        assert config.bypass_domains == (".internal",)

    def test_from_profile_auth_assembly(self):
        profile = ProxyProfile(proxies=(ProxyEndpointSpec(username="user", password="pass", host="1.2.3.4"),))
        config = ProxyConfig.from_profile(profile)
        parsed = config.proxies[0]
        assert parsed.auth.username == "user"
        assert parsed.auth.password == "pass"

    def test_from_profile_label_not_in_parsed(self):
        """Label 是用户标识，非网络属性——不进入 ParsedProxy。"""
        profile = ProxyProfile(proxies=(ProxyEndpointSpec(label="My Proxy", host="1.2.3.4"),))
        config = ProxyConfig.from_profile(profile)
        parsed = config.proxies[0]
        assert not hasattr(parsed, "label")

    def test_from_profile_empty(self):
        profile = ProxyProfile(name="empty")
        config = ProxyConfig.from_profile(profile)
        assert config.proxies == ()
        assert config.bypass_domains == ()

    def test_frozen(self):
        config = ProxyConfig()
        with pytest.raises(FrozenInstanceError):
            config.proxies = ()  # type: ignore[misc]


class TestEndpointKey:
    """endpoint_key — 自然主键 TYPE:host:port。"""

    def test_http(self):
        ep = ProxyEndpointSpec(type=ProxyType.HTTP, host="1.2.3.4", port=8080)
        assert endpoint_key(ep) == "HTTP:1.2.3.4:8080"

    def test_https(self):
        ep = ProxyEndpointSpec(type=ProxyType.HTTPS, host="proxy.example.com", port=8443)
        assert endpoint_key(ep) == "HTTPS:proxy.example.com:8443"

    def test_socks5(self):
        ep = ProxyEndpointSpec(type=ProxyType.SOCKS5, host="host", port=1080)
        assert endpoint_key(ep) == "SOCKS5:host:1080"

    def test_parsed_proxy_satisfies_protocol(self):
        parsed = ParsedProxy(type=ProxyType.HTTP, host="1.2.3.4", port=8080, auth=ProxyAuth())
        assert endpoint_key(parsed) == "HTTP:1.2.3.4:8080"

    def test_ipv6_bare_address(self):
        """IPv6 裸地址不加方括号。"""
        ep = ProxyEndpointSpec(type=ProxyType.HTTP, host="2001:db8::1", port=8080)
        assert endpoint_key(ep) == "HTTP:2001:db8::1:8080"


class TestEndpointDisplay:
    """endpoint_display — 人类可读 TYPE host:port。"""

    def test_http(self):
        ep = ProxyEndpointSpec(type=ProxyType.HTTP, host="1.2.3.4", port=8080)
        assert endpoint_display(ep) == "HTTP 1.2.3.4:8080"

    def test_socks5(self):
        ep = ProxyEndpointSpec(type=ProxyType.SOCKS5, host="host", port=1080)
        assert endpoint_display(ep) == "SOCKS5 host:1080"


class TestFindDuplicateEndpoint:
    """find_duplicate_endpoint — 按 endpoint_key 查找重复。"""

    def test_no_duplicate(self):
        eps = (ProxyEndpointSpec(host="1.2.3.4", port=8080), ProxyEndpointSpec(host="5.6.7.8", port=3128))
        assert find_duplicate_endpoint(eps, ProxyEndpointSpec(host="9.9.9.9", port=8080)) is None

    def test_duplicate_found(self):
        eps = (ProxyEndpointSpec(host="1.2.3.4", port=8080), ProxyEndpointSpec(host="5.6.7.8", port=3128))
        result = find_duplicate_endpoint(eps, ProxyEndpointSpec(host="1.2.3.4", port=8080))
        assert result == 0

    def test_duplicate_different_type_same_host_port(self):
        """相同 host:port 不同 type → 不同 endpoint_key → 不重复。"""
        eps = (ProxyEndpointSpec(type=ProxyType.HTTP, host="1.2.3.4", port=8080),)
        result = find_duplicate_endpoint(eps, ProxyEndpointSpec(type=ProxyType.SOCKS5, host="1.2.3.4", port=8080))
        assert result is None

    def test_exclude_index_skips_self(self):
        eps = (ProxyEndpointSpec(host="1.2.3.4", port=8080), ProxyEndpointSpec(host="5.6.7.8", port=3128))
        result = find_duplicate_endpoint(eps, ProxyEndpointSpec(host="1.2.3.4", port=8080), exclude_index=0)
        assert result is None

    def test_exclude_index_finds_other(self):
        eps = (
            ProxyEndpointSpec(host="1.2.3.4", port=8080),
            ProxyEndpointSpec(host="1.2.3.4", port=8080),
        )
        result = find_duplicate_endpoint(eps, ProxyEndpointSpec(host="1.2.3.4", port=8080), exclude_index=0)
        assert result == 1

    def test_empty_endpoints(self):
        assert find_duplicate_endpoint((), ProxyEndpointSpec(host="1.2.3.4", port=8080)) is None

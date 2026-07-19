"""代理配置类型 — 三层表示（ADR-0010 决策 2）。

持久化层: ProxyType / ProxyAuth / ProxyEndpointSpec / ProxyProfile
运行时层: ParsedProxy / ProxyConfig

对标 ai/_config.py + ai/_profile.py 模式。零内部导入（纯数据）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Protocol
from urllib.parse import quote, urlparse


class ProxyType(IntEnum):
    HTTP = 1
    HTTPS = 2
    SOCKS5 = 3


# ═══════════════════════════════════════════════════════════════════════
# 持久化层 — JSON 可序列化
# ═══════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ProxyAuth:
    """Auth 从 server 地址分离——对标 Playwright proxy dict + curl CURLOPT_PROXYUSERPWD。

    repr=False 确保 password 永不进日志。
    """

    username: str = ""
    password: str = field(default="", repr=False)


@dataclass(frozen=True)
class ProxyEndpointSpec:
    """用户可编辑的代理端点定义。字段与 preferences.json 一一对应。对标 FoxyProxy 代理条目。

    不含 label 的 ParsedProxy 转换——label 是用户标识，非网络属性。
    """

    label: str = ""
    type: ProxyType = ProxyType.HTTP
    host: str = ""
    port: int = 8080
    username: str = ""
    password: str = field(default="", repr=False)
    weight: int = 1

    @classmethod
    def from_url(cls, url: str) -> ProxyEndpointSpec:
        """从 URL 解析代理端点。使用 urllib.parse.urlparse（原生支持 IPv6 方括号格式）。

        若 URL 不含 :// 前缀，自动补 http://——兼容旧 proxy_pool 中的无 scheme URL。
        无效 URL 抛 ValueError（不静默降级）。

        >>> ProxyEndpointSpec.from_url("http://user:pass@1.2.3.4:8080")
        ProxyEndpointSpec(label='1.2.3.4:8080', type=HTTP, host='1.2.3.4', port=8080, username='user', ...)
        >>> ProxyEndpointSpec.from_url("1.2.3.4:8080")
        ProxyEndpointSpec(label='1.2.3.4:8080', type=HTTP, host='1.2.3.4', port=8080, ...)
        >>> ProxyEndpointSpec.from_url("http://[::1]:8080")
        ProxyEndpointSpec(label='::1:8080', type=HTTP, host='::1', port=8080, ...)
        >>> ProxyEndpointSpec.from_url("socks5://host:1080")
        ProxyEndpointSpec(label='host:1080', type=SOCKS5, host='host', port=1080, ...)
        """
        # 无 scheme + 无端口号 → 不是合法的代理端点格式（最小要求 host:port 或 scheme://host）
        if "://" not in url and ":" not in url:
            raise ValueError(f"无效的代理端点 URL（需 host:port 或 scheme 前缀）: {url!r}")

        # 无 scheme → 自动补 http://
        if "://" not in url:
            url = f"http://{url}"

        parsed = urlparse(url)

        if not parsed.hostname:
            raise ValueError(f"无法从 URL 解析主机名: {url!r}")

        # scheme → ProxyType
        scheme = parsed.scheme.lower()
        if scheme == "socks5":
            proxy_type = ProxyType.SOCKS5
        elif scheme == "https":
            proxy_type = ProxyType.HTTPS
        else:
            proxy_type = ProxyType.HTTP

        port = parsed.port or (1080 if proxy_type == ProxyType.SOCKS5 else 8080)

        return cls(
            label=f"{parsed.hostname}:{port}",
            type=proxy_type,
            host=parsed.hostname,
            port=port,
            username=parsed.username or "",
            password=parsed.password or "",
            weight=1,
        )


@dataclass(frozen=True)
class ProxyProfile:
    """可持久化的代理配置 Profile（对标 AIProfile）。

    纯 mechanism 数据——端点 + bypass 白名单，不做路由策略。
    path_mode 不属于此类型——它是爬虫级路由策略（CrawlerConfig.proxy_mode），
    与"用哪些端点"是独立维度。符合 ADR 决策 1 的 Mechanism/Strategy 分离。
    """

    name: str = ""
    uuid: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    proxies: tuple[ProxyEndpointSpec, ...] = ()
    bypass_domains: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        """序列化为 preferences.json 格式。"""
        return {
            "name": self.name,
            "uuid": self.uuid,
            "proxies": [
                {
                    "label": p.label,
                    "type": int(p.type),
                    "host": p.host,
                    "port": p.port,
                    "username": p.username,
                    "password": p.password,
                    "weight": p.weight,
                }
                for p in self.proxies
            ],
            "bypass_domains": list(self.bypass_domains),
        }

    @classmethod
    def from_dict(cls, d: dict) -> ProxyProfile:
        """从 preferences.json 格式重建。缺失键回退默认值（对标 AIProfile.from_dict() 防御模式）。"""
        raw_proxies = d.get("proxies", [])
        proxies = tuple(
            ProxyEndpointSpec(
                label=p.get("label", ""),
                type=ProxyType(int(p.get("type", 1))),
                host=p.get("host", ""),
                port=int(p.get("port", 8080)),
                username=p.get("username", ""),
                password=p.get("password", ""),
                weight=int(p.get("weight", 1)),
            )
            for p in raw_proxies
        )
        raw_bypass = d.get("bypass_domains", [])
        return cls(
            name=d.get("name", ""),
            uuid=d.get("uuid", ""),
            proxies=proxies,
            bypass_domains=tuple(raw_bypass),
        )


class HasEndpointIdentity(Protocol):
    """端点身份证协议 — 只需 type/host/port 三元组即可生成自然主键。

    由 ProxyEndpointSpec 和 ParsedProxy 结构化满足，无需显式继承。
    frozen dataclass 字段为只读 — Protocol 以 @property 声明匹配。
    """

    @property
    def type(self) -> ProxyType: ...
    @property
    def host(self) -> str: ...
    @property
    def port(self) -> int: ...


def endpoint_key(ep: HasEndpointIdentity) -> str:
    """返回端点自然主键 — ``TYPE:host:port``（ADR-0010，Squid hostname:type:port 标准）。

    用于 Profile 内端点去重校验与 proxy_last_used 节点存储。
    格式: ``{ProxyType.name}:{host}:{port}``，host 为裸地址（IPv4/IPv6 均不加方括号）。
    解析时: split(":", 1) 得 type → rsplit(":", 1) 得 host 与 port。IPv6 裸地址安全。
    """
    return f"{ep.type.name}:{ep.host}:{ep.port}"


def endpoint_display(ep: HasEndpointIdentity) -> str:
    """返回端点的人类可读标识，用于错误消息。格式: ``TYPE host:port``。"""
    return f"{ep.type.name} {ep.host}:{ep.port}"


def find_duplicate_endpoint(
    endpoints: tuple[ProxyEndpointSpec, ...] | list[ProxyEndpointSpec],
    new_ep: ProxyEndpointSpec,
    exclude_index: int | None = None,
) -> int | None:
    """在 endpoints 中按 endpoint_key 查找与 new_ep 重复的索引，未找到返回 None。

    exclude_index: 编辑模式时排除自身索引。
    """
    key = endpoint_key(new_ep)
    for i, ep in enumerate(endpoints):
        if i == exclude_index:
            continue
        if endpoint_key(ep) == key:
            return i
    return None


# ═══════════════════════════════════════════════════════════════════════
# 运行时层 — 已解析 + 已校验
# ═══════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ParsedProxy:
    """ProxyEndpointSpec → 校验后不可变端点。对标 ADR-0005 RuleSnapshot。

    host 存储裸地址（IPv4 "1.2.3.4" / IPv6 "2001:db8::1"，不含方括号）。
    to_url_with_auth() 生成时自动为 IPv6 地址添加方括号（RFC 2732）。
    """

    type: ProxyType
    host: str
    port: int
    auth: ProxyAuth
    weight: int = 1

    def _scheme_str(self) -> str:
        """ProxyType → URL scheme 字符串。"""
        if self.type == ProxyType.SOCKS5:
            return "socks5"
        elif self.type == ProxyType.HTTPS:
            return "https"
        return "http"

    def to_url_with_auth(self) -> str:
        """返回完整代理 URL（含 auth 凭证），全项目统一代理端点标识。

        IPv6 地址自动加方括号（RFC 2732）。
        密码中的特殊字符（@ : /）自动 URL 编码。

        WARNING: 返回值是敏感数据——调用方必须在进入日志/展示前用
        redact_proxy_url() 脱敏。
        """
        scheme = self._scheme_str()
        host_part = f"[{self.host}]" if ":" in self.host else self.host

        if self.auth.username:
            encoded_password = quote(self.auth.password, safe="")
            return f"{scheme}://{self.auth.username}:{encoded_password}@{host_part}:{self.port}"
        return f"{scheme}://{host_part}:{self.port}"

    def to_playwright_proxy(self) -> dict:
        """返回 Playwright proxy dict 格式。"""
        return {"server": self.to_url_with_auth()}


@dataclass(frozen=True)
class ProxyConfig:
    """不可变运行时快照（对标 ADR-0005 RuleSnapshot + AIConfig）。

    纯 mechanism 快照——端点 + bypass 白名单，不做路由策略。
    path_mode 属于 CrawlerConfig.proxy_mode，与代理端点选择是独立的两个维度。
    """

    proxies: tuple[ParsedProxy, ...] = ()
    bypass_domains: tuple[str, ...] = ()

    @classmethod
    def from_profile(cls, profile: ProxyProfile) -> ProxyConfig:
        """从 ProxyProfile 构造不可变运行时快照。

        ProxyEndpointSpec 扁平字段（username/password）→ ProxyAuth 对象。
        label 字段不带入 ParsedProxy（label 是用户标识，非网络属性）。
        """
        return cls(
            proxies=tuple(
                ParsedProxy(
                    type=spec.type,
                    host=spec.host,
                    port=spec.port,
                    auth=ProxyAuth(username=spec.username, password=spec.password),
                    weight=spec.weight,
                )
                for spec in profile.proxies
            ),
            bypass_domains=profile.bypass_domains,
        )

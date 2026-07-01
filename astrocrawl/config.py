from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Tuple

from astrocrawl._version import __version__

if TYPE_CHECKING:
    from astrocrawl._path_strategy import PathSwitch


class ConfigError(ValueError):
    """启动时配置验证错误。"""


class ConfigValidationError(Exception):
    """启动期配置一致性错误 — 规则冲突、版本不兼容等。

    子类化此异常的错误会被 CLI/GUI 入口点统一捕获并提供用户指引。
    错误消息应包含具体问题描述，子类可通过 str(e) 传递修复建议。
    """


DEFAULT_USER_AGENT = f"Mozilla/5.0 (compatible; AstroCrawl/{__version__}; +https://github.com/Etoileint/AstroCrawl)"


@dataclass(frozen=True)
class GlobalSettings:
    """跨会话全局设置 — Preferences 为 SSOT，引擎通过显式注入消费。

    对标 CrawlerConfig 的 frozen dataclass 模式：构造后不可变，通过 ``replace()`` 修改，
    通过 ``from_preferences()`` 从 Preferences 单例构造。
    """

    rules_dirs: Tuple[str, ...] = ()
    rules_dirs_enabled: bool = True
    rules_auto_update: bool = True
    trace_rules: bool = False
    log_level: int = logging.INFO
    output_gzip: bool = True
    clear_context_cookies: bool = False

    @classmethod
    def from_preferences(cls) -> "GlobalSettings":
        from astrocrawl.utils.preferences import get_preferences

        prefs = get_preferences()
        return cls(
            rules_dirs=tuple(prefs.get_rules_dirs()),
            rules_dirs_enabled=prefs.get_rules_dirs_enabled(),
            rules_auto_update=prefs.get_rules_auto_update(),
            trace_rules=prefs.get_trace_rules(),
            log_level=getattr(logging, prefs.get_log_level(), logging.INFO),
            output_gzip=prefs.get_output_gzip(),
            clear_context_cookies=prefs.get_clear_context_cookies(),
        )

    def __post_init__(self) -> None:
        if isinstance(self.rules_dirs, list):
            object.__setattr__(self, "rules_dirs", tuple(self.rules_dirs))

    def with_overrides(self, **kwargs) -> "GlobalSettings":
        valid = {k: v for k, v in kwargs.items() if k in self.__dataclass_fields__}
        return replace(self, **valid)


@dataclass(frozen=True)
class CrawlerConfig:
    page_timeout: int = 20000
    network_idle_timeout: int = 8000
    viewport_width: int = 1280
    viewport_height: int = 720
    user_agent: str = DEFAULT_USER_AGENT
    concurrency: int = 8
    domain_max_concurrency: int = 3
    domain_min_delay: float = 1.0
    domain_max_delay: float = 5.0
    queue_hard_maxsize: int = 50000
    max_retries: int = 3
    max_requeue: int = 1
    retry_backoff_base: float = 2.0
    output_buffer_size: int = 1024 * 1024
    max_text_length: int = 500000
    tracking_params: frozenset = field(
        default_factory=lambda: frozenset(
            {
                "utm_source",
                "utm_medium",
                "utm_campaign",
                "utm_term",
                "utm_content",
                "fbclid",
                "gclid",
                "msclkid",
                "ref",
                "source",
            }
        )
    )
    robots_respect: bool = True
    robots_user_agent: str = "AstroCrawl"
    robots_cache_ttl: int = 3600
    robots_cache_max_size: int = 1000
    content_hash_sample_size: int = 4096
    db_path: str = ""
    resume_if_exists: bool = True
    page_pool_size_per_context: int = 2
    respect_crawl_delay: bool = True
    output_flush_interval: float = 30.0
    max_total_pages: int = 0
    max_runtime_seconds: int = 0
    follow_nofollow: bool = True
    respect_meta_robots: bool = True
    use_sitemap: bool = True
    sitemap_max_recursion: int = 2
    sitemap_additional_paths: Tuple[str, ...] = field(
        default_factory=lambda: (
            "/sitemap.xml",
            "/sitemap_index.xml",
            "/sitemap-index.xml",
        )
    )
    sitemap_max_urls: int = 100000
    sitemap_fetch_concurrency: int = 10
    skip_duplicate_links: bool = False
    skip_non_essential_resources: bool = True
    proxy_mode: str = "direct_only"
    log_file: str = ""
    auth_basic_user: str = ""
    auth_basic_pass: str = field(default="", repr=False)
    auth_bearer_token: str = field(default="", repr=False)
    cookies_file: str = ""
    custom_headers: Tuple[str, ...] = field(default_factory=tuple, repr=False)
    exclude_patterns: Tuple[str, ...] = ()
    webhook_url: str = field(default="", repr=False)
    rules_max_generic: int = 20
    rules_cache_dir: str = ""
    rules_sources: Tuple[Dict[str, Any], ...] = ()
    extra_currency_symbols: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.concurrency <= 0:
            raise ValueError("并发数必须 > 0")
        if self.domain_max_concurrency <= 0:
            raise ValueError("域名最大并发必须 > 0")
        if self.page_pool_size_per_context <= 0:
            raise ValueError("页面池大小必须 > 0")
        if self.viewport_width <= 0 or self.viewport_height <= 0:
            raise ValueError("视口宽高必须 > 0")
        if self.max_total_pages < 0 or self.max_runtime_seconds < 0:
            raise ValueError("限制值必须 >= 0")
        if self.domain_min_delay > self.domain_max_delay:
            raise ValueError("domain_min_delay 不能大于 domain_max_delay")
        if self.content_hash_sample_size < 3:
            raise ValueError("content_hash_sample_size 至少为 3")
        if not self.user_agent or not self.user_agent.strip():
            raise ValueError("User-Agent 不能为空")
        if self.page_timeout < 1000:
            raise ValueError("page_timeout 必须 >= 1000ms")
        if self.network_idle_timeout < 500:
            raise ValueError("network_idle_timeout 必须 >= 500ms")
        if self.queue_hard_maxsize < 10:
            raise ValueError("queue_hard_maxsize 必须 >= 10")
        if self.robots_cache_ttl < 0:
            raise ValueError("robots_cache_ttl 必须 >= 0")
        if self.output_buffer_size < 4096:
            raise ValueError("output_buffer_size 必须 >= 4096")
        if self.max_text_length < 100:
            raise ValueError("max_text_length 必须 >= 100")
        if self.output_flush_interval < 1.0:
            raise ValueError("output_flush_interval 必须 >= 1.0")
        if self.max_retries < 1:
            raise ValueError("max_retries 必须 >= 1")
        if self.max_requeue < 0:
            raise ValueError("max_requeue 必须 >= 0")
        if self.retry_backoff_base <= 0:
            raise ValueError("retry_backoff_base 必须 > 0")
        if self.sitemap_max_recursion < 0:
            raise ValueError("sitemap_max_recursion 必须 >= 0")
        if self.sitemap_max_urls < 1:
            raise ValueError("sitemap_max_urls 必须 >= 1")
        if self.sitemap_fetch_concurrency < 1:
            raise ValueError("sitemap_fetch_concurrency 必须 >= 1")

        lowered = frozenset(p.lower() for p in self.tracking_params)
        object.__setattr__(self, "tracking_params", lowered)

        for fname in (
            "custom_headers",
            "exclude_patterns",
            "extra_currency_symbols",
            "rules_sources",
            "sitemap_additional_paths",
        ):
            val = getattr(self, fname)
            if isinstance(val, list):
                object.__setattr__(self, fname, tuple(val))

        if self.rules_max_generic < 0:
            raise ValueError("rules_max_generic 必须 >= 0")

    def __repr__(self) -> str:
        parts: list[str] = []
        for f in fields(self):
            v = getattr(self, f.name)
            if not f.repr:
                parts.append(f"{f.name}='***'" if v else f"{f.name}={v!r}")
            else:
                parts.append(f"{f.name}={v!r}")
        return f"CrawlerConfig({', '.join(parts)})"

    def get_path_switch(self) -> "PathSwitch":
        """返回与此 proxy_mode 配置对应的 PathSwitch 策略对象。"""
        from astrocrawl._path_strategy import PathSwitch

        return PathSwitch.for_mode(self.proxy_mode)

    def to_dict(self) -> dict:
        d = {}
        for k, v in self.__dict__.items():
            if k == "tracking_params" and isinstance(v, frozenset):
                d[k] = list(v)
            elif k in (
                "custom_headers",
                "exclude_patterns",
                "extra_currency_symbols",
                "rules_sources",
                "sitemap_additional_paths",
            ) and isinstance(v, tuple):
                d[k] = list(v)
            else:
                d[k] = v
        return d

    @classmethod
    def from_file(cls, path: str) -> "CrawlerConfig":
        """从 JSON / YAML / TOML 文件加载配置"""
        p = Path(path)
        suffix = p.suffix.lower()
        if suffix == ".json":
            with open(path) as f:
                data = json.load(f)
        elif suffix in (".yaml", ".yml"):
            try:
                import yaml
            except ImportError:
                raise ImportError("需要 PyYAML 来加载 .yaml 配置文件: pip install pyyaml")
            with open(path) as f:
                data = yaml.safe_load(f)
        elif suffix == ".toml":
            import tomllib

            with open(path, "rb") as f:
                data = tomllib.load(f)
        else:
            raise ValueError(f"不支持的配置文件格式: {suffix} (支持: .json, .yaml, .yml, .toml)")
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> "CrawlerConfig":
        data = data.copy()
        known_fields = set(cls.__dataclass_fields__)
        unknown = set(data) - known_fields
        if unknown:
            logging.getLogger("astrocrawl.config").warning(
                "忽略未知配置字段: %s",
                ", ".join(sorted(unknown)),
            )
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered)

    def print_report(self) -> None:
        """打印生效配置报告"""
        items = [
            ("并发数", self.concurrency),
            ("爬取深度", "N/A (运行时指定)"),
            ("域名最大并发", self.domain_max_concurrency),
            ("域名延迟范围", f"{self.domain_min_delay}-{self.domain_max_delay}s"),
            ("最大页面数", self.max_total_pages or "无限制"),
            ("最大运行时间", f"{self.max_runtime_seconds}s" if self.max_runtime_seconds else "无限制"),
            ("最大重试次数/重新入队", f"{self.max_retries}/{self.max_requeue}"),
            ("User-Agent", self.user_agent[:60]),
            ("遵从 robots.txt", "是" if self.robots_respect else "否"),
            ("使用 Sitemap", "是" if self.use_sitemap else "否"),
            ("日志文件", self.log_file or "仅控制台"),
        ]
        max_k = max(len(k) for k, _ in items)
        print("=" * 60)
        print("  AstroCrawl 配置报告")
        print("=" * 60)
        for k, v in items:
            print(f"  {k.rjust(max_k)}: {v}")
        print("=" * 60)

    def with_contact(self, contact: str) -> "CrawlerConfig":
        if not contact:
            return self
        ua = self.user_agent.rstrip()
        ua_clean = re.sub(r"\s*\([^)]*\)\s*$", "", ua).rstrip()
        new_ua = f"{ua_clean} ({contact})"
        return replace(self, user_agent=new_ua)

    @classmethod
    def from_env(cls, base: "CrawlerConfig | None" = None) -> "CrawlerConfig":
        """从环境变量 ASTROCRAWL_* 读取配置覆盖。
        支持的变量：ASTROCRAWL_CONCURRENCY, ASTROCRAWL_USER_AGENT, ASTROCRAWL_MAX_PAGES,
        ASTROCRAWL_MAX_RUNTIME, ASTROCRAWL_DB_PATH, ASTROCRAWL_LOG_FILE,
        ASTROCRAWL_CONTACT, ASTROCRAWL_PROXY_MODE.
        """
        cfg = base or DEFAULT_CONFIG
        overrides: Dict[str, Any] = {}
        env_map = {
            "ASTROCRAWL_CONCURRENCY": ("concurrency", int),
            "ASTROCRAWL_USER_AGENT": ("user_agent", str),
            "ASTROCRAWL_MAX_PAGES": ("max_total_pages", int),
            "ASTROCRAWL_MAX_RUNTIME": ("max_runtime_seconds", int),
            "ASTROCRAWL_DB_PATH": ("db_path", str),
            "ASTROCRAWL_LOG_FILE": ("log_file", str),
        }
        for env_key, (fname, cast) in env_map.items():
            val = os.environ.get(env_key)
            if val is not None:
                try:
                    overrides[fname] = cast(val)
                except (ValueError, TypeError):
                    logging.warning("环境变量 %s 的值 '%s' 无效，已忽略", env_key, val)
        contact = os.environ.get("ASTROCRAWL_CONTACT")
        rules_max_generic_env = os.environ.get("ASTROCRAWL_RULES_MAX_GENERIC")
        if rules_max_generic_env:
            try:
                overrides["rules_max_generic"] = int(rules_max_generic_env)
            except (ValueError, TypeError):
                logging.warning("环境变量 ASTROCRAWL_RULES_MAX_GENERIC 的值 '%s' 无效，已忽略", rules_max_generic_env)
        proxy_mode = os.environ.get("ASTROCRAWL_PROXY_MODE")
        if proxy_mode:
            overrides["proxy_mode"] = proxy_mode
        cfg = replace(cfg, **overrides)
        if contact:
            cfg = cfg.with_contact(contact)
        return cfg


DEFAULT_CONFIG = CrawlerConfig()

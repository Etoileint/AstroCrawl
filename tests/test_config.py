"""CrawlerConfig 配置加载与验证测试"""

from __future__ import annotations

import json
import logging
import os

import pytest

from astrocrawl._version import __version__
from astrocrawl.config import DEFAULT_CONFIG, DEFAULT_USER_AGENT, ConfigError, CrawlerConfig


class TestCrawlerConfig:
    def test_default_config_valid(self):
        cfg = DEFAULT_CONFIG
        assert cfg.concurrency == 8
        assert cfg.page_timeout == 20000
        assert cfg.robots_respect is True
        assert cfg.use_sitemap is True

    def test_default_user_agent_contains_version(self):
        assert __version__ in DEFAULT_USER_AGENT
        assert "AstroCrawl" in DEFAULT_USER_AGENT

    def test_from_dict_override(self):
        cfg = CrawlerConfig.from_dict({"concurrency": 5, "max_total_pages": 100})
        assert cfg.concurrency == 5
        assert cfg.max_total_pages == 100
        # 未指定的字段保持默认值
        assert cfg.robots_respect is True

    def test_concurrency_validation(self):
        with pytest.raises(ValueError):
            CrawlerConfig(concurrency=0)
        with pytest.raises(ValueError):
            CrawlerConfig(concurrency=-1)

    def test_domain_delay_validation(self):
        with pytest.raises(ValueError):
            CrawlerConfig(domain_min_delay=10.0, domain_max_delay=5.0)

    def test_user_agent_validation(self):
        with pytest.raises(ValueError):
            CrawlerConfig(user_agent="")
        with pytest.raises(ValueError):
            CrawlerConfig(user_agent="   ")

    def test_to_dict_roundtrip(self):
        cfg = CrawlerConfig(concurrency=4)
        d = cfg.to_dict()
        cfg2 = CrawlerConfig.from_dict(d)
        assert cfg2.concurrency == 4
        assert cfg2.user_agent == cfg.user_agent

    def test_with_contact(self):
        cfg = CrawlerConfig(user_agent="AstroCrawl/1.0")
        c = cfg.with_contact("admin@example.com")
        assert "(admin@example.com)" in c.user_agent

    def test_with_contact_no_duplicate(self):
        cfg = CrawlerConfig(user_agent="AstroCrawl/1.0 (old@test.com)")
        c = cfg.with_contact("new@test.com")
        assert "old@test.com" not in c.user_agent
        assert "new@test.com" in c.user_agent

    def test_with_contact_empty_returns_self(self):
        cfg = CrawlerConfig(user_agent="Test/1.0")
        assert cfg.with_contact("") is cfg

    def test_with_contact_default_ua_strips_compatible(self):
        cfg = CrawlerConfig()
        c = cfg.with_contact("admin@test.com")
        assert "(admin@test.com)" in c.user_agent
        assert "compatible" not in c.user_agent

    def test_with_contact_ua_no_parenthetical(self):
        cfg = CrawlerConfig(user_agent="PlainBot/1.0")
        c = cfg.with_contact("test@bot.com")
        assert c.user_agent == "PlainBot/1.0 (test@bot.com)"

    def test_tracking_params_lowercase(self):
        cfg = CrawlerConfig(tracking_params=frozenset({"UTM_SOURCE", "FbClid"}))
        assert "utm_source" in cfg.tracking_params
        assert "fbclid" in cfg.tracking_params
        assert "UTM_SOURCE" not in cfg.tracking_params

    def test_proxy_mode_validation_delegated_to_pathswitch(self):
        """无效 proxy_mode 不再在 __post_init__ 校验——由 PathSwitch.for_mode() 捕获。"""
        cfg = CrawlerConfig(proxy_mode="invalid_mode")
        # 构造时不再抛异常
        with pytest.raises(ValueError, match="Unknown proxy_mode"):
            cfg.get_path_switch()

    def test_valid_proxy_modes_accepted(self):
        for mode in ("prefer_proxy", "prefer_direct", "proxy_only", "direct_only"):
            cfg = CrawlerConfig(proxy_mode=mode)
            ps = cfg.get_path_switch()
            assert ps is not None

    def test_proxy_mode_without_proxy_raises_config_error(self):
        """proxy_only / prefer_proxy / prefer_direct 未配代理时启动即失败。"""
        from unittest.mock import MagicMock

        from astrocrawl.browser.context_pool import ContextPool

        for mode in ("proxy_only", "prefer_proxy", "prefer_direct"):
            cfg = CrawlerConfig(proxy_mode=mode)
            with pytest.raises(ConfigError, match="requires at least one proxy"):
                ContextPool(MagicMock(), max_slots=4, proxy_session=None, cfg=cfg)

    # ═══════════════════════════════════════════════════════════════════════
    # __repr__ 安全 — 敏感字段掩码
    # ═══════════════════════════════════════════════════════════════════════

    def test_repr_masks_sensitive_fields(self):
        cfg = CrawlerConfig(
            auth_basic_pass="s3cret!",
            auth_bearer_token="tok_abc123",
            webhook_url="https://hook.example.com/secret",
            custom_headers=["X-API-Key: abc123"],
        )
        r = repr(cfg)
        assert "auth_basic_pass='***'" in r
        assert "auth_bearer_token='***'" in r
        assert "webhook_url='***'" in r
        assert "custom_headers='***'" in r
        assert "s3cret!" not in r
        assert "tok_abc123" not in r
        assert "hook.example.com/secret" not in r
        assert "X-API-Key" not in r

    def test_repr_shows_empty_sensitive_fields(self):
        cfg = CrawlerConfig()
        r = repr(cfg)
        assert "auth_basic_pass=''" in r
        assert "auth_bearer_token=''" in r
        assert "webhook_url=''" in r
        # empty tuple preserves type via {v!r}
        assert "custom_headers=()" in r

    def test_repr_includes_nonsensitive_fields(self):
        cfg = CrawlerConfig(concurrency=12, user_agent="TestBot/1.0")
        r = repr(cfg)
        assert "concurrency=12" in r
        assert "user_agent='TestBot/1.0'" in r
        assert "max_retries=" in r

    def test_auth_basic_user_not_masked(self):
        cfg = CrawlerConfig(auth_basic_user="admin")
        r = repr(cfg)
        assert "auth_basic_user='admin'" in r

    def test_repr_contains_all_field_names(self):
        cfg = CrawlerConfig()
        r = repr(cfg)
        for name in cfg.__dataclass_fields__:
            assert f"{name}=" in r, f"Field '{name}' missing from repr"

    def test_from_dict_list_fields_handled_by_post_init(self):
        cfg = CrawlerConfig.from_dict(
            {
                "tracking_params": ["UTM_SOURCE", "fbclid"],
                "sitemap_additional_paths": ["/custom-sitemap.xml"],
            }
        )
        assert "utm_source" in cfg.tracking_params
        assert "fbclid" in cfg.tracking_params
        assert "UTM_SOURCE" not in cfg.tracking_params
        assert isinstance(cfg.tracking_params, frozenset)
        assert "/custom-sitemap.xml" in cfg.sitemap_additional_paths
        assert isinstance(cfg.sitemap_additional_paths, tuple)

    def test_from_dict_empty_uses_all_defaults(self):
        cfg = CrawlerConfig.from_dict({})
        assert cfg.concurrency == DEFAULT_CONFIG.concurrency
        assert cfg.user_agent == DEFAULT_CONFIG.user_agent

    def test_to_dict_empty_tuples_become_empty_lists(self):
        cfg = CrawlerConfig()
        d = cfg.to_dict()
        assert d["custom_headers"] == []
        assert d["exclude_patterns"] == []
        assert d["extra_currency_symbols"] == []
        assert d["sitemap_additional_paths"] == ["/sitemap.xml", "/sitemap_index.xml", "/sitemap-index.xml"]


# ═══════════════════════════════════════════════════════════════════════
# 值约束校验 — parametrize 覆盖所有 __post_init__ 边界
# ═══════════════════════════════════════════════════════════════════════

VALUE_CONSTRAINT_CASES = [
    # (field_name, invalid_value, expected_error_fragment)
    ("domain_max_concurrency", 0, "域名最大并发"),
    ("domain_max_concurrency", -1, "域名最大并发"),
    ("page_pool_size_per_context", 0, "页面池大小"),
    ("page_pool_size_per_context", -1, "页面池大小"),
    ("viewport_width", 0, "视口宽高"),
    ("viewport_height", 0, "视口宽高"),
    ("max_total_pages", -1, "限制值"),
    ("max_runtime_seconds", -1, "限制值"),
    ("content_hash_sample_size", 2, "content_hash_sample_size"),
    ("page_timeout", 999, "page_timeout"),
    ("network_idle_timeout", 499, "network_idle_timeout"),
    ("queue_hard_maxsize", 9, "queue_hard_maxsize"),
    ("robots_cache_ttl", -1, "robots_cache_ttl"),
    ("output_buffer_size", 4095, "output_buffer_size"),
    ("max_text_length", 99, "max_text_length"),
    ("output_flush_interval", 0.5, "output_flush_interval"),
    ("max_retries", 0, "max_retries"),
    ("max_requeue", -1, "max_requeue"),
    ("retry_backoff_base", 0, "retry_backoff_base"),
    ("sitemap_max_recursion", -1, "sitemap_max_recursion"),
    ("sitemap_max_urls", 0, "sitemap_max_urls"),
    ("sitemap_fetch_concurrency", 0, "sitemap_fetch_concurrency"),
    ("rules_max_generic", -1, "rules_max_generic"),
]


class TestConfigValueConstraints:
    """__post_init__ 值约束全覆盖 — parametrize 驱动。"""

    @pytest.mark.parametrize("field_name,invalid_value,expected_fragment", VALUE_CONSTRAINT_CASES)
    def test_invalid_value_raises_value_error(self, field_name, invalid_value, expected_fragment):
        with pytest.raises(ValueError, match=expected_fragment):
            CrawlerConfig(**{field_name: invalid_value})

    def test_global_settings_log_level_int(self):
        """GlobalSettings.log_level 为 int 类型。"""
        from astrocrawl.config import GlobalSettings

        gs = GlobalSettings(log_level=logging.DEBUG)
        assert gs.log_level == logging.DEBUG

    def test_global_settings_log_level_default(self):
        """GlobalSettings.log_level 默认值为 logging.INFO。"""
        from astrocrawl.config import GlobalSettings

        gs = GlobalSettings()
        assert gs.log_level == logging.INFO

    def test_global_settings_frozen(self):
        """GlobalSettings 为 frozen dataclass，构造后不可修改。"""
        from astrocrawl.config import GlobalSettings

        gs = GlobalSettings()
        with pytest.raises(Exception):
            gs.log_level = 10  # type: ignore[misc]

    def test_global_settings_rules_dirs_list_to_tuple(self):
        """直接构造和 with_overrides 均将 list→tuple，保证 Tuple 类型契约。"""
        from astrocrawl.config import GlobalSettings

        gs = GlobalSettings(rules_dirs=["/a", "/b"])
        assert isinstance(gs.rules_dirs, tuple)
        assert gs.rules_dirs == ("/a", "/b")

        gs2 = GlobalSettings().with_overrides(rules_dirs=["/x"])
        assert isinstance(gs2.rules_dirs, tuple)
        assert gs2.rules_dirs == ("/x",)

        # tuple 原样保留
        gs3 = GlobalSettings(rules_dirs=("/a",))
        assert isinstance(gs3.rules_dirs, tuple)

    def test_global_settings_with_overrides_empty(self):
        """with_overrides 无参数时返回等值新实例。"""
        from astrocrawl.config import GlobalSettings

        gs = GlobalSettings()
        gs2 = gs.with_overrides()
        assert gs2.log_level == gs.log_level
        assert gs2.rules_dirs == gs.rules_dirs
        assert gs2 is not gs  # replace 返回新实例

    def test_global_settings_with_overrides_filters_unknown(self):
        """with_overrides 忽略未知字段，不抛异常。"""
        from astrocrawl.config import GlobalSettings

        gs = GlobalSettings().with_overrides(unknown_field=123, log_level=logging.WARNING)
        assert gs.log_level == logging.WARNING
        assert not hasattr(gs, "unknown_field")

    def test_global_settings_from_preferences_all_fields(self, monkeypatch):
        """from_preferences 正确映射 Preferences 的 7 个字段到 GlobalSettings。"""
        from tests._fakes_gui import FakePreferences

        fake = FakePreferences()
        fake.set_rules_dirs(["/custom/rules"])
        fake.set_rules_dirs_enabled(False)
        fake.set_rules_auto_update(False)
        fake.set_trace_rules(True)
        fake.set_log_level("DEBUG")
        fake.set_output_gzip(False)
        fake.set_clear_context_cookies(True)
        monkeypatch.setattr("astrocrawl.utils.preferences._preferences", fake)

        from astrocrawl.config import GlobalSettings

        gs = GlobalSettings.from_preferences()
        assert gs.rules_dirs == ("/custom/rules",)
        assert gs.rules_dirs_enabled is False
        assert gs.rules_auto_update is False
        assert gs.trace_rules is True
        assert gs.log_level == logging.DEBUG
        assert gs.output_gzip is False
        assert gs.clear_context_cookies is True

    def test_global_settings_from_preferences_defaults(self, monkeypatch):
        """from_preferences 在 Preferences 缺失字段时使用 dataclass 默认值。"""
        from tests._fakes_gui import FakePreferences

        fake = FakePreferences()
        monkeypatch.setattr("astrocrawl.utils.preferences._preferences", fake)

        from astrocrawl.config import GlobalSettings

        gs = GlobalSettings.from_preferences()
        assert gs.rules_dirs == ()
        assert gs.rules_dirs_enabled is True
        assert gs.rules_auto_update is True
        assert gs.trace_rules is False
        assert gs.log_level == logging.INFO
        assert gs.output_gzip is True
        assert gs.clear_context_cookies is False


# ═══════════════════════════════════════════════════════════════════════
# from_file — JSON / YAML / TOML
# ═══════════════════════════════════════════════════════════════════════


class TestConfigFromFile:
    """CrawlerConfig.from_file() — 多格式加载。"""

    def test_from_json(self, tmp_path):
        f = tmp_path / "config.json"
        f.write_text(json.dumps({"concurrency": 3, "user_agent": "TestBot/1.0"}))
        cfg = CrawlerConfig.from_file(str(f))
        assert cfg.concurrency == 3
        assert cfg.user_agent == "TestBot/1.0"

    def test_from_yaml(self, tmp_path):
        pytest.importorskip("yaml")
        f = tmp_path / "config.yaml"
        f.write_text("concurrency: 7\nuser_agent: YamlBot/1.0\n")
        cfg = CrawlerConfig.from_file(str(f))
        assert cfg.concurrency == 7
        assert cfg.user_agent == "YamlBot/1.0"

    def test_from_toml(self, tmp_path):
        f = tmp_path / "config.toml"
        f.write_text('concurrency = 5\nuser_agent = "TomlBot/1.0"\n')
        cfg = CrawlerConfig.from_file(str(f))
        assert cfg.concurrency == 5
        assert cfg.user_agent == "TomlBot/1.0"

    def test_from_json_file_not_found(self, tmp_path):
        f = tmp_path / "nonexistent.json"
        with pytest.raises(FileNotFoundError):
            CrawlerConfig.from_file(str(f))

    def test_unsupported_format_raises(self, tmp_path):
        f = tmp_path / "config.ini"
        f.write_text("[section]\nkey=value\n")
        with pytest.raises(ValueError, match="不支持"):
            CrawlerConfig.from_file(str(f))

    def test_from_json_with_unknown_fields(self, tmp_path):
        """未知字段被忽略（仅 warning）。"""
        f = tmp_path / "config.json"
        f.write_text(json.dumps({"concurrency": 2, "unknown_field": "ignored"}))
        cfg = CrawlerConfig.from_file(str(f))
        assert cfg.concurrency == 2

    def test_from_file_no_extension(self, tmp_path):
        f = tmp_path / "config"
        f.write_text(json.dumps({"concurrency": 1}))
        with pytest.raises(ValueError, match="不支持"):
            CrawlerConfig.from_file(str(f))

    def test_from_yaml_uppercase_suffix(self, tmp_path):
        pytest.importorskip("yaml")
        f = tmp_path / "config.YAML"
        f.write_text("concurrency: 9\n")
        cfg = CrawlerConfig.from_file(str(f))
        assert cfg.concurrency == 9

    def test_from_json_invalid_content(self, tmp_path):
        f = tmp_path / "config.json"
        f.write_text("not valid json")
        with pytest.raises(json.JSONDecodeError):
            CrawlerConfig.from_file(str(f))


# ═══════════════════════════════════════════════════════════════════════
# from_env — 环境变量覆盖
# ═══════════════════════════════════════════════════════════════════════


class TestConfigFromEnv:
    """CrawlerConfig.from_env() — 环境变量注入。"""

    def test_env_overrides_single_field(self, monkeypatch):
        monkeypatch.setenv("ASTROCRAWL_CONCURRENCY", "4")
        cfg = CrawlerConfig.from_env()
        assert cfg.concurrency == 4

    def test_env_base_none_uses_default(self):
        cfg = CrawlerConfig.from_env(None)
        assert cfg.concurrency == DEFAULT_CONFIG.concurrency

    def test_env_overrides_multiple_fields(self, monkeypatch):
        monkeypatch.setenv("ASTROCRAWL_CONCURRENCY", "2")
        monkeypatch.setenv("ASTROCRAWL_MAX_PAGES", "500")
        monkeypatch.setenv("ASTROCRAWL_DB_PATH", "/tmp/test.db")
        cfg = CrawlerConfig.from_env()
        assert cfg.concurrency == 2
        assert cfg.max_total_pages == 500
        assert cfg.db_path == "/tmp/test.db"

    def test_env_invalid_int_ignored(self, monkeypatch, caplog):
        monkeypatch.setenv("ASTROCRAWL_CONCURRENCY", "not_a_number")
        cfg = CrawlerConfig.from_env()
        assert cfg.concurrency == DEFAULT_CONFIG.concurrency
        assert "无效" in caplog.text

    def test_env_log_level_handled_by_cli(self):
        """ASTROCRAWL_LOG_LEVEL 环境变量由 CLI _merge_cli_config 处理（从 from_env 迁移）。"""
        # from_env 不再处理 log_level — 由 CLI 层 _merge_cli_config 负责
        cfg = CrawlerConfig.from_env()
        assert "log_level" not in cfg.__dataclass_fields__

    def test_env_contact_appended(self, monkeypatch):
        monkeypatch.setenv("ASTROCRAWL_CONTACT", "test@bot.com")
        cfg = CrawlerConfig.from_env()
        assert "test@bot.com" in cfg.user_agent

    def test_env_no_vars_returns_default(self, monkeypatch):
        for key in os.environ:
            if key.startswith("ASTROCRAWL_"):
                monkeypatch.delenv(key, raising=False)
        cfg = CrawlerConfig.from_env()
        assert cfg.concurrency == DEFAULT_CONFIG.concurrency

    def test_env_accepts_custom_base(self, monkeypatch):
        base = CrawlerConfig(concurrency=16, user_agent="BaseBot/1.0")
        monkeypatch.setenv("ASTROCRAWL_MAX_PAGES", "200")
        cfg = CrawlerConfig.from_env(base)
        assert cfg.concurrency == 16
        assert cfg.user_agent == "BaseBot/1.0"
        assert cfg.max_total_pages == 200

    def test_env_proxy_mode(self, monkeypatch):
        monkeypatch.setenv("ASTROCRAWL_PROXY_MODE", "prefer_proxy")
        cfg = CrawlerConfig.from_env()
        assert cfg.proxy_mode == "prefer_proxy"

    def test_env_rules_max_generic(self, monkeypatch):
        monkeypatch.setenv("ASTROCRAWL_RULES_MAX_GENERIC", "50")
        cfg = CrawlerConfig.from_env()
        assert cfg.rules_max_generic == 50

    def test_env_rules_max_generic_invalid_ignored(self, monkeypatch, caplog):
        monkeypatch.setenv("ASTROCRAWL_RULES_MAX_GENERIC", "not_a_number")
        cfg = CrawlerConfig.from_env()
        assert cfg.rules_max_generic == DEFAULT_CONFIG.rules_max_generic
        assert "无效" in caplog.text

    def test_env_user_agent(self, monkeypatch):
        monkeypatch.setenv("ASTROCRAWL_USER_AGENT", "CustomBot/2.0")
        cfg = CrawlerConfig.from_env()
        assert cfg.user_agent == "CustomBot/2.0"

    def test_env_log_file(self, monkeypatch):
        monkeypatch.setenv("ASTROCRAWL_LOG_FILE", "/tmp/crawl.log")
        cfg = CrawlerConfig.from_env()
        assert cfg.log_file == "/tmp/crawl.log"

    def test_env_max_runtime(self, monkeypatch):
        monkeypatch.setenv("ASTROCRAWL_MAX_RUNTIME", "3600")
        cfg = CrawlerConfig.from_env()
        assert cfg.max_runtime_seconds == 3600

    def test_env_contact_and_proxy_mode_together(self, monkeypatch):
        monkeypatch.setenv("ASTROCRAWL_CONTACT", "contact@test.com")
        monkeypatch.setenv("ASTROCRAWL_PROXY_MODE", "prefer_proxy")
        cfg = CrawlerConfig.from_env()
        assert "contact@test.com" in cfg.user_agent
        assert cfg.proxy_mode == "prefer_proxy"


# ═══════════════════════════════════════════════════════════════════════
# print_report
# ═══════════════════════════════════════════════════════════════════════


class TestConfigPrintReport:
    """CrawlerConfig.print_report() — 配置报告输出。"""

    def test_print_report_output(self, capsys):
        cfg = CrawlerConfig(concurrency=4)
        cfg.print_report()
        out = capsys.readouterr().out
        assert "AstroCrawl" in out
        assert "配置报告" in out
        assert "4" in out

    def test_print_report_no_exception(self):
        cfg = DEFAULT_CONFIG
        cfg.print_report()

    def test_print_report_with_limits_and_log(self, capsys):
        cfg = CrawlerConfig(max_total_pages=500, max_runtime_seconds=1800, log_file="/tmp/crawl.log")
        cfg.print_report()
        out = capsys.readouterr().out
        assert "500" in out
        assert "1800s" in out
        assert "/tmp/crawl.log" in out


# ═══════════════════════════════════════════════════════════════════════
# Collection field immutability — Tuple types in frozen dataclass
# ═══════════════════════════════════════════════════════════════════════


class TestCollectionFieldImmutability:
    """frozen=True + Tuple = 真不可变。"""

    def test_defaults_are_empty_tuples(self):
        cfg = CrawlerConfig()
        assert cfg.custom_headers == ()
        assert cfg.exclude_patterns == ()
        assert cfg.extra_currency_symbols == ()
        assert cfg.rules_sources == ()

    def test_list_input_converted_to_tuple(self):
        cfg = CrawlerConfig(
            custom_headers=["X-1: v1"],
            exclude_patterns=["/admin"],
            extra_currency_symbols=["¥"],
            rules_sources=[{"name": "test"}],
        )
        assert isinstance(cfg.custom_headers, tuple)
        assert isinstance(cfg.exclude_patterns, tuple)
        assert isinstance(cfg.extra_currency_symbols, tuple)
        assert isinstance(cfg.rules_sources, tuple)

    def test_append_rejected(self):
        cfg = CrawlerConfig(custom_headers=("X:1",))
        with pytest.raises(AttributeError, match="tuple"):
            cfg.custom_headers.append("bad")  # type: ignore[union-attr]

    def test_to_dict_returns_independent_lists(self):
        cfg = CrawlerConfig(exclude_patterns=("/admin",))
        d = cfg.to_dict()
        assert isinstance(d["exclude_patterns"], list)
        # 修改返回的 list 不影响内部 tuple
        d["exclude_patterns"].append("/secret")
        assert "/secret" not in cfg.exclude_patterns

    def test_replace_with_list_argument_converts(self):
        from dataclasses import replace

        cfg = CrawlerConfig()
        cfg2 = replace(cfg, custom_headers=["X-replace"])
        assert isinstance(cfg2.custom_headers, tuple)
        assert cfg2.custom_headers == ("X-replace",)

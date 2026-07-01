"""特征测试：CLI 8.1 main.py — 命令行入口核心函数。

覆盖 parse_args / _load_file_config / _parse_set_value / _merge_cli_config / _install_signal_handlers。
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import astrocrawl.cli.main as cli_main
from astrocrawl.config import CrawlerConfig


class TestLoadFileConfig:
    """_load_file_config — 三种配置文件格式。"""

    def test_yaml_standard_format(self, tmp_path):
        yaml_path = tmp_path / "test.yaml"
        yaml_path.write_text("concurrency: 7\nrobots_respect: false\n", encoding="utf-8")
        cfg, extra = cli_main._load_file_config(str(yaml_path))
        assert cfg.concurrency == 7
        assert cfg.robots_respect is False
        assert extra == {}

    def test_toml_standard_format(self, tmp_path):
        toml_path = tmp_path / "test.toml"
        toml_path.write_text('concurrency = 4\nproxy_mode = "prefer_proxy"\n', encoding="utf-8")
        cfg, extra = cli_main._load_file_config(str(toml_path))
        assert cfg.concurrency == 4
        assert cfg.proxy_mode == "prefer_proxy"
        assert extra == {}

    def test_json_standard_format(self, tmp_path):
        json_path = tmp_path / "test.json"
        json_path.write_text(json.dumps({"concurrency": 3, "same_domain_only": False}), encoding="utf-8")
        cfg, extra = cli_main._load_file_config(str(json_path))
        assert cfg.concurrency == 3
        assert extra == {}

    def test_json_advanced_container_format(self, tmp_path):
        """GUI 保存的 "advanced" 容器格式。"""
        json_path = tmp_path / "advanced.json"
        data = {
            "urls": ["https://example.com", "https://test.org"],
            "depth": 5,
            "same_domain_only": False,
            "output_path": "/tmp/out.jsonl",
            "respect_robots": False,
            "advanced": {"concurrency": 12, "proxy_mode": "proxy_only", "max_total_pages": 500},
        }
        json_path.write_text(json.dumps(data), encoding="utf-8")
        cfg, extra = cli_main._load_file_config(str(json_path))
        assert cfg.concurrency == 12
        assert cfg.proxy_mode == "proxy_only"
        assert cfg.max_total_pages == 500
        assert extra["urls"] == ["https://example.com", "https://test.org"]
        assert extra["depth"] == 5
        assert extra["same_domain_only"] is False
        assert extra["output_path"] == "/tmp/out.jsonl"
        assert extra["respect_robots"] is False

    def test_json_advanced_partial_extra_keys(self, tmp_path):
        """advanced 容器中只包含部分 extra key。"""
        json_path = tmp_path / "partial.json"
        data = {"urls": ["https://a.com"], "depth": 2, "advanced": {"concurrency": 1}}
        json_path.write_text(json.dumps(data), encoding="utf-8")
        cfg, extra = cli_main._load_file_config(str(json_path))
        assert cfg.concurrency == 1
        assert extra["urls"] == ["https://a.com"]
        assert extra["depth"] == 2

    def test_json_no_advanced_key_uses_from_file(self, tmp_path):
        """无 'advanced' key 的标准 JSON → 委托 from_file()，不返回 extra。"""
        json_path = tmp_path / "std.json"
        json_path.write_text(json.dumps({"concurrency": 2}), encoding="utf-8")
        cfg, extra = cli_main._load_file_config(str(json_path))
        assert cfg.concurrency == 2
        assert extra == {}

    def test_nonexistent_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            cli_main._load_file_config(str(tmp_path / "nonexistent.yaml"))


class TestParseSetValue:
    """_parse_set_value — KEY=VALUE 类型自动推导（边界值 + 等价类补充）。"""

    def test_int_zero(self):
        assert cli_main._parse_set_value("page_timeout", "0") == ("cfg", 0)

    def test_int_negative(self):
        assert cli_main._parse_set_value("page_timeout", "-1") == ("cfg", -1)

    def test_int_large(self):
        assert cli_main._parse_set_value("max_total_pages", "999999999") == ("cfg", 999999999)

    def test_float_zero(self):
        assert cli_main._parse_set_value("domain_min_delay", "0.0") == ("cfg", 0.0)

    def test_float_negative(self):
        assert cli_main._parse_set_value("domain_min_delay", "-0.5") == ("cfg", -0.5)

    def test_str_with_equals(self):
        """字符串值包含 = 字符。"""
        target, val = cli_main._parse_set_value("user_agent", "Bot/1.0 (test=value)")
        assert target == "cfg"
        assert val == "Bot/1.0 (test=value)"

    def test_log_level_name_case_insensitive(self):
        assert cli_main._parse_set_value("log_level", "debug") == ("gs", logging.DEBUG)
        assert cli_main._parse_set_value("log_level", "INFO") == ("gs", logging.INFO)
        assert cli_main._parse_set_value("log_level", "Warning") == ("gs", logging.WARNING)

    def test_log_level_invalid_name(self):
        with pytest.raises(ValueError, match="log_level"):
            cli_main._parse_set_value("log_level", "VERBOSE")

    def test_global_settings_field(self):
        """识别 GlobalSettings 字段 → target='gs'。"""
        assert cli_main._parse_set_value("output_gzip", "true") == ("gs", True)
        assert cli_main._parse_set_value("rules_dirs_enabled", "true") == ("gs", True)

    def test_bool_edge_cases(self):
        """bool 解析的边界输入。"""
        # 仅小写 yes/no 被接受（与实现一致）
        assert cli_main._parse_set_value("output_gzip", "true") == ("gs", True)
        assert cli_main._parse_set_value("output_gzip", "false") == ("gs", False)

    def test_bool_invalid_value_rejected(self):
        with pytest.raises(ValueError, match="bool type"):
            cli_main._parse_set_value("output_gzip", "maybe")

    def test_complex_type_list_rejected(self):
        with pytest.raises(ValueError, match="compound type"):
            cli_main._parse_set_value("rules_sources", "['a', 'b']")

    def test_complex_type_dict_rejected(self):
        with pytest.raises(ValueError, match="compound type"):
            cli_main._parse_set_value("custom_headers", '{"k":"v"}')

    def test_unknown_field_helpful_message(self):
        with pytest.raises(ValueError, match="Unknown config field"):
            cli_main._parse_set_value("nonexistent_field_xyz", "42")


class TestMergeCliConfig:
    """_merge_cli_config — 四源配置合并。"""

    def test_minimal_url_only(self):
        """仅提供 URL 时的最小合并。"""
        with patch.object(sys, "argv", ["astrocrawl", "https://example.com"]):
            args = cli_main.parse_args()
        result = cli_main._merge_cli_config(args)
        assert result is not None
        assert result["urls"] == ["https://example.com"]
        assert result["depth"] == 2
        assert isinstance(result["cfg"], CrawlerConfig)
        assert result["output"] == "crawler_output.jsonl"

    def test_depth_override(self):
        with patch.object(sys, "argv", ["astrocrawl", "-d", "5", "https://example.com"]):
            args = cli_main.parse_args()
        result = cli_main._merge_cli_config(args)
        assert result["depth"] == 5

    def test_concurrency_override(self):
        with patch.object(sys, "argv", ["astrocrawl", "-c", "10", "https://example.com"]):
            args = cli_main.parse_args()
        result = cli_main._merge_cli_config(args)
        assert result["cfg"].concurrency == 10

    def test_no_robots_flag(self):
        with patch.object(sys, "argv", ["astrocrawl", "--no-robots", "https://example.com"]):
            args = cli_main.parse_args()
        result = cli_main._merge_cli_config(args)
        assert result["cfg"].robots_respect is False

    def test_same_domain_flag(self):
        with patch.object(sys, "argv", ["astrocrawl", "--same-domain", "https://example.com"]):
            args = cli_main.parse_args()
        result = cli_main._merge_cli_config(args)
        assert result["same_domain"] is True

    def test_proxy_mode(self):
        with patch.object(sys, "argv", ["astrocrawl", "--proxy-mode", "prefer-proxy", "https://example.com"]):
            args = cli_main.parse_args()
        result = cli_main._merge_cli_config(args)
        assert result["cfg"].proxy_mode == "prefer_proxy"

    def test_proxy_mode_underscore_normalization(self):
        """--proxy-mode 的连字符被归一化为下划线。"""
        with patch.object(sys, "argv", ["astrocrawl", "--proxy-mode", "proxy-only", "https://example.com"]):
            args = cli_main.parse_args()
        result = cli_main._merge_cli_config(args)
        assert result["cfg"].proxy_mode == "proxy_only"

    def test_proxy_mode_override_normalized_to_underscore(self):
        """proxy_mode_override 也必须归一化为下划线（防 context_pool.py:52 比较失败）。"""
        with patch.object(sys, "argv", ["astrocrawl", "--proxy-mode", "prefer-proxy", "https://example.com"]):
            args = cli_main.parse_args()
        result = cli_main._merge_cli_config(args)
        assert result["proxy_mode_override"] == "prefer_proxy"

    def test_output_override(self):
        with patch.object(sys, "argv", ["astrocrawl", "-o", "/tmp/my_output.jsonl", "https://example.com"]):
            args = cli_main.parse_args()
        result = cli_main._merge_cli_config(args)
        assert result["output"] == "/tmp/my_output.jsonl"

    def test_set_override_int(self):
        with patch.object(sys, "argv", ["astrocrawl", "--set", "page_timeout=60000", "https://example.com"]):
            args = cli_main.parse_args()
        result = cli_main._merge_cli_config(args)
        assert result["cfg"].page_timeout == 60000

    def test_set_override_bool(self):
        with patch.object(sys, "argv", ["astrocrawl", "--set", "output_gzip=true", "https://example.com"]):
            args = cli_main.parse_args()
        result = cli_main._merge_cli_config(args)
        assert result["global_settings"].output_gzip is True

    def test_set_override_invalid_format(self, capsys):
        """--set 不带 = 号 → 返回 None。"""
        with patch.object(sys, "argv", ["astrocrawl", "--set", "invalid_format", "https://example.com"]):
            args = cli_main.parse_args()
        result = cli_main._merge_cli_config(args)
        assert result is None
        assert "KEY=VALUE" in capsys.readouterr().out

    def test_set_override_unknown_field(self, capsys):
        with patch.object(sys, "argv", ["astrocrawl", "--set", "no_such=42", "https://example.com"]):
            args = cli_main.parse_args()
        result = cli_main._merge_cli_config(args)
        assert result is None
        assert "Unknown" in capsys.readouterr().out

    def test_set_multiple_overrides(self):
        with patch.object(
            sys,
            "argv",
            [
                "astrocrawl",
                "--set",
                "page_timeout=30000",
                "--set",
                "domain_min_delay=3.5",
                "https://example.com",
            ],
        ):
            args = cli_main.parse_args()
        result = cli_main._merge_cli_config(args)
        assert result["cfg"].page_timeout == 30000
        assert result["cfg"].domain_min_delay == 3.5

    def test_no_urls_error(self, capsys):
        with patch.object(sys, "argv", ["astrocrawl"]):
            args = cli_main.parse_args()
        result = cli_main._merge_cli_config(args)
        assert result is None
        assert "URL" in capsys.readouterr().out

    def test_log_level_env_var(self, monkeypatch):
        monkeypatch.setenv("ASTROCRAWL_LOG_LEVEL", "DEBUG")
        with patch.object(sys, "argv", ["astrocrawl", "https://example.com"]):
            args = cli_main.parse_args()
        result = cli_main._merge_cli_config(args)
        assert result["global_settings"].log_level == logging.DEBUG

    def test_log_level_env_var_invalid(self, monkeypatch):
        monkeypatch.setenv("ASTROCRAWL_LOG_LEVEL", "INVALID")
        with patch.object(sys, "argv", ["astrocrawl", "https://example.com"]):
            args = cli_main.parse_args()
        result = cli_main._merge_cli_config(args)
        assert result is not None  # 不应阻止爬取，仅忽略无效值

    def test_log_level_cli_overrides_env(self, monkeypatch):
        monkeypatch.setenv("ASTROCRAWL_LOG_LEVEL", "DEBUG")
        with patch.object(sys, "argv", ["astrocrawl", "--log-level", "ERROR", "https://example.com"]):
            args = cli_main.parse_args()
        result = cli_main._merge_cli_config(args)
        assert result["global_settings"].log_level == logging.ERROR

    def test_rules_dirs_global_settings(self):
        with patch.object(
            sys, "argv", ["astrocrawl", "--rules-dir", "/tmp/r1", "--rules-dir", "/tmp/r2", "https://example.com"]
        ):
            args = cli_main.parse_args()
        result = cli_main._merge_cli_config(args)
        assert list(result["global_settings"].rules_dirs) == ["/tmp/r1", "/tmp/r2"]

    def test_trace_rules_global_settings(self):
        with patch.object(sys, "argv", ["astrocrawl", "--trace-rules", "https://example.com"]):
            args = cli_main.parse_args()
        result = cli_main._merge_cli_config(args)
        assert result["global_settings"].trace_rules is True

    def test_config_file_basic(self, tmp_path):
        """标准 JSON 配置文件路径 — urls 由命令行提供（非 CrawlerConfig 字段）。"""
        config_path = tmp_path / "cfg.json"
        config_path.write_text(json.dumps({"concurrency": 6}), encoding="utf-8")
        with patch.object(sys, "argv", ["astrocrawl", "--config", str(config_path), "https://cfg.example.com"]):
            args = cli_main.parse_args()
        result = cli_main._merge_cli_config(args)
        assert result is not None
        assert result["cfg"].concurrency == 6
        assert result["urls"] == ["https://cfg.example.com"]

    def test_config_file_advanced(self, tmp_path):
        """GUI 导出的 advanced 容器格式配置文件。"""
        config_path = tmp_path / "adv_cfg.json"
        config_path.write_text(
            json.dumps(
                {
                    "urls": ["https://gui.example.com"],
                    "depth": 10,
                    "advanced": {"concurrency": 20, "max_total_pages": 1000},
                }
            ),
            encoding="utf-8",
        )
        with patch.object(sys, "argv", ["astrocrawl", "--config", str(config_path)]):
            args = cli_main.parse_args()
        result = cli_main._merge_cli_config(args)
        assert result["cfg"].concurrency == 20
        assert result["cfg"].max_total_pages == 1000
        assert result["urls"] == ["https://gui.example.com"]
        assert result["depth"] == 10

    def test_max_pages_flag(self):
        with patch.object(sys, "argv", ["astrocrawl", "--max-pages", "500", "https://example.com"]):
            args = cli_main.parse_args()
        result = cli_main._merge_cli_config(args)
        assert result["cfg"].max_total_pages == 500

    def test_max_runtime_flag(self):
        with patch.object(sys, "argv", ["astrocrawl", "--max-runtime", "3600", "https://example.com"]):
            args = cli_main.parse_args()
        result = cli_main._merge_cli_config(args)
        assert result["cfg"].max_runtime_seconds == 3600

    def test_sitemap_flag(self):
        with patch.object(sys, "argv", ["astrocrawl", "--sitemap", "https://example.com"]):
            args = cli_main.parse_args()
        result = cli_main._merge_cli_config(args)
        assert result["cfg"].use_sitemap is True

    def test_no_sitemap_flag(self):
        with patch.object(sys, "argv", ["astrocrawl", "--no-sitemap", "https://example.com"]):
            args = cli_main.parse_args()
        result = cli_main._merge_cli_config(args)
        assert result["cfg"].use_sitemap is False

    def test_skip_duplicate_links_flag(self):
        with patch.object(sys, "argv", ["astrocrawl", "--skip-duplicate-links", "https://example.com"]):
            args = cli_main.parse_args()
        result = cli_main._merge_cli_config(args)
        assert result["cfg"].skip_duplicate_links is True

    def test_log_file_flag(self):
        with patch.object(sys, "argv", ["astrocrawl", "--log-file", "/tmp/crawl.log", "https://example.com"]):
            args = cli_main.parse_args()
        result = cli_main._merge_cli_config(args)
        assert result["cfg"].log_file == "/tmp/crawl.log"

    def test_rules_auto_update_flag(self):
        with patch.object(sys, "argv", ["astrocrawl", "--rules-auto-update", "https://example.com"]):
            args = cli_main.parse_args()
        result = cli_main._merge_cli_config(args)
        assert result["global_settings"].rules_auto_update is True

    def test_no_rules_auto_update_flag(self):
        with patch.object(sys, "argv", ["astrocrawl", "--no-rules-auto-update", "https://example.com"]):
            args = cli_main.parse_args()
        result = cli_main._merge_cli_config(args)
        assert result["global_settings"].rules_auto_update is False

    def test_same_domain_from_config_extra(self, tmp_path):
        config_path = tmp_path / "cfg.json"
        config_path.write_text(
            json.dumps(
                {
                    "urls": ["https://a.com"],
                    "same_domain_only": False,
                    "advanced": {"concurrency": 3},
                }
            ),
            encoding="utf-8",
        )
        with patch.object(sys, "argv", ["astrocrawl", "--config", str(config_path)]):
            args = cli_main.parse_args()
        result = cli_main._merge_cli_config(args)
        assert result["same_domain"] is False

    def test_respect_robots_from_config_extra(self, tmp_path):
        """--no-robots 未指定时从 config extra 读取 respect_robots。"""
        config_path = tmp_path / "cfg.json"
        config_path.write_text(
            json.dumps(
                {
                    "urls": ["https://a.com"],
                    "respect_robots": False,
                    "advanced": {"concurrency": 3},
                }
            ),
            encoding="utf-8",
        )
        with patch.object(sys, "argv", ["astrocrawl", "--config", str(config_path)]):
            args = cli_main.parse_args()
        result = cli_main._merge_cli_config(args)
        assert result["cfg"].robots_respect is False


class TestParseArgs:
    """parse_args — 边界路径补充。"""

    def test_mixed_urls_and_flags(self):
        """裸 URL 与标志混合输入。"""
        with patch.object(sys, "argv", ["astrocrawl", "https://a.com", "-d", "3", "https://b.com", "--same-domain"]):
            args = cli_main.parse_args()
            assert args.urls == ["https://a.com", "https://b.com"]
            assert args.depth == 3
            assert args.same_domain is True

    def test_subcommand_keyword_in_url(self):
        """URL 包含 'rules' 字符串但不作为子命令——应识别为 URL。"""
        with patch.object(sys, "argv", ["astrocrawl", "https://example.com/rules/page"]):
            args = cli_main.parse_args()
            assert args.subcommand is None
            assert args.urls == ["https://example.com/rules/page"]

    def test_empty_args(self):
        with patch.object(sys, "argv", ["astrocrawl"]):
            args = cli_main.parse_args()
            assert args.subcommand is None
            assert args.urls == []


class TestInstallSignalHandlers:
    """_install_signal_handlers — 信号处理器安装/清理。"""

    def test_install_and_cleanup(self):
        """安装信号处理器并验证清理函数可调用。"""

        async def _test():
            crawler = MagicMock()
            cleanup = cli_main._install_signal_handlers(crawler)
            assert callable(cleanup)
            cleanup()  # 不应抛异常
            # 二次清理也安全
            cleanup()

        asyncio.run(_test())

    def test_signal_triggers_stop(self):
        """SIGINT 触发 crawler.request_stop()。"""

        async def _test():
            loop = asyncio.get_running_loop()
            crawler = MagicMock()
            cleanup = cli_main._install_signal_handlers(crawler)

            # 模拟 SIGINT 触发
            for sig_name in (signal.SIGINT, signal.SIGTERM):
                try:
                    handle = loop._signal_handlers[sig_name]
                    handle._callback(*handle._args)
                except (KeyError, NotImplementedError, AttributeError):
                    pass

            crawler.request_stop.assert_called()
            # 二次信号不重复调用
            call_count = crawler.request_stop.call_count
            for sig_name in (signal.SIGINT, signal.SIGTERM):
                try:
                    handle = loop._signal_handlers[sig_name]
                    handle._callback(*handle._args)
                except (KeyError, NotImplementedError, AttributeError):
                    pass
            assert crawler.request_stop.call_count == call_count

            cleanup()

        asyncio.run(_test())

    def test_signal_cleanup_removes_handlers(self):
        """清理后信号处理器被移除。"""

        async def _test():
            loop = asyncio.get_running_loop()
            crawler = MagicMock()
            cleanup = cli_main._install_signal_handlers(crawler)
            cleanup()

            for sig_name in (signal.SIGINT, signal.SIGTERM):
                assert sig_name not in loop._signal_handlers

        asyncio.run(_test())

    def test_signal_add_raises_not_implemented(self):
        """add_signal_handler 抛出 NotImplementedError 时优雅跳过。"""

        async def _test():
            loop = asyncio.get_running_loop()
            crawler = MagicMock()
            with patch.object(loop, "add_signal_handler", side_effect=NotImplementedError):
                cleanup = cli_main._install_signal_handlers(crawler)
            assert callable(cleanup)
            cleanup()

        asyncio.run(_test())

    def test_signal_remove_raises_runtime_error(self):
        """remove_signal_handler 抛出 RuntimeError 时清理不崩溃。"""

        async def _test():
            loop = asyncio.get_running_loop()
            crawler = MagicMock()
            cleanup = cli_main._install_signal_handlers(crawler)
            with patch.object(loop, "remove_signal_handler", side_effect=RuntimeError):
                cleanup()

        asyncio.run(_test())


class TestMainCliCrawl:
    """main_cli — crawl 执行路径烟雾测试。"""

    def test_crawl_path_runs_without_error(self, tmp_path, monkeypatch):
        """验证 crawl 路径可无错误地完成（模拟爬虫不做实际抓取）。"""
        from astrocrawl.config import GlobalSettings

        output = tmp_path / "out.jsonl"
        monkeypatch.setattr(sys, "argv", ["astrocrawl", "-d", "1", "-c", "1", "-o", str(output), "https://example.com"])

        with patch.object(GlobalSettings, "from_preferences", return_value=GlobalSettings()):
            with patch("astrocrawl.cli.main.create_crawler") as mock_create:
                mock_crawler = MagicMock()
                mock_crawler.run = AsyncMock()
                mock_crawler.output_path = str(output)
                mock_crawler.last_report = None
                mock_crawler.reporter = None
                mock_create.return_value = mock_crawler

                with patch("astrocrawl.cli.main.setup_root_logger"):
                    cli_main.main_cli()

                mock_create.assert_called_once()
                mock_crawler.run.assert_called_once()

    def test_crawl_path_keyboard_interrupt(self, monkeypatch):
        """KeyboardInterrupt 被优雅捕获而不传播。"""
        monkeypatch.setattr(sys, "argv", ["astrocrawl", "https://example.com"])

        from astrocrawl.config import GlobalSettings

        with patch.object(GlobalSettings, "from_preferences", return_value=GlobalSettings()):
            with patch("astrocrawl.cli.main.create_crawler") as mock_create:
                mock_crawler = MagicMock()
                mock_crawler.run = AsyncMock(side_effect=KeyboardInterrupt)
                mock_create.return_value = mock_crawler

                with patch("astrocrawl.cli.main.setup_root_logger"):
                    cli_main.main_cli()

    def test_crawl_path_reporter_summary(self, tmp_path, monkeypatch):
        """有 reporter 时输出摘要。"""
        output = tmp_path / "out.jsonl"
        monkeypatch.setattr(sys, "argv", ["astrocrawl", "-o", str(output), "https://example.com"])

        from astrocrawl.config import GlobalSettings

        with patch.object(GlobalSettings, "from_preferences", return_value=GlobalSettings()):
            with patch("astrocrawl.cli.main.create_crawler") as mock_create:
                mock_crawler = MagicMock()
                mock_crawler.run = AsyncMock()
                mock_crawler.output_path = str(output)
                mock_crawler.last_report = {"total_urls": 1}
                mock_crawler.reporter = MagicMock()
                mock_create.return_value = mock_crawler

                with patch("astrocrawl.cli.main.setup_root_logger"):
                    cli_main.main_cli()

                mock_crawler.reporter.print_summary.assert_called_once_with(str(output), {"total_urls": 1})

    def test_crawl_path_no_urls_early_return(self, monkeypatch):
        """无 URL 时 _merge_cli_config 返回 None → main_cli 直接返回。"""
        monkeypatch.setattr(sys, "argv", ["astrocrawl"])

        from astrocrawl.config import GlobalSettings

        with patch.object(GlobalSettings, "from_preferences", return_value=GlobalSettings()):
            with patch("astrocrawl.cli.main.create_crawler") as mock_create:
                cli_main.main_cli()
                mock_create.assert_not_called()

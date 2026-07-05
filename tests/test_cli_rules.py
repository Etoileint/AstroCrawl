"""特征测试：CLI rules 子命令 — 参数解析 + 各子命令路径。

测试文件覆盖 issue #125 rules 组的核心验收标准。
"""

from __future__ import annotations

import json
import sys
from unittest.mock import patch

import pytest

import astrocrawl.cli.main as cli_main
from astrocrawl.config import CrawlerConfig


class TestRulesArgParsing:
    """参数解析：各子命令正确路由。"""

    def test_rules_list_default(self):
        with patch.object(sys, "argv", ["astrocrawl", "rules", "list"]):
            args = cli_main.parse_args()
            assert args.subcommand == "rules"
            assert args.rules_action == "list"

    def test_rules_list_json_format(self):
        with patch.object(sys, "argv", ["astrocrawl", "rules", "list", "--format", "json"]):
            args = cli_main.parse_args()
            assert args.format == "json"

    def test_rules_show(self):
        with patch.object(sys, "argv", ["astrocrawl", "rules", "show", "my_rule"]):
            args = cli_main.parse_args()
            assert args.rules_action == "show"
            assert args.name == "my_rule"

    def test_rules_validate_name(self):
        with patch.object(sys, "argv", ["astrocrawl", "rules", "validate", "--name", "test"]):
            args = cli_main.parse_args()
            assert args.rules_action == "validate"
            assert args.name == "test"

    def test_rules_validate_all(self):
        with patch.object(sys, "argv", ["astrocrawl", "rules", "validate", "--all"]):
            args = cli_main.parse_args()
            assert args.all is True

    def test_rules_enable(self):
        with patch.object(sys, "argv", ["astrocrawl", "rules", "enable", "my_rule"]):
            args = cli_main.parse_args()
            assert args.rules_action == "enable"
            assert args.name == ["my_rule"]
            assert not args.all
            assert not args.dry_run

    def test_rules_enable_multiple(self):
        with patch.object(sys, "argv", ["astrocrawl", "rules", "enable", "a", "b", "c"]):
            args = cli_main.parse_args()
            assert args.name == ["a", "b", "c"]

    def test_rules_enable_all(self):
        with patch.object(sys, "argv", ["astrocrawl", "rules", "enable", "--all"]):
            args = cli_main.parse_args()
            assert args.name == []
            assert args.all

    def test_rules_disable(self):
        with patch.object(sys, "argv", ["astrocrawl", "rules", "disable", "my_rule"]):
            args = cli_main.parse_args()
            assert args.rules_action == "disable"

    def test_rules_export(self):
        with patch.object(sys, "argv", ["astrocrawl", "rules", "export", "my_rule"]):
            args = cli_main.parse_args()
            assert args.rules_action == "export"

    def test_rules_export_with_output(self):
        with patch.object(sys, "argv", ["astrocrawl", "rules", "export", "my_rule", "-o", "/tmp/out.json"]):
            args = cli_main.parse_args()
            assert args.output == "/tmp/out.json"

    def test_rules_import_cmd(self):
        with patch.object(sys, "argv", ["astrocrawl", "rules", "import", "/tmp/rule.json"]):
            args = cli_main.parse_args()
            assert args.rules_action == "import"
            assert args.file == "/tmp/rule.json"
            assert args.overwrite is False

    def test_rules_import_overwrite(self):
        with patch.object(sys, "argv", ["astrocrawl", "rules", "import", "/tmp/rule.json", "--overwrite"]):
            args = cli_main.parse_args()
            assert args.overwrite is True

    def test_rules_export_all(self):
        with patch.object(sys, "argv", ["astrocrawl", "rules", "export-all", "-o", "/tmp/export"]):
            args = cli_main.parse_args()
            assert args.rules_action == "export-all"
            assert args.output == "/tmp/export"

    def test_rules_reset(self):
        with patch.object(sys, "argv", ["astrocrawl", "rules", "reset", "--confirm"]):
            args = cli_main.parse_args()
            assert args.rules_action == "reset"
            assert args.confirm is True

    def test_rules_reset_requires_confirm(self):
        """reset 必须带 --confirm。"""
        with pytest.raises(SystemExit):
            with patch.object(sys, "argv", ["astrocrawl", "rules", "reset"]):
                cli_main.parse_args()

    def test_rules_generate_basic(self):
        with patch.object(
            sys,
            "argv",
            [
                "astrocrawl",
                "rules",
                "generate",
                "--url",
                "https://example.com",
                "--fields",
                "title,price",
                "--html",
                "<html></html>",
            ],
        ):
            args = cli_main.parse_args()
            assert args.rules_action == "generate"
            assert args.url == "https://example.com"
            assert args.fields == "title,price"
            assert args.html == "<html></html>"

    def test_rules_generate_with_html_file(self):
        with patch.object(
            sys,
            "argv",
            [
                "astrocrawl",
                "rules",
                "generate",
                "--url",
                "https://example.com",
                "--fields",
                "title",
                "--html-file",
                "/tmp/page.html",
            ],
        ):
            args = cli_main.parse_args()
            assert args.html_file == "/tmp/page.html"
            assert args.html is None

    def test_rules_generate_no_save(self):
        with patch.object(
            sys,
            "argv",
            [
                "astrocrawl",
                "rules",
                "generate",
                "--url",
                "https://example.com",
                "--fields",
                "title",
                "--html",
                "<html></html>",
                "--no-save",
            ],
        ):
            args = cli_main.parse_args()
            assert args.no_save is True

    def test_rules_generate_with_output(self):
        with patch.object(
            sys,
            "argv",
            [
                "astrocrawl",
                "rules",
                "generate",
                "--url",
                "https://example.com",
                "--fields",
                "title",
                "--html",
                "<html></html>",
                "-o",
                "/tmp/my_rule.json",
            ],
        ):
            args = cli_main.parse_args()
            assert args.output == "/tmp/my_rule.json"

    def test_rules_generate_with_overwrite(self):
        with patch.object(
            sys,
            "argv",
            [
                "astrocrawl",
                "rules",
                "generate",
                "--url",
                "https://example.com",
                "--fields",
                "title",
                "--html",
                "<html></html>",
                "--overwrite",
            ],
        ):
            args = cli_main.parse_args()
            assert args.overwrite is True

    def test_rules_generate_with_model_params(self):
        with patch.object(
            sys,
            "argv",
            [
                "astrocrawl",
                "rules",
                "generate",
                "--url",
                "https://example.com",
                "--fields",
                "title",
                "--html",
                "<html></html>",
                "--model",
                "gpt-4.5",
                "--temperature",
                "0.3",
                "--max-tokens",
                "4096",
                "--api-key",
                "sk-test",
                "--endpoint",
                "https://api.example.com/v1",
            ],
        ):
            args = cli_main.parse_args()
            assert args.model == "gpt-4.5"
            assert args.temperature == 0.3
            assert args.max_tokens == 4096
            assert args.api_key == "sk-test"
            assert args.endpoint == "https://api.example.com/v1"

    def test_rules_generate_requires_url(self):
        with pytest.raises(SystemExit):
            with patch.object(
                sys,
                "argv",
                [
                    "astrocrawl",
                    "rules",
                    "generate",
                    "--fields",
                    "title",
                    "--html",
                    "<html></html>",
                ],
            ):
                cli_main.parse_args()

    def test_rules_generate_requires_fields(self):
        with pytest.raises(SystemExit):
            with patch.object(
                sys,
                "argv",
                [
                    "astrocrawl",
                    "rules",
                    "generate",
                    "--url",
                    "https://example.com",
                    "--html",
                    "<html></html>",
                ],
            ):
                cli_main.parse_args()

    def test_rules_generate_requires_html_source(self):
        with pytest.raises(SystemExit):
            with patch.object(
                sys,
                "argv",
                [
                    "astrocrawl",
                    "rules",
                    "generate",
                    "--url",
                    "https://example.com",
                    "--fields",
                    "title",
                ],
            ):
                cli_main.parse_args()

    def test_rules_generate_output_and_no_save_mutex(self):
        with pytest.raises(SystemExit):
            with patch.object(
                sys,
                "argv",
                [
                    "astrocrawl",
                    "rules",
                    "generate",
                    "--url",
                    "https://example.com",
                    "--fields",
                    "title",
                    "--html",
                    "<html></html>",
                    "-o",
                    "/tmp/out.json",
                    "--no-save",
                ],
            ):
                cli_main.parse_args()


class TestRulesCommands:
    """rules 子命令实际执行。"""

    def test_rules_list(self, capsys):
        with patch.object(sys, "argv", ["astrocrawl", "rules", "list"]):
            args = cli_main.parse_args()
            cli_main._handle_rules(args)
        out = capsys.readouterr().out
        assert "Name" in out or "名称" in out or "[]" in out

    def test_rules_list_json(self, capsys):
        with patch.object(sys, "argv", ["astrocrawl", "rules", "list", "--format", "json"]):
            args = cli_main.parse_args()
            cli_main._handle_rules(args)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert isinstance(data, list)

    def test_rules_show_missing(self, capsys):
        with patch.object(sys, "argv", ["astrocrawl", "rules", "show", "nonexistent_rule_xyz"]):
            args = cli_main.parse_args()
            with pytest.raises(SystemExit):
                cli_main._handle_rules(args)

    def test_rules_validate_all(self, capsys):
        with patch.object(sys, "argv", ["astrocrawl", "rules", "validate", "--all"]):
            args = cli_main.parse_args()
            cli_main._handle_rules(args)
        out = capsys.readouterr().out
        assert "总计" in out
        assert "通过" in out or "失败" in out or "跳过" in out

    def test_rules_validate_all_json(self, capsys):
        with patch.object(sys, "argv", ["astrocrawl", "rules", "validate", "--all", "--format", "json"]):
            args = cli_main.parse_args()
            cli_main._handle_rules(args)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert isinstance(data, list)

    def test_rules_validate_all_with_extra_dirs(self, capsys, tmp_path):
        """validate_rule_files 扫描 extra_rules_dirs 中的规则文件。"""
        import json as _json

        from astrocrawl.rules._loader import validate_rule_files

        extra_dir = tmp_path / "extra_rules"
        extra_dir.mkdir()
        rule_file = extra_dir / "extra_rule.json"
        rule_file.write_text(
            _json.dumps(
                {
                    "name": "extra_test_rule",
                    "version": 1,
                    "schema_version": 1,
                    "enabled": True,
                    "fields": {"title": {"selector": "h1"}},
                }
            ),
            encoding="utf-8",
        )

        results = validate_rule_files(CrawlerConfig(), extra_rules_dirs=[str(extra_dir)])
        names = [r["name"] for r in results if r["status"] == "pass"]
        assert "extra_test_rule" in names, f"extra dir rule not found in validate results: {names}"

    def test_rules_validate_name(self, capsys):
        """校验单个已加载规则。"""
        from astrocrawl.rules._loader import build_rule_snapshot

        snapshot = build_rule_snapshot(CrawlerConfig())
        if not snapshot.rules:
            pytest.skip("没有加载的规则")
        rule_name = snapshot.rules[0].name
        with patch.object(sys, "argv", ["astrocrawl", "rules", "validate", "--name", rule_name]):
            args = cli_main.parse_args()
            cli_main._handle_rules(args)
        out = capsys.readouterr().out
        assert "格式有效" in out

    def test_rules_validate_name_missing(self, capsys):
        with patch.object(sys, "argv", ["astrocrawl", "rules", "validate", "--name", "nonexistent_rule_xyz"]):
            args = cli_main.parse_args()
            with pytest.raises(SystemExit):
                cli_main._handle_rules(args)

    def test_rules_export_missing_rule(self, capsys):
        with patch.object(sys, "argv", ["astrocrawl", "rules", "export", "nonexistent"]):
            args = cli_main.parse_args()
            with pytest.raises(SystemExit):
                cli_main._handle_rules(args)


class TestCrawlOptions:
    """S7 新增爬取选项。"""

    def test_trace_rules_flag(self):
        with patch.object(sys, "argv", ["astrocrawl", "--trace-rules", "https://example.com"]):
            args = cli_main.parse_args()
            assert args.trace_rules is True

    def test_rules_auto_update(self):
        with patch.object(sys, "argv", ["astrocrawl", "--rules-auto-update", "https://example.com"]):
            args = cli_main.parse_args()
            assert args.rules_auto_update is True

    def test_no_rules_auto_update(self):
        with patch.object(sys, "argv", ["astrocrawl", "--no-rules-auto-update", "https://example.com"]):
            args = cli_main.parse_args()
            assert args.rules_auto_update is False

    def test_rules_dir_append(self):
        with patch.object(
            sys,
            "argv",
            [
                "astrocrawl",
                "--rules-dir",
                "/tmp/r1",
                "--rules-dir",
                "/tmp/r2",
                "https://example.com",
            ],
        ):
            args = cli_main.parse_args()
            assert args.rules_dir == ["/tmp/r1", "/tmp/r2"]

    def test_backward_compat_no_subcommand(self):
        """N85: 无子命令时保持原行为。"""
        with patch.object(sys, "argv", ["astrocrawl", "https://example.com"]):
            args = cli_main.parse_args()
            assert args.subcommand is None
            assert args.urls == ["https://example.com"]


class TestParseSetValue:
    """_parse_set_value 类型解析单元测试。"""

    def test_int(self):
        from astrocrawl.cli.main import _parse_set_value

        assert _parse_set_value("page_timeout", "30000") == ("cfg", 30000)

    def test_float(self):
        from astrocrawl.cli.main import _parse_set_value

        assert _parse_set_value("domain_min_delay", "2.5") == ("cfg", 2.5)

    def test_bool_true(self):
        from astrocrawl.cli.main import _parse_set_value

        for v in ("true", "True", "1", "yes", "on"):
            assert _parse_set_value("output_gzip", v) == ("gs", True)

    def test_bool_false(self):
        from astrocrawl.cli.main import _parse_set_value

        for v in ("false", "False", "0", "no", "off"):
            assert _parse_set_value("output_gzip", v) == ("gs", False)

    def test_str(self):
        from astrocrawl.cli.main import _parse_set_value

        assert _parse_set_value("user_agent", "TestBot") == ("cfg", "TestBot")

    def test_log_level_name(self):
        import logging

        from astrocrawl.cli.main import _parse_set_value

        assert _parse_set_value("log_level", "DEBUG") == ("gs", logging.DEBUG)
        assert _parse_set_value("log_level", "WARNING") == ("gs", logging.WARNING)

    def test_log_level_int(self):
        from astrocrawl.cli.main import _parse_set_value

        assert _parse_set_value("log_level", "20") == ("gs", 20)

    def test_unknown_field(self):
        import pytest

        from astrocrawl.cli.main import _parse_set_value

        with pytest.raises(ValueError, match="Unknown config field"):
            _parse_set_value("no_such_field", "val")

    def test_complex_type_rejected(self):
        import pytest

        from astrocrawl.cli.main import _parse_set_value

        with pytest.raises(ValueError, match="compound type"):
            _parse_set_value("rules_sources", "[{}]")

    def test_invalid_bool(self):
        import pytest

        from astrocrawl.cli.main import _parse_set_value

        with pytest.raises(ValueError, match="bool type"):
            _parse_set_value("output_gzip", "maybe")


class TestRulesEditParsing:
    """rules edit 参数解析测试。"""

    def test_edit_routing(self):
        with patch.object(sys, "argv", ["astrocrawl", "rules", "edit", "myrule"]):
            args = cli_main.parse_args()
            assert args.rules_action == "edit"
            assert args.name == "myrule"


class TestRulesGenerateProfile:
    """rules generate --profile 参数解析测试。"""

    def test_generate_with_profile(self):
        with patch.object(
            sys,
            "argv",
            [
                "astrocrawl",
                "rules",
                "generate",
                "--url",
                "https://x.com",
                "--fields",
                "a",
                "--html",
                "<p>",
                "--profile",
                "prod",
            ],
        ):
            args = cli_main.parse_args()
            assert args.rules_action == "generate"
            assert args.profile == "prod"

    def test_generate_without_profile(self):
        with patch.object(
            sys,
            "argv",
            ["astrocrawl", "rules", "generate", "--url", "https://x.com", "--fields", "a", "--html", "<p>"],
        ):
            args = cli_main.parse_args()
            assert args.profile is None

    # ── --output-format ──

    @pytest.mark.parametrize(
        ("flag_value", "expected"),
        [
            ("auto", "auto"),
            ("json_schema", "json_schema"),
            ("json_object", "json_object"),
            ("off", "off"),
        ],
    )
    def test_generate_output_format_values(self, flag_value, expected):
        with patch.object(
            sys,
            "argv",
            [
                "astrocrawl",
                "rules",
                "generate",
                "--url",
                "https://x.com",
                "--fields",
                "a",
                "--html",
                "<p>",
                "--output-format",
                flag_value,
            ],
        ):
            args = cli_main.parse_args()
            assert args.output_format == expected

    def test_generate_output_format_default(self):
        with patch.object(
            sys,
            "argv",
            ["astrocrawl", "rules", "generate", "--url", "https://x.com", "--fields", "a", "--html", "<p>"],
        ):
            args = cli_main.parse_args()
            assert args.output_format == "auto"

    def test_generate_output_format_invalid(self):
        with patch.object(
            sys,
            "argv",
            [
                "astrocrawl",
                "rules",
                "generate",
                "--url",
                "https://x.com",
                "--fields",
                "a",
                "--html",
                "<p>",
                "--output-format",
                "invalid",
            ],
        ):
            with pytest.raises(SystemExit):
                cli_main.parse_args()

    # ── --rule-mode ──

    @pytest.mark.parametrize(
        ("flag_value", "expected"),
        [
            ("type", "type"),
            ("position", "position"),
        ],
    )
    def test_generate_rule_mode_values(self, flag_value, expected):
        with patch.object(
            sys,
            "argv",
            [
                "astrocrawl",
                "rules",
                "generate",
                "--url",
                "https://x.com",
                "--fields",
                "a",
                "--html",
                "<p>",
                "--rule-mode",
                flag_value,
            ],
        ):
            args = cli_main.parse_args()
            assert args.rule_mode == expected

    def test_generate_rule_mode_default(self):
        with patch.object(
            sys,
            "argv",
            ["astrocrawl", "rules", "generate", "--url", "https://x.com", "--fields", "a", "--html", "<p>"],
        ):
            args = cli_main.parse_args()
            assert args.rule_mode == "type"

    def test_generate_rule_mode_invalid(self):
        with patch.object(
            sys,
            "argv",
            [
                "astrocrawl",
                "rules",
                "generate",
                "--url",
                "https://x.com",
                "--fields",
                "a",
                "--html",
                "<p>",
                "--rule-mode",
                "invalid",
            ],
        ):
            with pytest.raises(SystemExit):
                cli_main.parse_args()


class TestSetFlagParsing:
    """--set flag 参数解析测试。"""

    def test_set_single(self):
        with patch.object(sys, "argv", ["astrocrawl", "--set", "page_timeout=30000", "https://x.com"]):
            args = cli_main.parse_args()
            assert args.set_overrides == ["page_timeout=30000"]

    def test_set_multiple(self):
        with patch.object(
            sys,
            "argv",
            ["astrocrawl", "--set", "page_timeout=30000", "--set", "output_gzip=false", "https://x.com"],
        ):
            args = cli_main.parse_args()
            assert args.set_overrides == ["page_timeout=30000", "output_gzip=false"]

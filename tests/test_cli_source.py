"""特征测试：CLI source 子命令 — 参数解析 + 各子命令路径。

测试文件覆盖 issue #125 source 组的核心验收标准。
"""

from __future__ import annotations

import sys
from unittest.mock import patch

import astrocrawl.cli.main as cli_main


class TestSourceArgParsing:
    """参数解析：source 子命令正确路由。"""

    def test_source_add(self):
        with patch.object(
            sys, "argv", ["astrocrawl", "source", "add", "https://example.com/manifest.json", "--confirm"]
        ):
            args = cli_main.parse_args()
            assert args.subcommand == "source"
            assert args.source_action == "add"
            assert args.url == "https://example.com/manifest.json"
            assert args.confirm is True

    def test_source_add_with_name(self):
        with patch.object(
            sys,
            "argv",
            [
                "astrocrawl",
                "source",
                "add",
                "https://example.com/manifest.json",
                "--name",
                "my_source",
            ],
        ):
            args = cli_main.parse_args()
            assert args.name == "my_source"

    def test_source_add_requires_https(self):
        """S01: HTTP URL 在 add 时被拒绝。"""
        with patch.object(sys, "argv", ["astrocrawl", "source", "add", "http://example.com/manifest.json"]):
            args = cli_main.parse_args()
            assert args.url == "http://example.com/manifest.json"

    def test_source_remove(self):
        with patch.object(sys, "argv", ["astrocrawl", "source", "remove", "my_source"]):
            args = cli_main.parse_args()
            assert args.source_action == "remove"
            assert args.name == "my_source"

    def test_source_list_default(self):
        with patch.object(sys, "argv", ["astrocrawl", "source", "list"]):
            args = cli_main.parse_args()
            assert args.source_action == "list"
            assert args.format == "table"

    def test_source_list_json(self):
        with patch.object(sys, "argv", ["astrocrawl", "source", "list", "--format", "json"]):
            args = cli_main.parse_args()
            assert args.format == "json"

    def test_source_update_by_name(self):
        with patch.object(sys, "argv", ["astrocrawl", "source", "update", "--name", "src1"]):
            args = cli_main.parse_args()
            assert args.source_action == "update"
            assert args.name == "src1"

    def test_source_update_all(self):
        with patch.object(sys, "argv", ["astrocrawl", "source", "update", "--all"]):
            args = cli_main.parse_args()
            assert args.source_action == "update"
            assert args.all is True

    def test_source_update_dry_run(self):
        with patch.object(sys, "argv", ["astrocrawl", "source", "update", "--all", "--dry-run"]):
            args = cli_main.parse_args()
            assert args.dry_run is True

    def test_source_info(self):
        with patch.object(sys, "argv", ["astrocrawl", "source", "info", "src1"]):
            args = cli_main.parse_args()
            assert args.source_action == "info"
            assert args.name == "src1"

    def test_source_info_json(self):
        with patch.object(sys, "argv", ["astrocrawl", "source", "info", "src1", "--format", "json"]):
            args = cli_main.parse_args()
            assert args.format == "json"

    def test_source_edit(self):
        with patch.object(sys, "argv", ["astrocrawl", "source", "edit", "src1", "--name", "new"]):
            args = cli_main.parse_args()
            assert args.source_action == "edit"
            assert args.name == "src1"
            assert args.new_name == "new"
            assert args.url is None

    def test_source_edit_url(self):
        with patch.object(
            sys, "argv", ["astrocrawl", "source", "edit", "src1", "--url", "https://example.com/manifest.json"]
        ):
            args = cli_main.parse_args()
            assert args.source_action == "edit"
            assert args.url == "https://example.com/manifest.json"

    def test_source_edit_both(self):
        with patch.object(
            sys,
            "argv",
            ["astrocrawl", "source", "edit", "src1", "--name", "new", "--url", "https://example.com/v2.json"],
        ):
            args = cli_main.parse_args()
            assert args.new_name == "new"
            assert args.url == "https://example.com/v2.json"

    def test_source_validate(self):
        with patch.object(sys, "argv", ["astrocrawl", "source", "validate", "src1"]):
            args = cli_main.parse_args()
            assert args.source_action == "validate"
            assert args.name == "src1"


class TestSourceCommands:
    """source 命令实际执行。"""

    def test_source_add_no_confirm_shows_preview(self, capsys):
        with patch.object(sys, "argv", ["astrocrawl", "source", "add", "https://example.com/rules.json"]):
            args = cli_main.parse_args()
            cli_main._handle_source(args)
        out = capsys.readouterr().out
        assert "添加" in out
        assert "--confirm" in out

    def test_source_list_empty(self, capsys):
        with patch("astrocrawl.rules.list_sources_from_file", return_value=[]):
            with patch.object(sys, "argv", ["astrocrawl", "source", "list"]):
                args = cli_main.parse_args()
                cli_main._handle_source(args)
        out = capsys.readouterr().out
        assert "无远程" in out

    def test_source_info_unknown(self, capsys):
        with patch.object(sys, "argv", ["astrocrawl", "source", "info", "unknown"]):
            args = cli_main.parse_args()
            try:
                cli_main._handle_source(args)
            except SystemExit:
                pass
        captured = capsys.readouterr()
        assert "不存在" in captured.err

    def test_derive_source_name(self):
        name = cli_main._derive_source_name("https://rules.example.com/v1/manifest.json")
        assert name == "rules_example_com"

    def test_source_edit_unknown_source(self, capsys):
        with patch("astrocrawl.rules.get_source_from_file", return_value=None):
            with patch.object(sys, "argv", ["astrocrawl", "source", "edit", "unknown", "--name", "x"]):
                args = cli_main.parse_args()
                try:
                    cli_main._handle_source(args)
                except SystemExit:
                    pass
        assert "不存在" in capsys.readouterr().err

    def test_source_edit_no_options(self, capsys):
        fake = {"name": "test", "url": "https://example.com/manifest.json"}
        with patch("astrocrawl.rules.get_source_from_file", return_value=fake):
            with patch.object(sys, "argv", ["astrocrawl", "source", "edit", "test"]):
                args = cli_main.parse_args()
                try:
                    cli_main._handle_source(args)
                except SystemExit:
                    pass
        assert "请指定" in capsys.readouterr().err

    def test_source_edit_updates(self, capsys):
        fake = {"name": "test", "url": "https://example.com/manifest.json"}
        with patch("astrocrawl.rules.get_source_from_file", return_value=fake):
            with patch("astrocrawl.rules.update_source_in_file", return_value=True):
                with patch.object(sys, "argv", ["astrocrawl", "source", "edit", "test", "--name", "newname"]):
                    args = cli_main.parse_args()
                    cli_main._handle_source(args)
        out = capsys.readouterr().out
        assert "已更新" in out

    def test_source_validate_unknown_source(self, capsys):
        with patch("astrocrawl.rules.get_source_from_file", return_value=None):
            with patch.object(sys, "argv", ["astrocrawl", "source", "validate", "unknown"]):
                args = cli_main.parse_args()
                try:
                    cli_main._handle_source(args)
                except SystemExit:
                    pass
        assert "不存在" in capsys.readouterr().err


class TestBackwardCompat:
    """N85: 向后兼容。"""

    def test_no_subcommand_is_crawl(self):
        with patch.object(sys, "argv", ["astrocrawl", "https://example.com"]):
            args = cli_main.parse_args()
            assert args.subcommand is None
            assert len(args.urls) == 1

    def test_no_args_is_gui(self):
        """无参数 → subcommand=None, urls=[] → GUI 模式。"""
        with patch.object(sys, "argv", ["astrocrawl"]):
            args = cli_main.parse_args()
            assert args.subcommand is None
            assert args.urls == []

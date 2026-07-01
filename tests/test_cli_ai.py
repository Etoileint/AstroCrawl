"""特征测试：CLI ai 子命令 — 参数解析 + 各子命令路径。

覆盖 8.6 ai 6 子命令: add / list / remove / set-default / show / test。
"""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

import astrocrawl.cli.main as cli_main


class TestAIParseArgs:
    """ai profile 参数解析测试。"""

    def test_ai_add(self):
        with patch.object(sys, "argv", ["astrocrawl", "ai", "profile", "add", "test-profile", "--provider", "openai"]):
            args = cli_main.parse_args()
            assert args.subcommand == "ai"
            assert args.ai_action == "profile"
            assert args.ai_profile_action == "add"
            assert args.name == "test-profile"
            assert args.provider == "openai"

    def test_ai_add_all_options(self):
        with patch.object(
            sys,
            "argv",
            [
                "astrocrawl",
                "ai",
                "profile",
                "add",
                "full-profile",
                "--provider",
                "anthropic",
                "--model",
                "claude-opus",
                "--api-key",
                "sk-test123",
                "--endpoint",
                "https://api.example.com",
                "--temperature",
                "0.5",
                "--max-tokens",
                "4096",
            ],
        ):
            args = cli_main.parse_args()
            assert args.ai_profile_action == "add"
            assert args.name == "full-profile"
            assert args.provider == "anthropic"
            assert args.model == "claude-opus"
            assert args.api_key == "sk-test123"
            assert args.endpoint == "https://api.example.com"
            assert args.temperature == 0.5
            assert args.max_tokens == 4096

    def test_ai_add_defaults(self):
        with patch.object(sys, "argv", ["astrocrawl", "ai", "profile", "add", "minimal"]):
            args = cli_main.parse_args()
            assert args.provider == "openai"
            assert args.model == "gpt-4o-mini"
            assert args.temperature == 0.1
            assert args.max_tokens == 2048
            assert args.api_key == ""
            assert args.endpoint == ""

    def test_ai_list(self):
        with patch.object(sys, "argv", ["astrocrawl", "ai", "profile", "list"]):
            args = cli_main.parse_args()
            assert args.ai_profile_action == "list"

    def test_ai_remove(self):
        with patch.object(sys, "argv", ["astrocrawl", "ai", "profile", "remove", "old-profile"]):
            args = cli_main.parse_args()
            assert args.ai_profile_action == "remove"
            assert args.name == "old-profile"

    def test_ai_set_default(self):
        with patch.object(sys, "argv", ["astrocrawl", "ai", "profile", "set-default", "my-profile"]):
            args = cli_main.parse_args()
            assert args.ai_profile_action == "set-default"
            assert args.name == "my-profile"

    def test_ai_show(self):
        with patch.object(sys, "argv", ["astrocrawl", "ai", "profile", "show", "my-profile"]):
            args = cli_main.parse_args()
            assert args.ai_profile_action == "show"
            assert args.name == "my-profile"

    def test_ai_test(self):
        with patch.object(sys, "argv", ["astrocrawl", "ai", "profile", "test", "my-profile"]):
            args = cli_main.parse_args()
            assert args.ai_profile_action == "test"
            assert args.name == "my-profile"


class TestAIProfileCommands:
    """ai profile 子命令实际执行（真实 Preferences + try/finally 清理）。"""

    @staticmethod
    def _make_profile(name="test_ai", provider="openai", model="gpt-4o-mini"):
        from astrocrawl.ai._profile import AIProfile

        return AIProfile(name=name, provider=provider, model=model, api_key="sk-test-key")

    # ── add ──

    def test_add_creates_profile(self, capsys):
        from astrocrawl.utils.preferences import get_preferences

        prefs = get_preferences()
        if prefs.get_ai_profile("test_add"):
            prefs.remove_ai_profile("test_add")

        try:
            with patch.object(sys, "argv", ["astrocrawl", "ai", "profile", "add", "test_add", "--provider", "openai"]):
                args = cli_main.parse_args()
                cli_main._handle_ai(args)
            out = capsys.readouterr().out
            assert "test_add" in out
            saved = prefs.get_ai_profile("test_add")
            assert saved is not None
            assert saved.provider == "openai"
        finally:
            prefs.remove_ai_profile("test_add")

    def test_add_duplicate_name_error(self, capsys):
        from astrocrawl.utils.preferences import get_preferences

        prefs = get_preferences()
        profile = self._make_profile("test_dup_ai")
        prefs.save_ai_profile(profile)
        try:
            with patch.object(sys, "argv", ["astrocrawl", "ai", "profile", "add", "test_dup_ai"]):
                args = cli_main.parse_args()
                with pytest.raises(SystemExit):
                    cli_main._handle_ai(args)
            captured = capsys.readouterr()
            assert "already exists" in captured.err or "已存在" in captured.err
        finally:
            prefs.remove_ai_profile("test_dup_ai")

    # ── list ──

    def test_list_empty(self, capsys):
        prefs_path = "astrocrawl.utils.preferences.get_preferences"
        with patch(prefs_path) as mock_prefs:
            mock_prefs.return_value.get_ai_profiles.return_value = []
            mock_prefs.return_value.get_active_profile_name.return_value = ""
            with patch.object(sys, "argv", ["astrocrawl", "ai", "profile", "list"]):
                args = cli_main.parse_args()
                cli_main._handle_ai(args)
            assert "(no AI profiles)" in capsys.readouterr().out

    def test_list_with_profiles(self, capsys):
        from astrocrawl.utils.preferences import get_preferences

        prefs = get_preferences()
        profile = self._make_profile("test_list_ai")
        prefs.save_ai_profile(profile)
        try:
            with patch.object(sys, "argv", ["astrocrawl", "ai", "profile", "list"]):
                args = cli_main.parse_args()
                cli_main._handle_ai(args)
            out = capsys.readouterr().out
            assert "test_list_ai" in out
            assert "openai" in out
        finally:
            prefs.remove_ai_profile("test_list_ai")

    # ── remove ──

    def test_remove_existing(self, capsys):
        from astrocrawl.utils.preferences import get_preferences

        prefs = get_preferences()
        profile = self._make_profile("test_rm_ai")
        prefs.save_ai_profile(profile)
        try:
            with patch.object(sys, "argv", ["astrocrawl", "ai", "profile", "remove", "test_rm_ai"]):
                args = cli_main.parse_args()
                cli_main._handle_ai(args)
            captured = capsys.readouterr()
            assert "deleted" in captured.out or "已删除" in captured.out
            assert prefs.get_ai_profile("test_rm_ai") is None
        finally:
            prefs.remove_ai_profile("test_rm_ai")

    def test_remove_nonexistent(self, capsys):
        with patch.object(sys, "argv", ["astrocrawl", "ai", "profile", "remove", "nonexistent_xyz"]):
            args = cli_main.parse_args()
            with pytest.raises(SystemExit):
                cli_main._handle_ai(args)
        captured = capsys.readouterr()
        assert "does not exist" in captured.err or "不存在" in captured.err

    # ── set-default ──

    def test_set_default_existing(self, capsys):
        from astrocrawl.utils.preferences import get_preferences

        prefs = get_preferences()
        profile = self._make_profile("test_sd_ai")
        prefs.save_ai_profile(profile)
        try:
            with patch.object(sys, "argv", ["astrocrawl", "ai", "profile", "set-default", "test_sd_ai"]):
                args = cli_main.parse_args()
                cli_main._handle_ai(args)
            out = capsys.readouterr().out
            assert "test_sd_ai" in out
        finally:
            prefs.remove_ai_profile("test_sd_ai")

    def test_set_default_nonexistent(self, capsys):
        with patch.object(sys, "argv", ["astrocrawl", "ai", "profile", "set-default", "nonexistent_xyz"]):
            args = cli_main.parse_args()
            with pytest.raises(SystemExit):
                cli_main._handle_ai(args)
        captured = capsys.readouterr()
        assert "does not exist" in captured.err or "不存在" in captured.err

    # ── show ──

    def test_show_existing(self, capsys):
        from astrocrawl.utils.preferences import get_preferences

        prefs = get_preferences()
        profile = self._make_profile("test_show_ai")
        prefs.save_ai_profile(profile)
        try:
            with patch.object(sys, "argv", ["astrocrawl", "ai", "profile", "show", "test_show_ai"]):
                args = cli_main.parse_args()
                cli_main._handle_ai(args)
            out = capsys.readouterr().out
            assert "test_show_ai" in out
            assert "openai" in out
            # API key should be masked
            assert "sk-test-key" not in out
            assert "sk-test-..." in out
        finally:
            prefs.remove_ai_profile("test_show_ai")

    def test_show_nonexistent(self, capsys):
        with patch.object(sys, "argv", ["astrocrawl", "ai", "profile", "show", "nonexistent_xyz"]):
            args = cli_main.parse_args()
            with pytest.raises(SystemExit):
                cli_main._handle_ai(args)
        captured = capsys.readouterr()
        assert "does not exist" in captured.err or "不存在" in captured.err

    # ── test ──

    def test_test_nonexistent_profile(self, capsys):
        with patch.object(sys, "argv", ["astrocrawl", "ai", "profile", "test", "nonexistent_xyz"]):
            args = cli_main.parse_args()
            with pytest.raises(SystemExit):
                cli_main._handle_ai(args)
        captured = capsys.readouterr()
        assert "does not exist" in captured.err or "不存在" in captured.err

    def test_test_no_api_key(self, capsys):
        from astrocrawl.utils.preferences import get_preferences

        prefs = get_preferences()
        from astrocrawl.ai._profile import AIProfile

        profile = AIProfile(name="test_nokey", provider="openai", model="gpt-4o-mini")
        prefs.save_ai_profile(profile)
        try:
            with patch.object(sys, "argv", ["astrocrawl", "ai", "profile", "test", "test_nokey"]):
                args = cli_main.parse_args()
                with pytest.raises(SystemExit):
                    cli_main._handle_ai(args)
            captured = capsys.readouterr()
            assert "API Key" in captured.err or "API" in captured.err
        finally:
            prefs.remove_ai_profile("test_nokey")

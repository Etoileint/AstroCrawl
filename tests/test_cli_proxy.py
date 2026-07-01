"""特征测试：CLI proxy 子命令 — 参数解析 + 各子命令路径。

覆盖 8.5 proxy 6 子命令: add / list / remove / set / show / test。
"""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

import astrocrawl.cli.main as cli_main


class TestProxyArgParsing:
    """参数解析：proxy 子命令正确路由。"""

    def test_proxy_add(self):
        with patch.object(
            sys, "argv", ["astrocrawl", "proxy", "profile", "add", "myprofile", "--proxies", "http://1.2.3.4:8080"]
        ):
            args = cli_main.parse_args()
            assert args.subcommand == "proxy"
            assert args.profile_action == "add"
            assert args.name == "myprofile"
            assert args.proxies == ["http://1.2.3.4:8080"]

    def test_proxy_add_multiple_proxies(self):
        with patch.object(
            sys,
            "argv",
            ["astrocrawl", "proxy", "profile", "add", "multi", "--proxies", "http://a:8080", "socks5://b:1080"],
        ):
            args = cli_main.parse_args()
            assert args.proxies == ["http://a:8080", "socks5://b:1080"]

    def test_proxy_add_with_bypass(self):
        with patch.object(
            sys,
            "argv",
            [
                "astrocrawl",
                "proxy",
                "profile",
                "add",
                "p",
                "--proxies",
                "http://x:8080",
                "--bypass",
                ".internal",
                "192.168.*",
            ],
        ):
            args = cli_main.parse_args()
            assert args.bypass == [".internal", "192.168.*"]

    def test_proxy_add_requires_proxies(self):
        with pytest.raises(SystemExit):
            with patch.object(sys, "argv", ["astrocrawl", "proxy", "profile", "add", "no_proxy"]):
                cli_main.parse_args()

    def test_proxy_list(self):
        with patch.object(sys, "argv", ["astrocrawl", "proxy", "profile", "list"]):
            args = cli_main.parse_args()
            assert args.profile_action == "list"

    def test_proxy_remove(self):
        with patch.object(sys, "argv", ["astrocrawl", "proxy", "profile", "remove", "myprofile"]):
            args = cli_main.parse_args()
            assert args.profile_action == "remove"
            assert args.name == "myprofile"

    def test_proxy_set(self):
        with patch.object(sys, "argv", ["astrocrawl", "proxy", "profile", "set", "p", "--consumer", "ai"]):
            args = cli_main.parse_args()
            assert args.profile_action == "set"
            assert args.name == "p"
            assert args.consumer == "ai"

    def test_proxy_set_with_node(self):
        with patch.object(
            sys,
            "argv",
            ["astrocrawl", "proxy", "profile", "set", "p", "--consumer", "preview", "--node", "http:1.2.3.4:8080"],
        ):
            args = cli_main.parse_args()
            assert args.consumer == "preview"
            assert args.node == "http:1.2.3.4:8080"

    def test_proxy_set_requires_consumer(self):
        with pytest.raises(SystemExit):
            with patch.object(sys, "argv", ["astrocrawl", "proxy", "profile", "set", "p"]):
                cli_main.parse_args()

    def test_proxy_show(self):
        with patch.object(sys, "argv", ["astrocrawl", "proxy", "profile", "show", "p"]):
            args = cli_main.parse_args()
            assert args.profile_action == "show"
            assert args.name == "p"

    def test_proxy_test(self):
        with patch.object(sys, "argv", ["astrocrawl", "proxy", "profile", "test", "p"]):
            args = cli_main.parse_args()
            assert args.profile_action == "test"
            assert args.name == "p"


class TestProxyCommands:
    """proxy 子命令实际执行（mock Preferences 层）。"""

    # ── helpers ──

    @staticmethod
    def _make_profile(name="test_profile", proxies=None):
        from astrocrawl.proxy._config import ProxyEndpointSpec, ProxyProfile

        if proxies is None:
            proxies = (ProxyEndpointSpec.from_url("http://1.2.3.4:8080"),)
        return ProxyProfile(name=name, proxies=proxies)

    # ── add ──

    def test_add_creates_profile(self, capsys):
        from astrocrawl.utils.preferences import get_preferences

        prefs = get_preferences()
        with patch.object(
            sys, "argv", ["astrocrawl", "proxy", "profile", "add", "test_add", "--proxies", "http://1.2.3.4:8080"]
        ):
            args = cli_main.parse_args()
            cli_main._handle_proxy(args)
        out = capsys.readouterr().out
        assert "test_add" in out
        # cleanup
        prefs.remove_proxy_profile("test_add")

    def test_add_duplicate_name_error(self, capsys):
        from astrocrawl.utils.preferences import get_preferences

        prefs = get_preferences()
        profile = self._make_profile("test_dup_add")
        prefs.save_proxy_profile(profile)
        try:
            with patch.object(
                sys,
                "argv",
                ["astrocrawl", "proxy", "profile", "add", "test_dup_add", "--proxies", "http://1.2.3.4:8080"],
            ):
                args = cli_main.parse_args()
                with pytest.raises(SystemExit):
                    cli_main._handle_proxy(args)
            assert "已存在" in capsys.readouterr().err
        finally:
            prefs.remove_proxy_profile("test_dup_add")

    def test_add_invalid_url_error(self, capsys):
        with patch.object(sys, "argv", ["astrocrawl", "proxy", "profile", "add", "bad", "--proxies", "not_a_url"]):
            args = cli_main.parse_args()
            with pytest.raises(SystemExit):
                cli_main._handle_proxy(args)
        assert "无效的代理端点" in capsys.readouterr().err

    def test_add_duplicate_endpoint_error(self, capsys):
        with patch.object(
            sys,
            "argv",
            [
                "astrocrawl",
                "proxy",
                "profile",
                "add",
                "dup_ep",
                "--proxies",
                "http://1.2.3.4:8080",
                "http://1.2.3.4:8080",
            ],
        ):
            args = cli_main.parse_args()
            with pytest.raises(SystemExit):
                cli_main._handle_proxy(args)
        assert "代理端点重复" in capsys.readouterr().err

    def test_add_with_bypass_domains(self, capsys):
        from astrocrawl.utils.preferences import get_preferences

        prefs = get_preferences()
        with patch.object(
            sys,
            "argv",
            [
                "astrocrawl",
                "proxy",
                "profile",
                "add",
                "test_bypass",
                "--proxies",
                "http://1.2.3.4:8080",
                "--bypass",
                ".internal",
            ],
        ):
            args = cli_main.parse_args()
            cli_main._handle_proxy(args)
        out = capsys.readouterr().out
        assert "test_bypass" in out
        # verify bypass saved
        saved = prefs.get_proxy_profile("test_bypass")
        assert saved is not None
        assert ".internal" in saved.bypass_domains
        prefs.remove_proxy_profile("test_bypass")

    # ── list ──

    def test_list_empty(self, capsys):
        with patch("astrocrawl.utils.preferences.get_preferences") as mock_prefs:
            mock_prefs.return_value.get_proxy_profiles.return_value = []
            with patch.object(sys, "argv", ["astrocrawl", "proxy", "profile", "list"]):
                args = cli_main.parse_args()
                cli_main._handle_proxy(args)
        assert "(no proxy profiles)" in capsys.readouterr().out

    def test_list_with_profiles(self, capsys):
        from astrocrawl.utils.preferences import get_preferences

        prefs = get_preferences()
        profile = self._make_profile("test_list_profile")
        prefs.save_proxy_profile(profile)
        try:
            with patch.object(sys, "argv", ["astrocrawl", "proxy", "profile", "list"]):
                args = cli_main.parse_args()
                cli_main._handle_proxy(args)
            out = capsys.readouterr().out
            assert "test_list_profile" in out
        finally:
            prefs.remove_proxy_profile("test_list_profile")

    # ── remove ──

    def test_remove_existing(self, capsys):
        from astrocrawl.utils.preferences import get_preferences

        prefs = get_preferences()
        profile = self._make_profile("test_remove_ok")
        prefs.save_proxy_profile(profile)
        with patch.object(sys, "argv", ["astrocrawl", "proxy", "profile", "remove", "test_remove_ok"]):
            args = cli_main.parse_args()
            cli_main._handle_proxy(args)
        out = capsys.readouterr().out
        assert "已删除" in out
        assert prefs.get_proxy_profile("test_remove_ok") is None

    def test_remove_nonexistent(self, capsys):
        with patch.object(sys, "argv", ["astrocrawl", "proxy", "profile", "remove", "no_such_profile_xyz"]):
            args = cli_main.parse_args()
            with pytest.raises(SystemExit):
                cli_main._handle_proxy(args)
        assert "不存在" in capsys.readouterr().err

    # ── set ──

    def test_set_existing_profile(self, capsys):
        from astrocrawl.utils.preferences import get_preferences

        prefs = get_preferences()
        profile = self._make_profile("test_set_ok")
        prefs.save_proxy_profile(profile)
        try:
            with patch.object(
                sys,
                "argv",
                ["astrocrawl", "proxy", "profile", "set", "test_set_ok", "--consumer", "ai"],
            ):
                args = cli_main.parse_args()
                cli_main._handle_proxy(args)
            out = capsys.readouterr().out
            assert "已将" in out
        finally:
            prefs.remove_proxy_profile("test_set_ok")

    def test_set_nonexistent_profile(self, capsys):
        with patch.object(
            sys,
            "argv",
            ["astrocrawl", "proxy", "profile", "set", "no_such", "--consumer", "preview"],
        ):
            args = cli_main.parse_args()
            with pytest.raises(SystemExit):
                cli_main._handle_proxy(args)
        assert "不存在" in capsys.readouterr().err

    def test_set_with_node(self, capsys):
        from astrocrawl.utils.preferences import get_preferences

        prefs = get_preferences()
        profile = self._make_profile("test_set_node")
        prefs.save_proxy_profile(profile)
        try:
            with patch.object(
                sys,
                "argv",
                [
                    "astrocrawl",
                    "proxy",
                    "profile",
                    "set",
                    "test_set_node",
                    "--consumer",
                    "source",
                    "--node",
                    "http:1.2.3.4:8080",
                ],
            ):
                args = cli_main.parse_args()
                cli_main._handle_proxy(args)
            out = capsys.readouterr().out
            assert "test_set_node" in out
            assert "http:1.2.3.4:8080" in out
        finally:
            prefs.remove_proxy_profile("test_set_node")

    # ── show ──

    def test_show_existing(self, capsys):
        from astrocrawl.utils.preferences import get_preferences

        prefs = get_preferences()
        profile = self._make_profile("test_show_ok")
        prefs.save_proxy_profile(profile)
        try:
            with patch.object(sys, "argv", ["astrocrawl", "proxy", "profile", "show", "test_show_ok"]):
                args = cli_main.parse_args()
                cli_main._handle_proxy(args)
            out = capsys.readouterr().out
            assert "test_show_ok" in out
            assert "端点" in out
        finally:
            prefs.remove_proxy_profile("test_show_ok")

    def test_show_nonexistent(self, capsys):
        with patch.object(sys, "argv", ["astrocrawl", "proxy", "profile", "show", "no_such"]):
            args = cli_main.parse_args()
            with pytest.raises(SystemExit):
                cli_main._handle_proxy(args)
        assert "不存在" in capsys.readouterr().err

    def test_show_with_consumer_usage(self, capsys):
        from astrocrawl.utils.preferences import get_preferences

        prefs = get_preferences()
        profile = self._make_profile("test_show_used")
        prefs.save_proxy_profile(profile)
        prefs.set_proxy_last_used("ai", profile.uuid, "")
        try:
            with patch.object(sys, "argv", ["astrocrawl", "proxy", "profile", "show", "test_show_used"]):
                args = cli_main.parse_args()
                cli_main._handle_proxy(args)
            out = capsys.readouterr().out
            assert "使用者" in out
        finally:
            prefs.remove_proxy_profile("test_show_used")

    # ── test ──

    def test_test_nonexistent_profile(self, capsys):
        with patch.object(sys, "argv", ["astrocrawl", "proxy", "profile", "test", "no_such"]):
            args = cli_main.parse_args()
            with pytest.raises(SystemExit):
                cli_main._handle_proxy(args)
        assert "不存在" in capsys.readouterr().err

    def test_test_empty_endpoints(self, capsys):
        from astrocrawl.proxy._config import ProxyProfile

        fake_profile = ProxyProfile(name="empty_eps", proxies=())
        with patch("astrocrawl.utils.preferences.get_preferences") as mock_prefs:
            mock_prefs.return_value.get_proxy_profile.return_value = fake_profile
            with patch.object(sys, "argv", ["astrocrawl", "proxy", "profile", "test", "empty_eps"]):
                args = cli_main.parse_args()
                cli_main._handle_proxy(args)
        assert "Profile has no endpoints" in capsys.readouterr().out

    def test_test_with_endpoints(self, capsys):
        from astrocrawl.utils.preferences import get_preferences

        prefs = get_preferences()
        profile = self._make_profile("test_probe_ok")
        prefs.save_proxy_profile(profile)
        try:
            with patch.object(sys, "argv", ["astrocrawl", "proxy", "profile", "test", "test_probe_ok"]):
                args = cli_main.parse_args()
                cli_main._handle_proxy(args)
            out = capsys.readouterr().out
            assert "test_probe_ok" in out
        finally:
            prefs.remove_proxy_profile("test_probe_ok")

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any, List, Optional

from astrobasis import setup_root_logger
from astrocrawl._version import __version__
from astrocrawl.cli._i18n import tr
from astrocrawl.config import DEFAULT_CONFIG, CrawlerConfig
from astrocrawl.crawler.engine import AsyncCrawler, create_crawler

# ═══════════════════════════════════════════════════════════════════
# Argument Parser
# ═══════════════════════════════════════════════════════════════════


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """解析命令行参数。N85 向后兼容：无子命令→爬取，rules/source→子命令。"""
    if argv is None:
        argv = sys.argv[1:]

    # 前置分离：URL 参数 vs 子命令
    urls: List[str] = []
    parser_args: List[str] = []

    if not argv:
        pass
    elif argv[0] in ("rules", "source", "proxy", "ai"):
        parser_args = argv
    else:
        in_proxies = False
        for a in argv:
            if a in ("rules", "source", "proxy", "ai"):
                parser_args = argv[argv.index(a) :]
                break
            # --proxies 后的 http(s):// URL 是代理端点，非爬取目标
            if a == "--proxies":
                in_proxies = True
                parser_args.append(a)
                continue
            if in_proxies:
                if a.startswith("-"):
                    in_proxies = False
                    parser_args.append(a)
                else:
                    parser_args.append(a)
                continue
            if a.startswith("https://") or a.startswith("http://"):
                urls.append(a)
            else:
                parser_args.append(a)

    parser = argparse.ArgumentParser(
        description=tr("AstroCrawl v{version} — Production-grade async web crawler").format(version=__version__)
    )
    _add_crawl_options(parser)
    _add_subcommands(parser)

    ns = parser.parse_args(parser_args)
    # 注入提取的 URL
    if not hasattr(ns, "urls") or not ns.urls:
        ns.urls = urls
    return ns


def _add_crawl_options(parser: argparse.ArgumentParser) -> None:
    """添加爬取相关选项（URL 通过 parse_args 前置提取，不在此处声明 positional）。"""
    parser.add_argument("-d", "--depth", type=int, default=None, help=tr("Crawl depth"))
    parser.add_argument("-c", "--concurrency", type=int, default=None, help=tr("Concurrency"))
    parser.add_argument("-o", "--output", type=str, default=None, help=tr("Output file"))
    parser.add_argument("--profile", type=str, default=None, help=tr("Proxy Profile name (overrides proxy_last_used)"))
    parser.add_argument(
        "--proxies",
        type=str,
        default=None,
        nargs="+",
        help=tr("Temporary proxy endpoints url1 url2 ... (not persisted)"),
    )
    parser.add_argument("--same-domain", action="store_true", default=None, help=tr("Same domain only"))
    parser.add_argument("--no-robots", action="store_true", default=None, help=tr("Do not respect robots.txt"))
    parser.add_argument("--config", type=str, help=tr("JSON config file"))
    parser.add_argument("--max-pages", type=int, default=None, help=tr("Maximum pages"))
    parser.add_argument("--max-runtime", type=int, default=None, help=tr("Maximum runtime seconds"))
    parser.add_argument("--sitemap", action="store_true", default=None)
    parser.add_argument("--no-sitemap", action="store_false", dest="sitemap")
    parser.add_argument("--contact", type=str, default="")
    parser.add_argument("--skip-duplicate-links", action="store_true", default=None)
    parser.add_argument("--log-level", type=str, default=None, choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--log-file", type=str, default=None, help=tr("Log file path"))
    parser.add_argument(
        "--proxy-mode",
        type=str,
        default=None,
        choices=["direct-only", "prefer-direct", "prefer-proxy", "proxy-only"],
        help=tr("Proxy mode: direct-only | prefer-direct | prefer-proxy | proxy-only"),
    )
    # S7: 规则引擎爬取选项
    parser.add_argument(
        "--trace-rules", action="store_true", default=None, help=tr("Enable rule trace diagnostic output")
    )
    parser.add_argument(
        "--rules-auto-update",
        action="store_true",
        default=None,
        dest="rules_auto_update",
        help=tr("Auto-update remote rule sources on startup"),
    )
    parser.add_argument(
        "--no-rules-auto-update",
        action="store_false",
        dest="rules_auto_update",
        help=tr("Disable remote rule source auto-update"),
    )
    parser.add_argument(
        "--rules-dir",
        type=str,
        default=None,
        action="append",
        help=tr("Additional rule search directories (repeatable)"),
    )
    parser.add_argument(
        "--set",
        dest="set_overrides",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help=tr("Override config fields (repeatable, use --config for complex types)"),
    )


def _add_subcommands(parser: argparse.ArgumentParser) -> None:
    """添加 rules / source / proxy / ai 子命令组。"""
    sub = parser.add_subparsers(dest="subcommand", title=tr("Subcommands"))

    # ── rules ──
    rules = sub.add_parser("rules", help=tr("Rule management"))
    rules_sub = rules.add_subparsers(dest="rules_action")

    _add_rules_list(rules_sub)
    _add_rules_show(rules_sub)
    _add_rules_validate(rules_sub)
    _add_rules_enable(rules_sub)
    _add_rules_disable(rules_sub)
    _add_rules_export(rules_sub)
    _add_rules_import(rules_sub)
    _add_rules_export_all(rules_sub)
    _add_rules_edit(rules_sub)
    _add_rules_reset(rules_sub)
    _add_rules_generate(rules_sub)
    _add_rules_preview(rules_sub)

    # ── source ──
    source = sub.add_parser("source", help=tr("Remote source management"))
    src_sub = source.add_subparsers(dest="source_action")

    _add_source_add(src_sub)
    _add_source_remove(src_sub)
    _add_source_list(src_sub)
    _add_source_update(src_sub)
    _add_source_info(src_sub)
    _add_source_edit(src_sub)
    _add_source_validate(src_sub)

    # ── proxy ──
    proxy = sub.add_parser("proxy", help=tr("Proxy configuration"))
    proxy_sub = proxy.add_subparsers(dest="proxy_action")

    # proxy profile — Profile CRUD
    profile = proxy_sub.add_parser("profile", help=tr("Proxy Profile management"))
    profile_sub = profile.add_subparsers(dest="profile_action")

    _add_proxy_add(profile_sub)
    _add_proxy_list(profile_sub)
    _add_proxy_remove(profile_sub)
    _add_proxy_set(profile_sub)
    _add_proxy_show(profile_sub)
    _add_proxy_test(profile_sub)

    # ── ai ──
    ai = sub.add_parser("ai", help=tr("AI configuration"))
    ai_sub = ai.add_subparsers(dest="ai_action")

    ai_profile = ai_sub.add_parser("profile", help=tr("AI Profile management"))
    ai_profile_sub = ai_profile.add_subparsers(dest="ai_profile_action")

    _add_ai_add(ai_profile_sub)
    _add_ai_list(ai_profile_sub)
    _add_ai_remove(ai_profile_sub)
    _add_ai_set_default(ai_profile_sub)
    _add_ai_show(ai_profile_sub)
    _add_ai_test(ai_profile_sub)


def _add_rules_list(sub) -> None:
    p = sub.add_parser("list", help=tr("List all rules"))
    p.add_argument("--format", choices=["json", "table"], default="table")
    p.add_argument("--enabled-only", action="store_true")


def _add_rules_show(sub) -> None:
    p = sub.add_parser("show", help=tr("Show rule details"))
    p.add_argument("name", help=tr("Rule name"))
    p.add_argument("--format", choices=["json", "table"], default="table")


def _add_rules_validate(sub) -> None:
    p = sub.add_parser("validate", help=tr("Validate rules"))
    group = p.add_mutually_exclusive_group()
    group.add_argument("--all", action="store_true", help=tr("Validate all rules"))
    group.add_argument("--name", type=str, help=tr("Validate specific rule"))
    p.add_argument("--format", choices=("table", "json"), default="table", help=tr("Output format"))


def _add_rules_enable(sub) -> None:
    p = sub.add_parser("enable", help=tr("Enable rules"))
    p.add_argument("name", nargs="*", help=tr("Rule names (multiple allowed)"))
    p.add_argument("--all", action="store_true", help=tr("Enable all rules"))
    p.add_argument("--dry-run", action="store_true", help=tr("Preview only, do not execute"))


def _add_rules_disable(sub) -> None:
    p = sub.add_parser("disable", help=tr("Disable rules"))
    p.add_argument("name", nargs="*", help=tr("Rule names (multiple allowed)"))
    p.add_argument("--all", action="store_true", help=tr("Disable all rules"))
    p.add_argument("--dry-run", action="store_true", help=tr("Preview only, do not execute"))


def _add_rules_export(sub) -> None:
    p = sub.add_parser("export", help=tr("Export rule"))
    p.add_argument("name", help=tr("Rule name"))
    p.add_argument("-o", "--output", type=str, help=tr("Output file path"))


def _add_rules_import(sub) -> None:
    p = sub.add_parser("import", help=tr("Import rule"))
    p.add_argument("file", type=str, help=tr("Rule JSON file"))
    p.add_argument("--overwrite", action="store_true", help=tr("Overwrite existing rule"))


def _add_rules_export_all(sub) -> None:
    p = sub.add_parser("export-all", help=tr("Export all rules"))
    p.add_argument("-o", "--output", type=str, default=None, help=tr("Output directory"))


def _add_rules_edit(sub) -> None:
    p = sub.add_parser("edit", help=tr("Edit rule (user rules only)"))
    p.add_argument("name", help=tr("Rule name"))


def _add_rules_reset(sub) -> None:
    p = sub.add_parser("reset", help=tr("Reset to factory rules"))
    p.add_argument("--confirm", action="store_true", required=True, help=tr("Confirm reset"))


def _add_rules_generate(sub) -> None:
    p = sub.add_parser("generate", help=tr("AI generate extraction rule"))
    p.add_argument("--url", required=True, help=tr("Target page URL"))
    p.add_argument(
        "--fields", required=True, help=tr("Fields to extract, comma-separated (e.g. title,price,description)")
    )
    html_src = p.add_mutually_exclusive_group(required=True)
    html_src.add_argument("--html", help=tr("HTML source text"))
    html_src.add_argument("--html-file", help=tr("HTML file path"))
    p.add_argument("--model", help=tr("AI model (default: preference or gpt-4o-mini)"))
    p.add_argument("--temperature", type=float, help=tr("Generation temperature (default: preference or 0.1)"))
    p.add_argument("--max-tokens", type=int, help=tr("Max tokens (default: preference or 2048)"))
    p.add_argument("--api-key", help=tr("API Key (default: preference)"))
    p.add_argument("--endpoint", help=tr("API Endpoint (default: preference)"))
    p.add_argument(
        "--tier",
        choices=["off", "canonical", "strict"],
        default="canonical",
        help=tr("HTML preprocessing tier: off, canonical (recommended), strict"),
    )
    p.add_argument(
        "--rule-mode",
        choices=["type", "position"],
        default="type",
        help=tr("AI selector strategy: type=by element type (recommended), position=by DOM position"),
    )
    output_group = p.add_mutually_exclusive_group()
    output_group.add_argument("-o", "--output", help=tr("Output file path (default: save to ~/.astrocrawl/rules/)"))
    output_group.add_argument("--no-save", action="store_true", help=tr("Output JSON to stdout only, do not save"))
    p.add_argument("--overwrite", action="store_true", help=tr("Overwrite existing rule"))
    p.add_argument(
        "--output-format",
        choices=["auto", "json_schema", "json_object", "off"],
        default="auto",
        help=tr("Structured output format: auto (recommended), json_schema, json_object, off"),
    )
    p.add_argument("--profile", help=tr("AI Profile name (default: active profile)"))


def _add_rules_preview(sub) -> None:
    p = sub.add_parser("preview", help=tr("Preview rule extraction on a URL"))
    p.add_argument("--rule", required=True, help=tr("Rule name"))
    p.add_argument("--url", required=True, help=tr("Target page URL"))
    p.add_argument("--theme", choices=["light", "dark"], default="light", help=tr("Browser theme (default: light)"))


def _add_source_add(sub) -> None:
    p = sub.add_parser("add", help=tr("Add remote source"))
    p.add_argument("url", help=tr("Manifest HTTPS URL"))
    p.add_argument("--name", type=str, default=None, help=tr("Source name (derived from URL by default)"))
    p.add_argument("--confirm", action="store_true", help=tr("Confirm add"))


def _add_source_remove(sub) -> None:
    p = sub.add_parser("remove", help=tr("Remove remote source"))
    p.add_argument("name", help=tr("Source name"))


def _add_source_list(sub) -> None:
    p = sub.add_parser("list", help=tr("List all remote sources"))
    p.add_argument("--format", choices=["json", "table"], default="table")


def _add_source_update(sub) -> None:
    p = sub.add_parser("update", help=tr("Update remote source"))
    group = p.add_mutually_exclusive_group()
    group.add_argument("--name", type=str, help=tr("Update specific source"))
    group.add_argument("--all", action="store_true", help=tr("Update all sources"))
    p.add_argument("--dry-run", action="store_true", help=tr("Preview changes only, do not download"))


def _add_source_edit(sub) -> None:
    p = sub.add_parser("edit", help=tr("Edit remote source metadata"))
    p.add_argument("name", help=tr("Source name"))
    p.add_argument("--name", dest="new_name", type=str, default=None, help=tr("New name"))
    p.add_argument("--url", type=str, default=None, help=tr("New Manifest URL"))


def _add_source_validate(sub) -> None:
    p = sub.add_parser("validate", help=tr("Validate remote source manifest reachability"))
    p.add_argument("name", help=tr("Source name"))


def _add_source_info(sub) -> None:
    p = sub.add_parser("info", help=tr("Show remote source details"))
    p.add_argument("name", help=tr("Source name"))
    p.add_argument("--format", choices=["json", "table"], default="table")


# ═══════════════════════════════════════════════════════════════════
# Config Loading
# ═══════════════════════════════════════════════════════════════════

_CONFIG_EXTRA_KEYS = ("urls", "depth", "same_domain_only", "output_path", "respect_robots")


def _load_file_config(config_path: str):
    """加载配置文件，支持 GUI "advanced" 容器格式与标准格式。

    GUI 保存格式为 JSON 容器：{"urls": [...], "depth": N, ..., "advanced": {...}}
    标准格式由 CrawlerConfig.from_file() 处理（.json/.yaml/.toml）。
    """
    path = Path(config_path)

    # JSON: 检测 GUI "advanced" 容器格式（GUI↔CLI 互通）
    if path.suffix.lower() == ".json":
        raw = json.loads(path.read_text(encoding="utf-8"))
        if "advanced" in raw:
            cfg = CrawlerConfig.from_dict(raw["advanced"])
            extra = {k: raw[k] for k in _CONFIG_EXTRA_KEYS if k in raw}
            return cfg, extra
        # 标准 JSON 格式 — 继续往下走 from_file() 路径

    # 标准格式：委托 CrawlerConfig.from_file() 统一入口（JSON/YAML/TOML）
    return CrawlerConfig.from_file(config_path), {}


def _parse_set_value(key: str, val: str) -> tuple[str, Any]:
    """将 --set KEY=VALUE 的字符串值转换为字段类型。

    Returns:
        ("cfg", parsed_value) 或 ("gs", parsed_value) — 指示字段归属。
    """
    import logging as _logging

    from astrocrawl.config import GlobalSettings

    # 先查 CrawlerConfig，再查 GlobalSettings
    field = DEFAULT_CONFIG.__dataclass_fields__.get(key)
    target = "cfg"
    if field is None:
        field = GlobalSettings.__dataclass_fields__.get(key)
        target = "gs"
    if field is None:
        available = sorted(set(DEFAULT_CONFIG.__dataclass_fields__) | set(GlobalSettings.__dataclass_fields__))
        raise ValueError(f"Unknown config field '{key}'. Available fields: {available}")

    type_str = field.type  # from __future__ import annotations → 字符串类型

    # 特殊: log_level 接受 int 或日志级别名
    if key == "log_level":
        try:
            return (target, int(val))
        except ValueError:
            level = getattr(_logging, val.upper(), None)
            if level is None:
                raise ValueError(f"log_level must be an integer or DEBUG/INFO/WARNING/ERROR, got: {val}") from None
            return (target, level)

    # 复杂类型 → 拒绝，提示用 --config
    if "[" in type_str or "tuple" in type_str or "frozenset" in type_str or "dict" in type_str or "set" in type_str:
        raise ValueError(f"Field '{key}' is a compound type ({type_str}), use --config file instead")

    if type_str == "bool":
        v = val.lower()
        if v in ("true", "1", "yes", "on"):
            return (target, True)
        if v in ("false", "0", "no", "off"):
            return (target, False)
        raise ValueError(f"Field '{key}' is bool type, expected true/false/1/0, got: {val}")

    if type_str == "int":
        return (target, int(val))

    if type_str == "float":
        return (target, float(val))

    return (target, val)


def _merge_cli_config(args) -> Optional[dict]:
    file_cfg = DEFAULT_CONFIG
    extra: dict = {}

    if args.config and Path(args.config).exists():
        file_cfg, extra = _load_file_config(args.config)

    from dataclasses import replace

    from astrocrawl.config import GlobalSettings

    cfg_overrides: dict = {}
    gs_overrides: dict = {}
    if args.concurrency is not None:
        cfg_overrides["concurrency"] = args.concurrency
    if args.max_pages is not None:
        cfg_overrides["max_total_pages"] = args.max_pages
    if args.max_runtime is not None:
        cfg_overrides["max_runtime_seconds"] = args.max_runtime
    if args.no_robots is not None:
        cfg_overrides["robots_respect"] = not args.no_robots
    elif extra.get("respect_robots") is not None:
        cfg_overrides["robots_respect"] = extra["respect_robots"]
    if args.sitemap is not None:
        cfg_overrides["use_sitemap"] = args.sitemap
    if args.skip_duplicate_links is not None:
        cfg_overrides["skip_duplicate_links"] = args.skip_duplicate_links
    if args.log_file is not None:
        cfg_overrides["log_file"] = args.log_file
    if args.proxy_mode is not None:
        cfg_overrides["proxy_mode"] = args.proxy_mode.replace("-", "_")
    # GlobalSettings 字段 (分流到 gs_overrides)
    if args.log_level is not None:
        gs_overrides["log_level"] = getattr(logging, args.log_level.upper(), logging.INFO)
    if args.trace_rules is not None:
        gs_overrides["trace_rules"] = args.trace_rules
    if getattr(args, "rules_auto_update", None) is not None:
        gs_overrides["rules_auto_update"] = args.rules_auto_update
    if getattr(args, "rules_dir", None):
        gs_overrides["rules_dirs"] = list(args.rules_dir)
    # ASTROCRAWL_LOG_LEVEL 环境变量处理 (从 CrawlerConfig.from_env 迁移)
    log_level_env = os.environ.get("ASTROCRAWL_LOG_LEVEL")
    if log_level_env:
        level = getattr(logging, log_level_env.upper(), None)
        if level is not None:
            gs_overrides.setdefault("log_level", level)
        else:
            logging.warning("env ASTROCRAWL_LOG_LEVEL value '%s' invalid, ignored", log_level_env)

    # --set KEY=VALUE 通用覆盖 — 按字段归属分流
    set_errors: list[str] = []
    for item in getattr(args, "set_overrides", []) or []:
        key, sep, val = item.partition("=")
        if not sep:
            print(tr("Error: --set requires KEY=VALUE format, got: {item}").format(item=item))
            return None
        try:
            target, parsed = _parse_set_value(key.strip(), val)
            if target == "gs":
                gs_overrides[key.strip()] = parsed
            else:
                cfg_overrides[key.strip()] = parsed
        except (ValueError, TypeError) as e:
            set_errors.append(tr("Field '{key}': {error}").format(key=key.strip(), error=e))
    if set_errors:
        for err in set_errors:
            print(tr("Error: {msg}").format(msg=err))
        return None

    cfg = CrawlerConfig.from_env(file_cfg)
    cfg = replace(cfg, **cfg_overrides)
    cfg = cfg.with_contact(args.contact)
    global_settings = GlobalSettings.from_preferences().with_overrides(**gs_overrides)

    # ── 代理配置解析链 ─────────────────────────
    # 优先级: --proxies > --profile > proxy_pool (配置文件) > direct_only
    # 每一步 --proxy-mode 可独立覆盖 CrawlerConfig.proxy_mode
    from astrocrawl.proxy._config import ProxyEndpointSpec, ProxyProfile
    from astrocrawl.utils.preferences import get_preferences

    proxy_profile: Optional[ProxyProfile] = None
    prefs = get_preferences()

    if args.proxies:
        # --proxies url1 url2 → 临时 Profile（最高优先级，不持久化）
        try:
            temp_proxies = tuple(ProxyEndpointSpec.from_url(u) for u in args.proxies)
        except ValueError as e:
            print(tr("Error: invalid proxy endpoint URL — {msg}").format(msg=e), file=sys.stderr)
            return None
        proxy_profile = ProxyProfile(name="__cli_temp__", proxies=temp_proxies)
    elif args.profile:
        # --profile NAME → 从 Preferences 加载
        proxy_profile = prefs.get_proxy_profile(args.profile)
        if proxy_profile is None:
            print(tr("Error: proxy Profile '{name}' does not exist.").format(name=args.profile))
            print(tr("  Available Profiles: {names}").format(names=prefs.get_proxy_profile_names()))
            return None

    # --proxy-mode 可独立覆盖
    proxy_mode_override = None
    if args.proxy_mode is not None:
        proxy_mode_override = args.proxy_mode.replace("-", "_")
    elif cfg.proxy_mode:
        proxy_mode_override = cfg.proxy_mode

    urls = args.urls or extra.get("urls") or []
    if not urls:
        print(tr("Error: at least one start URL required (via command line or config file)"))
        return None

    depth = args.depth if args.depth is not None else extra.get("depth") if extra.get("depth") is not None else 2

    output = args.output or extra.get("output_path") or "crawler_output.jsonl"
    same_domain: bool = True
    if args.same_domain is not None:
        same_domain = args.same_domain
    elif extra.get("same_domain_only") is not None:
        same_domain = extra["same_domain_only"]

    return {
        "cfg": cfg,
        "global_settings": global_settings,
        "urls": urls,
        "depth": depth,
        "proxy_profile": proxy_profile,
        "proxy_mode_override": proxy_mode_override,
        "output": output,
        "same_domain": same_domain,
    }


def _find_user_rule_file(name: str, rules_dirs: list | None = None) -> Path | None:
    """在用户可编辑目录中查找规则文件（不含 pip/远程）。"""
    user_dir = Path.home() / ".astrocrawl" / "rules"
    dirs = [user_dir] + [Path(p).expanduser().resolve() for p in (rules_dirs or [])]
    for d in reversed(dirs):
        candidate = d / f"{name}.json"
        if candidate.is_file():
            return candidate
    return None


def _rule_exists_in_readonly(name: str) -> bool:
    """检查规则是否存在于只读源（pip 预置或远程缓存）。"""
    import os as _os

    pip_dir = Path(__file__).resolve().parent.parent / "rules"
    if (pip_dir / f"{name}.json").is_file():
        return True
    remote_dir = Path.home() / ".astrocrawl" / "rules_cache"
    if remote_dir.is_dir():
        for _root, _dirs, files in _os.walk(str(remote_dir)):
            if f"{name}.json" in files:
                return True
    return False


# ═══════════════════════════════════════════════════════════════════
# Subcommand handlers
# ═══════════════════════════════════════════════════════════════════


def _print_validate_table(results: list) -> None:
    """以表格格式打印校验结果。"""
    passed = [r for r in results if r["status"] == "pass"]
    failed = [r for r in results if r["status"] == "fail"]
    skipped = [r for r in results if r["status"] == "skip"]

    print(tr("Total: {p} passed, {f} failed, {s} skipped").format(p=len(passed), f=len(failed), s=len(skipped)))
    if failed:
        print(tr("Failed:"))
        for r in failed:
            name = r.get("name", r["path"])
            err = r.get("error", tr("Unknown error"))
            source = r.get("source", "?")
            print(tr("  FAIL  {name}  ({source})  — {error}").format(name=name, source=source, error=err))
    if passed:
        print(tr("Passed ({n}):").format(n=len(passed)))
        for r in passed:
            print(
                tr("  PASS  {name}  ({source})  v{version}  {count} fields").format(
                    name=r["name"], source=r["source"], version=r["schema_version"], count=r["fields_count"]
                )
            )
    if skipped:
        print(tr("Skipped ({n}):").format(n=len(skipped)))
        for r in skipped:
            print(
                tr("  SKIP  {path}  ({source})  — {reason}").format(
                    path=r["path"], source=r.get("source", "?"), reason=r.get("error", "")
                )
            )


def _handle_rules(args) -> None:
    """处理 rules 子命令。"""
    from astrocrawl.config import CrawlerConfig, GlobalSettings
    from astrocrawl.rules import (
        build_rule_snapshot,
        export_all_rules,
        export_rule_to_file,
        import_rule,
        import_rule_preview,
    )

    cfg = CrawlerConfig.from_env()
    gs = GlobalSettings.from_preferences()
    extra_rules_dirs = list(gs.rules_dirs)
    snapshot = build_rule_snapshot(
        cfg,
        extra_rules_dirs=extra_rules_dirs,
        rules_dirs_enabled=gs.rules_dirs_enabled,
    )

    action = args.rules_action

    if action == "list":
        rules = snapshot.rules if snapshot.rules else []
        if args.enabled_only:
            rules = [r for r in rules if r.enabled]
        if args.format == "json":
            print(
                json.dumps(
                    [
                        {
                            "name": r.name,
                            "display_name": r.display_name,
                            "version": r.version,
                            "scope": r.match.scope.value,
                            "domains": r.match.domains,
                            "fields": len(r.fields),
                        }
                        for r in rules
                    ],
                    indent=2,
                    ensure_ascii=False,
                )
            )
        else:
            print(
                "{Name:30s} {Scope:20s} {Fields:>6s}  {Domains}".format(
                    Name=tr("Name"), Scope=tr("Scope"), Fields=tr("Fields"), Domains=tr("Domains")
                )
            )
            print("-" * 90)
            for r in rules:
                domains = ",".join(r.match.domains[:3])
                if len(r.match.domains) > 3:
                    domains += f" +{len(r.match.domains) - 3}"
                print(f"{r.name:30s} {r.match.scope.value:20s} {len(r.fields):>6d}  {domains}")

    elif action == "show":
        rule = snapshot.by_name.get(args.name)
        if not rule:
            print(tr("Error: rule '{name}' does not exist").format(name=args.name), file=sys.stderr)
            sys.exit(1)
        if args.format == "json":
            from astrocrawl.rules import rule_to_dict

            print(json.dumps(rule_to_dict(rule), indent=2, ensure_ascii=False))
        else:
            print(tr("Name:         {name}").format(name=rule.name))
            print(tr("Display:      {display}").format(display=rule.display_name))
            print(tr("Version:      {version}").format(version=rule.version))
            print(tr("Enabled:      {enabled}").format(enabled=rule.enabled))
            print(tr("Scope:        {scope}").format(scope=rule.match.scope.value))
            print(tr("Domains:      {domains}").format(domains=", ".join(rule.match.domains) or tr("(generic)")))
            print(tr("Fields ({n}):").format(n=len(rule.fields)))
            for fname, fcfg in rule.fields.items():
                print(f"  {fname}: {fcfg.selector} ({fcfg.extract})")

    elif action == "validate":
        if args.all:
            from astrocrawl.rules import validate_rule_files

            results = validate_rule_files(cfg, extra_rules_dirs=extra_rules_dirs)
            if args.format == "json":
                print(json.dumps(results, indent=2, ensure_ascii=False))
            else:
                _print_validate_table(results)
            if any(r["status"] == "fail" for r in results):
                sys.exit(1)
        elif args.name:
            rule = snapshot.by_name.get(args.name)
            if not rule:
                print(tr("Error: rule '{name}' does not exist").format(name=args.name), file=sys.stderr)
                sys.exit(1)
            from astrocrawl.rules import rule_to_dict, validate_rule

            try:
                validate_rule(rule_to_dict(rule))
                print(tr("Rule '{name}': valid").format(name=args.name))
            except ValueError as e:
                print(tr("Rule '{name}': validation failed — {error}").format(name=args.name, error=e), file=sys.stderr)
                sys.exit(1)
        else:
            print(tr("Error: specify --all or --name <rule_name>"), file=sys.stderr)
            sys.exit(1)

    elif action == "enable" or action == "disable":
        target_state = action == "enable"
        action_word_en = tr("enabled") if target_state else tr("disabled")

        if args.all:
            names = [r.name for r in snapshot.rules if r.name != "default"]
        elif args.name:
            names = args.name
        else:
            print(tr("Error: specify rule name(s) or use --all"), file=sys.stderr)
            sys.exit(1)

        changed: list[str] = []
        unchanged: list[str] = []
        skipped: list[tuple[str, str]] = []

        for name in names:
            if name == "default":
                skipped.append((name, tr("default rule cannot be modified")))
                continue
            rule = snapshot.by_name.get(name)
            if rule is None:
                skipped.append((name, tr("does not exist")))
                continue
            if rule.enabled == target_state:
                unchanged.append(name)
            else:
                changed.append(name)

        # --dry-run 预览
        if args.dry_run:
            if changed:
                print(tr("[DRY RUN] Will {action} {n} rules:").format(action=action_word_en, n=len(changed)))
                print("  " + ", ".join(changed))
            if unchanged:
                print(tr("[DRY RUN] Already {action} ({n} rules)").format(action=action_word_en, n=len(unchanged)))
            if skipped:
                print(tr("[DRY RUN] Will skip:"))
                for n, reason in skipped:
                    print(f"  {n}: {reason}")
            return

        # 批量写入 — 一次原子操作
        if changed:
            from astrocrawl.rules import set_rules_enabled

            bulk = dict.fromkeys(changed, target_state)
            set_rules_enabled(bulk)

        print(tr("{action_word} {n} rules").format(action_word=action_word_en, n=len(changed)))
        if unchanged:
            print(tr("Already {action}: {n} rules (no change)").format(action=action_word_en, n=len(unchanged)))
        if skipped:
            for n, reason in skipped:
                print(tr("Skipped: {name} ({reason})").format(name=n, reason=reason), file=sys.stderr)
            sys.exit(1)

    elif action == "export":
        if args.name == "default":
            print(tr("Error: default rule cannot be exported"), file=sys.stderr)
            sys.exit(1)
        rule = snapshot.by_name.get(args.name)
        if not rule:
            print(tr("Error: rule '{name}' does not exist").format(name=args.name), file=sys.stderr)
            sys.exit(1)
        out_path = Path(args.output) if args.output else Path(f"{args.name}.json")
        export_rule_to_file(rule, out_path)
        print(tr("Rule exported to: {path}").format(path=out_path))

    elif action == "import":
        src = Path(args.file)
        if not src.exists():
            print(tr("Error: file not found: {path}").format(path=args.file), file=sys.stderr)
            sys.exit(1)
        try:
            preview = import_rule_preview(src)
            print(
                tr("Preview: {name} ({count} fields, domains={domains})").format(
                    name=preview["name"], count=preview["fields_count"], domains=preview["domains"]
                )
            )
            dest = Path.home() / ".astrocrawl" / "rules"
            dest.mkdir(parents=True, exist_ok=True)
            result = import_rule(src, dest, overwrite=args.overwrite)
            print(tr("Rule imported: {name}").format(name=result))
        except FileExistsError:
            print(tr("Error: rule already exists, use --overwrite to replace"), file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(tr("Error: import failed — {error}").format(error=e), file=sys.stderr)
            sys.exit(1)

    elif action == "export-all":
        out_dir = Path(args.output) if args.output else Path("rules_export")
        # N37: 不导出 default 规则
        user_rules = [r for r in snapshot.by_name.values() if r.name != "default"]
        count = export_all_rules(user_rules, out_dir)
        print(tr("Exported {count} rules to: {dir}").format(count=count, dir=out_dir))

    elif action == "reset":
        rules_dir = Path.home() / ".astrocrawl" / "rules"
        if rules_dir.is_dir():
            for f in rules_dir.glob("*.json"):
                f.unlink()
        print(tr("User rules reset."))

    elif action == "generate":
        from astrocrawl.ai import (
            AIClient,
            AIConfig,
            AIRateLimitError,
            GenerationParams,
            OutputConstraint,
            get_rule_gen_limiter,
        )
        from astrocrawl.rules import PreprocessTier, RuleGenerator, safe_write_rule_file
        from astrocrawl.utils.preferences import get_preferences

        prefs = get_preferences()

        # --profile: 零副作用读取指定 profile（不修改活跃 profile）
        if args.profile:
            prof = prefs.get_ai_profile(args.profile)
            if prof is None:
                names = prefs.get_ai_profile_names()
                print(
                    tr("Error: Profile '{name}' not found. Available: {names}").format(
                        name=args.profile, names=", ".join(names)
                    ),
                    file=sys.stderr,
                )
                sys.exit(1)
        else:
            # fallback: 第一个 enabled profile
            profiles = prefs.get_ai_profiles()
            prof = next((p for p in profiles if p.enabled), None)

        if prof is None or not prof.api_key:
            print(tr("Error: no API Key configured. Provide via GUI AI Settings or --api-key"), file=sys.stderr)
            sys.exit(1)

        # CLI 显式 flag 覆盖 profile 字段值
        api_key = args.api_key or prof.api_key
        endpoint = args.endpoint or prof.endpoint
        provider = prof.provider
        model = args.model or prof.model
        temperature = args.temperature if args.temperature is not None else prof.temperature
        max_tokens_val = args.max_tokens if args.max_tokens is not None else prof.max_tokens

        tier_map = {"off": 0, "canonical": 1, "strict": 2}
        tier = PreprocessTier(tier_map[args.tier])

        if args.html is not None:
            html = args.html
        else:
            html_path = Path(args.html_file)
            if not html_path.exists():
                print(tr("Error: HTML file not found: {path}").format(path=args.html_file), file=sys.stderr)
                sys.exit(1)
            html = html_path.read_text(encoding="utf-8")

        field_list = [f.strip() for f in args.fields.split(",") if f.strip()]
        if not field_list:
            print(tr("Error: --fields cannot be empty"), file=sys.stderr)
            sys.exit(1)

        try:
            with get_rule_gen_limiter().acquire_sync():
                config = AIConfig(api_key=api_key, provider=provider, base_url=endpoint, default_model=model)
                proxy_parsed = prefs.get_parsed_proxy_for("ai")
                proxy_url = proxy_parsed.to_url_with_auth() if proxy_parsed else None
                client = AIClient(config, proxy_url=proxy_url)
                generator = RuleGenerator(client)
                if args.output_format == "off":
                    params = GenerationParams(temperature=temperature, max_tokens=max_tokens_val)
                elif args.output_format == "json_object":
                    params = GenerationParams(
                        temperature=temperature,
                        max_tokens=max_tokens_val,
                        output=OutputConstraint(format="json_object"),
                    )
                else:  # auto / json_schema
                    from astrocrawl.rules import RuleSchema

                    params = GenerationParams(
                        temperature=temperature,
                        max_tokens=max_tokens_val,
                        output=OutputConstraint(format="json_schema", schema_model=RuleSchema),
                    )
                gen_result = generator.generate_sync(args.url, html, field_list, params, tier=tier, mode=args.rule_mode)
        except AIRateLimitError as e:
            print(tr("Error: {msg}").format(msg=e), file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(tr("Error: AI generation failed — {error}").format(error=e), file=sys.stderr)
            sys.exit(1)

        name = gen_result.get("name", "?")
        fields_count = len(gen_result.get("fields", {}))
        domains = gen_result.get("match", {}).get("domains", [])
        print(
            tr("Preview: {name} ({count} fields, domains={domains})").format(
                name=name, count=fields_count, domains=domains
            )
        )

        if args.no_save:
            print(json.dumps(gen_result, indent=2, ensure_ascii=False))
        elif args.output:
            out_path = Path(args.output)
            safe_write_rule_file(out_path, gen_result)
            print(tr("Rule saved to: {path}").format(path=out_path))
        else:
            dest_dir = Path.home() / ".astrocrawl" / "rules"
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_path = dest_dir / f"{name}.json"
            if dest_path.exists() and not args.overwrite:
                print(
                    tr("Error: rule '{name}' already exists, use --overwrite to replace").format(name=name),
                    file=sys.stderr,
                )
                sys.exit(1)
            safe_write_rule_file(dest_path, gen_result)
            print(tr("Rule saved to: {path}").format(path=dest_path))

    elif action == "preview":
        import asyncio

        from astrocrawl.browser._preview import (
            PreviewBrowser,
            PreviewFieldParams,
            PreviewParams,
            PreviewResult,
            assign_field_colors,
        )
        from astrocrawl.rules._schema import FieldRule

        rule = snapshot.by_name.get(args.rule)
        if rule is None:
            print(tr("Error: rule '{name}' does not exist").format(name=args.rule), file=sys.stderr)
            sys.exit(1)

        fields_dict = getattr(rule, "fields", {}) or {}
        raw_fields: list[dict[str, Any]] = []
        for fname, fcfg in fields_dict.items():
            if isinstance(fcfg, FieldRule):
                entry = {
                    "name": fname,
                    "selector": fcfg.selector,
                    "extract": fcfg.extract,
                    "attr": fcfg.attr,
                    "multiple": fcfg.multiple,
                }
                if fcfg.fallback:
                    entry["fallback"] = [
                        {
                            "selector": fb.selector,
                            "extract": fb.extract,
                            "attr": fb.attr,
                            "multiple": fb.multiple,
                        }
                        for fb in fcfg.fallback
                    ]
            elif isinstance(fcfg, dict):
                entry = {
                    "name": fname,
                    "selector": fcfg.get("selector", ""),
                    "extract": fcfg.get("extract", "text"),
                    "attr": fcfg.get("attr", ""),
                    "multiple": fcfg.get("multiple", False),
                }
                fb = fcfg.get("fallback", [])
                if fb:
                    entry["fallback"] = [
                        {"selector": fb[i], "extract": "text"} if isinstance(fb[i], str) else fb[i]
                        for i in range(len(fb))
                    ]
            else:
                entry = {"name": fname, "selector": str(fcfg), "extract": "text"}
            raw_fields.append(entry)

        colored = assign_field_colors(raw_fields)
        field_params = [
            PreviewFieldParams(
                name=f["name"],
                selector=f["selector"],
                extract=f.get("extract", "text"),
                attr=f.get("attr", ""),
                multiple=f.get("multiple", False),
                color=f["color"],
                fallback=f.get("fallback", []),
            )
            for f in colored
        ]

        preview_params = PreviewParams(fields=field_params, rule_name=args.rule, theme_mode=args.theme)

        async def _run_preview() -> PreviewResult:
            browser = PreviewBrowser(theme_mode=args.theme)
            run_task = asyncio.create_task(browser.run())
            try:
                _deadline = asyncio.get_running_loop().time() + 15.0
                while not browser._ready:
                    if run_task.done():
                        exc = run_task.exception()
                        if exc:
                            raise RuntimeError(f"Browser launch failed: {exc}") from exc
                        raise RuntimeError("Browser launch failed")
                    if asyncio.get_running_loop().time() > _deadline:
                        raise RuntimeError("Browser launch timeout (15s)")
                    await asyncio.sleep(0.05)
                _handle, result = await browser.open_page(args.url, preview_params, rule_name=args.rule)
                return result
            finally:
                browser.request_stop()
                if not run_task.done():
                    await run_task

        try:
            preview_result = asyncio.run(_run_preview())
        except Exception as exc:
            print(tr("Error: preview failed — {error}").format(error=exc), file=sys.stderr)
            sys.exit(1)

        print(
            json.dumps(
                {
                    "rule": args.rule,
                    "url": args.url,
                    "total": preview_result.total,
                    "matched": preview_result.matched,
                    "unmatched": preview_result.unmatched,
                    "fallback_activated": preview_result.fallback_activated,
                    "main_active": preview_result.main_active,
                    "fallback_count": preview_result.fallback_count,
                },
                indent=2,
                ensure_ascii=False,
            )
        )

    elif action == "edit":
        import os
        import subprocess
        import tempfile

        rule_path = _find_user_rule_file(args.name, extra_rules_dirs)
        if rule_path is None:
            if _rule_exists_in_readonly(args.name):
                print(
                    tr("Error: rule '{name}' is a preset or remote rule, not editable").format(name=args.name),
                    file=sys.stderr,
                )
            else:
                print(tr("Error: rule '{name}' does not exist").format(name=args.name), file=sys.stderr)
            sys.exit(1)

        if _rule_exists_in_readonly(args.name):
            print(
                tr(
                    "Warning: a preset/remote rule named '{name}' also exists — user version will not take effect. "
                    "Rename user rule to override."
                ).format(name=args.name),
                file=sys.stderr,
            )

        editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"

        original = rule_path.read_text(encoding="utf-8")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", prefix=f"{args.name}_", delete=False) as tmp:
            tmp.write(original)
            tmp_path = tmp.name

        ret = subprocess.call([editor, tmp_path])
        if ret != 0:
            Path(tmp_path).unlink(missing_ok=True)
            sys.exit(ret)

        try:
            from astrocrawl.rules import safe_read_rule_file
            from astrocrawl.rules import validate_rule as _validate_rule

            data = safe_read_rule_file(Path(tmp_path))
            _validate_rule(data)
            safe_write_rule_file(rule_path, data)
            print(tr("Rule '{name}' saved and validated.").format(name=args.name))
        except (ValueError, json.JSONDecodeError) as e:
            print(tr("Error: validation failed — {error}").format(error=e), file=sys.stderr)
            print(tr("Temporary file kept at: {path}").format(path=tmp_path), file=sys.stderr)
            print(tr("Fix and run: astrocrawl rules import {path} --overwrite").format(path=tmp_path), file=sys.stderr)
            sys.exit(1)
        else:
            Path(tmp_path).unlink(missing_ok=True)


def _add_proxy_add(sub) -> None:
    p = sub.add_parser("add", help=tr("Create new proxy Profile"))
    p.add_argument("name", help=tr("Profile name"))
    p.add_argument(
        "--proxies",
        required=True,
        nargs="+",
        help=tr("Proxy endpoint URLs (e.g. http://1.2.3.4:8080 socks5://5.6.7.8:1080)"),
    )
    p.add_argument("--bypass", nargs="+", default=[], help=tr("Bypass domain whitelist (e.g. .internal 192.168.*)"))


def _add_proxy_list(sub) -> None:
    sub.add_parser("list", help=tr("List all proxy Profiles"))


def _add_proxy_remove(sub) -> None:
    p = sub.add_parser("remove", help=tr("Delete proxy Profile"))
    p.add_argument("name", help=tr("Profile name"))


def _add_proxy_set(sub) -> None:
    p = sub.add_parser("set", help=tr("Set default proxy for a consumer"))
    p.add_argument("name", help=tr("Profile name"))
    p.add_argument(
        "--consumer",
        required=True,
        choices=["preview", "ai", "source"],
        help=tr("Consumer: preview | ai | source"),
    )
    p.add_argument(
        "--node",
        type=str,
        default="",
        help=tr("Proxy node TYPE:host:port (uses first endpoint if not specified)"),
    )


def _add_proxy_show(sub) -> None:
    p = sub.add_parser("show", help=tr("Show proxy Profile details"))
    p.add_argument("name", help=tr("Profile name"))


def _add_proxy_test(sub) -> None:
    p = sub.add_parser("test", help=tr("Test proxy endpoint connectivity"))
    p.add_argument("name", help=tr("Profile name"))


def _add_ai_add(sub) -> None:
    p = sub.add_parser("add", help=tr("Create new AI Profile"))
    p.add_argument("name", help=tr("Profile name"))
    p.add_argument("--provider", default="openai", help=tr("AI provider (default: openai)"))
    p.add_argument("--model", default="gpt-4o-mini", help=tr("Model name (default: gpt-4o-mini)"))
    p.add_argument("--api-key", default="", help=tr("API key"))
    p.add_argument("--endpoint", default="", help=tr("API endpoint (leave empty for default)"))
    p.add_argument("--temperature", type=float, default=0.1, help=tr("Generation temperature (default: 0.1)"))
    p.add_argument("--max-tokens", type=int, default=2048, help=tr("Max tokens (default: 2048)"))


def _add_ai_list(sub) -> None:
    sub.add_parser("list", help=tr("List all AI Profiles"))


def _add_ai_remove(sub) -> None:
    p = sub.add_parser("remove", help=tr("Delete AI Profile"))
    p.add_argument("name", help=tr("Profile name"))


def _add_ai_set_default(sub) -> None:
    p = sub.add_parser("set-default", help=tr("Set active AI Profile"))
    p.add_argument("name", help=tr("Profile name"))


def _add_ai_show(sub) -> None:
    p = sub.add_parser("show", help=tr("Show AI Profile details"))
    p.add_argument("name", help=tr("Profile name"))


def _add_ai_test(sub) -> None:
    p = sub.add_parser("test", help=tr("Test AI Profile connectivity"))
    p.add_argument("name", help=tr("Profile name"))


def _handle_proxy(args) -> None:
    """处理 proxy 子命令。"""
    from astrocrawl.proxy._config import ProxyEndpointSpec, ProxyProfile, endpoint_key
    from astrocrawl.proxy._consumers import PROXY_CONSUMERS
    from astrocrawl.proxy._probe import probe_one
    from astrocrawl.utils.preferences import get_preferences

    prefs = get_preferences()
    action = args.profile_action

    if action == "add":
        name = args.name
        if prefs.get_proxy_profile(name):
            print(tr("Error: Profile '{name}' already exists").format(name=name), file=sys.stderr)
            sys.exit(1)
        try:
            proxies = tuple(ProxyEndpointSpec.from_url(u) for u in args.proxies)
        except ValueError as e:
            print(tr("Error: invalid proxy endpoint URL — {msg}").format(msg=e), file=sys.stderr)
            sys.exit(1)
        # 端点去重校验
        seen: dict[str, int] = {}
        for i, ep in enumerate(proxies):
            key = endpoint_key(ep)
            if key in seen:
                first = proxies[seen[key]]
                print(
                    tr("Error: duplicate proxy endpoint — {key} (index {i1} vs {i2}, label='{l1}' vs '{l2}')").format(
                        key=key, i1=seen[key], i2=i, l1=first.label, l2=ep.label
                    ),
                    file=sys.stderr,
                )
                sys.exit(1)
            seen[key] = i
        profile = ProxyProfile(name=name, proxies=proxies, bypass_domains=tuple(args.bypass))
        prefs.save_proxy_profile(profile)
        print(tr("Profile '{name}' created ({n} endpoints)").format(name=name, n=len(proxies)))
        for ep in proxies:
            print(f"  {ep.label}  (type={ep.type.name}, port={ep.port})")

    elif action == "list":
        profiles = prefs.get_proxy_profiles()
        if not profiles:
            print(tr("(no proxy profiles)"))
            return
        print(
            "{Name:30s} {Endpoints:>6s}  {Bypass}".format(
                Name=tr("Name"), Endpoints=tr("Endpoints"), Bypass=tr("Bypass")
            )
        )
        print("-" * 60)
        for p in profiles:
            bypass = ", ".join(p.bypass_domains) if p.bypass_domains else tr("(none)")
            print(f"{p.name:30s} {len(p.proxies):>6d}  {bypass}")

    elif action == "remove":
        if prefs.get_proxy_profile(args.name) is None:
            print(tr("Error: Profile '{name}' does not exist").format(name=args.name), file=sys.stderr)
            sys.exit(1)
        prefs.remove_proxy_profile(args.name)
        print(tr("Profile '{name}' deleted.").format(name=args.name))

    elif action == "set":
        set_profile = prefs.get_proxy_profile(args.name)
        if set_profile is None:
            print(tr("Error: Profile '{name}' does not exist").format(name=args.name), file=sys.stderr)
            sys.exit(1)
        node = getattr(args, "node", "") or ""
        if not node and set_profile.proxies:
            first = set_profile.proxies[0]
            node = f"{first.type.name}:{first.host}:{first.port}"
        prefs.set_proxy_last_used(args.consumer, set_profile.uuid, node)
        label = PROXY_CONSUMERS.get(args.consumer, args.consumer)
        print(
            tr("Set '{consumer}' proxy to Profile '{name}', node={node}.").format(
                consumer=label, name=args.name, node=node
            )
        )

    elif action == "show":
        profile = prefs.get_proxy_profile(args.name)  # type: ignore[assignment]
        if profile is None:
            print(tr("Error: Profile '{name}' does not exist").format(name=args.name), file=sys.stderr)
            sys.exit(1)
        print(tr("Profile: {name}").format(name=profile.name))
        print(tr("Endpoints ({n}):").format(n=len(profile.proxies)))
        for ep in profile.proxies:
            auth = f" (auth: {ep.username})" if ep.username else ""
            host_display = f"[{ep.host}]" if ":" in ep.host else ep.host
            print(f"  - {ep.label}  type={ep.type.name}  {host_display}:{ep.port}  weight={ep.weight}{auth}")
        if profile.bypass_domains:
            print(tr("Bypass domains: {domains}").format(domains=", ".join(profile.bypass_domains)))
        else:
            print(tr("Bypass domains: (none)"))
        # 显示各 consumer 使用状态
        consumers_in_use = []
        for ck in ("preview", "ai", "source"):
            entry = prefs.get_proxy_last_used(ck)
            if entry and entry.get("profile") == profile.uuid:
                node = entry.get("node", "")
                node_info = f" ({node})" if node else ""
                consumers_in_use.append(f"{PROXY_CONSUMERS.get(ck, ck)}{node_info}")
        if consumers_in_use:
            print(tr("In use by: {consumers}").format(consumers=", ".join(consumers_in_use)))
        else:
            print(tr("In use by: (none)"))

    elif action == "test":
        import asyncio as _asyncio

        from astrocrawl.proxy._config import ProxyConfig

        profile = prefs.get_proxy_profile(args.name)  # type: ignore[assignment]
        if profile is None:
            print(tr("Error: Profile '{name}' does not exist").format(name=args.name), file=sys.stderr)
            sys.exit(1)
        if not profile.proxies:
            print(tr("Profile has no endpoints."))
            return

        config = ProxyConfig.from_profile(profile)

        async def _run_test():
            results = {}
            for parsed in config.proxies:
                result = await probe_one(parsed)
                results[parsed.to_url_with_auth()] = result
            return results

        results = _asyncio.run(_run_test())

        print(tr("Profile '{name}' endpoint connectivity test:").format(name=args.name))
        for parsed in config.proxies:
            url = parsed.to_url_with_auth()
            r = results[url]
            status = tr("reachable") if r.reachable else tr("unreachable")
            latency = f"  latency={r.latency_ms:.1f}ms" if r.latency_ms is not None else ""
            error = f"  error={r.error}" if r.error else ""
            print(f"  {parsed.type.name} {parsed.host}:{parsed.port}  {status}{latency}{error}")


def _handle_ai(args) -> None:
    """处理 ai 子命令。"""
    from astrocrawl.ai._profile import AIProfile
    from astrocrawl.ai._provider_registry import get_list_models_func
    from astrocrawl.utils.preferences import get_preferences

    prefs = get_preferences()
    action = getattr(args, "ai_profile_action", None)

    if action == "add":
        name = args.name
        if prefs.get_ai_profile(name):
            print(tr("Error: Profile '{name}' already exists").format(name=name), file=sys.stderr)
            sys.exit(1)
        profile = AIProfile(
            name=name,
            provider=args.provider,
            model=args.model,
            api_key=args.api_key,
            endpoint=args.endpoint,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
        )
        prefs.save_ai_profile(profile)
        print(tr("Profile '{name}' created:").format(name=name))
        print(f"  {tr('Provider')}: {profile.provider}")
        print(f"  {tr('Model')}: {profile.model}")
        if profile.api_key:
            masked = profile.api_key[:8] + "..." if len(profile.api_key) > 8 else profile.api_key
            print(f"  {tr('API Key')}: {masked}")
        if profile.endpoint:
            print(f"  {tr('Endpoint')}: {profile.endpoint}")
        print(f"  {tr('Temperature')}: {profile.temperature}")
        print(f"  {tr('Max Tokens')}: {profile.max_tokens}")

    elif action == "list":
        profiles = prefs.get_ai_profiles()
        if not profiles:
            print(tr("(no AI profiles)"))
            return
        active_name = prefs.get_active_profile_name()
        print(
            "{Name:30s} {Provider:12s} {Model:20s} {Status}".format(
                Name=tr("Name"), Provider=tr("Provider"), Model=tr("Model"), Status=tr("Status")
            )
        )
        print("-" * 75)
        for p in profiles:
            star = tr("☆ ") if p.name == active_name else "  "
            status = (
                tr("ok")
                if p.last_test_status == "ok"
                else (tr("failed") if p.last_test_status == "failed" else tr("untested"))
            )
            print(f"{star}{p.name:28s} {p.provider:12s} {p.model:20s} {status}")

    elif action == "remove":
        if prefs.get_ai_profile(args.name) is None:
            print(tr("Error: Profile '{name}' does not exist").format(name=args.name), file=sys.stderr)
            sys.exit(1)
        prefs.remove_ai_profile(args.name)
        print(tr("Profile '{name}' deleted.").format(name=args.name))

    elif action == "set-default":
        if prefs.get_ai_profile(args.name) is None:
            print(tr("Error: Profile '{name}' does not exist").format(name=args.name), file=sys.stderr)
            sys.exit(1)
        prefs.set_active_ai_profile(args.name)
        print(tr("Active AI Profile set to '{name}'.").format(name=args.name))

    elif action == "show":
        profile = prefs.get_ai_profile(args.name)  # type: ignore[assignment]
        if profile is None:
            print(tr("Error: Profile '{name}' does not exist").format(name=args.name), file=sys.stderr)
            sys.exit(1)
        print(tr("Name:         {name}").format(name=profile.name))
        print(tr("Provider:     {provider}").format(provider=profile.provider))
        print(tr("Model:        {model}").format(model=profile.model))
        masked = profile.api_key[:8] + "..." if len(profile.api_key) > 8 else (profile.api_key or tr("(none)"))
        print(tr("API Key:      {key}").format(key=masked))
        print(tr("Endpoint:     {endpoint}").format(endpoint=profile.endpoint or tr("(default)")))
        print(tr("Temperature:  {temp}").format(temp=profile.temperature))
        print(tr("Max Tokens:   {tokens}").format(tokens=profile.max_tokens))
        print(tr("Enabled:      {enabled}").format(enabled=profile.enabled))
        status = (
            tr("ok")
            if profile.last_test_status == "ok"
            else (tr("failed") if profile.last_test_status == "failed" else tr("untested"))
        )
        print(tr("Test Status:  {status}").format(status=status))
        if profile.last_test_time:
            print(tr("Last Tested:  {time}").format(time=profile.last_test_time))

    elif action == "test":
        profile = prefs.get_ai_profile(args.name)  # type: ignore[assignment]
        if profile is None:
            print(tr("Error: Profile '{name}' does not exist").format(name=args.name), file=sys.stderr)
            sys.exit(1)
        if not profile.api_key:
            print(tr("Error: Profile '{name}' has no API Key configured").format(name=args.name), file=sys.stderr)
            sys.exit(1)

        list_models = get_list_models_func(profile.provider)
        if list_models is None:
            print(
                tr("Error: Provider '{provider}' is not installed or does not support model listing").format(
                    provider=profile.provider
                ),
                file=sys.stderr,
            )
            sys.exit(1)

        import asyncio as _asyncio
        from dataclasses import replace
        from datetime import datetime, timezone

        async def _run_test():
            if _asyncio.iscoroutinefunction(list_models):
                return await list_models(profile.endpoint, profile.api_key, timeout=15.0)
            return list_models(profile.endpoint, profile.api_key, timeout=15.0)

        try:
            models = _asyncio.run(_run_test())
            print(tr("Profile '{name}' test: OK").format(name=args.name))
            print(tr("  Models available ({n}):").format(n=len(models)))
            for m in models[:20]:
                print(f"    - {m}")
            if len(models) > 20:
                print(tr("    ... and {n} more").format(n=len(models) - 20))
            updated = replace(
                profile,
                last_test_status="ok",
                last_test_time=datetime.now(timezone.utc).isoformat(),
            )
            prefs.save_ai_profile(updated)
        except Exception as e:
            print(tr("Profile '{name}' test: FAILED — {error}").format(name=args.name, error=e))
            updated = replace(
                profile,
                last_test_status="failed",
                last_test_time=datetime.now(timezone.utc).isoformat(),
            )
            prefs.save_ai_profile(updated)
            sys.exit(1)


def _handle_source(args) -> None:
    """处理 source 子命令。"""
    from astrocrawl.rules import (
        SourceManager,
        add_source_to_file,
        get_source_from_file,
        list_sources_from_file,
        remove_source_from_file,
        update_source_in_file,
    )
    from astrocrawl.utils.preferences import get_preferences

    prefs = get_preferences()
    source_proxy = prefs.get_parsed_proxy_for("source")
    source_proxy_url = source_proxy.to_url_with_auth() if source_proxy else None

    action = args.source_action

    if action == "add":
        name = args.name or _derive_source_name(args.url)
        if not args.confirm:
            print(tr("Will add remote source:"))
            print(tr("  Name: {name}").format(name=name))
            print(tr("  URL:  {url}").format(url=args.url))
            print(tr("Use --confirm to proceed."))
            return
        try:
            entry = add_source_to_file(name, args.url)
            print(tr("Source '{name}' added: {url}").format(name=entry["name"], url=entry["url"]))
        except ValueError as e:
            print(tr("Error: {msg}").format(msg=e), file=sys.stderr)
            sys.exit(1)

    elif action == "remove":
        if remove_source_from_file(args.name):
            print(tr("Source '{name}' removed.").format(name=args.name))
        else:
            print(tr("Error: source '{name}' does not exist").format(name=args.name), file=sys.stderr)
            sys.exit(1)

    elif action == "list":
        sources = list_sources_from_file()
        if args.format == "json":
            print(json.dumps([{"name": s["name"], "url": s["url"]} for s in sources], indent=2, ensure_ascii=False))
        elif not sources:
            print(tr("(no remote sources)"))
        else:
            print("{Name:30s} {URL:50s}".format(Name=tr("Name"), URL=tr("URL")))
            print("-" * 85)
            for s in sources:
                print(f"{s.get('name', ''):30s} {s.get('url', ''):50s}")

    elif action == "update":
        import asyncio as _asyncio

        import aiohttp

        target_name = args.name if args.name else None
        if not target_name and not args.all:
            print(tr("Error: specify --name <source_name> or --all"), file=sys.stderr)
            return

        async def _run_update():
            async with aiohttp.ClientSession() as session:
                cache_dir = Path.home() / ".astrocrawl" / "rules_cache"
                cache_dir.mkdir(parents=True, exist_ok=True)
                mgr = SourceManager(session, cache_dir, auto_update=True, proxy_url=source_proxy_url)
                try:
                    if target_name:
                        result = await mgr.update_source(target_name, dry_run=args.dry_run)
                        updated = [target_name] if result.get("updated") else []
                    else:
                        result = await mgr.update_all(dry_run=args.dry_run)
                        updated = result.get("sources_updated", [])
                except Exception as e:
                    print(tr("Error: update failed — {error}").format(error=e), file=sys.stderr)
                    sys.exit(1)
                if args.dry_run:
                    print(tr("Preview complete."))
                elif updated:
                    print(tr("Updated sources: {sources}").format(sources=", ".join(updated)))
                else:
                    print(tr("All sources up to date."))

        _asyncio.run(_run_update())

    elif action == "info":
        info_entry = get_source_from_file(args.name)
        if not info_entry:
            print(tr("Error: source '{name}' does not exist").format(name=args.name), file=sys.stderr)
            sys.exit(1)
        if args.format == "json":
            print(json.dumps(info_entry, indent=2, ensure_ascii=False))
        else:
            print(tr("Name:       {name}").format(name=info_entry.get("name", "")))
            print(tr("URL:        {url}").format(url=info_entry.get("url", "")))
            print(tr("Title:      {title}").format(title=info_entry.get("title", "")))
            print(tr("Maintainer: {maintainer}").format(maintainer=info_entry.get("maintainer", "")))
            print(tr("Homepage:   {homepage}").format(homepage=info_entry.get("homepage", "")))

    elif action == "edit":
        src_entry = get_source_from_file(args.name)
        if not src_entry:
            print(tr("Error: source '{name}' does not exist").format(name=args.name), file=sys.stderr)
            sys.exit(1)

        updates: dict = {}
        if args.new_name:
            updates["name"] = args.new_name
        if args.url:
            from astrocrawl.rules import validate_source_url

            try:
                validate_source_url(args.url)
            except ValueError as e:
                print(tr("Error: invalid URL — {error}").format(error=e), file=sys.stderr)
                sys.exit(1)
            updates["url"] = args.url
            # URL 变更时清除旧缓存
            if args.url != src_entry.get("url", ""):
                import shutil

                cache_dir = Path.home() / ".astrocrawl" / "rules_cache" / src_entry.get("name", "")
                if cache_dir.exists():
                    shutil.rmtree(cache_dir)
                    print(tr("URL changed, cache cleared: {path}").format(path=cache_dir))

        if not updates:
            print(tr("Error: specify --name or --url at least"), file=sys.stderr)
            sys.exit(1)

        ok = update_source_in_file(args.name, **updates)
        if not ok:
            print(tr("Error: failed to update source '{name}'").format(name=args.name), file=sys.stderr)
            sys.exit(1)
        print(tr("Source '{name}' updated:").format(name=args.name))
        for k, v in updates.items():
            print(f"  {k}: {v}")

    elif action == "validate":
        src_entry = get_source_from_file(args.name)
        if not src_entry:
            print(tr("Error: source '{name}' does not exist").format(name=args.name), file=sys.stderr)
            sys.exit(1)

        url = src_entry.get("url", "")
        from astrocrawl.rules import validate_source_url

        try:
            validate_source_url(url)
        except ValueError as e:
            print(tr("Error: invalid URL — {error}").format(error=e), file=sys.stderr)
            sys.exit(1)

        print(tr("Validating source '{name}' ({url})...").format(name=args.name, url=url))
        try:
            import aiohttp

            from astrocrawl.rules import SourceManager

            async def _do_validate():
                cache_dir = Path.home() / ".astrocrawl" / "rules_cache"
                cache_dir.mkdir(parents=True, exist_ok=True)
                async with aiohttp.ClientSession() as session:
                    mgr = SourceManager(session, cache_dir, auto_update=False, proxy_url=source_proxy_url)
                    manifest = await mgr.fetch_manifest(args.name)
                    return manifest

            manifest = asyncio.run(_do_validate())
            rules_count = len(manifest.get("rules", []))
            print(tr("Source '{name}' valid — manifest reachable, {n} rules").format(name=args.name, n=rules_count))
        except Exception as e:
            print(tr("Source '{name}' validation failed — {error}").format(name=args.name, error=e), file=sys.stderr)
            sys.exit(1)


def _derive_source_name(url: str) -> str:
    """从 URL 派生默认源名称。"""
    from urllib.parse import urlparse

    hostname = urlparse(url).hostname or "unknown"
    return hostname.replace(".", "_").replace("-", "_")


# ═══════════════════════════════════════════════════════════════════
# Main CLI Entry
# ═══════════════════════════════════════════════════════════════════


def main_cli() -> None:
    args = parse_args()

    # 子命令路由
    if args.subcommand == "rules":
        _handle_rules(args)
        return
    if args.subcommand == "source":
        _handle_source(args)
        return
    if args.subcommand == "proxy":
        _handle_proxy(args)
        return
    if args.subcommand == "ai":
        _handle_ai(args)
        return

    # 爬取路径（向后兼容 N85）
    resolved = _merge_cli_config(args)
    if resolved is None:
        return

    cfg = resolved["cfg"]
    global_settings = resolved["global_settings"]
    setup_root_logger(global_settings.log_level, cfg.log_file)
    cfg.print_report()

    urls = resolved["urls"]
    depth = resolved["depth"]
    proxy_profile = resolved["proxy_profile"]
    proxy_mode_override = resolved["proxy_mode_override"]
    output = resolved["output"]
    same_domain = resolved["same_domain"]

    async def run() -> None:
        crawler = create_crawler(
            start_urls=urls,
            depth=depth,
            concurrency=cfg.concurrency,
            output_path=output,
            same_domain_only=same_domain,
            cfg=cfg,
            global_settings=global_settings,
            proxy_profile=proxy_profile,
            proxy_mode_override=proxy_mode_override,
        )
        cleanup = _install_signal_handlers(crawler)
        try:
            await crawler.run()
            if crawler.reporter is not None:
                crawler.reporter.print_summary(str(crawler.output_path), crawler.last_report)
        finally:
            cleanup()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


def _install_signal_handlers(crawler: AsyncCrawler):
    loop = asyncio.get_running_loop()
    shutdown_requested = False

    def _on_signal() -> None:
        nonlocal shutdown_requested
        if shutdown_requested:
            return
        shutdown_requested = True
        print(tr("Termination signal received, shutting down gracefully..."), file=sys.stderr)
        crawler.request_stop()

    for sig_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig_name, _on_signal)
        except (NotImplementedError, ValueError):
            pass

    def _cleanup() -> None:
        for sig_name in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.remove_signal_handler(sig_name)
            except (NotImplementedError, RuntimeError, ValueError):
                pass

    return _cleanup

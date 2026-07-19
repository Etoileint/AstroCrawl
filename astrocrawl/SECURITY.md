# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in AstroCrawl, please **do not** report it via a public GitHub Issue.

Instead, send an email to **etoileint@163.com** with details of the vulnerability. Include:

- A description of the vulnerability
- Steps to reproduce
- Affected versions (if known)

I will respond as soon as possible. Once the vulnerability is confirmed and fixed, I will publish a security advisory and credit you (if you wish).

**Disclosure timeline**: I aim to acknowledge reports within 48 hours, confirm within 7 days, and release a fix within 30 days for critical issues. Coordinated disclosure is preferred — please allow time for a fix before publishing.

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

## Scope

Security issues of particular concern include:

- **Credential leaks**: proxy credentials in logs, debug output, or child process arguments
- **API key exposure**: keys in configuration files, CLI output, or AI provider request logs
- **RCE**: remote code execution through rule sources, AI-generated content, or deserialization
- **SSRF**: server-side request forgery through URL handling, DNS rebinding, or redirect chains
- **Injection**: prompt injection through AI inputs, CSS selector injection in extraction rules
- **Data integrity**: rule file tampering, manifest hash mismatch, TOCTOU race conditions
- **Information disclosure**: sensitive data in error messages, health endpoints, or exported reports
- **Denial of service**: ReDoS, unbounded caching, memory amplification in transforms

## Security Design Principles

These are the security guarantees the codebase provides. When adding features, ensure these invariants hold.

### Credential Protection

- **API key masking**: `AIProfile.__repr__` masks keys to first 8 chars + `"..."`. `AIConfig.api_key` is `field(repr=False)`. `CrawlerConfig` marks `auth_basic_pass`, `auth_bearer_token`, `webhook_url`, and `custom_headers` as `repr=False`.
- **Proxy URL redaction**: `redact_proxy_url()` strips credentials before logging. Never log raw proxy URLs.
- **File permissions**: Output files and Preferences are written with `chmod 0o600`. `atomic_write_json()` enforces this at the POSIX layer.

### Rule Security

- **HTTPS-only remote sources**: Rule manifests and rule files are fetched exclusively over HTTPS.
- **SHA256 manifest verification**: Remote rule sources are verified against SHA256 hashes before loading. Manifest format is versioned (`schema_version`), and versions < 1 or > 2 are rejected to prevent downgrade attacks.
- **ReDoS immunity**: `google-re2` is a hard dependency — all rule URL patterns and transforms use linear-time regex. No stdlib `re` fallback exists.
- **DNS rebinding protection**: 12 IP ranges are blocked at the DNS resolution layer (CGNAT, benchmark, loopback, IPv4-mapped IPv6, etc.).
- **Unicode sanitization**: Display text fields (`display_name`, `author`, `description`) are sanitized for bidi control characters, isolates, and C0 controls per Unicode TR36.
- **Atomic file writes**: `atomic_write_json()` uses `mkstemp → write → fsync → os.replace → chmod`, protecting against crash-induced corruption.
- **Concurrent access**: `fcntl.flock` (LOCK_EX/LOCK_SH) on `.lock` files for `rules_state.json` and `sources.json` — kernel-released on process exit, no deadlock risk.

### AI Safety

- **5-layer prompt injection defense** (OWASP LLM01):
  1. URL structural reconstruction (`urlparse → urlunparse`) drops control characters
  2. Field requirement validation per `RULE_NAME_PATTERN` — illegal chars discarded with WARNING
  3. User content wrapped in `<html_source>...</html_source>` XML boundary tags
  4. Output validation via `validate_rule()` — the only non-bypassable gate
  5. Human-in-the-loop: preview → confirm → save workflow
- **Structured output**: `OutputConstraint` with `json_schema → json_object → off` capability-aware degradation. Schema normalization fixes 5 categories of Pydantic-to-OpenAI-strict-mode violations.

### Defense in Depth

- **Extraction layer truncation**: `max_text_length` enforced at extraction time (byte-aware), not just at transform — prevents OOM from overly broad selectors.
- **Transform amplification guard**: Two independent gates — absolute byte ceiling (S27) and proportional guard (N104, `TRANSFORM_MEMORY_MULTIPLIER`).
- **L1 validation gate**: `validate_rule()` is called at the persistence layer (`safe_write_rule_file`) — cannot be bypassed regardless of code path (import, AI generation, remote download, GUI edit).

### Operational Security

- **Chromium sandbox**: Playwright Chromium runs with `--log-level=3` to suppress debug output that may contain proxy credentials. Context pool isolates page contexts.
- **Config immutability**: `CrawlerConfig(frozen=True)` and `AIProfile(frozen=True)` — shared across coroutines safely, modified only via `replace()`.
- **Graceful degradation**: robots.txt fetch failure → fail-open. Rule loading failure → keep last valid snapshot. Preferences corruption → auto-delete + fallback defaults. Each failure mode defaults to safe operation.
- **Fail-fast startup**: Proxy-requiring modes raise `ConfigError` when no proxies are configured — prevents silent degradation that could expose direct IP connections.

## Out of Scope

The following are explicitly NOT considered security vulnerabilities in AstroCrawl:

- **Crawled website vulnerabilities**: AstroCrawl is a tool — it crawls websites as configured. XSS, SQL injection, or other vulnerabilities in crawled websites are the responsibility of the website owner.
- **User-configured rule behavior**: Rules that extract excessive data or match unintended pages are configuration issues, not vulnerabilities.
- **AI provider security**: The security of third-party AI API endpoints (OpenAI, Anthropic, Google) is outside AstroCrawl's scope. We validate output structure, not content safety.
- **Denial of service against crawled targets**: Rate limiting is configurable. Aggressive crawling without rate limits is a misuse issue.

## Known Limitations

- **HTTP health endpoint**: `/health` (port 9090) has no built-in authentication. It exposes crawl status, queue depth, and worker states. Deploy behind a reverse proxy with authentication if exposed beyond localhost.
- **GUI Content Security Policy**: Qt6 QWebEngine does not support CSP headers in embedded Chromium. The Preview panel renders untrusted HTML — rule selectors are validated server-side, but CSS/JS in previewed pages executes in the browser context.
- **SourceManager and proxy**: Remote rule sources do not route through proxy (architectural limitation, not a bug). This is documented in ADR-0010 and tracked as a forward gap.
- **Thread pool isolation**: Extraction uses a dedicated `ThreadPoolExecutor(max_workers=4)`. Python threads have no cancellation mechanism — a stuck extraction worker consumes a slot until process exit. The dedicated pool isolates this from the main event loop.
- **Output file race conditions**: Output files are written with atomic `os.replace()`, but the output directory itself is user-specified. Other processes with write access to the output directory could observe or modify intermediate crawl results.
- **CLI config override**: `--set KEY=VALUE` accepts arbitrary config key paths. Invalid keys are rejected via `__dataclass_fields__` type coercion, but the override mechanism itself is a privileged operation — only expose CLI access to trusted users.
- **Rate limiting**: Proxy circuit breakers and domain rate limiters protect upstream infrastructure, not AstroCrawl itself. A malicious local user with CLI access can disable all rate limits and exhaust proxy resources.

## Secure Development

When contributing to AstroCrawl, follow these security practices:

- **Private by default**: All new modules use `_` prefix for internal functions. Only export the minimal public API in `__init__.py`.
- **Dataclass fields**: Mark sensitive fields with `repr=False`. Use `frozen=True` for shared configuration objects.
- **Logging**: Use `_log_safe_url()` for any URL that may contain credentials. Follow logfmt `event=xxx key=value` format — no raw URLs or keys in log messages.
- **Atomic I/O**: Use `atomic_write_json()` for all JSON persistence. Never write directly to the target path — always use temp file + fsync + rename.
- **Validation gates**: All data persistence paths must go through `validate_rule()` or equivalent validation. Add new gates at the persistence layer, not at individual call sites.

# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in AstroProject, please **do not** report it via a public GitHub Issue.

Instead, send an email to **etoileint@163.com** with details of the vulnerability. Include:

- A description of the vulnerability
- Steps to reproduce
- Affected package(s) and versions

I will respond as soon as possible. Once confirmed and fixed, I will publish a security advisory and credit you (if you wish).

**Disclosure timeline**: Acknowledge within 48 hours, confirm within 7 days, release a fix within 30 days for critical issues. Coordinated disclosure is preferred — please allow time for a fix before publishing.

## Supported Versions

| Package | Version | Supported |
|---------|---------|-----------|
| astrobase | 0.1.x | :white_check_mark: |
| astroframe | 0.1.x | :white_check_mark: |
| astrocrawl | 0.1.x | :white_check_mark: |
| astroflow | — | Planning |

## Scope

Security concerns relevant to each package:

### astrobase (天枢 — Pure Mechanism)

- **Log integrity**: logfmt output must not leak sensitive data
- **Atomic I/O correctness**: crash-safe file writes, TOCTOU hardening

### astroframe (筑穹 — Plugin Platform)

- **Sandbox escape**: seccomp-bpf / Landlock / PEP 578 bypass attempts
- **Signature spoofing**: sigstore/GPG verification bypass
- **Manifest tampering**: capability declaration forgery
- **IPC injection**: malicious payloads over multiprocessing.connection
- **AST scan evasion**: code patterns that bypass S5 security scanning

### astrocrawl (摘星 — Crawler Engine)

- **Credential leaks**: proxy credentials in logs, debug output, or child process arguments
- **API key exposure**: keys in configuration, CLI output, or AI provider request logs
- **RCE**: remote code execution through rule sources, AI-generated content, or deserialization
- **SSRF**: server-side request forgery through URL handling, DNS rebinding, or redirect chains
- **Injection**: prompt injection through AI inputs, CSS selector injection in extraction rules
- **Data integrity**: rule file tampering, manifest hash mismatch, TOCTOU race conditions
- **Information disclosure**: sensitive data in error messages, health endpoints, or exported reports
- **Denial of service**: ReDoS, unbounded caching, memory amplification in transforms

### astroflow (织霞 — Workflow Engine)

- Planning phase — security model TBD.

## Per-Package Security Documentation

Each package maintains its own `SECURITY.md` with detailed design principles, known limitations, and secure development guidelines specific to its domain.

- [astrobase/SECURITY.md](astrobase/SECURITY.md)
- [astroframe/SECURITY.md](astroframe/SECURITY.md)
- [astrocrawl/SECURITY.md](astrocrawl/SECURITY.md)
- [astroflow/SECURITY.md](astroflow/SECURITY.md)

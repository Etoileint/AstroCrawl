# Security Policy — astroframe

## Scope

astroframe is the universal plugin platform runtime. Its security model is the foundation for all plugins in the AstroProject ecosystem.

Security concerns:

- **Sandbox escape**: seccomp-bpf / Landlock / PEP 578 bypass
- **Signature spoofing**: sigstore/GPG verification bypass
- **Manifest tampering**: capability declaration forgery or injection
- **IPC injection**: malicious payloads over multiprocessing.connection
- **AST scan evasion**: code patterns that bypass S5 security scanning
- **Privilege escalation**: plugin gaining capabilities beyond its manifest declaration

## Security Architecture

### Defense-in-Depth (4 Layers)

1. **S5 AST Scanner** — Causal-root tracing (7 roots), two-dimension enforcement. Load-time gate: blocks plugin code from entering the process.
2. **S9 seccomp-bpf** — Syscall whitelist, kernel-level blocking.
3. **S10 Landlock** — Filesystem ACL, kernel-level blocking.
4. **S8 PEP 578** — Audit hooks, runtime verification.

### Fail-Closed

Any security mechanism failure → affected plugin unavailable, rest operate normally. There is no "degraded security" mode.

### Subprocess Isolation

All plugins run in isolated child processes via `multiprocessing.connection` IPC. Unified sandbox principle — same isolation regardless of trust level.

### Manifest as SSOT

`astroframe-plugin.json` is the sole source of truth for plugin capabilities. Entry points only signal package existence. No capability metadata is derived from Python imports.

## Out of Scope

- **Plugin logic bugs**: A plugin that correctly declares a Processor capability but has bugs in its processing logic is a plugin issue, not a platform vulnerability.
- **Resource exhaustion**: Plugin subprocess memory/CPU limits are configured by the engine consumer, not enforced by astroframe itself.

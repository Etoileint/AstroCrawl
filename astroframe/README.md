# astroframe <em>筑穹</em>

> 筑其基者，擎九霄穹

Universal plugin platform runtime — Protocol-based capability registration, subprocess sandbox (seccomp-bpf + Landlock + PEP 578 audit hooks), sigstore/GPG signature verification, and AST security scanning. All plugins run in isolated subprocess sandboxes.

## Features

- **Plugin Discovery** — `entry_points`-based discovery with manifest (`astroframe-plugin.json`) as single source of truth
- **Capability Registry** — Protocol-typed capability groups (Processor, Exporter, ChatProvider, Transform, CLI subcommand, GUI page, etc.)
- **Four-Layer Security** — S5 AST scanner → S9 seccomp-bpf syscall whitelist → S10 Landlock filesystem ACL → S8 PEP 578 audit hooks
- **Subprocess Sandbox** — Every plugin runs in an isolated child process with IPC via `multiprocessing.connection`
- **Signature Verification** — Sigstore / GPG plugin signature verification pipeline
- **Schema Validation** — JSON Schema-based plugin config validation at load time
- **Lifecycle Management** — LOADED → ENABLED → DISABLED → ERROR state machine with crash recovery and dependency cascade resolution
- **Fail-Closed** — Any security mechanism failure → affected plugin unavailable, rest operate normally
- **Plugin Error Isolation** — Engine always starts; single plugin failure blast radius limited to its capabilities

## Install

```bash
pip install astroframe
```

With sigstore signature verification:

```bash
pip install "astroframe[sigstore]"
```

## Quick Start

```python
from astroframe import PluginRegistry, discover_plugins

# Discover plugins via entry_points
manifests = discover_plugins()

# Register and validate
registry = PluginRegistry()
registry.register_all(manifests)

# Lifecycle — load and enable
registry.load_all()
registry.enable_all()
```

## Architecture

```
entry_points → manifest → AST scan → subprocess sandbox
                                       ├── seccomp-bpf (syscall whitelist)
                                       ├── Landlock (filesystem ACL)
                                       └── PEP 578 (audit hooks)
```

All plugins — including first-party consumers like astrocrawl — run through the same pipeline. No BUILTIN concept, no special-cased identity.

## Plugin Manifest

Plugins declare themselves via `astroframe-plugin.json`:

```json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "group": "processor",
  "capabilities": [...],
  "entry_point": "my_plugin:factory",
  "config_schema": {...}
}
```

Entry point in `pyproject.toml`:

```toml
[project.entry-points."astroframe.plugins"]
my-plugin = "my_plugin:get_manifest_path"
```

## Optional Dependencies

| Extra | Purpose |
|-------|---------|
| `sigstore` | Sigstore-based plugin signature verification |

## License

Apache 2.0

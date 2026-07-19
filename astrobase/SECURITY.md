# Security Policy — astrobase

## Scope

astrobase is the pure mechanism layer — zero hard dependencies beyond Python 3.12 stdlib. It provides four modules used by all other packages in the monorepo.

Security concerns are narrowly scoped:

- **Log integrity**: `LogfmtLogger` must not emit sensitive data. Callers are responsible for redacting credentials before passing them as logfmt key=value pairs.
- **Atomic I/O correctness**: `atomic_write_json()` must be crash-safe. The implementation (mkstemp → write → fsync → os.replace → chmod 0o600) guarantees readers never see partial data.
- **TOCTOU**: File permissions (`chmod 0o600`) are set before data becomes visible at the target path via `os.replace()`.
- **JSON compatibility**: `_json_compat` delegates to orjson or stdlib json. Neither path introduces injection risk for JSON-serializable Python objects.

## Out of Scope

astrobase has no network, no subprocess, no filesystem traversal, and no user-controlled code execution paths. The following are callers' responsibility:

- **What gets logged**: astrobase provides the logging mechanism; callers decide what data to log.
- **What gets written**: astrobase provides atomic file I/O; callers decide what data to persist.

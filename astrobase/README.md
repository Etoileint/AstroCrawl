# astrobase <em>天枢</em>

Pure mechanism layer for the Astro ecosystem. Zero hard dependencies beyond Python 3.12 stdlib.

## Install

```bash
pip install astrobase
```

For JSON performance acceleration:

```bash
pip install astrobase[fast]
```

Or from source:

```bash
pip install -e .
```

## What's inside

| Module | Purpose |
|--------|---------|
| `_logging` | Structured logfmt logging — mandatory event field, key=value output |
| `_atomic` | POSIX atomic file writes — mkstemp → fsync → os.replace |
| `_json_compat` | orjson / stdlib json compatibility layer with graceful fallback |
| `_types` | Shared protocols — `AsyncCloseable` for async resource lifecycle |

## Usage

### Structured logging

```python
from astrobase import LogfmtLogger

logger = LogfmtLogger("my_module")
logger.info("request_complete", url="https://example.com", status=200)
# → ts=2026-07-17T... level=INFO logger=my_module event=request_complete url=https://example.com status=200
```

### Atomic file writes

```python
from astrobase import atomic_write_json

data = {"key": "value", "nested": [1, 2, 3]}
atomic_write_json("/path/to/config.json", data)
# POSIX atomic: write to temp → fsync → os.replace. Concurrent reads always see complete data.
```

### JSON compatibility

```python
from astrobase import _json_dumps

data = {"items": ["a", "b", "c"]}
result = _json_dumps(data)
# Uses orjson if installed (`pip install astrobase[fast]`), stdlib json otherwise.
```

### Async resource protocol

```python
from astrobase import AsyncCloseable

class MyResource(AsyncCloseable):
    async def aclose(self) -> None:
        await self._client.close()
```

## License

Apache 2.0

"""astroweave — Workflow engine for the Astro ecosystem (ADR-0014).

Orchestrate crawl pipelines, AI extraction, and rules-based processing
as composable DAG workflows. Registered as an astroframe platform plugin.
"""

from __future__ import annotations

from astroweave._version import __version__, __version_info__

__all__ = ["__version__", "__version_info__"]

"""astroguard — Global monitoring and observability for the Astro ecosystem (ADR-0014).

Unified observability panel — health checks, anomaly detection, and system-wide
monitoring. Observes but does not execute. Registered as an astroframe platform plugin.
"""

from __future__ import annotations

from astroguard._version import __version__, __version_info__

__all__ = ["__version__", "__version_info__"]

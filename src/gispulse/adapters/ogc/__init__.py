"""OGC service adapters — WFS / OGC API Features clients and fetchers.

Importing this package self-registers the core OGC transport adapters
in :data:`core.sources.PROTOCOLS` (issue #192), so the ETL fetch path
has a real WFS fetcher to dispatch to.
"""

from __future__ import annotations

# Side-effect import: WfsFetcher registers itself in PROTOCOLS on import.
from gispulse.adapters.ogc import wfs_fetcher  # noqa: F401

__all__ = ["wfs_fetcher"]

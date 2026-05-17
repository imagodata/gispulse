"""STAC service adapter — SpatioTemporal Asset Catalog fetcher.

Importing this package self-registers the core STAC transport adapter
in :data:`core.sources.PROTOCOLS` (issue #192), so the ETL fetch path
has a real STAC fetcher to dispatch to.
"""

from __future__ import annotations

# Side-effect import: StacFetcher registers itself in PROTOCOLS on import.
from gispulse.adapters.stac import stac_fetcher  # noqa: F401

__all__ = ["stac_fetcher"]
